"""
Experience calculation.

Naively summing date ranges is wrong in a way that matters: a contractor with
three concurrent clients over four years reads as twelve years of experience.
This module merges overlapping intervals before measuring, parses the date
formats that actually appear on resumes, and computes per-skill duration with a
recency weighting.

Deliberately excluded: employment gaps are measured (they are useful context for
a recruiter to see) but never scored. Gap length correlates with parental leave,
caregiving, illness, and immigration status. See docs/security.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Iterable, Sequence

MONTHS: Final[dict[str, int]] = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

PRESENT_TOKENS: Final[frozenset[str]] = frozenset(
    {"present", "current", "now", "ongoing", "to date", "today"}
)

SEASONS: Final[dict[str, int]] = {
    "winter": 1, "spring": 4, "summer": 6, "fall": 9, "autumn": 9,
}

_MONTH_YEAR = re.compile(
    r"\b(?P<month>[A-Za-z]{3,9})\.?\s*,?\s*(?P<year>(?:19|20)\d{2})\b"
)
_NUMERIC = re.compile(
    r"\b(?P<month>0?[1-9]|1[0-2])[/\-.](?P<year>(?:19|20)\d{2})\b"
)
_YEAR_ONLY = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")


@dataclass(frozen=True, slots=True)
class Interval:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("interval end precedes start")

    @property
    def months(self) -> int:
        return months_between(self.start, self.end)


@dataclass(slots=True)
class RoleExperience:
    """One employment entry extracted from a resume."""

    title: str
    organisation: str | None
    start: date | None
    end: date | None
    is_current: bool = False
    technologies: list[str] = field(default_factory=list)
    raw_range: str | None = None

    @property
    def interval(self) -> Interval | None:
        if self.start is None:
            return None
        return Interval(self.start, self.end or date.today())


def months_between(start: date, end: date) -> int:
    """Whole months between two dates, floored at zero."""
    if end < start:
        return 0
    return (end.year - start.year) * 12 + (end.month - start.month)


def parse_date_token(token: str, *, is_end: bool = False) -> date | None:
    """
    Parse one side of a date range.

    Handles 'Jan 2019', 'January 2019', '01/2019', '2019', 'Summer 2020',
    'Present'. Returns None when nothing usable is found — callers must treat
    that as unknown, never as zero.
    """
    if not token:
        return None
    cleaned = token.strip().strip(",.").lower()
    if not cleaned:
        return None

    if any(marker in cleaned for marker in PRESENT_TOKENS):
        return date.today()

    match = _MONTH_YEAR.search(cleaned)
    if match:
        name = match.group("month").lower().rstrip(".")
        if name in MONTHS:
            return date(int(match.group("year")), MONTHS[name], 1)
        if name in SEASONS:
            return date(int(match.group("year")), SEASONS[name], 1)

    match = _NUMERIC.search(cleaned)
    if match:
        return date(int(match.group("year")), int(match.group("month")), 1)

    for season, month in SEASONS.items():
        if season in cleaned:
            year_match = _YEAR_ONLY.search(cleaned)
            if year_match:
                return date(int(year_match.group("year")), month, 1)

    match = _YEAR_ONLY.search(cleaned)
    if match:
        year = int(match.group("year"))
        # A bare year on the end of a range means the end of that year.
        return date(year, 12, 1) if is_end else date(year, 1, 1)

    return None


def parse_range(raw: str) -> tuple[date | None, date | None, bool]:
    """
    Parse '2019 - Present', 'Jan 2019 – Mar 2021', '03/2019 to 05/2021'.

    Returns (start, end, is_current).
    """
    if not raw:
        return None, None, False

    normalised = (
        raw.replace("\u2013", "-").replace("\u2014", "-").replace("—", "-")
    )
    parts = re.split(r"\s*(?:-|to|until|through)\s*", normalised, flags=re.I)
    parts = [p for p in parts if p.strip()]

    if len(parts) == 1:
        start = parse_date_token(parts[0])
        return start, None, False

    start = parse_date_token(parts[0])
    end_raw = parts[-1]
    is_current = any(token in end_raw.lower() for token in PRESENT_TOKENS)
    end = parse_date_token(end_raw, is_end=True)
    return start, end, is_current


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """
    Merge overlapping and adjacent intervals.

    This is the fix for the concurrent-contract problem. Two roles running
    2019-2021 and 2020-2022 produce a single 2019-2022 interval, not six years.
    """
    ordered = sorted(intervals, key=lambda i: (i.start, i.end))
    if not ordered:
        return []

    merged: list[Interval] = [ordered[0]]
    for current in ordered[1:]:
        last = merged[-1]
        # Treat a one-month seam as contiguous — resumes routinely round.
        if months_between(last.end, current.start) <= 1:
            if current.end > last.end:
                merged[-1] = Interval(last.start, current.end)
        else:
            merged.append(current)
    return merged


def total_experience_months(roles: Sequence[RoleExperience]) -> int:
    """Total distinct months of employment, overlap removed."""
    intervals = [r.interval for r in roles if r.interval is not None]
    return sum(i.months for i in merge_intervals(intervals))


def total_experience_years(roles: Sequence[RoleExperience]) -> float:
    return round(total_experience_months(roles) / 12.0, 1)


def gaps(roles: Sequence[RoleExperience], *, min_months: int = 3) -> list[Interval]:
    """
    Employment gaps longer than `min_months`.

    Surfaced to recruiters as context only. Never fed into scoring — see the
    module docstring.
    """
    intervals = merge_intervals(
        [r.interval for r in roles if r.interval is not None]
    )
    found: list[Interval] = []
    for earlier, later in zip(intervals, intervals[1:]):
        if months_between(earlier.end, later.start) >= min_months:
            found.append(Interval(earlier.end, later.start))
    return found


def skill_experience_months(
    roles: Sequence[RoleExperience], skill: str
) -> int:
    """
    Months of experience with one specific technology.

    Only counts roles where the skill is named, then merges overlaps. This is
    what lets us distinguish "React for 5 years" from "React listed once".
    """
    needle = skill.strip().lower()
    matching = [
        r.interval
        for r in roles
        if r.interval is not None
        and any(needle == t.strip().lower() for t in r.technologies)
    ]
    return sum(i.months for i in merge_intervals(matching))


def recency_weight(last_used: date | None, *, half_life_years: float = 3.0) -> float:
    """
    Exponential decay on how recently a skill was used.

    React used daily until last month and React last touched in 2018 are not the
    same signal. Half-life of three years by default: skill last used three
    years ago carries half the weight of current use.
    """
    if last_used is None:
        return 0.0
    months_ago = months_between(last_used, date.today())
    if months_ago <= 0:
        return 1.0
    return 0.5 ** (months_ago / (half_life_years * 12.0))


def last_used(roles: Sequence[RoleExperience], skill: str) -> date | None:
    needle = skill.strip().lower()
    ends = [
        (r.interval.end if r.interval else None)
        for r in roles
        if any(needle == t.strip().lower() for t in r.technologies)
    ]
    valid = [e for e in ends if e is not None]
    return max(valid) if valid else None


def skill_profile(roles: Sequence[RoleExperience], skill: str) -> dict:
    """Everything the UI needs to explain a skill-duration requirement."""
    months = skill_experience_months(roles, skill)
    used = last_used(roles, skill)
    return {
        "skill": skill,
        "months": months,
        "years": round(months / 12.0, 1),
        "last_used": used.isoformat() if used else None,
        "recency_weight": round(recency_weight(used), 3),
        "is_current": bool(used and months_between(used, date.today()) <= 3),
    }


def meets_duration_requirement(
    roles: Sequence[RoleExperience],
    required_years: float,
    skill: str | None = None,
) -> tuple[bool, float]:
    """
    Check a duration requirement, returning (met, actual_years).

    A shortfall is reported as a number rather than a pass/fail cliff so the UI
    can say '4 years against a stated 5+ requirement' instead of silently
    filtering the candidate out. Hard year cliffs have a well-documented
    disparate-impact profile by age.
    """
    if skill:
        months = skill_experience_months(roles, skill)
    else:
        months = total_experience_months(roles)
    years = round(months / 12.0, 1)
    return years >= required_years, years
