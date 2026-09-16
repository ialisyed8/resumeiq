"""Candidate detail, evidence, decisions, overrides, deletion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import DbSession, ReadUser, WriteUser
from app.core.errors import NotFoundError
from app.models import (
    Candidate,
    CandidateIdentity,
    CandidateScore,
    Decision,
    DecisionAction,
    JobDescription,
    Requirement,
    RequirementEvidence,
    ResumeDocument,
    ScreeningBatch,
    ScreeningQuestion,
    Verdict,
)
from app.services import audit
from app.services.storage import storage

router = APIRouter(tags=["candidates"])

TIER_META = {
    "meets_all": ("Meets every must-have", "Strong Match"),
    "one_short": ("One requirement short", "Worth a Look"),
    "multiple_gaps": ("Multiple gaps", "Below Bar"),
}

CONFIDENCE_LABELS = {
    "high": ("High confidence", "Direct evidence with surrounding context"),
    "medium": ("Medium confidence", "Indirect or limited evidence"),
    "low": ("Low confidence", "Weak or ambiguous evidence"),
}


def _confidence_band(value: float) -> str:
    return "high" if value >= 0.75 else ("medium" if value >= 0.45 else "low")


async def _scoped(session, batch_id, candidate_id, org_id):
    batch = await session.scalar(
        select(ScreeningBatch).where(
            ScreeningBatch.id == batch_id, ScreeningBatch.organization_id == org_id
        )
    )
    if batch is None:
        raise NotFoundError("That screening does not exist.")
    candidate = await session.scalar(
        select(Candidate).where(
            Candidate.id == candidate_id,
            Candidate.organization_id == org_id,
            # The candidate must belong to *this* batch's job. Checking only the
            # organisation lets a caller pair batch X with a candidate from job Y
            # and receive a coherent-looking page built from mismatched evidence.
            Candidate.job_description_id == batch.job_description_id,
            Candidate.deleted_at.is_(None),
        )
    )
    if candidate is None:
        raise NotFoundError("That candidate does not exist in this screening.")
    return batch, candidate


@router.get("/screenings/{batch_id}/candidates/{candidate_id}")
async def candidate_detail(
    batch_id: uuid.UUID, candidate_id: uuid.UUID, session: DbSession,
    principal: ReadUser, blind: bool | None = None,
):
    """
    Everything the detail screen renders.

    Every requirement carries either a quoted span from the resume or an
    absence statement naming the terms that were searched for. There is no
    third option — an unexplained number is not an explanation.
    """
    batch, candidate = await _scoped(session, batch_id, candidate_id, principal.organization_id)
    effective_blind = batch.blind_screening if blind is None else blind

    requirements = (
        await session.scalars(
            select(Requirement)
            .where(Requirement.job_description_id == batch.job_description_id)
            .order_by(Requirement.display_order)
        )
    ).all()

    evidence_rows = (
        await session.scalars(
            select(RequirementEvidence).where(
                RequirementEvidence.screening_batch_id == batch_id,
                RequirementEvidence.candidate_id == candidate_id,
            )
        )
    ).all()
    evidence_by_req = {str(e.requirement_id): e for e in evidence_rows}

    score = await session.scalar(
        select(CandidateScore).where(
            CandidateScore.screening_batch_id == batch_id,
            CandidateScore.candidate_id == candidate_id,
            CandidateScore.scorer_version == batch.scorer_version,
        )
    )

    coverage_items = []
    for requirement in requirements:
        row = evidence_by_req.get(str(requirement.id))
        if row is None:
            coverage_items.append({
                "requirement_id": str(requirement.id), "text": requirement.text,
                "necessity": requirement.necessity.value,
                "weight": requirement.weight.value, "verdict": "not_met",
                "evidence_band": "none", "evidence_grade": 0.0, "quote": None,
                "absence_statement": "This requirement was added after the screening ran. Re-run to evaluate it.",
                "confidence_band": "low", "search_terms": requirement.aliases[:6],
            })
            continue

        band = _confidence_band(row.confidence)
        label, description = CONFIDENCE_LABELS[band]
        coverage_items.append({
            "requirement_id": str(requirement.id),
            "text": requirement.text,
            "necessity": requirement.necessity.value,
            "weight": requirement.weight.value,
            "verdict": row.effective_verdict.value,
            "ai_verdict": row.verdict.value,
            "evidence_band": row.evidence_band,
            "evidence_grade": row.evidence_grade,
            "quote": row.evidence_quote,
            "page": row.evidence_page,
            "char_start": row.evidence_char_start,
            "char_end": row.evidence_char_end,
            "absence_statement": row.absence_statement,
            "reasoning": row.reasoning,
            "confidence": row.confidence,
            "confidence_band": band,
            "confidence_label": label,
            "confidence_description": description,
            "method": row.method.value,
            "model_version": row.model_version,
            "quote_validated": row.quote_validated,
            "search_terms": requirement.aliases[:6],
            "override": (
                {
                    "verdict": row.override_verdict.value,
                    "reason": row.override_reason,
                    "at": row.override_at.isoformat() if row.override_at else None,
                }
                if row.override_verdict else None
            ),
        })

    gaps = [
        c for c in coverage_items
        if c["necessity"] == "must_have" and c["verdict"] != "met"
    ]

    questions = (
        await session.scalars(
            select(ScreeningQuestion).where(
                ScreeningQuestion.screening_batch_id == batch_id,
                ScreeningQuestion.candidate_id == candidate_id,
            )
        )
    ).all()

    decisions = (
        await session.scalars(
            select(Decision)
            .where(Decision.screening_batch_id == batch_id, Decision.candidate_id == candidate_id)
            .order_by(Decision.created_at.desc())
        )
    ).all()

    payload = {
        "candidate_id": str(candidate.id),
        "reference": candidate.reference,
        "display_name": f"Candidate #{candidate.reference}",
        "title": candidate.current_title,
        "experience_years": (
            round(candidate.total_experience_months / 12, 1)
            if candidate.total_experience_months else None
        ),
        "decision_status": candidate.decision_status,
        "blind": effective_blind,
        "coverage": coverage_items,
        "gaps": [{"requirement_id": g["requirement_id"], "text": g["text"],
                  "absence_statement": g.get("absence_statement")} for g in gaps],
        "questions": [
            {
                "id": str(q.id), "requirement_id": str(q.requirement_id),
                "question": q.question, "rationale": q.rationale,
                "added_to_interview": q.added_to_interview,
            }
            for q in questions
        ],
        "decision_trail": [
            {
                "action": d.action.value, "note": d.note,
                "coverage": d.coverage_at_decision,
                "at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
        "disclaimer": (
            "This score ranks the resume against the job description. It is not "
            "a hiring recommendation."
        ),
    }

    if score:
        label, match_level = TIER_META[score.coverage_tier.value]
        payload["score"] = {
            "must_haves_met": score.must_haves_met,
            "must_haves_total": score.must_haves_total,
            "coverage_tier": score.coverage_tier.value,
            "tier_label": label, "match_level": match_level,
            "final_score": round(score.final_score, 1),
            "must_have_score": round(score.must_have_score, 3),
            "nice_to_have_bonus": round(score.nice_to_have_bonus, 2),
            "rank": score.rank, "scorer_version": score.scorer_version,
            "category_weights": score.category_weights,
            "tier_note": (
                "This score orders candidates within the "
                f"'{label.lower()}' tier. It cannot move a candidate into a higher tier."
            ),
        }

    if not effective_blind:
        identity = await session.scalar(
            select(CandidateIdentity).where(CandidateIdentity.candidate_id == candidate.id)
        )
        if identity:
            identity.revealed_at = datetime.now(UTC)
            identity.revealed_by = principal.user_id
            payload["display_name"] = identity.full_name or payload["display_name"]
            payload["identity"] = {
                "full_name": identity.full_name, "email": identity.email,
                "phone": identity.phone,
            }
            await audit.record(
                session, organization_id=principal.organization_id,
                action=audit.Action.IDENTITY_REVEALED, entity_type="candidate",
                entity_id=candidate.id, actor_id=principal.user_id,
                actor_label=principal.full_name,
            )

    return payload


@router.get("/screenings/{batch_id}/candidates/{candidate_id}/document")
async def candidate_document(
    batch_id: uuid.UUID, candidate_id: uuid.UUID, session: DbSession, principal: ReadUser
):
    """Source document plus extracted text, for the highlight viewer."""
    _, candidate = await _scoped(session, batch_id, candidate_id, principal.organization_id)
    document = await session.scalar(
        select(ResumeDocument).where(ResumeDocument.candidate_id == candidate_id)
    )
    if document is None:
        raise NotFoundError("No document is attached to that candidate.")
    return {
        "document_id": str(document.id), "filename": document.filename,
        "page_count": document.page_count,
        "extraction_method": document.extraction_method.value if document.extraction_method else None,
        "extraction_confidence": document.extraction_confidence,
        "extracted_text": document.extracted_text,
        "download_url": storage.presign_get(document.storage_key),
    }


class DecisionRequest(BaseModel):
    action: str = Field(pattern="^(shortlist|reject|interview|on_hold|reset)$")
    note: str | None = Field(default=None, max_length=2000)


@router.post("/screenings/{batch_id}/candidates/{candidate_id}/decision")
async def record_decision(
    batch_id: uuid.UUID, candidate_id: uuid.UUID, payload: DecisionRequest,
    session: DbSession, principal: WriteUser,
):
    """
    Record a recruiter decision.

    Nothing in this system rejects a candidate automatically. Every decision is
    a person's, attributed, and logged with the coverage that was on screen when
    it was taken.
    """
    batch, candidate = await _scoped(session, batch_id, candidate_id, principal.organization_id)

    score = await session.scalar(
        select(CandidateScore).where(
            CandidateScore.screening_batch_id == batch_id,
            CandidateScore.candidate_id == candidate_id,
            CandidateScore.scorer_version == batch.scorer_version,
        )
    )
    coverage = f"{score.must_haves_met}/{score.must_haves_total}" if score else None

    previous = candidate.decision_status
    status_map = {
        "shortlist": "shortlisted", "reject": "rejected",
        "interview": "interview", "on_hold": "on_hold", "reset": "new",
    }
    candidate.decision_status = status_map[payload.action]

    session.add(
        Decision(
            screening_batch_id=batch_id, candidate_id=candidate_id,
            user_id=principal.user_id, action=DecisionAction(payload.action),
            previous_status=previous, note=payload.note,
            coverage_at_decision=coverage, scorer_version=batch.scorer_version,
        )
    )

    action_map = {
        "shortlist": audit.Action.CANDIDATE_SHORTLISTED,
        "reject": audit.Action.CANDIDATE_REJECTED,
        "interview": audit.Action.CANDIDATE_INTERVIEW,
        "on_hold": audit.Action.CANDIDATE_ON_HOLD,
        "reset": "candidate.reset",
    }
    await audit.record(
        session, organization_id=principal.organization_id,
        action=action_map[payload.action], entity_type="candidate",
        entity_id=candidate_id, actor_id=principal.user_id,
        actor_label=principal.full_name,
        previous_value={"status": previous},
        new_value={"status": candidate.decision_status, "coverage": coverage},
        note=payload.note, scorer_version=batch.scorer_version,
    )

    label = {
        "shortlisted": "Shortlisted", "rejected": "Rejected",
        "interview": "Moved to interview", "on_hold": "Put on hold", "new": "Reset",
    }[candidate.decision_status]
    return {
        "candidate_id": str(candidate_id), "status": candidate.decision_status,
        "message": f"Candidate #{candidate.reference} — {label}.",
    }


class OverrideRequest(BaseModel):
    verdict: str = Field(pattern="^(met|partial|not_met)$")
    reason: str = Field(min_length=3, max_length=2000)


@router.patch("/screenings/{batch_id}/candidates/{candidate_id}/evidence/{requirement_id}")
async def override_evidence(
    batch_id: uuid.UUID, candidate_id: uuid.UUID, requirement_id: uuid.UUID,
    payload: OverrideRequest, session: DbSession, principal: WriteUser,
):
    """
    Override an evidence verdict.

    The original verdict is preserved alongside the override. The pair is the
    most valuable evaluation data this system produces — it is a labelled
    disagreement between the model and someone who read the resume.
    """
    await _scoped(session, batch_id, candidate_id, principal.organization_id)
    row = await session.scalar(
        select(RequirementEvidence).where(
            RequirementEvidence.screening_batch_id == batch_id,
            RequirementEvidence.candidate_id == candidate_id,
            RequirementEvidence.requirement_id == requirement_id,
        )
    )
    if row is None:
        raise NotFoundError("There is no evidence record for that requirement.")

    row.override_verdict = Verdict(payload.verdict)
    row.override_by = principal.user_id
    row.override_at = datetime.now(UTC)
    row.override_reason = payload.reason

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.EVIDENCE_OVERRIDDEN, entity_type="evidence",
        entity_id=row.id, actor_id=principal.user_id, actor_label=principal.full_name,
        previous_value={"verdict": row.verdict.value, "grade": row.evidence_grade},
        new_value={"verdict": payload.verdict},
        note=payload.reason,
    )
    return {
        "requirement_id": str(requirement_id),
        "ai_verdict": row.verdict.value,
        "override_verdict": payload.verdict,
        "message": "Override saved. Re-score to apply it to the ranking.",
    }


@router.get("/screenings/{batch_id}/candidates/{candidate_id}/report/pdf")
async def candidate_report_pdf(
    batch_id: uuid.UUID,
    candidate_id: uuid.UUID,
    session: DbSession,
    principal: ReadUser,
    blind: bool | None = None,
):
    """
    One candidate's screening report as a PDF.

    Built from exactly the payload the detail screen renders, so the document a
    hiring manager receives cannot drift from what the recruiter saw. Identity is
    included only when blind screening is off, and either choice is audited —
    this file leaves the system and outlives the audit trail.
    """
    from app.services.report_pdf import build_candidate_report

    batch, candidate = await _scoped(
        session, batch_id, candidate_id, principal.organization_id
    )
    effective_blind = batch.blind_screening if blind is None else blind

    detail = await candidate_detail(
        batch_id, candidate_id, session, principal, blind=effective_blind
    )

    job = await session.scalar(
        select(JobDescription).where(JobDescription.id == batch.job_description_id)
    )

    pdf = build_candidate_report(
        detail,
        job_title=job.title if job else "Screening",
        screening_name=batch.name,
        batch_id=str(batch.id),
        blind=effective_blind,
    )

    await audit.record(
        session,
        organization_id=principal.organization_id,
        action=audit.Action.REPORT_EXPORTED,
        entity_type="candidate",
        entity_id=candidate.id,
        actor_id=principal.user_id,
        actor_label=principal.full_name,
        scorer_version=batch.scorer_version,
        new_value={
            "format": "pdf",
            "scope": "single_candidate",
            "identity_included": not effective_blind,
        },
        note=(
            "Candidate report exported with name and contact details."
            if not effective_blind else None
        ),
    )

    stem = f"candidate-{candidate.reference}"
    if not effective_blind:
        identity = detail.get("identity") or {}
        name = (identity.get("full_name") or "").strip()
        if name:
            stem = name.lower().replace(" ", "-")
    suffix = "" if effective_blind else "-identified"
    filename = f"resumeiq-{stem}{suffix}.pdf"

    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(candidate_id: uuid.UUID, session: DbSession, principal: WriteUser):
    """
    Erase a candidate.

    Removes the stored document from object storage and the identity row
    outright, and soft-deletes the candidate so audit history remains coherent.
    Supports GDPR Article 17 requests.
    """
    candidate = await session.scalar(
        select(Candidate).where(
            Candidate.id == candidate_id,
            Candidate.organization_id == principal.organization_id,
        )
    )
    if candidate is None:
        raise NotFoundError("That candidate does not exist.")

    documents = (
        await session.scalars(
            select(ResumeDocument).where(ResumeDocument.candidate_id == candidate_id)
        )
    ).all()
    for document in documents:
        try:
            storage.delete(document.storage_key)
        except Exception:
            pass  # storage may already be gone; the row removal is what matters
        await session.delete(document)

    identity = await session.scalar(
        select(CandidateIdentity).where(CandidateIdentity.candidate_id == candidate_id)
    )
    if identity:
        await session.delete(identity)

    candidate.deleted_at = datetime.now(UTC)
    candidate.structured_profile = {}
    candidate.current_title = None

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.CANDIDATE_DELETED, entity_type="candidate",
        entity_id=candidate_id, actor_id=principal.user_id,
        actor_label=principal.full_name,
        note=f"Candidate #{candidate.reference} erased on request.",
    )
