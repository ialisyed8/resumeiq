"""
Evidence ladder.

Converts an evidence verdict into a graded score in [0, 1]. The ladder is
deliberately coarse: the difference between "led a 3-year React migration" and
"familiar with React" is large and meaningful, while the difference between two
pieces of strong direct evidence is noise we should not pretend to measure.

Grades are assigned by the verification layer (deterministic matcher or LLM
adjudicator) and validated here. Nothing else in the system is allowed to invent
a grade outside these bands.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Verdict(str, Enum):
    """The three states a requirement can be in for a candidate."""

    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"


class EvidenceBand(str, Enum):
    """Named rungs on the ladder. The value is the wire format."""

    DIRECT_WITH_CONTEXT = "direct_with_context"
    DIRECT = "direct"
    SKILLS_LIST = "skills_list"
    STRONG_PROXY = "strong_proxy"
    HEDGED = "hedged"
    NONE = "none"


#: The ladder itself. Ordered strongest to weakest.
BAND_GRADES: Final[dict[EvidenceBand, float]] = {
    EvidenceBand.DIRECT_WITH_CONTEXT: 1.0,
    EvidenceBand.DIRECT: 0.8,
    EvidenceBand.SKILLS_LIST: 0.6,
    EvidenceBand.STRONG_PROXY: 0.4,
    EvidenceBand.HEDGED: 0.2,
    EvidenceBand.NONE: 0.0,
}

#: Grades at or above this count the requirement as satisfied for the coverage gate.
MET_THRESHOLD: Final[float] = 0.6

#: Grades in [PARTIAL_THRESHOLD, MET_THRESHOLD) count as partial evidence.
PARTIAL_THRESHOLD: Final[float] = 0.2

BAND_DESCRIPTIONS: Final[dict[EvidenceBand, str]] = {
    EvidenceBand.DIRECT_WITH_CONTEXT: (
        "Direct evidence with duration or outcome — the resume says what was "
        "done, for how long, and to what effect."
    ),
    EvidenceBand.DIRECT: (
        "Direct evidence without duration — the resume describes the work but "
        "not how long it ran or what it produced."
    ),
    EvidenceBand.SKILLS_LIST: (
        "Named in a skills list only — no supporting description anywhere in "
        "the document."
    ),
    EvidenceBand.STRONG_PROXY: (
        "Strong proxy — a closely implied technology or practice rather than "
        "the requirement itself."
    ),
    EvidenceBand.HEDGED: (
        "Hedged or peripheral — wording such as 'familiar with', 'exposure to', "
        "or 'assisted with'."
    ),
    EvidenceBand.NONE: "No evidence found in the submitted resume.",
}

#: Phrases that cap a claim at HEDGED regardless of what the model returned.
#: Keyword presence is not the same as demonstrated capability, and models are
#: reliably too generous here.
HEDGE_MARKERS: Final[tuple[str, ...]] = (
    "familiar with",
    "familiarity with",
    "exposure to",
    "exposed to",
    "some experience",
    "basic knowledge",
    "basic understanding",
    "working knowledge",
    "coursework in",
    "course work in",
    "studied",
    "learning",
    "currently learning",
    "self-taught in",
    "assisted with",
    "helped with",
    "shadowed",
    "interested in",
    "eager to learn",
    "aware of",
    "conversant with",
    "beginner",
)


@dataclass(frozen=True, slots=True)
class GradedEvidence:
    """A single requirement/candidate evidence result after grading."""

    requirement_id: str
    verdict: Verdict
    band: EvidenceBand
    grade: float
    quote: str | None = None
    reasoning: str | None = None
    confidence: float = 0.0
    method: str = "unknown"

    @property
    def satisfies(self) -> bool:
        """Whether this counts toward the coverage gate."""
        return self.grade >= MET_THRESHOLD


def grade_for_band(band: EvidenceBand) -> float:
    return BAND_GRADES[band]


def verdict_for_grade(grade: float) -> Verdict:
    """Derive the verdict from a grade so the two can never disagree."""
    if grade >= MET_THRESHOLD:
        return Verdict.MET
    if grade >= PARTIAL_THRESHOLD:
        return Verdict.PARTIAL
    return Verdict.NOT_MET


def band_for_grade(grade: float) -> EvidenceBand:
    """Snap an arbitrary grade to the nearest rung at or below it."""
    for band, value in BAND_GRADES.items():
        if grade >= value:
            return band
    return EvidenceBand.NONE


def contains_hedge(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in HEDGE_MARKERS)


def apply_hedge_cap(grade: float, quote: str | None) -> float:
    """
    Cap a grade at the hedged rung when the supporting quote hedges.

    This runs after the model returns its grade. A model asked "does this
    candidate know Kubernetes?" given the text "familiar with Kubernetes" will
    often answer 0.8. The text does not support that, and this is the guard.
    """
    if contains_hedge(quote):
        return min(grade, BAND_GRADES[EvidenceBand.HEDGED])
    return grade


def normalise_grade(grade: float) -> float:
    """Clamp to [0, 1]. Model output is untrusted and may be out of range."""
    if grade != grade:  # NaN
        return 0.0
    return max(0.0, min(1.0, float(grade)))
