"""
The ranking engine.

Two-stage by construction:

  Stage 1 (gate)   Evaluate must-have requirements. A candidate missing any
                   must-have lands in a lower coverage tier. This is a *tier*,
                   not a deduction, and nothing computed in stage 2 can move a
                   candidate across a tier boundary.

  Stage 2 (score)  Within a tier, order by weighted evidence quality plus a
                   capped nice-to-have bonus.

The design intent: a candidate who meets every mandatory requirement always
outranks one who does not, regardless of how many preferred technologies the
latter accumulates. That property is enforced structurally (sort key ordering),
not by weight tuning, and is covered by tests in tests/unit/test_gate.py.

Scoring is a pure function of stored evidence. It never reads a PDF and never
calls a model, which is what makes POST /screenings/{id}/rescore fast enough to
drive an interactive weight slider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Iterable, Sequence

from app.scoring.ladder import GradedEvidence, Verdict

#: Bump this whenever scoring behaviour changes. Persisted alongside every
#: score so any historical ranking can be reproduced or diffed against a new
#: scorer. See docs/ranking.md.
SCORER_VERSION: Final[str] = "2.1.0"


class Necessity(str, Enum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


class CoverageTier(str, Enum):
    """Ordered best to worst. Sort order depends on this ordering."""

    MEETS_ALL = "meets_all"
    ONE_SHORT = "one_short"
    MULTIPLE_GAPS = "multiple_gaps"


TIER_RANK: Final[dict[CoverageTier, int]] = {
    CoverageTier.MEETS_ALL: 0,
    CoverageTier.ONE_SHORT: 1,
    CoverageTier.MULTIPLE_GAPS: 2,
}

TIER_LABELS: Final[dict[CoverageTier, str]] = {
    CoverageTier.MEETS_ALL: "Meets every must-have",
    CoverageTier.ONE_SHORT: "One requirement short",
    CoverageTier.MULTIPLE_GAPS: "Multiple gaps",
}

#: Recruiter-facing match level. Deliberately ordinal — see docs/ranking.md on
#: why we do not present a calibrated probability.
TIER_MATCH_LEVEL: Final[dict[CoverageTier, str]] = {
    CoverageTier.MEETS_ALL: "Strong Match",
    CoverageTier.ONE_SHORT: "Worth a Look",
    CoverageTier.MULTIPLE_GAPS: "Below Bar",
}

#: Requirement weight labels to multipliers.
WEIGHT_MULTIPLIERS: Final[dict[str, float]] = {
    "High": 1.0,
    "Medium": 0.65,
    "Low": 0.35,
}

#: The must-have portion of the final score.
MUST_HAVE_SCALE: Final[float] = 90.0

#: Hard ceiling on the nice-to-have contribution, in final-score points. A
#: candidate cannot stack preferred skills into a higher tier because the tier
#: is decided before this is applied — but we cap it anyway so the number stays
#: interpretable.
BONUS_CAP: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class RequirementSpec:
    """The scoring-relevant view of a requirement."""

    id: str
    text: str
    necessity: Necessity
    weight: str = "Medium"

    @property
    def multiplier(self) -> float:
        return WEIGHT_MULTIPLIERS.get(self.weight, WEIGHT_MULTIPLIERS["Medium"])


@dataclass(frozen=True, slots=True)
class CategoryWeights:
    """
    Recruiter-adjustable category weights.

    These reorder candidates *within* a tier. They are surfaced in the UI with
    that caveat attached, and `rescore` re-applies them without re-running
    extraction.
    """

    skills: float = 35.0
    experience: float = 30.0
    projects: float = 20.0
    education: float = 10.0
    certifications: float = 5.0

    def normalised(self) -> "CategoryWeights":
        total = (
            self.skills
            + self.experience
            + self.projects
            + self.education
            + self.certifications
        )
        if total <= 0:
            return CategoryWeights()
        factor = 100.0 / total
        return CategoryWeights(
            skills=self.skills * factor,
            experience=self.experience * factor,
            projects=self.projects * factor,
            education=self.education * factor,
            certifications=self.certifications * factor,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "skills": self.skills,
            "experience": self.experience,
            "projects": self.projects,
            "education": self.education,
            "certifications": self.certifications,
        }


@dataclass(slots=True)
class CandidateScore:
    """The complete, reproducible scoring result for one candidate."""

    candidate_id: str
    must_haves_met: int
    must_haves_total: int
    tier: CoverageTier
    missing_requirements: list[str] = field(default_factory=list)
    partial_requirements: list[str] = field(default_factory=list)
    must_have_score: float = 0.0
    nice_to_have_bonus: float = 0.0
    final_score: float = 0.0
    rank: int = 0
    scorer_version: str = SCORER_VERSION

    @property
    def tier_label(self) -> str:
        return TIER_LABELS[self.tier]

    @property
    def match_level(self) -> str:
        return TIER_MATCH_LEVEL[self.tier]

    @property
    def coverage_ratio(self) -> str:
        return f"{self.must_haves_met} / {self.must_haves_total}"

    def as_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "must_haves_met": self.must_haves_met,
            "must_haves_total": self.must_haves_total,
            "coverage_tier": self.tier.value,
            "tier_label": self.tier_label,
            "match_level": self.match_level,
            "missing_requirements": self.missing_requirements,
            "partial_requirements": self.partial_requirements,
            "must_have_score": round(self.must_have_score, 4),
            "nice_to_have_bonus": round(self.nice_to_have_bonus, 4),
            "final_score": round(self.final_score, 2),
            "rank": self.rank,
            "scorer_version": self.scorer_version,
        }


def tier_for(met: int, total: int) -> CoverageTier:
    """Map a coverage count to a tier. Total of zero means nothing to gate on."""
    if total == 0:
        return CoverageTier.MEETS_ALL
    short = total - met
    if short <= 0:
        return CoverageTier.MEETS_ALL
    if short == 1:
        return CoverageTier.ONE_SHORT
    return CoverageTier.MULTIPLE_GAPS


def score_candidate(
    candidate_id: str,
    requirements: Sequence[RequirementSpec],
    evidence: dict[str, GradedEvidence],
    weights: CategoryWeights | None = None,
) -> CandidateScore:
    """
    Score a single candidate against a requirement set.

    `evidence` maps requirement id to the graded result. A requirement with no
    entry is treated as no evidence found — never as a pass.
    """
    weights = (weights or CategoryWeights()).normalised()

    musts = [r for r in requirements if r.necessity is Necessity.MUST_HAVE]
    nices = [r for r in requirements if r.necessity is Necessity.NICE_TO_HAVE]

    met = 0
    missing: list[str] = []
    partial: list[str] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for req in musts:
        ev = evidence.get(req.id)
        grade = ev.grade if ev else 0.0
        weighted_sum += grade * req.multiplier
        weight_total += req.multiplier

        if ev and ev.satisfies:
            met += 1
        else:
            missing.append(req.text)
            if ev and ev.verdict is Verdict.PARTIAL:
                partial.append(req.text)

    must_have_score = (weighted_sum / weight_total) if weight_total else 0.0

    # Nice-to-haves contribute a capped bonus. Note this is computed after the
    # tier is fixed, so it can only reorder inside a tier.
    bonus_raw = 0.0
    bonus_weight = 0.0
    for req in nices:
        ev = evidence.get(req.id)
        grade = ev.grade if ev else 0.0
        bonus_raw += grade * req.multiplier
        bonus_weight += req.multiplier
    bonus_fraction = (bonus_raw / bonus_weight) if bonus_weight else 0.0
    nice_bonus = min(BONUS_CAP, bonus_fraction * BONUS_CAP)

    tier = tier_for(met, len(musts))
    final = (must_have_score * MUST_HAVE_SCALE) + nice_bonus

    return CandidateScore(
        candidate_id=candidate_id,
        must_haves_met=met,
        must_haves_total=len(musts),
        tier=tier,
        missing_requirements=missing,
        partial_requirements=partial,
        must_have_score=must_have_score,
        nice_to_have_bonus=nice_bonus,
        final_score=final,
    )


def rank_candidates(scores: Iterable[CandidateScore]) -> list[CandidateScore]:
    """
    Order candidates: tier first, then score, then coverage count, then id.

    The tier term is the gate. Because it is the primary sort key, no amount of
    score can lift a candidate above someone in a better tier. The trailing id
    term makes the ordering total and therefore stable across runs, which
    matters for regression testing.
    """
    ordered = sorted(
        scores,
        key=lambda s: (
            TIER_RANK[s.tier],
            -s.final_score,
            -s.must_haves_met,
            s.candidate_id,
        ),
    )
    for index, score in enumerate(ordered, start=1):
        score.rank = index
    return ordered


def score_batch(
    requirements: Sequence[RequirementSpec],
    evidence_by_candidate: dict[str, dict[str, GradedEvidence]],
    weights: CategoryWeights | None = None,
) -> list[CandidateScore]:
    """Score and rank an entire screening batch. Pure; safe to call repeatedly."""
    scores = [
        score_candidate(candidate_id, requirements, evidence, weights)
        for candidate_id, evidence in evidence_by_candidate.items()
    ]
    return rank_candidates(scores)


def tier_counts(scores: Iterable[CandidateScore]) -> dict[str, int]:
    counts = {tier.value: 0 for tier in CoverageTier}
    for score in scores:
        counts[score.tier.value] += 1
    return counts
