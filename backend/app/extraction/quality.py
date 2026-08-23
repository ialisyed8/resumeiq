"""
Document quality gate.

Runs before any model call. Its job is to answer one question: can this document
be read well enough to screen fairly?

The critical property: a document that fails is *quarantined*, never scored. A
badly parsed resume matches nothing and would otherwise rank last, which is an
adverse decision caused by a file format. That is both unfair and indefensible
in a dispute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.core.config import settings

#: Below this many characters per page, assume there is no usable text layer.
MIN_CHARS_PER_PAGE = 150
#: Fraction of characters that should be letters in real prose.
MIN_ALPHA_RATIO = 0.60
#: Resumes below this length are almost certainly a failed extraction.
MIN_TOTAL_CHARS = 200


class QualityVerdict(str, Enum):
    GOOD = "good"
    OCR_REQUIRED = "ocr_required"
    NEEDS_REVIEW = "needs_review"
    UNREADABLE = "unreadable"


@dataclass(slots=True)
class QualityReport:
    verdict: QualityVerdict
    confidence: float
    signals: dict = field(default_factory=dict)
    reason: str | None = None

    @property
    def passable(self) -> bool:
        return self.verdict is QualityVerdict.GOOD

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 3),
            "signals": self.signals,
            "reason": self.reason,
        }


_WORD = re.compile(r"[A-Za-z]{2,}")
_REPLACEMENT = re.compile(r"[\ufffd\u0000-\u0008\u000b\u000c\u000e-\u001f]")


def alpha_ratio(text: str) -> float:
    if not text:
        return 0.0
    considered = [c for c in text if not c.isspace()]
    if not considered:
        return 0.0
    return sum(1 for c in considered if c.isalpha()) / len(considered)


def word_ratio(text: str) -> float:
    """Fraction of whitespace tokens that look like real words."""
    tokens = text.split()
    if not tokens:
        return 0.0
    return len(_WORD.findall(text)) / len(tokens)


def corruption_ratio(text: str) -> float:
    if not text:
        return 1.0
    return len(_REPLACEMENT.findall(text)) / max(1, len(text))


def mean_line_length(text: str) -> float:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return sum(len(ln) for ln in lines) / len(lines)


def assess(text: str, page_count: int, *, has_text_layer: bool = True) -> QualityReport:
    """
    Score extraction quality in [0, 1] and pick a verdict.

    Signals are all cheap and deterministic. They are stored on the document so
    a recruiter can be told *why* something needs review, and so we can tune the
    threshold against real failures later.
    """
    text = text or ""
    pages = max(1, page_count)
    total = len(text.strip())
    per_page = total / pages

    signals = {
        "total_chars": total,
        "page_count": pages,
        "chars_per_page": round(per_page, 1),
        "alpha_ratio": round(alpha_ratio(text), 3),
        "word_ratio": round(word_ratio(text), 3),
        "corruption_ratio": round(corruption_ratio(text), 5),
        "mean_line_length": round(mean_line_length(text), 1),
        "has_text_layer": has_text_layer,
    }

    if not has_text_layer or total < MIN_TOTAL_CHARS or per_page < MIN_CHARS_PER_PAGE:
        return QualityReport(
            verdict=QualityVerdict.OCR_REQUIRED,
            confidence=0.0,
            signals=signals,
            reason=(
                "No usable text layer was found. This looks like a scanned "
                "document and needs OCR before it can be screened."
            ),
        )

    # Weighted blend. Alpha ratio and word ratio are the strongest indicators of
    # genuine prose versus extraction garbage.
    density = min(1.0, per_page / 900.0)
    score = (
        0.35 * signals["alpha_ratio"]
        + 0.30 * min(1.0, signals["word_ratio"])
        + 0.20 * density
        + 0.15 * (1.0 - min(1.0, signals["corruption_ratio"] * 200))
    )
    score = max(0.0, min(1.0, score))
    signals["score"] = round(score, 3)

    if signals["alpha_ratio"] < MIN_ALPHA_RATIO:
        return QualityReport(
            verdict=QualityVerdict.NEEDS_REVIEW,
            confidence=score,
            signals=signals,
            reason=(
                "The extracted text does not read as prose — the layout may have "
                "confused the parser. Review the original before relying on this."
            ),
        )

    if score < settings.EXTRACTION_CONFIDENCE_THRESHOLD:
        return QualityReport(
            verdict=QualityVerdict.NEEDS_REVIEW,
            confidence=score,
            signals=signals,
            reason=(
                f"Extraction confidence {score:.2f} is below the "
                f"{settings.EXTRACTION_CONFIDENCE_THRESHOLD:.2f} threshold. "
                "Held for manual review rather than screened."
            ),
        )

    return QualityReport(verdict=QualityVerdict.GOOD, confidence=score, signals=signals)


def detect_hidden_text(spans: list[dict]) -> list[dict]:
    """
    Find white-on-white or microscopic text — a real and widely used ATS
    keyword-stuffing technique.

    Each span needs `text`, `color` (int RGB), `size` (pt). Flagged spans are
    excluded from scoring and recorded on the document.
    """
    suspicious = []
    for span in spans:
        text = (span.get("text") or "").strip()
        if not text:
            continue
        size = span.get("size", 12) or 12
        color = span.get("color", 0)
        if size < 4.0:
            suspicious.append({"text": text[:120], "reason": "font smaller than 4pt",
                               "size": size})
            continue
        # Near-white on a white page.
        r, g, b = (color >> 16) & 255, (color >> 8) & 255, color & 255
        if r > 245 and g > 245 and b > 245:
            suspicious.append({"text": text[:120], "reason": "text colour matches page background",
                               "color": f"#{r:02x}{g:02x}{b:02x}"})
    return suspicious
