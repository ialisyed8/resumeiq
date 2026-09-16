"""Dashboard metrics, reports, exports, audit trail."""

from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select

from app.core.deps import DbSession, ReadUser
from app.core.errors import NotFoundError
from app.models import (
    AuditLog,
    Candidate,
    CandidateIdentity,
    CandidateScore,
    CoverageTier,
    JobDescription,
    JobStatus,
    Requirement,
    RequirementEvidence,
    ScreeningBatch,
)
from app.services import audit

router = APIRouter(tags=["reports"])


@router.get("/dashboard")
async def dashboard(session: DbSession, principal: ReadUser):
    """Metrics for the dashboard. All scoped to the caller's organization."""
    org = principal.organization_id

    active_jobs = await session.scalar(
        select(func.count(JobDescription.id)).where(
            JobDescription.organization_id == org,
            JobDescription.archived_at.is_(None),
            JobDescription.status.in_([JobStatus.ACTIVE, JobStatus.REQUIREMENTS_READY]),
        )
    ) or 0

    screened = await session.scalar(
        select(func.count(Candidate.id)).where(
            Candidate.organization_id == org, Candidate.deleted_at.is_(None)
        )
    ) or 0

    status_counts = dict(
        (
            await session.execute(
                select(Candidate.decision_status, func.count())
                .where(Candidate.organization_id == org, Candidate.deleted_at.is_(None))
                .group_by(Candidate.decision_status)
            )
        ).all()
    )

    latest = await session.scalar(
        select(ScreeningBatch)
        .where(ScreeningBatch.organization_id == org)
        .order_by(ScreeningBatch.created_at.desc())
        .limit(1)
    )

    pool = {"meets_all": 0, "one_short": 0, "multiple_gaps": 0}
    avg_coverage = None
    common_gaps: list[dict] = []

    if latest:
        for tier, count in (
            await session.execute(
                select(CandidateScore.coverage_tier, func.count())
                .where(
                    CandidateScore.screening_batch_id == latest.id,
                    CandidateScore.scorer_version == latest.scorer_version,
                )
                .group_by(CandidateScore.coverage_tier)
            )
        ).all():
            pool[tier.value] = count

        avg_coverage = await session.scalar(
            select(func.avg(CandidateScore.must_haves_met)).where(
                CandidateScore.screening_batch_id == latest.id,
                CandidateScore.scorer_version == latest.scorer_version,
            )
        )

        gap_rows = (
            await session.execute(
                select(Requirement.text, func.count(RequirementEvidence.id))
                .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
                .where(
                    RequirementEvidence.screening_batch_id == latest.id,
                    RequirementEvidence.verdict != "met",
                    Requirement.necessity == "must_have",
                )
                .group_by(Requirement.text)
                .order_by(func.count(RequirementEvidence.id).desc())
                .limit(5)
            )
        ).all()
        common_gaps = [{"requirement": text, "missing_count": count} for text, count in gap_rows]

    recent = (
        await session.execute(
            select(ScreeningBatch, JobDescription)
            .join(JobDescription, JobDescription.id == ScreeningBatch.job_description_id)
            .where(ScreeningBatch.organization_id == org)
            .order_by(ScreeningBatch.created_at.desc())
            .limit(5)
        )
    ).all()

    return {
        "kpis": {
            "active_jobs": active_jobs,
            "candidates_screened": screened,
            "shortlisted": status_counts.get("shortlisted", 0),
            "interviews": status_counts.get("interview", 0),
            "average_must_have_coverage": (
                round(float(avg_coverage), 1) if avg_coverage is not None else None
            ),
        },
        "pool": pool,
        "common_gaps": common_gaps,
        "recent_screenings": [
            {
                "id": str(batch.id), "job_title": job.title, "name": batch.name,
                "status": batch.status.value, "total": batch.total_documents,
                "quarantined": batch.quarantined_count,
                "created_at": batch.created_at.isoformat(),
            }
            for batch, job in recent
        ],
    }


@router.get("/screenings/{batch_id}/report")
async def screening_report(batch_id: uuid.UUID, session: DbSession, principal: ReadUser):
    batch = await session.scalar(
        select(ScreeningBatch).where(
            ScreeningBatch.id == batch_id,
            ScreeningBatch.organization_id == principal.organization_id,
        )
    )
    if batch is None:
        raise NotFoundError("That screening does not exist.")

    job = await session.scalar(
        select(JobDescription).where(JobDescription.id == batch.job_description_id)
    )

    tiers = {t.value: 0 for t in CoverageTier}
    for tier, count in (
        await session.execute(
            select(CandidateScore.coverage_tier, func.count())
            .where(
                CandidateScore.screening_batch_id == batch_id,
                CandidateScore.scorer_version == batch.scorer_version,
            )
            .group_by(CandidateScore.coverage_tier)
        )
    ).all():
        tiers[tier.value] = count

    coverage_rows = (
        await session.execute(
            select(
                Requirement.text, Requirement.necessity,
                func.count(RequirementEvidence.id).filter(RequirementEvidence.verdict == "met"),
                func.count(RequirementEvidence.id),
            )
            .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
            .where(RequirementEvidence.screening_batch_id == batch_id)
            .group_by(Requirement.text, Requirement.necessity, Requirement.display_order)
            .order_by(Requirement.display_order)
        )
    ).all()

    top = (
        await session.execute(
            select(CandidateScore, Candidate)
            .join(Candidate, Candidate.id == CandidateScore.candidate_id)
            .where(
                CandidateScore.screening_batch_id == batch_id,
                CandidateScore.scorer_version == batch.scorer_version,
                Candidate.deleted_at.is_(None),
            )
            .order_by(CandidateScore.rank)
            .limit(10)
        )
    ).all()

    decisions = dict(
        (
            await session.execute(
                select(Candidate.decision_status, func.count())
                .where(
                    Candidate.job_description_id == batch.job_description_id,
                    Candidate.deleted_at.is_(None),
                )
                .group_by(Candidate.decision_status)
            )
        ).all()
    )

    return {
        "screening": {
            "id": str(batch.id), "name": batch.name, "job_title": job.title,
            "status": batch.status.value, "scorer_version": batch.scorer_version,
            "created_at": batch.created_at.isoformat(),
        },
        "summary": {
            "submitted": batch.total_documents,
            "screened": batch.processed_count,
            "quarantined": batch.quarantined_count,
            "failed": batch.failed_count,
        },
        "coverage_tiers": tiers,
        "requirement_coverage": [
            {
                "requirement": text, "necessity": necessity.value,
                "met": met or 0, "evaluated": total or 0,
            }
            for text, necessity, met, total in coverage_rows
        ],
        "top_candidates": [
            {
                "rank": score.rank,
                "reference": f"Candidate #{candidate.reference}",
                "coverage": f"{score.must_haves_met} / {score.must_haves_total}",
                "score": round(score.final_score, 1),
                "tier": score.coverage_tier.value,
                "decision_status": candidate.decision_status,
            }
            for score, candidate in top
        ],
        "decisions": decisions,
    }


@router.get("/screenings/{batch_id}/export/csv")
async def export_csv(
    batch_id: uuid.UUID,
    session: DbSession,
    principal: ReadUser,
    blind: bool | None = None,
):
    """
    CSV export.

    Names appear only when blind screening is off for this batch, or when the
    caller explicitly passes blind=false. An export leaves the system — it gets
    emailed, dropped in shared drives, and outlives the audit trail — so
    revealing identity into one is recorded the same way revealing it on screen
    is.
    """
    batch = await session.scalar(
        select(ScreeningBatch).where(
            ScreeningBatch.id == batch_id,
            ScreeningBatch.organization_id == principal.organization_id,
        )
    )
    if batch is None:
        raise NotFoundError("That screening does not exist.")

    effective_blind = batch.blind_screening if blind is None else blind

    rows = (
        await session.execute(
            select(CandidateScore, Candidate)
            .join(Candidate, Candidate.id == CandidateScore.candidate_id)
            .where(
                CandidateScore.screening_batch_id == batch_id,
                CandidateScore.scorer_version == batch.scorer_version,
                Candidate.deleted_at.is_(None),
            )
            .order_by(CandidateScore.rank)
        )
    ).all()

    identities: dict[uuid.UUID, CandidateIdentity] = {}
    if not effective_blind and rows:
        found = await session.scalars(
            select(CandidateIdentity).where(
                CandidateIdentity.candidate_id.in_([c.id for _, c in rows])
            )
        )
        identities = {i.candidate_id: i for i in found}

        await audit.record(
            session,
            organization_id=principal.organization_id,
            action=audit.Action.REPORT_EXPORTED,
            entity_type="screening",
            entity_id=batch.id,
            actor_id=principal.user_id,
            actor_label=principal.full_name,
            new_value={"format": "csv", "rows": len(rows), "identities_included": True},
            note="CSV export included candidate names and contact details.",
        )
    else:
        await audit.record(
            session,
            organization_id=principal.organization_id,
            action=audit.Action.REPORT_EXPORTED,
            entity_type="screening",
            entity_id=batch.id,
            actor_id=principal.user_id,
            actor_label=principal.full_name,
            new_value={"format": "csv", "rows": len(rows), "identities_included": False},
        )

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    columns = ["Rank", "Reference"]
    if not effective_blind:
        columns += ["Name", "Email", "Phone"]
    columns += [
        "Title", "Experience (years)", "Must-haves met", "Must-haves total",
        "Coverage tier", "Score", "Missing requirements", "Decision",
        "Scorer version",
    ]
    writer.writerow(columns)

    for score, candidate in rows:
        row = [score.rank, f"Candidate #{candidate.reference}"]
        if not effective_blind:
            identity = identities.get(candidate.id)
            row += [
                (identity.full_name if identity else "") or "",
                (identity.email if identity else "") or "",
                (identity.phone if identity else "") or "",
            ]
        row += [
            candidate.current_title or "",
            round(candidate.total_experience_months / 12, 1)
            if candidate.total_experience_months else "",
            score.must_haves_met,
            score.must_haves_total,
            score.coverage_tier.value,
            round(score.final_score, 1),
            "; ".join(score.missing_requirements),
            candidate.decision_status,
            score.scorer_version,
        ]
        writer.writerow(row)

    buffer.seek(0)
    suffix = "" if effective_blind else "-identified"
    filename = f"resumeiq-{batch_id}{suffix}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/screenings/{batch_id}/audit")
async def screening_audit(
    batch_id: uuid.UUID, session: DbSession, principal: ReadUser,
    limit: int = Query(100, ge=1, le=500),
):
    batch = await session.scalar(
        select(ScreeningBatch).where(
            ScreeningBatch.id == batch_id,
            ScreeningBatch.organization_id == principal.organization_id,
        )
    )
    if batch is None:
        raise NotFoundError("That screening does not exist.")

    # Entries for the batch itself, plus entries for the candidates inside it.
    # Previously this ignored batch_id entirely and returned the whole
    # organisation's activity — a correctness bug in a compliance feature.
    candidate_ids = (
        await session.scalars(
            select(Candidate.id).where(
                Candidate.job_description_id == batch.job_description_id
            )
        )
    ).all()

    rows = (
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == principal.organization_id,
                or_(
                    AuditLog.entity_id == batch.id,
                    AuditLog.entity_id.in_(candidate_ids) if candidate_ids else False,
                ),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(row.id), "action": row.action, "actor": row.actor_label,
                "entity_type": row.entity_type,
                "entity_id": str(row.entity_id) if row.entity_id else None,
                "previous_value": row.previous_value, "new_value": row.new_value,
                "note": row.note, "scorer_version": row.scorer_version,
                "at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }
