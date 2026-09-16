"""Experience calculation: overlap merging is the case that matters most."""
from datetime import date

import pytest

from app.scoring.experience import (
    Interval,
    RoleExperience,
    gaps,
    meets_duration_requirement,
    merge_intervals,
    parse_date_token,
    parse_range,
    recency_weight,
    skill_experience_months,
    skill_profile,
    total_experience_years,
)


class TestDateParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("Jan 2019", date(2019, 1, 1)),
        ("January 2019", date(2019, 1, 1)),
        ("Sept 2020", date(2020, 9, 1)),
        ("01/2019", date(2019, 1, 1)),
        ("12/2021", date(2021, 12, 1)),
        ("2019", date(2019, 1, 1)),
        ("Summer 2020", date(2020, 6, 1)),
        ("Fall 2018", date(2018, 9, 1)),
    ])
    def test_formats(self, raw, expected):
        assert parse_date_token(raw) == expected

    def test_bare_year_as_end_is_december(self):
        assert parse_date_token("2019", is_end=True) == date(2019, 12, 1)

    @pytest.mark.parametrize("token", ["Present", "current", "NOW", "ongoing"])
    def test_present_resolves_to_today(self, token):
        assert parse_date_token(token) == date.today()

    def test_garbage_returns_none(self):
        assert parse_date_token("sometime later") is None
        assert parse_date_token("") is None

    @pytest.mark.parametrize("raw", [
        "Jan 2019 - Mar 2021", "Jan 2019 – Mar 2021", "Jan 2019 to Mar 2021",
    ])
    def test_range_separators(self, raw):
        start, end, current = parse_range(raw)
        assert start == date(2019, 1, 1)
        assert end == date(2021, 3, 1)
        assert current is False

    def test_present_range_flags_current(self):
        start, end, current = parse_range("2019 - Present")
        assert current is True
        assert end == date.today()


class TestOverlapMerging:
    def test_concurrent_contracts_are_not_double_counted(self):
        """Three concurrent clients over four years is four years, not twelve."""
        roles = [
            RoleExperience("Contractor", "A", date(2019, 1, 1), date(2023, 1, 1)),
            RoleExperience("Contractor", "B", date(2019, 6, 1), date(2022, 6, 1)),
            RoleExperience("Contractor", "C", date(2020, 1, 1), date(2023, 1, 1)),
        ]
        assert total_experience_years(roles) == 4.0

    def test_sequential_roles_sum(self):
        roles = [
            RoleExperience("Dev", "A", date(2018, 1, 1), date(2020, 1, 1)),
            RoleExperience("Dev", "B", date(2020, 1, 1), date(2022, 1, 1)),
        ]
        assert total_experience_years(roles) == 4.0

    def test_one_month_seam_treated_as_continuous(self):
        merged = merge_intervals([
            Interval(date(2019, 1, 1), date(2020, 12, 1)),
            Interval(date(2021, 1, 1), date(2022, 1, 1)),
        ])
        assert len(merged) == 1

    def test_real_gap_is_preserved(self):
        merged = merge_intervals([
            Interval(date(2019, 1, 1), date(2020, 1, 1)),
            Interval(date(2021, 6, 1), date(2022, 1, 1)),
        ])
        assert len(merged) == 2

    def test_empty_input(self):
        assert merge_intervals([]) == []
        assert total_experience_years([]) == 0.0

    def test_role_with_no_dates_is_skipped_not_zeroed(self):
        roles = [
            RoleExperience("Dev", "A", date(2019, 1, 1), date(2022, 1, 1)),
            RoleExperience("Volunteer", "B", None, None),
        ]
        assert total_experience_years(roles) == 3.0


class TestGaps:
    def test_gap_detected(self):
        roles = [
            RoleExperience("Dev", "A", date(2018, 1, 1), date(2019, 1, 1)),
            RoleExperience("Dev", "B", date(2020, 6, 1), date(2022, 1, 1)),
        ]
        found = gaps(roles)
        assert len(found) == 1
        assert found[0].months == 17

    def test_short_gap_ignored(self):
        roles = [
            RoleExperience("Dev", "A", date(2018, 1, 1), date(2019, 1, 1)),
            RoleExperience("Dev", "B", date(2019, 3, 1), date(2022, 1, 1)),
        ]
        assert gaps(roles) == []


class TestSkillSpecific:
    @pytest.fixture
    def roles(self):
        return [
            RoleExperience("FE", "A", date(2017, 1, 1), date(2019, 1, 1), technologies=["React", "jQuery"]),
            RoleExperience("FE", "B", date(2019, 1, 1), date(2024, 1, 1), technologies=["React", "TypeScript"]),
            RoleExperience("BE", "C", date(2015, 1, 1), date(2017, 1, 1), technologies=["Python"]),
        ]

    def test_skill_duration(self, roles):
        assert skill_experience_months(roles, "React") == 84  # 7 years
        assert skill_experience_months(roles, "TypeScript") == 60
        assert skill_experience_months(roles, "Python") == 24

    def test_unknown_skill_is_zero(self, roles):
        assert skill_experience_months(roles, "Kubernetes") == 0

    def test_case_insensitive(self, roles):
        assert skill_experience_months(roles, "react") == 84

    def test_profile_shape(self, roles):
        profile = skill_profile(roles, "React")
        assert profile["years"] == 7.0
        assert profile["last_used"] == "2024-01-01"
        assert 0.0 <= profile["recency_weight"] <= 1.0


class TestRecency:
    def test_current_use_is_full_weight(self):
        assert recency_weight(date.today()) == 1.0

    def test_decay_over_time(self):
        recent = recency_weight(date(date.today().year - 1, 1, 1))
        old = recency_weight(date(date.today().year - 8, 1, 1))
        assert recent > old
        assert old < 0.25

    def test_never_used_is_zero(self):
        assert recency_weight(None) == 0.0


class TestDurationRequirement:
    def test_shortfall_reported_as_number_not_cliff(self):
        roles = [RoleExperience("Dev", "A", date(2021, 1, 1), date(2025, 1, 1))]
        met, years = meets_duration_requirement(roles, 5.0)
        assert met is False
        assert years == 4.0  # UI shows "4 years against a stated 5+ requirement"

    def test_met(self):
        roles = [RoleExperience("Dev", "A", date(2017, 1, 1), date(2025, 1, 1))]
        met, years = meets_duration_requirement(roles, 5.0)
        assert met is True and years == 8.0
