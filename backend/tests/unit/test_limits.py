"""
Rate limiting and AI budget.

These are cost controls, so the tests are written from the attacker's side:
can a user get more than their allowance by trying harder, waiting differently,
or coming back from another tab?
"""
from datetime import UTC, datetime

import pytest

from app.core.errors import RateLimitError
from app.core.limits import (
    TIER_LIMITS,
    BudgetExceededError,
    BudgetStatus,
    PlanLimits,
    Tier,
    enforce_rate_limit,
    month_start,
)


class TestTierConfiguration:
    def test_every_tier_has_a_limit(self):
        for tier in Tier:
            assert tier in TIER_LIMITS
            limit, window = TIER_LIMITS[tier]
            assert limit > 0 and window > 0

    def test_expensive_tiers_are_tighter_than_cheap_ones(self):
        """An AI call costs money; a results refresh does not. Limits must reflect that."""
        ai_limit, _ = TIER_LIMITS[Tier.AI]
        read_limit, _ = TIER_LIMITS[Tier.READ]
        upload_limit, _ = TIER_LIMITS[Tier.UPLOAD]
        assert ai_limit < read_limit
        assert upload_limit < read_limit

    def test_auth_is_the_tightest(self):
        """Credential stuffing is the highest-frequency attack surface."""
        auth_limit, _ = TIER_LIMITS[Tier.AUTH]
        assert auth_limit <= min(l for l, _ in TIER_LIMITS.values())


class TestRateLimitEnforcement:
    async def test_allows_traffic_under_the_limit(self):
        for _ in range(5):
            await enforce_rate_limit(Tier.AI, "user-under-limit")

    async def test_blocks_past_the_limit(self):
        limit, _ = TIER_LIMITS[Tier.AI]
        subject = "user-over-limit"
        for _ in range(limit):
            await enforce_rate_limit(Tier.AI, subject)
        with pytest.raises(RateLimitError):
            await enforce_rate_limit(Tier.AI, subject)

    async def test_subjects_have_separate_allowances(self):
        limit, _ = TIER_LIMITS[Tier.AI]
        for _ in range(limit):
            await enforce_rate_limit(Tier.AI, "user-a")
        await enforce_rate_limit(Tier.AI, "user-b")  # unaffected

    async def test_tiers_have_separate_allowances(self):
        """Exhausting AI must not lock a user out of reading their own results."""
        limit, _ = TIER_LIMITS[Tier.AI]
        subject = "user-mixed"
        for _ in range(limit):
            await enforce_rate_limit(Tier.AI, subject)
        await enforce_rate_limit(Tier.READ, subject)

    async def test_second_tab_does_not_double_the_allowance(self):
        """
        The limit is keyed on the principal, not the connection. Two tabs are the
        same user and must share one budget.
        """
        limit, _ = TIER_LIMITS[Tier.UPLOAD]
        subject = "user-two-tabs"
        for _ in range(limit):
            await enforce_rate_limit(Tier.UPLOAD, subject)
        with pytest.raises(RateLimitError):
            await enforce_rate_limit(Tier.UPLOAD, subject)

    async def test_message_does_not_leak_internals(self):
        limit, _ = TIER_LIMITS[Tier.AI]
        subject = "user-message"
        for _ in range(limit):
            await enforce_rate_limit(Tier.AI, subject)
        with pytest.raises(RateLimitError) as exc:
            await enforce_rate_limit(Tier.AI, subject)
        message = str(exc.value).lower()
        for leak in ("anthropic", "redis", "claude", "token", "api key"):
            assert leak not in message


class TestPlanLimits:
    def test_defaults_when_no_plan_configured(self):
        limits = PlanLimits.for_organization(None)
        assert limits.monthly_screenings > 0
        assert limits.monthly_candidates > 0

    def test_plan_overrides_defaults(self):
        limits = PlanLimits.for_organization(
            {"plan": {"monthly_screenings": 500, "monthly_candidates": 20000}}
        )
        assert limits.monthly_screenings == 500
        assert limits.monthly_candidates == 20000

    def test_partial_plan_keeps_other_defaults(self):
        default = PlanLimits.for_organization(None)
        limits = PlanLimits.for_organization({"plan": {"monthly_screenings": 7}})
        assert limits.monthly_screenings == 7
        assert limits.monthly_candidates == default.monthly_candidates

    def test_unrelated_settings_ignored(self):
        limits = PlanLimits.for_organization({"demo": True, "theme": "dark"})
        assert limits.monthly_screenings == PlanLimits().monthly_screenings


class TestBudgetStatus:
    def _status(self, s_used, s_limit, c_used=0, c_limit=1000):
        return BudgetStatus(s_used, s_limit, c_used, c_limit, month_start())

    def test_remaining(self):
        assert self._status(10, 50).screenings_remaining == 40

    def test_never_negative(self):
        assert self._status(80, 50).screenings_remaining == 0

    def test_exhausted_on_screenings(self):
        assert self._status(50, 50).exhausted

    def test_exhausted_on_candidates(self):
        assert self._status(1, 50, c_used=1000, c_limit=1000).exhausted

    def test_not_exhausted_under_both(self):
        assert not self._status(10, 50, c_used=100, c_limit=1000).exhausted

    def test_serialises_for_the_api(self):
        payload = self._status(10, 50).as_dict()
        for key in ("screenings_used", "screenings_limit", "screenings_remaining",
                    "candidates_remaining", "period_start"):
            assert key in payload


class TestMonthWindow:
    def test_starts_at_first_of_month_midnight(self):
        start = month_start(datetime(2026, 8, 15, 13, 47, tzinfo=UTC))
        assert (start.day, start.hour, start.minute) == (1, 0, 0)
        assert start.month == 8

    def test_is_timezone_aware(self):
        assert month_start().tzinfo is not None


class TestBudgetError:
    def test_uses_payment_required_not_server_error(self):
        """402 tells a client this is a plan limit, not an outage to retry."""
        assert BudgetExceededError().status_code == 402

    def test_message_names_no_provider(self):
        message = BudgetExceededError().message.lower()
        for leak in ("anthropic", "claude", "openai", "token", "api"):
            assert leak not in message

    def test_message_tells_the_user_what_to_do(self):
        assert "administrator" in BudgetExceededError().message.lower()
