"""Resume upload: presigned direct-to-storage, plus a server-side fallback."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.config import settings
from app.core.deps import CurrentUser, DbSession, UploadUser
from app.core.errors import ConflictError, NotFoundError, UploadError
from app.models import Candidate, CandidateIdentity, DocumentStatus, JobDescription, ResumeDocument
from app.services import audit, malware
from app.services.storage import (
    build_key, content_hash, sanitise_filename, storage, validate_upload,
)

router = APIRouter(tags=["uploads"])


class PresignRequest(BaseModel):
    filename: str = Field(max_length=512)
    mime_type: str = Field(max_length=160)
    size_bytes: int = Field(gt=0)


@router.post("/uploads/presign")
async def presign(payload: PresignRequest, principal: UploadUser):
    """
    Issue a presigned PUT so large files bypass the API process entirely.

    The key is generated server-side and never derived from the filename.
    """
    if payload.size_bytes > settings.MAX_UPLOAD_BYTES:
        limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadError(f"That file is larger than the {limit} MB limit.")

    key = build_key(str(principal.organization_id), payload.filename)
    return {
        "storage_key": key,
        "upload_url": storage.presign_put(key, payload.mime_type),
        "expires_in": settings.PRESIGNED_URL_TTL_SECONDS,
    }


async def _next_reference(session, job_id: uuid.UUID) -> str:
    count = await session.scalar(
        select(func.count(Candidate.id)).where(Candidate.job_description_id == job_id)
    )
    return f"{(count or 0) + 1:03d}"


@router.post("/jobs/{job_id}/candidates", status_code=status.HTTP_201_CREATED)
async def upload_candidates(
    job_id: uuid.UUID,
    session: DbSession,
    principal: UploadUser,
    files: list[UploadFile] = File(...),
):
    """
    Accept resumes for a job.

    Duplicates are detected by content hash and reported rather than silently
    creating a second candidate record.
    """
    job = await session.scalar(
        select(JobDescription).where(
            JobDescription.id == job_id,
            JobDescription.organization_id == principal.organization_id,
        )
    )
    if job is None:
        raise NotFoundError("That job does not exist.")

    existing_count = await session.scalar(
        select(func.count(Candidate.id)).where(Candidate.job_description_id == job_id)
    ) or 0
    if existing_count + len(files) > settings.MAX_CANDIDATES_PER_SCREENING:
        raise ConflictError(
            f"A screening can hold up to {settings.MAX_CANDIDATES_PER_SCREENING} "
            f"candidates. This job already has {existing_count}."
        )

    accepted, rejected, duplicates = [], [], []

    for upload in files:
        content = await upload.read()
        safe_name = sanitise_filename(upload.filename or "resume")

        try:
            mime = validate_upload(safe_name, content, upload.content_type)
        except UploadError as exc:
            rejected.append({"filename": safe_name, "reason": exc.message})
            continue

        # Malware scan sits between validation and storage: a file that fails
        # here is never written to object storage and never reaches the worker.
        # Cheap structural checks run first so obviously-bad uploads do not
        # consume scanner capacity.
        scan_report = await malware.scan(content, filename=safe_name)
        try:
            malware.enforce(scan_report, filename=safe_name)
        except UploadError as exc:
            rejected.append({"filename": safe_name, "reason": exc.message})
            if scan_report.result is malware.ScanResult.INFECTED:
                await audit.record(
                    session,
                    organization_id=principal.organization_id,
                    action=audit.Action.UPLOAD_REJECTED_MALWARE,
                    entity_type="job",
                    entity_id=job_id,
                    actor_id=principal.user_id,
                    actor_label=principal.full_name,
                    new_value={
                        "filename": safe_name,
                        "signature": scan_report.signature,
                    },
                    note="Upload rejected by malware scanner; file was not stored.",
                )
            continue

        digest = content_hash(content)
        duplicate = await session.scalar(
            select(ResumeDocument)
            .join(Candidate, Candidate.id == ResumeDocument.candidate_id)
            .where(
                ResumeDocument.file_hash == digest,
                Candidate.job_description_id == job_id,
                Candidate.deleted_at.is_(None),
            )
        )
        if duplicate:
            duplicates.append({
                "filename": safe_name,
                "reason": "This resume has already been uploaded for this job.",
            })
            continue

        key = build_key(str(principal.organization_id), safe_name)
        storage.put(key, content, mime)

        candidate = Candidate(
            organization_id=principal.organization_id,
            job_description_id=job_id,
            reference=await _next_reference(session, job_id),
        )
        session.add(candidate)
        await session.flush()

        # Identity row is created empty; the worker populates it from the
        # document header and it stays out of the scoring path either way.
        session.add(CandidateIdentity(candidate_id=candidate.id))

        document = ResumeDocument(
            candidate_id=candidate.id, storage_key=key, filename=safe_name,
            mime_type=mime, size_bytes=len(content), file_hash=digest,
            status=DocumentStatus.PENDING,
        )
        session.add(document)
        await session.flush()

        accepted.append({
            "candidate_id": str(candidate.id), "document_id": str(document.id),
            "filename": safe_name, "reference": candidate.reference,
            "size_bytes": len(content),
        })

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.RESUME_UPLOADED, entity_type="job", entity_id=job_id,
        actor_id=principal.user_id, actor_label=principal.full_name,
        new_value={"accepted": len(accepted), "rejected": len(rejected),
                   "duplicates": len(duplicates)},
    )

    return {
        "accepted": accepted, "rejected": rejected, "duplicates": duplicates,
        "summary": {
            "accepted": len(accepted), "rejected": len(rejected),
            "duplicates": len(duplicates),
        },
    }
