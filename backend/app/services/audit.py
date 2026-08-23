"""
Audit logging.

Append-only by construction: this module offers a `record` function and no
update or delete path. The application database role is granted INSERT and
SELECT on audit_logs only.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, request_id_var
from app.models import AuditLog

logger = get_logger(__name__)


class Action:
    LOGIN = "user.login"
    LOGIN_FAILED = "user.login_failed"
    LOGOUT = "user.logout"
    PASSWORD_RESET = "user.password_reset"
    JOB_CREATED = "job.created"
    JOB_ANALYSED = "job.analysed"
    REQUIREMENT_ADDED = "requirement.added"
    REQUIREMENT_EDITED = "requirement.edited"
    REQUIREMENT_NECESSITY_CHANGED = "requirement.necessity_changed"
    REQUIREMENT_DELETED = "requirement.deleted"
    WEIGHTS_CHANGED = "scoring.weights_changed"
    RESUME_UPLOADED = "resume.uploaded"
    UPLOAD_REJECTED_MALWARE = "resume.rejected_malware"
    RESUME_PROCESSED = "resume.processed"
    RESUME_QUARANTINED = "resume.quarantined"
    RESUME_DELETED = "resume.deleted"
    SCREENING_STARTED = "screening.started"
    SCREENING_COMPLETED = "screening.completed"
    SCREENING_FAILED = "screening.failed"
    RESCORE = "screening.rescored"
    CANDIDATE_SHORTLISTED = "candidate.shortlisted"
    CANDIDATE_REJECTED = "candidate.rejected"
    CANDIDATE_INTERVIEW = "candidate.interview"
    CANDIDATE_ON_HOLD = "candidate.on_hold"
    CANDIDATE_DELETED = "candidate.deleted"
    IDENTITY_REVEALED = "candidate.identity_revealed"
    EVIDENCE_OVERRIDDEN = "evidence.overridden"
    BLIND_DISABLED = "screening.blind_disabled"
    REPORT_EXPORTED = "report.exported"
    INJECTION_DETECTED = "security.injection_detected"


async def record(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    actor_label: str = "System",
    previous_value: Any = None,
    new_value: Any = None,
    note: str | None = None,
    scorer_version: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Write one audit entry. Never raises into the caller's transaction path."""
    entry = AuditLog(
        organization_id=organization_id,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        previous_value=_jsonable(previous_value),
        new_value=_jsonable(new_value),
        note=note,
        scorer_version=scorer_version,
        request_id=request_id_var.get(),
        ip_address=ip_address,
    )
    session.add(entry)
    logger.info("audit", action=action, entity=entity_type, actor=actor_label)


def _jsonable(value: Any) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    return {"value": str(value)}
