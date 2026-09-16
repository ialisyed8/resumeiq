"""File upload validation. Uploaded resumes are untrusted input."""
import io
import zipfile

import pytest

from app.core.errors import UploadError
from app.services.storage import (
    build_key,
    content_hash,
    sanitise_filename,
    sniff_mime,
    validate_upload,
)

PDF = b"%PDF-1.7\n" + b"x" * 500
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 500


def make_docx(entries=None, size=400):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<xml/>" * (size // 6))
        for name, content in (entries or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestFilenameHandling:
    @pytest.mark.parametrize("evil,expected_not", [
        ("../../../etc/passwd", ".."),
        ("..\\..\\windows\\system32\\cfg", ".."),
        ("/absolute/path/resume.pdf", "/"),
        ("resume\x00.pdf", "\x00"),
    ])
    def test_path_traversal_stripped(self, evil, expected_not):
        assert expected_not not in sanitise_filename(evil)

    def test_shell_metacharacters_removed(self):
        cleaned = sanitise_filename("resume; rm -rf /.pdf")
        assert ";" not in cleaned and " " not in cleaned

    def test_empty_name_gets_fallback(self):
        assert sanitise_filename("") == "upload"

    def test_storage_key_is_not_derived_from_filename(self):
        key = build_key("org-1", "../../evil.pdf")
        assert ".." not in key
        assert "evil" not in key
        assert key.startswith("orgs/org-1/resumes/")

    def test_storage_keys_are_unique(self):
        a = build_key("org-1", "resume.pdf")
        b = build_key("org-1", "resume.pdf")
        assert a != b


class TestMagicByteValidation:
    def test_real_pdf_accepted(self):
        assert validate_upload("cv.pdf", PDF) == "application/pdf"

    def test_real_docx_accepted(self):
        assert validate_upload("cv.docx", make_docx())

    def test_executable_renamed_as_pdf_rejected(self):
        # Caught by the unknown-signature check before the PDF-specific one.
        # Either path is a correct rejection; what matters is that it never stores.
        with pytest.raises(UploadError):
            validate_upload("payload.pdf", b"MZ\x90\x00" + b"\x00" * 500)

    def test_zip_renamed_as_pdf_rejected(self):
        with pytest.raises(UploadError, match="not a PDF"):
            validate_upload("payload.pdf", make_docx())

    def test_script_renamed_as_pdf_rejected(self):
        with pytest.raises(UploadError):
            validate_upload("cv.pdf", b"#!/bin/sh\nrm -rf /\n" + b"x" * 500)

    def test_html_renamed_as_docx_rejected(self):
        with pytest.raises(UploadError):
            validate_upload("cv.docx", b"<html><script>alert(1)</script></html>" + b"x" * 400)

    def test_unknown_signature_rejected(self):
        with pytest.raises(UploadError, match="do not match"):
            validate_upload("cv.pdf", b"\x01\x02\x03\x04" + b"x" * 500)

    def test_sniff(self):
        assert sniff_mime(PDF) == "application/pdf"
        assert sniff_mime(PNG) == "image/png"
        assert sniff_mime(b"nonsense") is None


class TestSizeAndBombs:
    def test_empty_rejected(self):
        with pytest.raises(UploadError, match="empty"):
            validate_upload("cv.pdf", b"")

    def test_oversized_rejected(self):
        with pytest.raises(UploadError, match="larger than"):
            validate_upload("cv.pdf", b"%PDF-" + b"x" * (11 * 1024 * 1024))

    def test_zip_bomb_rejected(self):
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bomb.txt", "0" * (60 * 1024 * 1024))
        with pytest.raises(UploadError):
            validate_upload("cv.docx", bomb.getvalue())

    def test_zip_with_traversal_entry_rejected(self):
        evil = make_docx({"../../../etc/passwd": "root::0:0"})
        with pytest.raises(UploadError):
            validate_upload("cv.docx", evil)


class TestExtensionAllowlist:
    @pytest.mark.parametrize("name", [
        "cv.exe", "cv.sh", "cv.js", "cv.php", "cv.svg", "cv.zip", "cv",
    ])
    def test_disallowed_extensions(self, name):
        with pytest.raises(UploadError, match="not supported"):
            validate_upload(name, PDF)


class TestHashing:
    def test_identical_content_same_hash(self):
        assert content_hash(PDF) == content_hash(PDF)

    def test_different_content_different_hash(self):
        assert content_hash(PDF) != content_hash(PDF + b"x")
