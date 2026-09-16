"""
Screening orchestration and results assembly.

The important separation: `rescore` reads stored evidence and recomputes ranks
without touching a PDF or a model. That is what makes an interactive weight
slider possible, and it is only achievable because evidence is persisted as
first-class data rather than collapsed into a score.

Blind screening is applied here, at the serialisation boundary. The scorer never
sees identity in the first place — this layer decides whether the *response*
carries it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Candidate,
    CandidateIdentity,
    Requirement,
    RequirementEvidence,
    ResumeDocument,
    ScreeningBatch,
)
from app.models import (
    CandidateScore as ScoreRow,
)
from app.models import (
    CoverageTier as TierEnum,
)
from app.scoring.engine import (
    SCORER_VERSION,
    CategoryWeights,
    RequirementSpec,
    score_batch,
)
from app.scoring.engine import (
    Necessity as ScoringNecessity,
)
from app.scoring.ladder import EvidenceBand, GradedEvidence, Verdict

logger = get_logger(__name__)


@dataclass(slots=True)
class ResultsPage:
    items: list[dict]
    total: int
    page: int
    page_size: int
    tier_counts: dict[str, int]


def _to_spec(requirement: Requirement) -> RequirementSpec:
    return RequirementSpec(
        id=str(requirement.id),
        text=requirement.text,
        necessity=(
            ScoringNecessity.MUST_HAVE
            if requirement.necessity.value == "must_have"
            else ScoringNecessity.NICE_TO_HAVE
        ),
        weight=requirement.weight.value,
    )


def _to_graded(row: RequirementEvidence) -> GradedEvidence:
    """
    Build the scoring view of an evidence row.

    A recruiter override replaces the grade but the original verdict stays on
    the row — both are shown in the UI and both are kept for evaluation data.
    """
    verdict = row.effective_verdict
    grade = row.evidence_grade
    if row.override_verdict is not None:
        grade = {
            Verdict.MET: 0.8, Verdict.PARTIAL: 0.4, Verdict.NOT_MET: 0.0,
        }[Verdict(row.override_verdict.value)]

    return GradedEvidence(
        requirement_id=str(row.requirement_id),
        verdict=Verdict(verdict.value),
        band=EvidenceBand(row.evidence_band),
        grade=grade,
        quote=row.evidence_quote,
        reasoning=row.reasoning,
        confidence=row.confidence,
        method=row.method.value,
    )


async def load_scoring_inputs(
    session: AsyncSession, batch: ScreeningBatch
) -> tuple[list[RequirementSpec], dict[str, dict[str, GradedEvidence]]]:
    """Load requirements and stored evidence. No PDFs, no model calls."""
    requirements = (
        await session.scalars(
            select(Requirement)
            .where(Requirement.job_description_id == batch.job_description_id)
            .order_by(Requirement.display_order)
        )
    ).all()

    rows = (
        await session.scalars(
            select(RequirementEvidence).where(
                RequirementEvidence.screening_batch_id == batch.id
            )
        )
    ).all()

    evidence: dict[str, dict[str, GradedEvidence]] = {}
    for row in rows:
        evidence.setdefault(str(row.candidate_id), {})[str(row.requirement_id)] = (
            _to_graded(row)
        )

    # Candidates with no evidence rows still need scoring — they score zero and
    # land in the bottom tier, which is correct and visible, not silent.
    candidate_ids = (
        await session.scalars(
            select(Candidate.id).where(
                Candidate.job_description_id == batch.job_description_id,
                Candidate.deleted_at.is_(None),
            )
        )
    ).all()
    for cid in candidate_ids:
        evidence.setdefault(str(cid), {})

    return [_to_spec(r) for r in requirements], evidence


async def rescore(
    session: AsyncSession, batch: ScreeningBatch, weights: CategoryWeights | None = None
) -> list[dict]:
    """
    Recompute ranks from stored evidence.

    Fast enough to run on every slider movement. Writes a new set of score rows
    tagged with the scorer version rather than mutating the old ones, so any
    previous ranking remains reproducible.
    """
    weights = weights or CategoryWeights(**(batch.category_weights or {}))
    specs, evidence = await load_scoring_inputs(session, batch)
    scores = score_batch(specs, evidence, weights)

    # Replace this scorer version's rows for this batch.
    existing = (
        await session.scalars(
            select(ScoreRow).where(
                ScoreRow.screening_batch_id == batch.id,
                ScoreRow.scorer_version == SCORER_VERSION,
            )
        )
    ).all()
    for row in existing:
        await session.delete(row)
    await session.flush()

    for score in scores:
        session.add(
            ScoreRow(
                screening_batch_id=batch.id,
                candidate_id=uuid.UUID(score.candidate_id),
                must_haves_met=score.must_haves_met,
                must_haves_total=score.must_haves_total,
                coverage_tier=TierEnum(score.tier.value),
                must_have_score=score.must_have_score,
                nice_to_have_bonus=score.nice_to_have_bonus,
                final_score=score.final_score,
                rank=score.rank,
                missing_requirements=score.missing_requirements,
                scorer_version=SCORER_VERSION,
                category_weights=weights.as_dict(),
            )
        )

    batch.category_weights = weights.as_dict()
    batch.scorer_version = SCORER_VERSION
    await session.flush()

    logger.info(
        "rescored", batch=str(batch.id), candidates=len(scores),
        scorer_version=SCORER_VERSION,
    )
    return [s.as_dict() for s in scores]


async def get_results(
    session: AsyncSession,
    batch: ScreeningBatch,
    *,
    page: int = 1,
    page_size: int = 25,
    tier: str | None = None,
    min_experience: int | None = None,
    max_experience: int | None = None,
    decision_status: str | None = None,
    query: str | None = None,
    sort: str = "rank",
    blind: bool = True,
) -> ResultsPage:
    """
    Server-side paginated, filtered, sorted results.

    Sorting defaults to rank, which already encodes tier-then-score. Sorting by
    raw score is offered but still groups by tier in the UI — the gate is not
    something the user can sort away.
    """
    stmt = (
        select(ScoreRow, Candidate)
        .join(Candidate, Candidate.id == ScoreRow.candidate_id)
        .where(
            ScoreRow.screening_batch_id == batch.id,
            ScoreRow.scorer_version == batch.scorer_version,
            Candidate.deleted_at.is_(None),
        )
    )

    if tier and tier != "all":
        stmt = stmt.where(ScoreRow.coverage_tier == TierEnum(tier))
    if decision_status and decision_status != "all":
        stmt = stmt.where(Candidate.decision_status == decision_status)
    if min_experience is not None:
        stmt = stmt.where(Candidate.total_experience_months >= min_experience * 12)
    if max_experience is not None:
        stmt = stmt.where(Candidate.total_experience_months <= max_experience * 12)
    if query:
        pattern = f"%{query.lower()}%"
        # Blind mode must not let a name search leak identity by filtering on it.
        conditions = [func.lower(Candidate.current_title).like(pattern)]
        if not blind:
            stmt = stmt.outerjoin(
                CandidateIdentity, CandidateIdentity.candidate_id == Candidate.id
            )
            conditions.append(func.lower(CandidateIdentity.full_name).like(pattern))
        stmt = stmt.where(func.or_(*conditions))

    total = await session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    order = {
        "rank": ScoreRow.rank.asc(),
        "score": ScoreRow.final_score.desc(),
        "experience": Candidate.total_experience_months.desc().nullslast(),
        "coverage": ScoreRow.must_haves_met.desc(),
    }.get(sort, ScoreRow.rank.asc())

    rows = (
        await session.execute(
            stmt.order_by(order).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()

    counts_rows = (
        await session.execute(
            select(ScoreRow.coverage_tier, func.count())
            .where(
                ScoreRow.screening_batch_id == batch.id,
                ScoreRow.scorer_version == batch.scorer_version,
            )
            .group_by(ScoreRow.coverage_tier)
        )
    ).all()
    counts = {t.value: 0 for t in TierEnum}
    for tier_value, count in counts_rows:
        counts[tier_value.value] = count

    identities: dict[uuid.UUID, CandidateIdentity] = {}
    if not blind:
        ids = [candidate.id for _, candidate in rows]
        if ids:
            found = await session.scalars(
                select(CandidateIdentity).where(
                    CandidateIdentity.candidate_id.in_(ids)
                )
            )
            identities = {i.candidate_id: i for i in found}

    items = [
        serialise_candidate_row(score, candidate, identities.get(candidate.id), blind)
        for score, candidate in rows
    ]

    return ResultsPage(
        items=items, total=total, page=page, page_size=page_size, tier_counts=counts
    )


def serialise_candidate_row(
    score: ScoreRow,
    candidate: Candidate,
    identity: CandidateIdentity | None,
    blind: bool,
) -> dict:
    """
    One row of the ranked table.

    In blind mode the response contains no name, email, phone, or photo. The
    reference (`Candidate #001`) is stable so a recruiter can discuss a
    candidate without revealing them.
    """
    tier_labels = {
        "meets_all": ("Meets every must-have", "Strong Match"),
        "one_short": ("One requirement short", "Worth a Look"),
        "multiple_gaps": ("Multiple gaps", "Below Bar"),
    }
    label, match_level = tier_labels[score.coverage_tier.value]

    payload = {
        "candidate_id": str(candidate.id),
        "reference": candidate.reference,
        "display_name": f"Candidate #{candidate.reference}",
        "title": candidate.current_title,
        "experience_years": (
            round(candidate.total_experience_months / 12, 1)
            if candidate.total_experience_months else None
        ),
        "rank": score.rank,
        "must_haves_met": score.must_haves_met,
        "must_haves_total": score.must_haves_total,
        "coverage_tier": score.coverage_tier.value,
        "tier_label": label,
        "match_level": match_level,
        "final_score": round(score.final_score, 1),
        "missing_requirements": score.missing_requirements,
        "decision_status": candidate.decision_status,
        "scorer_version": score.scorer_version,
        "blind": blind,
    }

    if not blind and identity is not None:
        payload["display_name"] = identity.full_name or payload["display_name"]
        payload["identity"] = {
            "full_name": identity.full_name,
            "email": identity.email,
            "phone": identity.phone,
        }

    return payload


async def get_quarantine(session: AsyncSession, batch: ScreeningBatch) -> list[dict]:
    """
    Documents held out of the ranking.

    These are surfaced separately and prominently. A resume that could not be
    read is not a weak candidate, and must never be presented as one.
    """
    rows = (
        await session.execute(
            select(ResumeDocument, Candidate)
            .join(Candidate, Candidate.id == ResumeDocument.candidate_id)
            .where(
                Candidate.job_description_id == batch.job_description_id,
                ResumeDocument.status.in_(["quarantined", "failed"]),
                Candidate.deleted_at.is_(None),
            )
        )
    ).all()

    return [
        {
            "document_id": str(doc.id),
            "candidate_id": str(candidate.id),
            "filename": doc.filename,
            "status": doc.status.value,
            "reason": doc.quarantine_reason or "This document could not be read reliably.",
            "extraction_confidence": doc.extraction_confidence,
            "signals": doc.quality_signals,
            "can_retry": doc.status.value != "failed",
            "can_ocr": (doc.quality_signals or {}).get("has_text_layer") is False,
        }
        for doc, candidate in rows
    ]
