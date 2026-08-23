"""
Pydantic schemas for model output.

Model responses are untrusted external data. Every field is bounded: enums are
closed, floats are clamped to range, strings are length-limited. A response that
does not validate is rejected and retried, never coerced into something usable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=3, max_length=500)
    kind: Literal[
        "skill", "experience_duration", "education",
        "certification", "domain", "responsibility",
    ] = "skill"
    necessity: Literal["must_have", "nice_to_have"] = "must_have"
    weight: Literal["High", "Medium", "Low"] = "Medium"
    aliases: list[str] = Field(default_factory=list, max_length=25)
    source_span: str | None = Field(default=None, max_length=1000)
    min_years: float | None = Field(default=None, ge=0, le=50)

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, value: list[str]) -> list[str]:
        return [a.strip()[:80] for a in value if a and a.strip()][:25]


class JobAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(min_length=1, max_length=255)
    seniority: str | None = Field(default=None, max_length=80)
    min_years_experience: float | None = Field(default=None, ge=0, le=50)
    requirements: list[ExtractedRequirement] = Field(min_length=1, max_length=40)


class ExtractedRole(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=255)
    organisation: str | None = Field(default=None, max_length=255)
    date_range: str | None = Field(default=None, max_length=120)
    technologies: list[str] = Field(default_factory=list, max_length=60)
    highlights: list[str] = Field(default_factory=list, max_length=20)
    evidence_text: str | None = Field(default=None, max_length=2000)
    page: int | None = Field(default=None, ge=1, le=100)


class ExtractedEducation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Deliberately no institution name and no graduation year. Both are
    # excluded from extraction so they cannot reach the scorer. See
    # docs/security.md on proxy variables.
    degree_level: str | None = Field(default=None, max_length=120)
    field_of_study: str | None = Field(default=None, max_length=200)
    evidence_text: str | None = Field(default=None, max_length=1000)
    page: int | None = Field(default=None, ge=1, le=100)


class ExtractedProject(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=255)
    description: str | None = Field(default=None, max_length=1500)
    technologies: list[str] = Field(default_factory=list, max_length=40)
    evidence_text: str | None = Field(default=None, max_length=1500)
    page: int | None = Field(default=None, ge=1, le=100)


class ExtractedCertification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=255)
    issuer: str | None = Field(default=None, max_length=255)
    evidence_text: str | None = Field(default=None, max_length=1000)
    page: int | None = Field(default=None, ge=1, le=100)


class ResumeProfile(BaseModel):
    """Structured candidate profile. Contains no identifying fields by design."""

    model_config = ConfigDict(extra="ignore")

    current_title: str | None = Field(default=None, max_length=255)
    roles: list[ExtractedRole] = Field(default_factory=list, max_length=40)
    education: list[ExtractedEducation] = Field(default_factory=list, max_length=15)
    projects: list[ExtractedProject] = Field(default_factory=list, max_length=30)
    certifications: list[ExtractedCertification] = Field(default_factory=list, max_length=25)
    skills: list[str] = Field(default_factory=list, max_length=120)
    domains: list[str] = Field(default_factory=list, max_length=20)


class EvidenceVerdict(BaseModel):
    """One requirement adjudication."""

    model_config = ConfigDict(extra="ignore")

    requirement_id: str = Field(max_length=64)
    verdict: Literal["met", "partial", "not_met"]
    evidence_band: Literal[
        "direct_with_context", "direct", "skills_list",
        "strong_proxy", "hedged", "none",
    ]
    evidence_grade: float = Field(ge=0.0, le=1.0)
    evidence_quote: str | None = Field(default=None, max_length=1200)
    reasoning: str = Field(max_length=600)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)

    @field_validator("evidence_grade", "confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        if value != value:  # NaN
            return 0.0
        return max(0.0, min(1.0, value))


class VerificationBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdicts: list[EvidenceVerdict] = Field(min_length=1, max_length=40)


class GeneratedQuestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requirement_id: str = Field(max_length=64)
    question: str = Field(min_length=10, max_length=500)
    rationale: str | None = Field(default=None, max_length=400)


class QuestionSet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    questions: list[GeneratedQuestion] = Field(default_factory=list, max_length=12)
