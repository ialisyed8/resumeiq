"""
Data retention enforcement.

The application has advertised a retention period in Settings since day one and
never enforced it. That is worse than having no setting: it is a promise to a
customer that the code does not keep.

Design constraints:

* **Idempotent.** Safe to run every night, twice, or after a crash mid-run.
* **Conservative.** Deletes only what is provably past the organisation's own
  configured window. Never touches recent data.
* **Audit survives.** Screening decisions must remain explicable long after the
  resume behind them is gone, so candidates are anonymised rather than erased
  and audit rows are governed by a separate, longer window.
* **Bounded.** Processes in batches so a first run against years of backlog does
  not hold a transaction open for an hour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

# ORM and storage imports are deferred into the functions that need them so the
# window arithmetic above stays importable without a database driver installed.
# That keeps the pure logic unit-testable in isolation.
if TYPE_CHECKING:  # pragma: no cover
    from app.models import Candidate, Organization

logger = get_logger(__name__)

#: Rows per transaction. Keeps locks short on a large first run.
BATCH_SIZE = 200


@dataclass(slots=True)
class RetentionReport:
    organizations: int = 0
    candidates_anonymised: int = 0
    documents_deleted: int = 0
    storage_objects_deleted: int = 0
    storage_failures: int = 0
    audit_rows_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "organizations": self.organizations,
            "candidates_anonymised": self.candidates_anonymised,
            "documents_deleted": self.documents_deleted,
            "storage_objects_deleted": self.storage_objects_deleted,
            "storage_failures": self.storage_failures,
            "audit_rows_deleted": self.audit_rows_deleted,
            "errors": self.errors[:20],
        }


def cutoff_for(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=days)


async def anonymise_candidate(
    session: AsyncSession, candidate: Candidate, report: RetentionReport
) -> None:
    """
    Strip a candidate to the point where they are no longer a person.

    Identity row and stored documents are destroyed; the candidate row and its
    scores survive so the screening that referenced them still reconciles. This
    is the difference between "we deleted your data" and "we lost our own audit
    trail".
    """
    from app.models import CandidateIdentity, ResumeDocument
    from app.services.storage import storage

    documents = (
        await session.scalars(
            select(ResumeDocument).where(ResumeDocument.candidate_id == candidate.id)
        )
    ).all()

    for document in documents:
        try:
            storage.delete(document.storage_key)
            report.storage_objects_deleted += 1
        except Exception as exc:
            # Do not abort: an object already gone, or a transient storage
            # error, must not block the database side of the erasure.
            report.storage_failures += 1
            logger.warning(
                "retention_storage_delete_failed",
                document=str(document.id), error=type(exc).__name__,
            )
        await session.delete(document)
        report.documents_deleted += 1

    identity = await session.scalar(
        select(CandidateIdentity).where(
            CandidateIdentity.candidate_id == candidate.id
        )
    )
    if identity is not None:
        await session.delete(identity)

    candidate.structured_profile = {}
    candidate.current_title = None
    candidate.deleted_at = candidate.deleted_at or datetime.now(UTC)
    report.candidates_anonymised += 1


async def enforce_for_organization(
    session: AsyncSession,
    organization: Organization,
    report: RetentionReport,
    *,
    now: datetime | None = None,
) -> None:
    """Apply one organisation's own configured window."""
    from app.models import Candidate
    from app.services import audit

    data_cutoff = cutoff_for(organization.data_retention_days, now)

    candidates = (
        await session.scalars(
            select(Candidate)
            .where(
                Candidate.organization_id == organization.id,
                Candidate.created_at < data_cutoff,
                # Already-anonymised rows have no identity row left; re-running
                # is harmless but pointless, so skip them. This is what makes
                # the job idempotent.
                Candidate.current_title.is_not(None),
            )
            .limit(BATCH_SIZE)
        )
    ).all()

    for candidate in candidates:
        await anonymise_candidate(session, candidate, report)

    if candidates:
        await audit.record(
            session,
            organization_id=organization.id,
            action="retention.enforced",
            entity_type="organization",
            entity_id=organization.id,
            actor_label="Retention job",
            new_value={
                "candidates_anonymised": len(candidates),
                "retention_days": organization.data_retention_days,
                "cutoff": data_cutoff.isoformat(),
            },
            note=(
                f"Anonymised {len(candidates)} candidates older than "
                f"{organization.data_retention_days} days."
            ),
        )


async def prune_audit_logs(
    session: AsyncSession,
    organization: Organization,
    report: RetentionReport,
    *,
    now: datetime | None = None,
) -> None:
    """
    Remove audit rows past the audit window.

    Governed separately and set far longer than candidate data by default,
    because a hiring decision may need to be explained years after the resume
    behind it has been erased.
    """
    from app.core.config import settings
    from app.models import AuditLog

    audit_cutoff = cutoff_for(settings.AUDIT_RETENTION_DAYS, now)
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(
                AuditLog.organization_id == organization.id,
                AuditLog.created_at < audit_cutoff,
            )
            .limit(BATCH_SIZE)
        )
    ).all()
    for row in rows:
        await session.delete(row)
        report.audit_rows_deleted += 1


async def run_retention(
    session: AsyncSession, *, now: datetime | None = None
) -> RetentionReport:
    """Entry point. Safe to call repeatedly."""
    from app.models import Organization

    report = RetentionReport()
    organizations = (await session.scalars(select(Organization))).all()

    for organization in organizations:
        report.organizations += 1
        try:
            await enforce_for_organization(session, organization, report, now=now)
            await prune_audit_logs(session, organization, report, now=now)
            await session.flush()
        except Exception as exc:
            # One organisation's failure must not stop the rest.
            report.errors.append(f"{organization.id}: {type(exc).__name__}")
            logger.exception("retention_failed", organization=str(organization.id))
            await session.rollback()

    await session.commit()
    logger.info("retention_complete", **report.as_dict())
    return report
