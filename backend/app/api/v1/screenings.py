"""Screening lifecycle: start, poll, stream progress, results, rescore."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.core.deps import AiUser, DbSession, ReadUser
from app.core.errors import ConflictError, NotFoundError
from app.core.limits import enforce_budget, get_budget
from app.models import (
    BatchStatus,
    Candidate,
    JobDescription,
    Requirement,
    ResumeDocument,
    ScreeningBatch,
)
from app.scoring.engine import SCORER_VERSION, CategoryWeights
from app.services import audit
from app.services.screening import get_quarantine, get_results, rescore

router = APIRouter(tags=["screenings"])


class ScreeningCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    blind_screening: bool = True


class RescoreRequest(BaseModel):
    skills: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    projects: float = Field(ge=0, le=100)
    education: float = Field(ge=0, le=100)
    certifications: float = Field(ge=0, le=100)


async def _get_batch(session, batch_id: uuid.UUID, org_id: uuid.UUID) -> ScreeningBatch:
    batch = await session.scalar(
        select(ScreeningBatch).where(
            ScreeningBatch.id == batch_id, ScreeningBatch.organization_id == org_id
        )
    )
    if batch is None:
        raise NotFoundError("That screening does not exist.")
    return batch


@router.post("/jobs/{job_id}/screenings", status_code=status.HTTP_202_ACCEPTED)
async def start_screening(
    job_id: uuid.UUID, payload: ScreeningCreate, session: DbSession, principal: AiUser
):
    """
    Queue a screening run.

    Returns 202 immediately. Screening involves parsing, embedding, and model
    calls across the whole pool — minutes of work that cannot happen inside an
    HTTP request. Progress is available via GET /screenings/{id}/events.
    """
    job = await session.scalar(
        select(JobDescription).where(
            JobDescription.id == job_id,
            JobDescription.organization_id == principal.organization_id,
        )
    )
    if job is None:
        raise NotFoundError("That job does not exist.")

    must_haves = await session.scalar(
        select(func.count(Requirement.id)).where(
            Requirement.job_description_id == job_id,
            Requirement.necessity == "must_have",
        )
    )
    if not must_haves:
        raise ConflictError(
            "Add at least one must-have requirement before screening. "
            "Without one there is nothing to rank candidates against."
        )

    pending = await session.scalar(
        select(func.count(ResumeDocument.id))
        .join(Candidate, Candidate.id == ResumeDocument.candidate_id)
        .where(
            Candidate.job_description_id == job_id, Candidate.deleted_at.is_(None)
        )
    )
    if not pending:
        raise ConflictError("Upload at least one resume before starting a screening.")

    # Refuse before any expensive work is queued. Checked here rather than in the
    # worker because a rejected batch must never reach the model at all.
    await enforce_budget(
        session,
        principal.organization_id,
        additional_screenings=1,
        additional_candidates=pending,
    )

    batch = ScreeningBatch(
        organization_id=principal.organization_id,
        job_description_id=job_id,
        name=payload.name or f"{job.title} — {datetime.now(UTC):%d %b %Y}",
        status=BatchStatus.QUEUED,
        total_documents=pending,
        scorer_version=SCORER_VERSION,
        category_weights=job.category_weights or CategoryWeights().as_dict(),
        blind_screening=payload.blind_screening,
        created_by=principal.user_id,
    )
    session.add(batch)
    await session.flush()

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.SCREENING_STARTED, entity_type="screening",
        entity_id=batch.id, actor_id=principal.user_id, actor_label=principal.full_name,
        scorer_version=SCORER_VERSION,
        new_value={"documents": pending, "blind": payload.blind_screening},
    )

    # Enqueue for the worker. Import here so the API does not need arq at boot.
    from app.workers.queue import enqueue_screening
    await enqueue_screening(str(batch.id))

    budget = await get_budget(session, principal.organization_id)

    return {
        "batch_id": str(batch.id), "status": batch.status.value,
        "total_documents": pending,
        "budget": budget.as_dict(),
        "events_url": f"/api/screenings/{batch.id}/events",
        "message": "Screening queued. This continues in the background.",
    }


@router.get("/screenings/{batch_id}")
async def get_screening(batch_id: uuid.UUID, session: DbSession, principal: ReadUser):
    batch = await _get_batch(session, batch_id, principal.organization_id)
    job = await session.scalar(
        select(JobDescription).where(JobDescription.id == batch.job_description_id)
    )
    return {
        "id": str(batch.id), "name": batch.name, "status": batch.status.value,
        "job": {"id": str(job.id), "title": job.title},
        "total_documents": batch.total_documents,
        "processed_count": batch.processed_count,
        "quarantined_count": batch.quarantined_count,
        "failed_count": batch.failed_count,
        "progress": (
            round(batch.processed_count / batch.total_documents * 100)
            if batch.total_documents else 0
        ),
        "blind_screening": batch.blind_screening,
        "category_weights": batch.category_weights,
        "scorer_version": batch.scorer_version,
        "model_versions": batch.model_versions,
        "error_message": batch.error_message,
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
    }


@router.get("/screenings/{batch_id}/events")
async def screening_events(batch_id: uuid.UUID, request: Request, session: DbSession, principal: ReadUser):
    """
    Server-sent progress events.

    The UI shows a live pipeline; this is where those stage transitions come
    from. Nginx must not buffer this route — see infrastructure/nginx.conf.
    """
    await _get_batch(session, batch_id, principal.organization_id)

    async def stream():
        from app.db.session import SessionFactory

        last = None
        for _ in range(1800):  # ~15 minutes at 0.5s
            if await request.is_disconnected():
                break
            async with SessionFactory() as poll:
                batch = await poll.scalar(
                    select(ScreeningBatch).where(ScreeningBatch.id == batch_id)
                )
                if batch is None:
                    break
                payload = {
                    "status": batch.status.value,
                    "processed": batch.processed_count,
                    "total": batch.total_documents,
                    "quarantined": batch.quarantined_count,
                    "failed": batch.failed_count,
                    "progress": (
                        round(batch.processed_count / batch.total_documents * 100)
                        if batch.total_documents else 0
                    ),
                }
                if payload != last:
                    yield f"event: progress\ndata: {json.dumps(payload)}\n\n"
                    last = payload
                if batch.status in (BatchStatus.COMPLETED, BatchStatus.FAILED, BatchStatus.CANCELLED):
                    yield f"event: {batch.status.value}\ndata: {json.dumps(payload)}\n\n"
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disables nginx proxy buffering
        },
    )


@router.get("/screenings/{batch_id}/results")
async def screening_results(
    batch_id: uuid.UUID,
    session: DbSession,
    principal: ReadUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    tier: str | None = None,
    decision_status: str | None = None,
    min_experience: int | None = Query(None, ge=0, le=60),
    max_experience: int | None = Query(None, ge=0, le=60),
    q: str | None = Query(None, max_length=200),
    sort: str = Query("rank"),
    blind: bool | None = None,
):
    batch = await _get_batch(session, batch_id, principal.organization_id)
    effective_blind = batch.blind_screening if blind is None else blind

    if blind is False and batch.blind_screening:
        await audit.record(
            session, organization_id=principal.organization_id,
            action=audit.Action.BLIND_DISABLED, entity_type="screening",
            entity_id=batch.id, actor_id=principal.user_id,
            actor_label=principal.full_name,
            note="Identifying details revealed in the ranked list.",
        )

    results = await get_results(
        session, batch, page=page, page_size=page_size, tier=tier,
        decision_status=decision_status, min_experience=min_experience,
        max_experience=max_experience, query=q, sort=sort, blind=effective_blind,
    )
    return {
        "items": results.items, "total": results.total, "page": results.page,
        "page_size": results.page_size, "tier_counts": results.tier_counts,
        "blind_screening": effective_blind,
        "scorer_version": batch.scorer_version,
        "disclaimer": (
            "Scores rank resumes against this job description only. They are not "
            "a hiring recommendation, and every shortlist and rejection is logged "
            "for audit."
        ),
    }


@router.get("/screenings/{batch_id}/quarantine")
async def screening_quarantine(batch_id: uuid.UUID, session: DbSession, principal: ReadUser):
    batch = await _get_batch(session, batch_id, principal.organization_id)
    items = await get_quarantine(session, batch)
    return {
        "items": items, "count": len(items),
        "note": (
            "These resumes could not be read reliably. They are held out of the "
            "ranking rather than scored low."
        ),
    }


@router.post("/screenings/{batch_id}/rescore")
async def rescore_batch(
    batch_id: uuid.UUID, payload: RescoreRequest, session: DbSession, principal: AiUser
):
    """
    Recompute ranks with new weights.

    Reads stored evidence only — no PDF is reopened and no model is called, so
    this returns fast enough to drive a slider.
    """
    batch = await _get_batch(session, batch_id, principal.organization_id)
    if batch.status not in (BatchStatus.COMPLETED, BatchStatus.SCORING):
        raise ConflictError("Wait for the screening to finish before re-scoring.")

    before = dict(batch.category_weights or {})
    weights = CategoryWeights(**payload.model_dump())
    scores = await rescore(session, batch, weights)

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.RESCORE, entity_type="screening", entity_id=batch.id,
        actor_id=principal.user_id, actor_label=principal.full_name,
        previous_value=before, new_value=weights.as_dict(),
        scorer_version=SCORER_VERSION,
        note="Re-ranked from stored evidence; no re-extraction.",
    )
    return {
        "scorer_version": SCORER_VERSION, "candidates_scored": len(scores),
        "category_weights": weights.as_dict(),
        "note": "Weights reorder candidates within a coverage tier only.",
    }


@router.get("/screenings")
async def list_screenings(
    session: DbSession, principal: ReadUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
):
    base = (
        select(ScreeningBatch, JobDescription)
        .join(JobDescription, JobDescription.id == ScreeningBatch.job_description_id)
        .where(ScreeningBatch.organization_id == principal.organization_id)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await session.execute(
            base.order_by(ScreeningBatch.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(batch.id), "name": batch.name, "job_title": job.title,
                "status": batch.status.value,
                "total_documents": batch.total_documents,
                "quarantined_count": batch.quarantined_count,
                "created_at": batch.created_at.isoformat(),
                "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
            }
            for batch, job in rows
        ],
        "total": total, "page": page, "page_size": page_size,
    }
