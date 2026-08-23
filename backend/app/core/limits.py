"""
Cost and abuse controls.

Two distinct protections that are easy to conflate:

**Rate limiting** bounds request *frequency*. It stops a hot loop, a runaway
script, or a stuck retry from hammering an endpoint. Limits are tiered because
one number cannot serve both a results page a recruiter refreshes constantly and
a screening that costs real money to start.

**AI budget** bounds *spend*. Rate limiting alone does not do this: 100
screenings a day, every day, is within any reasonable rate limit and still
unbounded cost. The budget is a hard monthly ceiling per organisation, checked
before an expensive operation is queued.

Both are keyed on the authenticated principal rather than IP. IP-based limiting
is trivially bypassed by opening another tab behind a NAT, and shared office IPs
would collide.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, RateLimitError
from app.core.logging import get_logger
from app.core.ratelimit import limiter

logger = get_logger(__name__)


class Tier(str, Enum):
    """
    Rate-limit tiers.

    Deliberately not one global number. A recruiter refreshing a results page is
    not the same event as starting a screening that costs a dollar, and treating
    them alike either throttles normal use or leaves the expensive path open.
    """

    AUTH = "auth"            # login, register, reset — credential-stuffing surface
    READ = "read"            # results, dashboards, candidate detail
    WRITE = "write"          # requirement edits, decisions, overrides
    UPLOAD = "upload"        # file ingest: bandwidth and storage
    AI = "ai"                # job analysis, screening: real money per call


#: requests per window, window seconds
TIER_LIMITS: dict[Tier, tuple[int, int]] = {
    Tier.AUTH: (settings.AUTH_RATE_LIMIT_PER_MINUTE, 60),
    Tier.READ: (settings.RATE_LIMIT_PER_MINUTE, 60),
    Tier.WRITE: (60, 60),
    Tier.UPLOAD: (20, 60),
    Tier.AI: (10, 60),
}

TIER_MESSAGE: dict[Tier, str] = {
    Tier.AUTH: "Too many attempts. Wait a minute before trying again.",
    Tier.READ: "You are making requests faster than we can serve them. Wait a moment.",
    Tier.WRITE: "Too many changes at once. Wait a moment and try again.",
    Tier.UPLOAD: "Too many uploads at once. Wait a moment before uploading more.",
    Tier.AI: (
        "Too many screening operations at once. These are expensive to run — "
        "wait a minute before starting another."
    ),
}


class BudgetExceededError(AppError):
    """
    Raised when an organisation has spent its monthly AI allowance.

    Deliberately says nothing about the provider, the model, or the underlying
    cost. That is our commercial detail, not the user's, and leaking it invites
    probing.
    """

    status_code = 402  # Payment Required
    code = "ai_budget_exceeded"
    message = (
        "Your organisation has reached its monthly screening limit. "
        "Contact your administrator to raise it."
    )


#: Defaults live at module level rather than being read back off the dataclass.
#: With `slots=True`, `cls.monthly_screenings` returns the slot descriptor, not
#: the default value — a quiet TypeError waiting for the first org without a
#: configured plan.
DEFAULT_MONTHLY_SCREENINGS = 50
DEFAULT_MONTHLY_CANDIDATES = 2_000


@dataclass(frozen=True, slots=True)
class PlanLimits:
    """
    Per-organisation ceilings.

    Stored on the organisation row under `settings["plan"]` so a limit can be
    raised for one customer without a deploy.
    """

    monthly_screenings: int = DEFAULT_MONTHLY_SCREENINGS
    monthly_candidates: int = DEFAULT_MONTHLY_CANDIDATES
    max_candidates_per_screening: int = settings.MAX_CANDIDATES_PER_SCREENING

    @classmethod
    def for_organization(cls, org_settings: dict | None) -> "PlanLimits":
        plan = (org_settings or {}).get("plan") or {}
        return cls(
            monthly_screenings=int(
                plan.get("monthly_screenings", DEFAULT_MONTHLY_SCREENINGS)
            ),
            monthly_candidates=int(
                plan.get("monthly_candidates", DEFAULT_MONTHLY_CANDIDATES)
            ),
            max_candidates_per_screening=int(
                plan.get(
                    "max_candidates_per_screening",
                    settings.MAX_CANDIDATES_PER_SCREENING,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    screenings_used: int
    screenings_limit: int
    candidates_used: int
    candidates_limit: int
    period_start: datetime

    @property
    def screenings_remaining(self) -> int:
        return max(0, self.screenings_limit - self.screenings_used)

    @property
    def candidates_remaining(self) -> int:
        return max(0, self.candidates_limit - self.candidates_used)

    @property
    def exhausted(self) -> bool:
        return self.screenings_remaining <= 0 or self.candidates_remaining <= 0

    def as_dict(self) -> dict:
        return {
            "screenings_used": self.screenings_used,
            "screenings_limit": self.screenings_limit,
            "screenings_remaining": self.screenings_remaining,
            "candidates_used": self.candidates_used,
            "candidates_limit": self.candidates_limit,
            "candidates_remaining": self.candidates_remaining,
            "period_start": self.period_start.isoformat(),
        }


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def enforce_rate_limit(tier: Tier, subject: str) -> None:
    """
    Apply the tier's limit to a subject key.

    `subject` should identify the principal, not the connection — otherwise a
    second browser tab, or a colleague behind the same office NAT, gets a
    separate allowance.
    """
    limit, window = TIER_LIMITS[tier]
    try:
        await limiter.check(f"{tier.value}:{subject}", limit, window)
    except RateLimitError:
        logger.warning("rate_limited", tier=tier.value, subject=subject, limit=limit)
        raise RateLimitError(TIER_MESSAGE[tier]) from None


async def get_budget(
    session: AsyncSession, organization_id: uuid.UUID
) -> BudgetStatus:
    """
    Current month's AI consumption for an organisation.

    Counted from persisted rows rather than a counter, so it survives a Redis
    flush and cannot drift out of sync with reality.
    """
    from app.models import Candidate, Organization, ScreeningBatch

    since = month_start()

    org = await session.scalar(
        select(Organization).where(Organization.id == organization_id)
    )
    limits = PlanLimits.for_organization(org.settings if org else None)

    screenings = await session.scalar(
        select(func.count(ScreeningBatch.id)).where(
            ScreeningBatch.organization_id == organization_id,
            ScreeningBatch.created_at >= since,
        )
    ) or 0

    candidates = await session.scalar(
        select(func.count(Candidate.id)).where(
            Candidate.organization_id == organization_id,
            Candidate.created_at >= since,
        )
    ) or 0

    return BudgetStatus(
        screenings_used=screenings,
        screenings_limit=limits.monthly_screenings,
        candidates_used=candidates,
        candidates_limit=limits.monthly_candidates,
        period_start=since,
    )


async def enforce_budget(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    additional_screenings: int = 0,
    additional_candidates: int = 0,
) -> BudgetStatus:
    """
    Refuse an operation that would exceed the organisation's monthly allowance.

    Called *before* work is queued, never after. A budget checked after the
    Anthropic call has already happened is an accounting record, not a control.

    Concurrency: the count is taken inside the caller's transaction, and the row
    that increments it is written in that same transaction. Two simultaneous
    requests can therefore both observe the pre-write count and both proceed —
    a small overshoot, bounded by concurrency, not an unbounded bypass. A
    `SELECT ... FOR UPDATE` on the organisation row would close it entirely at
    the cost of serialising every screening start; that trade is worth making
    only if overshoot proves to matter in practice.
    """
    status = await get_budget(session, organization_id)

    would_screen = status.screenings_used + additional_screenings
    would_candidates = status.candidates_used + additional_candidates

    if would_screen > status.screenings_limit:
        logger.warning(
            "budget_exceeded", organization=str(organization_id), kind="screenings",
            used=status.screenings_used, limit=status.screenings_limit,
        )
        raise BudgetExceededError(
            f"Your organisation has used {status.screenings_used} of "
            f"{status.screenings_limit} screenings this month. "
            "Contact your administrator to raise the limit."
        )

    if would_candidates > status.candidates_limit:
        logger.warning(
            "budget_exceeded", organization=str(organization_id), kind="candidates",
            used=status.candidates_used, limit=status.candidates_limit,
        )
        raise BudgetExceededError(
            f"Your organisation has screened {status.candidates_used} of "
            f"{status.candidates_limit} candidates this month. "
            "Contact your administrator to raise the limit."
        )

    return status
