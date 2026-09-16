"""Jobs and requirements. The requirement review step lives here."""

from __future__ import annotations

import uuid
from datetime import UTC

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai.client import get_client
from app.core.deps import AiUser, DbSession, ReadUser, WriteUser
from app.core.errors import NotFoundError
from app.core.limits import enforce_budget
from app.matching.normalization import aliases_for, normalise_skill
from app.models import (
    JobDescription,
    JobStatus,
    Necessity,
    Requirement,
    RequirementKind,
    RequirementWeight,
)
from app.scoring.engine import CategoryWeights
from app.services import audit

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    raw_text: str = Field(min_length=40, max_length=60_000)
    source_url: str | None = Field(default=None, max_length=1024)


class RequirementIn(BaseModel):
    text: str = Field(min_length=2, max_length=500)
    kind: str = "skill"
    necessity: str = "must_have"
    weight: str = "Medium"
    aliases: list[str] = Field(default_factory=list, max_length=25)
    min_years: float | None = Field(default=None, ge=0, le=50)


class RequirementPatch(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    necessity: str | None = None
    weight: str | None = None
    aliases: list[str] | None = None
    display_order: int | None = None


class WeightsIn(BaseModel):
    skills: float = Field(ge=0, le=100)
    experience: float = Field(ge=0, le=100)
    projects: float = Field(ge=0, le=100)
    education: float = Field(ge=0, le=100)
    certifications: float = Field(ge=0, le=100)


def _serialise_requirement(r: Requirement) -> dict:
    return {
        "id": str(r.id), "text": r.text, "kind": r.kind.value,
        "necessity": r.necessity.value, "weight": r.weight.value,
        "aliases": r.aliases, "canonical_skill": r.canonical_skill,
        "min_years": r.min_years, "display_order": r.display_order,
        "recruiter_edited": r.recruiter_edited, "source_span": r.source_span,
    }


async def _get_job(session, job_id: uuid.UUID, org_id: uuid.UUID) -> JobDescription:
    job = await session.scalar(
        select(JobDescription).where(
            JobDescription.id == job_id,
            JobDescription.organization_id == org_id,  # tenancy enforced on every read
        )
    )
    if job is None:
        raise NotFoundError("That job does not exist.")
    return job


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate, session: DbSession, principal: AiUser):
    """
    Create a job and extract requirements from the description.

    The extracted list is a *proposal*. Nothing is screened until the recruiter
    reviews it — that review is what turns "what the JD says" into "what the
    role needs", and it is why every downstream explanation traces to a
    human-approved criterion.
    """
    # Job analysis is one model call. Cheap individually, unbounded in a loop.
    await enforce_budget(session, principal.organization_id)

    job = JobDescription(
        organization_id=principal.organization_id,
        title=payload.title or "Untitled role",
        raw_text=payload.raw_text,
        source_url=payload.source_url,
        created_by=principal.user_id,
        status=JobStatus.DRAFT,
        category_weights=CategoryWeights().as_dict(),
    )
    session.add(job)
    await session.flush()

    client = get_client()
    extracted = 0
    if client.enabled:
        analysis = await client.analyse_job(payload.raw_text)
        job.title = payload.title or analysis.title
        job.seniority = analysis.seniority
        job.min_years_experience = analysis.min_years_experience

        for order, item in enumerate(analysis.requirements):
            canonical = normalise_skill(item.text)
            aliases = list(dict.fromkeys(
                [*item.aliases, *(aliases_for(canonical) if canonical else [])]
            ))
            session.add(
                Requirement(
                    job_description_id=job.id, text=item.text,
                    kind=RequirementKind(item.kind),
                    necessity=Necessity(item.necessity),
                    weight=RequirementWeight(item.weight),
                    canonical_skill=canonical, aliases=aliases[:25],
                    source_span=item.source_span, min_years=item.min_years,
                    display_order=order,
                )
            )
            extracted += 1
        job.status = JobStatus.REQUIREMENTS_READY

    await session.flush()
    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.JOB_CREATED, entity_type="job", entity_id=job.id,
        actor_id=principal.user_id, actor_label=principal.full_name,
        new_value={"title": job.title, "requirements_extracted": extracted},
    )

    return {
        "id": str(job.id), "title": job.title, "status": job.status.value,
        "seniority": job.seniority, "min_years_experience": job.min_years_experience,
        "requirements_extracted": extracted,
        "ai_enabled": client.enabled,
        "message": (
            f"{extracted} requirements extracted. Review them before screening."
            if client.enabled else
            "Job created. Requirement extraction needs ANTHROPIC_API_KEY to be set; "
            "you can add requirements manually in the meantime."
        ),
    }


@router.get("")
async def list_jobs(session: DbSession, principal: ReadUser, limit: int = 50):
    rows = await session.execute(
        select(
            JobDescription,
            select(func.count(Requirement.id))
            .where(Requirement.job_description_id == JobDescription.id)
            .scalar_subquery(),
        )
        .where(
            JobDescription.organization_id == principal.organization_id,
            JobDescription.archived_at.is_(None),
        )
        .order_by(JobDescription.created_at.desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "id": str(job.id), "title": job.title, "status": job.status.value,
                "requirement_count": count,
                "created_at": job.created_at.isoformat(),
            }
            for job, count in rows.all()
        ]
    }


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, session: DbSession, principal: ReadUser):
    job = await _get_job(session, job_id, principal.organization_id)
    requirements = await session.scalars(
        select(Requirement)
        .where(Requirement.job_description_id == job.id)
        .order_by(Requirement.display_order)
    )
    items = [_serialise_requirement(r) for r in requirements]
    return {
        "id": str(job.id), "title": job.title, "raw_text": job.raw_text,
        "status": job.status.value, "seniority": job.seniority,
        "min_years_experience": job.min_years_experience,
        "category_weights": job.category_weights,
        "requirements": items,
        "must_have_count": sum(1 for r in items if r["necessity"] == "must_have"),
        "nice_to_have_count": sum(1 for r in items if r["necessity"] == "nice_to_have"),
    }


@router.get("/{job_id}/requirements")
async def list_requirements(job_id: uuid.UUID, session: DbSession, principal: ReadUser):
    await _get_job(session, job_id, principal.organization_id)
    rows = await session.scalars(
        select(Requirement)
        .where(Requirement.job_description_id == job_id)
        .order_by(Requirement.display_order)
    )
    return {"items": [_serialise_requirement(r) for r in rows]}


@router.post("/{job_id}/requirements", status_code=status.HTTP_201_CREATED)
async def add_requirement(
    job_id: uuid.UUID, payload: RequirementIn, session: DbSession, principal: WriteUser
):
    await _get_job(session, job_id, principal.organization_id)
    max_order = await session.scalar(
        select(func.coalesce(func.max(Requirement.display_order), -1)).where(
            Requirement.job_description_id == job_id
        )
    )
    canonical = normalise_skill(payload.text)
    requirement = Requirement(
        job_description_id=job_id, text=payload.text,
        kind=RequirementKind(payload.kind), necessity=Necessity(payload.necessity),
        weight=RequirementWeight(payload.weight),
        canonical_skill=canonical,
        aliases=list(dict.fromkeys([
            *payload.aliases, *(aliases_for(canonical) if canonical else [])
        ]))[:25],
        min_years=payload.min_years, display_order=max_order + 1,
        recruiter_edited=True,
    )
    session.add(requirement)
    await session.flush()

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.REQUIREMENT_ADDED, entity_type="requirement",
        entity_id=requirement.id, actor_id=principal.user_id,
        actor_label=principal.full_name,
        new_value={"text": requirement.text, "necessity": requirement.necessity.value},
    )
    return _serialise_requirement(requirement)


@router.patch("/{job_id}/requirements/{requirement_id}")
async def update_requirement(
    job_id: uuid.UUID, requirement_id: uuid.UUID, payload: RequirementPatch,
    session: DbSession, principal: WriteUser,
):
    """
    Edit a requirement.

    Necessity changes are audited separately — moving something between
    must-have and nice-to-have re-tiers the entire candidate pool, and that
    should be traceable to a person and a moment.
    """
    await _get_job(session, job_id, principal.organization_id)
    requirement = await session.scalar(
        select(Requirement).where(
            Requirement.id == requirement_id,
            Requirement.job_description_id == job_id,
        )
    )
    if requirement is None:
        raise NotFoundError("That requirement does not exist.")

    before = _serialise_requirement(requirement)
    necessity_changed = False

    if payload.text is not None:
        requirement.text = payload.text
        requirement.canonical_skill = normalise_skill(payload.text)
    if payload.necessity is not None and payload.necessity != requirement.necessity.value:
        requirement.necessity = Necessity(payload.necessity)
        necessity_changed = True
    if payload.weight is not None:
        requirement.weight = RequirementWeight(payload.weight)
    if payload.aliases is not None:
        requirement.aliases = payload.aliases[:25]
    if payload.display_order is not None:
        requirement.display_order = payload.display_order

    requirement.recruiter_edited = True
    await session.flush()

    await audit.record(
        session, organization_id=principal.organization_id,
        action=(
            audit.Action.REQUIREMENT_NECESSITY_CHANGED if necessity_changed
            else audit.Action.REQUIREMENT_EDITED
        ),
        entity_type="requirement", entity_id=requirement.id,
        actor_id=principal.user_id, actor_label=principal.full_name,
        previous_value=before, new_value=_serialise_requirement(requirement),
    )
    return _serialise_requirement(requirement)


@router.delete("/{job_id}/requirements/{requirement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_requirement(
    job_id: uuid.UUID, requirement_id: uuid.UUID, session: DbSession, principal: WriteUser
):
    await _get_job(session, job_id, principal.organization_id)
    requirement = await session.scalar(
        select(Requirement).where(
            Requirement.id == requirement_id,
            Requirement.job_description_id == job_id,
        )
    )
    if requirement is None:
        raise NotFoundError("That requirement does not exist.")

    before = _serialise_requirement(requirement)
    await session.delete(requirement)
    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.REQUIREMENT_DELETED, entity_type="requirement",
        entity_id=requirement_id, actor_id=principal.user_id,
        actor_label=principal.full_name, previous_value=before,
    )


@router.patch("/{job_id}/weights")
async def update_weights(
    job_id: uuid.UUID, payload: WeightsIn, session: DbSession, principal: WriteUser
):
    job = await _get_job(session, job_id, principal.organization_id)
    before = dict(job.category_weights or {})
    job.category_weights = payload.model_dump()
    await session.flush()

    await audit.record(
        session, organization_id=principal.organization_id,
        action=audit.Action.WEIGHTS_CHANGED, entity_type="job", entity_id=job.id,
        actor_id=principal.user_id, actor_label=principal.full_name,
        previous_value=before, new_value=job.category_weights,
        note="Weights order candidates within a coverage tier only.",
    )
    return {"category_weights": job.category_weights}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_job(job_id: uuid.UUID, session: DbSession, principal: WriteUser):
    from datetime import datetime

    job = await _get_job(session, job_id, principal.organization_id)
    job.archived_at = datetime.now(UTC)
    job.status = JobStatus.ARCHIVED
    await audit.record(
        session, organization_id=principal.organization_id, action="job.archived",
        entity_type="job", entity_id=job.id, actor_id=principal.user_id,
        actor_label=principal.full_name,
    )
