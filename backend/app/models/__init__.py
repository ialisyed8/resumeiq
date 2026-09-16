"""
ORM models.

Two structural decisions carry most of the weight here:

1. **Identity is a separate table with separate grants.** `candidates` holds
   everything the scoring pipeline may see; `candidate_identities` holds name,
   email, phone, address, photo. The worker's database role has no SELECT on
   the identity table (see migrations/versions/0002_grants.py). Redaction is
   enforced by permissions, not by developer discipline.

2. **`requirement_evidence` is the backbone.** One row per candidate per
   requirement, carrying the verdict, the quoted proof, its offsets in the
   source document, and how it was derived. The entire candidate detail screen
   is one query against this table, and scoring reads only from it — which is
   what makes re-scoring possible without touching a PDF or a model.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as _SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.core.config import settings as app_settings


def SAEnum(enum_cls, **kwargs):
    """
    Postgres enum column that persists the enum *value*, not the member name.

    SQLAlchemy stores `UserRole.ADMIN` as the string "ADMIN", but the migration
    creates the type with lowercase values ("admin", "recruiter").
    `values_callable` aligns the two. Every enum column in this file routes
    through here so the mismatch cannot recur one column at a time.
    """
    kwargs.setdefault("values_callable", lambda e: [member.value for member in e])
    return _SAEnum(enum_cls, **kwargs)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    RECRUITER = "recruiter"
    ADMIN = "admin"


class JobStatus(str, enum.Enum):
    DRAFT = "draft"
    REQUIREMENTS_READY = "requirements_ready"
    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class RequirementKind(str, enum.Enum):
    SKILL = "skill"
    EXPERIENCE_DURATION = "experience_duration"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    DOMAIN = "domain"
    RESPONSIBILITY = "responsibility"


class Necessity(str, enum.Enum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


class RequirementWeight(str, enum.Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class ExtractionMethod(str, enum.Enum):
    PYMUPDF = "pymupdf"
    PYTHON_DOCX = "python_docx"
    LIBREOFFICE = "libreoffice"
    OCR_TESSERACT = "ocr_tesseract"
    OCR_VISION = "ocr_vision"


class BatchStatus(str, enum.Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    MATCHING = "matching"
    VERIFYING = "verifying"
    SCORING = "scoring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Verdict(str, enum.Enum):
    MET = "met"
    PARTIAL = "partial"
    NOT_MET = "not_met"


class CoverageTier(str, enum.Enum):
    MEETS_ALL = "meets_all"
    ONE_SHORT = "one_short"
    MULTIPLE_GAPS = "multiple_gaps"


class DecisionAction(str, enum.Enum):
    SHORTLIST = "shortlist"
    REJECT = "reject"
    INTERVIEW = "interview"
    ON_HOLD = "on_hold"
    RESET = "reset"


class MatchMethod(str, enum.Enum):
    ALIAS = "alias"
    LEXICAL = "lexical"
    EMBEDDING_LLM = "embedding_llm"
    LLM_DIRECT = "llm_direct"
    RECRUITER_OVERRIDE = "recruiter_override"
    NOT_EVALUATED = "not_evaluated"


# --------------------------------------------------------------------------
# Tenancy and identity
# --------------------------------------------------------------------------

class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    data_retention_days: Mapped[int] = mapped_column(
        Integer, default=app_settings.DATA_RETENTION_DAYS, nullable=False
    )
    blind_screening_default: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    users: Mapped[list[User]] = relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "email", name="uq_user_org_email"),
        Index("ix_users_email_lower", text("lower(email)")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role"), default=UserRole.RECRUITER, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(back_populates="users")


class RefreshToken(Base, TimestampMixin):
    """Hashed refresh tokens with family tracking for rotation-reuse detection."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[str | None] = mapped_column(String(64))


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------
# Jobs and requirements
# --------------------------------------------------------------------------

class JobDescription(Base, TimestampMixin):
    __tablename__ = "job_descriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    seniority: Mapped[str | None] = mapped_column(String(80))
    min_years_experience: Mapped[float | None] = mapped_column(Float)
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"), default=JobStatus.DRAFT, nullable=False
    )
    category_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requirements: Mapped[list[Requirement]] = relationship(
        back_populates="job", cascade="all, delete-orphan",
        order_by="Requirement.display_order",
    )


class Requirement(Base, TimestampMixin):
    """
    One atomic, individually checkable requirement.

    `recruiter_edited` matters: it distinguishes what the model proposed from
    what the human confirmed, which is the difference between "our model
    weighted these features" and "the recruiter approved these criteria" if the
    ranking is ever challenged.
    """

    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirements_job_necessity", "job_description_id", "necessity"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_description_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[RequirementKind] = mapped_column(
        SAEnum(RequirementKind, name="requirement_kind"),
        default=RequirementKind.SKILL, nullable=False,
    )
    necessity: Mapped[Necessity] = mapped_column(
        SAEnum(Necessity, name="requirement_necessity"),
        default=Necessity.MUST_HAVE, nullable=False,
    )
    weight: Mapped[RequirementWeight] = mapped_column(
        SAEnum(RequirementWeight, name="requirement_weight"),
        default=RequirementWeight.MEDIUM, nullable=False,
    )
    canonical_skill: Mapped[str | None] = mapped_column(String(160), index=True)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)
    source_span: Mapped[str | None] = mapped_column(Text)
    min_years: Mapped[float | None] = mapped_column(Float)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    recruiter_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    job: Mapped[JobDescription] = relationship(back_populates="requirements")


# --------------------------------------------------------------------------
# Candidates — scoring data and identity are deliberately separate
# --------------------------------------------------------------------------

class Candidate(Base, TimestampMixin):
    """
    Everything the scoring pipeline is allowed to see.

    Note the absence of name, email, phone, address, photo, date of birth, and
    school name. Those live in CandidateIdentity.
    """

    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_description_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference: Mapped[str] = mapped_column(String(24), nullable=False)
    current_title: Mapped[str | None] = mapped_column(String(255))
    total_experience_months: Mapped[int | None] = mapped_column(Integer)
    structured_profile: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    decision_status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    identity: Mapped[CandidateIdentity | None] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", uselist=False
    )
    documents: Mapped[list[ResumeDocument]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("job_description_id", "reference", name="uq_candidate_ref"),
        Index("ix_candidates_org_job", "organization_id", "job_description_id"),
    )


class CandidateIdentity(Base, TimestampMixin):
    """
    Personally identifying data. Restricted grants — the worker role cannot read
    this table. Revealing it in the UI is an audited action.
    """

    __tablename__ = "candidate_identities"

    id: Mapped[uuid.UUID] = _uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    full_name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)
    photo_storage_key: Mapped[str | None] = mapped_column(String(512))
    links: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revealed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    candidate: Mapped[Candidate] = relationship(back_populates="identity")


class ResumeDocument(Base, TimestampMixin):
    __tablename__ = "resume_documents"
    __table_args__ = (
        Index("ix_documents_status", "status"),
        Index("ix_documents_hash", "file_hash"),
        CheckConstraint(
            "extraction_confidence >= 0 AND extraction_confidence <= 1",
            name="ck_extraction_confidence_range",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_method: Mapped[ExtractionMethod | None] = mapped_column(
        SAEnum(ExtractionMethod, name="extraction_method")
    )
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    quality_signals: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status"),
        default=DocumentStatus.PENDING, nullable=False,
    )
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    injection_flags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="documents")
    chunks: Mapped[list[ResumeChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ResumeChunk(Base):
    """
    A semantic unit of a resume — one role, one project, one skills block.

    Chunked semantically rather than by fixed token count so that retrieved
    evidence is a coherent, quotable passage.
    """

    __tablename__ = "resume_chunks"
    __table_args__ = (
        Index("ix_chunks_document", "resume_document_id"),
        Index("ix_chunks_section", "section"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    resume_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resume_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str | None] = mapped_column(String(80))
    page: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(app_settings.EMBEDDING_DIMENSIONS)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[ResumeDocument] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------
# Screening, evidence, scores
# --------------------------------------------------------------------------

class ScreeningBatch(Base, TimestampMixin):
    __tablename__ = "screening_batches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_description_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[BatchStatus] = mapped_column(
        SAEnum(BatchStatus, name="batch_status"), default=BatchStatus.QUEUED,
        nullable=False, index=True,
    )
    total_documents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quarantined_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    scorer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    blind_screening: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    model_versions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class RequirementEvidence(Base):
    """
    The explainability backbone. One row per candidate per requirement.

    Both the model's verdict and any recruiter override are kept. The original
    is never overwritten — the pair becomes labelled evaluation data, and the
    audit trail needs to show what the system said versus what the human
    decided.
    """

    __tablename__ = "requirement_evidence"
    __table_args__ = (
        UniqueConstraint(
            "screening_batch_id", "candidate_id", "requirement_id",
            name="uq_evidence_batch_candidate_requirement",
        ),
        Index("ix_evidence_batch_candidate", "screening_batch_id", "candidate_id"),
        Index("ix_evidence_requirement", "requirement_id"),
        CheckConstraint(
            "evidence_grade >= 0 AND evidence_grade <= 1", name="ck_evidence_grade_range"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence_range"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    screening_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )

    verdict: Mapped[Verdict] = mapped_column(
        SAEnum(Verdict, name="evidence_verdict"), nullable=False
    )
    evidence_band: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_grade: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    evidence_page: Mapped[int | None] = mapped_column(Integer)
    evidence_char_start: Mapped[int | None] = mapped_column(Integer)
    evidence_char_end: Mapped[int | None] = mapped_column(Integer)
    absence_statement: Mapped[str | None] = mapped_column(Text)
    reasoning: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    method: Mapped[MatchMethod] = mapped_column(
        SAEnum(MatchMethod, name="match_method"), nullable=False
    )
    model_version: Mapped[str | None] = mapped_column(String(64))
    quote_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    validation_similarity: Mapped[float | None] = mapped_column(Float)

    # Recruiter override — kept alongside, never replacing, the model verdict.
    override_verdict: Mapped[Verdict | None] = mapped_column(
        SAEnum(Verdict, name="evidence_verdict", create_type=False)
    )
    override_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    override_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def effective_verdict(self) -> Verdict:
        return self.override_verdict or self.verdict


class CandidateScore(Base):
    """
    Versioned scoring output. A new scorer version produces new rows rather than
    mutating old ones, so any historical ranking can be reproduced exactly.
    """

    __tablename__ = "candidate_scores"
    __table_args__ = (
        UniqueConstraint(
            "screening_batch_id", "candidate_id", "scorer_version",
            name="uq_score_batch_candidate_version",
        ),
        Index("ix_scores_batch_rank", "screening_batch_id", "rank"),
        Index("ix_scores_batch_tier", "screening_batch_id", "coverage_tier"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    screening_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    must_haves_met: Mapped[int] = mapped_column(Integer, nullable=False)
    must_haves_total: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_tier: Mapped[CoverageTier] = mapped_column(
        SAEnum(CoverageTier, name="coverage_tier"), nullable=False
    )
    must_have_score: Mapped[float] = mapped_column(Float, nullable=False)
    nice_to_have_bonus: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_requirements: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, nullable=False
    )
    scorer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    category_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScreeningQuestion(Base):
    __tablename__ = "screening_questions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    screening_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    edited_by_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    added_to_interview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------
# Decisions and audit
# --------------------------------------------------------------------------

class Decision(Base):
    """Recruiter decisions. Append-only; a change writes a new row."""

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_batch_candidate", "screening_batch_id", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    screening_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[DecisionAction] = mapped_column(
        SAEnum(DecisionAction, name="decision_action"), nullable=False
    )
    previous_status: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    coverage_at_decision: Mapped[str | None] = mapped_column(String(16))
    scorer_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AuditLog(Base):
    """
    Append-only audit trail.

    No ORM update or delete path exists for this table, and the application
    database role is granted INSERT and SELECT only (see the grants migration).
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org_created", "organization_id", "created_at"),
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_action", "action"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    previous_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    scorer_version: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "AuditLog",
    "Base",
    "BatchStatus",
    "Candidate",
    "CandidateIdentity",
    "CandidateScore",
    "CoverageTier",
    "Decision",
    "DecisionAction",
    "DocumentStatus",
    "ExtractionMethod",
    "JobDescription",
    "JobStatus",
    "MatchMethod",
    "Necessity",
    "Organization",
    "PasswordResetToken",
    "RefreshToken",
    "Requirement",
    "RequirementEvidence",
    "RequirementKind",
    "RequirementWeight",
    "ResumeChunk",
    "ResumeDocument",
    "ScreeningBatch",
    "ScreeningQuestion",
    "User",
    "UserRole",
    "Verdict",
]
