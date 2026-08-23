"""
Screening question generation.

Questions come from gaps, and the framing is deliberate: a gap means the resume
did not mention something, which is an interview topic, not a deficiency. The
templates below are the offline fallback used when the model is unavailable, so
the feature still works in seeded/demo mode.
"""

from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "Accessibility (WCAG)": (
        "Tell us about a production interface where you implemented accessibility "
        "requirements. What did you test with, and what did you change as a result?"
    ),
    "Team leadership": (
        "Describe a time you mentored or led other engineers. What did you own, "
        "and how did you know whether it was working?"
    ),
    "GraphQL": (
        "Walk us through a GraphQL schema you have worked with. What were the "
        "trade-offs against REST for that product?"
    ),
    "Testing (unit + E2E)": (
        "How do you decide what to cover with unit tests versus end-to-end tests? "
        "Give an example from a codebase you owned."
    ),
    "Performance at scale": (
        "Tell us about a performance problem you diagnosed. How did you measure "
        "it, and what was the outcome?"
    ),
    "Design systems": (
        "Describe a design system or component library you contributed to. How "
        "did you handle breaking changes for consumers?"
    ),
    "Kubernetes": (
        "Describe a service you have run on Kubernetes. What did you own about "
        "its deployment and operation?"
    ),
    "AWS": (
        "Which AWS services have you worked with directly, and what did you build "
        "with them?"
    ),
    "Docker": (
        "How have you used containers in your workflow — local development, CI, "
        "or production?"
    ),
}

GENERIC = (
    "Tell us about your experience with {requirement}. A specific example from "
    "recent work would be helpful."
)


def fallback_question(requirement_text: str, canonical: str | None = None) -> str:
    """Deterministic question when the model is unavailable."""
    key = canonical or requirement_text
    if key in TEMPLATES:
        return TEMPLATES[key]
    for name, template in TEMPLATES.items():
        if name.lower() in requirement_text.lower():
            return template
    return GENERIC.format(requirement=requirement_text.lower())


def rationale_for(absence: str | None) -> str:
    return absence or "No direct evidence found in the submitted resume."
