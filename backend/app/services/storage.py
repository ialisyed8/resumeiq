"""
Object storage abstraction over S3/MinIO.

PDFs never go in Postgres. Storage keys are randomised — the uploaded filename
is untrusted and is kept only as a display label.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from app.core.config import settings
from app.core.errors import UploadError
from app.core.logging import get_logger

logger = get_logger(__name__)

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt",
    ".png", ".jpg", ".jpeg", ".tiff", ".webp",
}
ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.oasis.opendocument.text",
    "application/rtf", "text/rtf", "text/plain",
    "image/png", "image/jpeg", "image/tiff", "image/webp",
}

#: Magic bytes. Extension and Content-Type are both attacker-controlled; the
#: file signature is the only one that reflects actual content.
MAGIC = {
    b"%PDF-": "application/pdf",
    b"PK\x03\x04": "application/zip",  # docx/odt are zip containers
    b"\xd0\xcf\x11\xe0": "application/msword",  # OLE2
    b"{\\rtf": "application/rtf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"II*\x00": "image/tiff",
    b"MM\x00*": "image/tiff",
}

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
MAX_ZIP_RATIO = 120  # guards against decompression bombs in docx/odt


@dataclass(slots=True)
class StoredFile:
    storage_key: str
    filename: str
    mime_type: str
    size_bytes: int
    file_hash: str


def sanitise_filename(name: str) -> str:
    """Strip paths and dangerous characters. Never trust an uploaded filename."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = _SAFE_NAME.sub("_", base)[:200]
    return base or "upload"


def sniff_mime(head: bytes) -> str | None:
    for signature, mime in MAGIC.items():
        if head.startswith(signature):
            return mime
    return None


def validate_upload(filename: str, content: bytes, declared_mime: str | None = None) -> str:
    """
    Validate an upload before it is stored. Returns the resolved MIME type.

    Checks extension, size, and — decisively — the file signature.
    """
    if not content:
        raise UploadError("That file is empty.")

    if len(content) > settings.MAX_UPLOAD_BYTES:
        limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadError(f"That file is larger than the {limit} MB limit.")

    suffix = PurePosixPath(sanitise_filename(filename)).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise UploadError(
            f"{suffix or 'That file type'} is not supported. Upload a PDF or DOCX."
        )

    actual = sniff_mime(content[:16])
    if actual is None:
        raise UploadError(
            "That file's contents do not match any supported document format."
        )

    # A .pdf whose bytes say zip is either broken or an attempt at something.
    if suffix == ".pdf" and actual != "application/pdf":
        raise UploadError("That file is named .pdf but is not a PDF document.")
    if suffix in {".docx", ".odt"} and actual != "application/zip":
        raise UploadError(f"That file is named {suffix} but is not a valid document.")

    if actual == "application/zip":
        _check_zip_bomb(content)

    resolved = (
        declared_mime
        if declared_mime in ALLOWED_MIME
        else (mimetypes.guess_type(filename)[0] or actual)
    )
    if resolved not in ALLOWED_MIME:
        resolved = actual
    return resolved


def _check_zip_bomb(content: bytes) -> None:
    """Reject archives whose declared uncompressed size is implausible."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            uncompressed = sum(info.file_size for info in archive.infolist())
            if uncompressed > len(content) * MAX_ZIP_RATIO:
                raise UploadError("That document could not be accepted.")
            for info in archive.infolist():
                if info.filename.startswith("/") or ".." in info.filename:
                    raise UploadError("That document could not be accepted.")
    except zipfile.BadZipFile as exc:
        raise UploadError("That document appears to be corrupted.") from exc


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_key(org_id: str, filename: str) -> str:
    """Randomised, tenant-scoped storage key. Not derived from user input."""
    today = datetime.now(UTC).strftime("%Y/%m/%d")
    suffix = PurePosixPath(sanitise_filename(filename)).suffix.lower()
    return f"orgs/{org_id}/resumes/{today}/{uuid.uuid4().hex}{suffix}"


class ObjectStorage:
    """S3-compatible storage. Same code path for MinIO and AWS."""

    def __init__(self):
        self._client = None

    def _s3(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT or None,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
                use_ssl=settings.S3_USE_SSL,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            )
        return self._client

    def ensure_bucket(self) -> None:
        client = self._s3()
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET)
        except Exception:
            client.create_bucket(Bucket=settings.S3_BUCKET)
            logger.info("bucket_created", bucket=settings.S3_BUCKET)

    def put(self, key: str, content: bytes, mime_type: str) -> None:
        """
        Store an object.

        Server-side encryption is opt-in via S3_SERVER_SIDE_ENCRYPTION because
        MinIO rejects the whole request when SSE is requested without KMS
        configured. Turn it on for production S3, where it should be on.
        """
        params = {
            "Bucket": settings.S3_BUCKET,
            "Key": key,
            "Body": content,
            "ContentType": mime_type,
        }
        sse = (settings.S3_SERVER_SIDE_ENCRYPTION or "").strip()
        if sse and sse.lower() not in {"none", "false", "off"}:
            params["ServerSideEncryption"] = sse
        self._s3().put_object(**params)

    def get(self, key: str) -> bytes:
        return self._s3().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self._s3().delete_object(Bucket=settings.S3_BUCKET, Key=key)

    def presign_put(self, key: str, mime_type: str) -> str:
        return self._s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": mime_type},
            ExpiresIn=settings.PRESIGNED_URL_TTL_SECONDS,
        )

    def presign_get(self, key: str) -> str:
        return self._s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET, "Key": key},
            ExpiresIn=settings.PRESIGNED_URL_TTL_SECONDS,
        )

    def download_to_temp(self, key: str, suffix: str = "") -> str:
        import tempfile

        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        handle.write(self.get(key))
        handle.close()
        return handle.name


storage = ObjectStorage()
