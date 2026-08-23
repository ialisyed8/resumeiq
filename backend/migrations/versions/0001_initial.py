"""Initial schema.

Creates the pgvector extension, all tables, and the indexes the hot queries
need. Autogenerate will not create the extension or the HNSW index, so both are
written explicitly here.

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    uid = postgresql.UUID(as_uuid=True)
    ts = sa.DateTime(timezone=True)

    op.create_table(
        "organizations",
        sa.Column("id", uid, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False, unique=True),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("data_retention_days", sa.Integer, nullable=False, server_default="180"),
        sa.Column("blind_screening_default", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "users",
        sa.Column("id", uid, primary_key=True),
        sa.Column("organization_id", uid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("job_title", sa.String(160)),
        sa.Column("role", sa.Enum("recruiter", "admin", name="user_role"), nullable=False, server_default="recruiter"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("email_verified_at", ts),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", ts),
        sa.Column("last_login_at", ts),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("organization_id", "email", name="uq_user_org_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_org", "users", ["organization_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("revoked_at", ts),
        sa.Column("replaced_by", sa.String(64)),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_refresh_family", "refresh_tokens", ["family"])
    op.create_index("ix_refresh_user", "refresh_tokens", ["user_id"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", ts, nullable=False),
        sa.Column("used_at", ts),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "job_descriptions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("organization_id", uid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("seniority", sa.String(80)),
        sa.Column("min_years_experience", sa.Float),
        sa.Column("status", sa.Enum("draft", "requirements_ready", "active", "closed", "archived", name="job_status"), nullable=False, server_default="draft"),
        sa.Column("category_weights", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", uid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("archived_at", ts),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_org", "job_descriptions", ["organization_id"])

    op.create_table(
        "requirements",
        sa.Column("id", uid, primary_key=True),
        sa.Column("job_description_id", uid, sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("kind", sa.Enum("skill", "experience_duration", "education", "certification", "domain", "responsibility", name="requirement_kind"), nullable=False, server_default="skill"),
        sa.Column("necessity", sa.Enum("must_have", "nice_to_have", name="requirement_necessity"), nullable=False, server_default="must_have"),
        sa.Column("weight", sa.Enum("High", "Medium", "Low", name="requirement_weight"), nullable=False, server_default="Medium"),
        sa.Column("canonical_skill", sa.String(160)),
        sa.Column("aliases", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("source_span", sa.Text),
        sa.Column("min_years", sa.Float),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recruiter_edited", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_requirements_job", "requirements", ["job_description_id"])
    op.create_index("ix_requirements_job_necessity", "requirements", ["job_description_id", "necessity"])

    op.create_table(
        "candidates",
        sa.Column("id", uid, primary_key=True),
        sa.Column("organization_id", uid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_description_id", uid, sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("reference", sa.String(24), nullable=False),
        sa.Column("current_title", sa.String(255)),
        sa.Column("total_experience_months", sa.Integer),
        sa.Column("structured_profile", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("decision_status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("deleted_at", ts),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_description_id", "reference", name="uq_candidate_ref"),
    )
    op.create_index("ix_candidates_org_job", "candidates", ["organization_id", "job_description_id"])
    op.create_index("ix_candidates_deleted", "candidates", ["deleted_at"])

    # Identity is a separate table so it can carry separate grants.
    op.create_table(
        "candidate_identities",
        sa.Column("id", uid, primary_key=True),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("full_name", sa.String(255)),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(64)),
        sa.Column("address", sa.Text),
        sa.Column("photo_storage_key", sa.String(512)),
        sa.Column("links", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("revealed_at", ts),
        sa.Column("revealed_by", uid, sa.ForeignKey("users.id")),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "resume_documents",
        sa.Column("id", uid, primary_key=True),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer),
        sa.Column("extraction_method", sa.Enum("pymupdf", "python_docx", "libreoffice", "ocr_tesseract", "ocr_vision", name="extraction_method")),
        sa.Column("extraction_confidence", sa.Float),
        sa.Column("quality_signals", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.Enum("pending", "processing", "extracted", "quarantined", "failed", name="document_status"), nullable=False, server_default="pending"),
        sa.Column("quarantine_reason", sa.Text),
        sa.Column("extracted_text", sa.Text),
        sa.Column("injection_flags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("processed_at", ts),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("extraction_confidence >= 0 AND extraction_confidence <= 1", name="ck_extraction_confidence_range"),
    )
    op.create_index("ix_documents_candidate", "resume_documents", ["candidate_id"])
    op.create_index("ix_documents_status", "resume_documents", ["status"])
    op.create_index("ix_documents_hash", "resume_documents", ["file_hash"])

    op.create_table(
        "resume_chunks",
        sa.Column("id", uid, primary_key=True),
        sa.Column("resume_document_id", uid, sa.ForeignKey("resume_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("section", sa.String(80)),
        sa.Column("page", sa.Integer),
        sa.Column("char_start", sa.Integer, nullable=False),
        sa.Column("char_end", sa.Integer, nullable=False),
        sa.Column("embedding", sa.dialects.postgresql.ARRAY(sa.Float)),  # replaced below
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
    )
    # Use the real pgvector type and an HNSW index for approximate search.
    op.execute("ALTER TABLE resume_chunks DROP COLUMN embedding")
    op.execute(f"ALTER TABLE resume_chunks ADD COLUMN embedding vector({EMBEDDING_DIM})")
    op.execute(
        "CREATE INDEX ix_chunks_embedding ON resume_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.create_index("ix_chunks_document", "resume_chunks", ["resume_document_id"])
    op.create_index("ix_chunks_section", "resume_chunks", ["section"])

    op.create_table(
        "screening_batches",
        sa.Column("id", uid, primary_key=True),
        sa.Column("organization_id", uid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_description_id", uid, sa.ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.Enum("queued", "extracting", "matching", "verifying", "scoring", "completed", "failed", "cancelled", name="batch_status"), nullable=False, server_default="queued"),
        sa.Column("total_documents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quarantined_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scorer_version", sa.String(32), nullable=False),
        sa.Column("category_weights", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("blind_screening", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("model_versions", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text),
        sa.Column("started_at", ts),
        sa.Column("completed_at", ts),
        sa.Column("created_by", uid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_batches_org", "screening_batches", ["organization_id"])
    op.create_index("ix_batches_job", "screening_batches", ["job_description_id"])
    op.create_index("ix_batches_status", "screening_batches", ["status"])

    op.create_table(
        "requirement_evidence",
        sa.Column("id", uid, primary_key=True),
        sa.Column("screening_batch_id", uid, sa.ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", uid, sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verdict", sa.Enum("met", "partial", "not_met", name="evidence_verdict"), nullable=False),
        sa.Column("evidence_band", sa.String(32), nullable=False),
        sa.Column("evidence_grade", sa.Float, nullable=False),
        sa.Column("evidence_quote", sa.Text),
        sa.Column("evidence_page", sa.Integer),
        sa.Column("evidence_char_start", sa.Integer),
        sa.Column("evidence_char_end", sa.Integer),
        sa.Column("absence_statement", sa.Text),
        sa.Column("reasoning", sa.Text),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("method", sa.Enum("alias", "lexical", "embedding_llm", "llm_direct", "recruiter_override", "not_evaluated", name="match_method"), nullable=False),
        sa.Column("model_version", sa.String(64)),
        sa.Column("quote_validated", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("validation_similarity", sa.Float),
        sa.Column("override_verdict", sa.Enum("met", "partial", "not_met", name="evidence_verdict", create_type=False)),
        sa.Column("override_by", uid, sa.ForeignKey("users.id")),
        sa.Column("override_at", ts),
        sa.Column("override_reason", sa.Text),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("screening_batch_id", "candidate_id", "requirement_id", name="uq_evidence_batch_candidate_requirement"),
        sa.CheckConstraint("evidence_grade >= 0 AND evidence_grade <= 1", name="ck_evidence_grade_range"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_evidence_confidence_range"),
    )
    op.create_index("ix_evidence_batch_candidate", "requirement_evidence", ["screening_batch_id", "candidate_id"])
    op.create_index("ix_evidence_requirement", "requirement_evidence", ["requirement_id"])

    op.create_table(
        "candidate_scores",
        sa.Column("id", uid, primary_key=True),
        sa.Column("screening_batch_id", uid, sa.ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("must_haves_met", sa.Integer, nullable=False),
        sa.Column("must_haves_total", sa.Integer, nullable=False),
        sa.Column("coverage_tier", sa.Enum("meets_all", "one_short", "multiple_gaps", name="coverage_tier"), nullable=False),
        sa.Column("must_have_score", sa.Float, nullable=False),
        sa.Column("nice_to_have_bonus", sa.Float, nullable=False, server_default="0"),
        sa.Column("final_score", sa.Float, nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("missing_requirements", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("scorer_version", sa.String(32), nullable=False),
        sa.Column("category_weights", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("computed_at", ts, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("screening_batch_id", "candidate_id", "scorer_version", name="uq_score_batch_candidate_version"),
    )
    op.create_index("ix_scores_batch_rank", "candidate_scores", ["screening_batch_id", "rank"])
    op.create_index("ix_scores_batch_tier", "candidate_scores", ["screening_batch_id", "coverage_tier"])

    op.create_table(
        "screening_questions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("screening_batch_id", uid, sa.ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requirement_id", uid, sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("rationale", sa.Text),
        sa.Column("edited_by_user", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("added_to_interview", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_questions_batch", "screening_questions", ["screening_batch_id"])
    op.create_index("ix_questions_candidate", "screening_questions", ["candidate_id"])

    op.create_table(
        "decisions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("screening_batch_id", uid, sa.ForeignKey("screening_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", uid, sa.ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("action", sa.Enum("shortlist", "reject", "interview", "on_hold", "reset", name="decision_action"), nullable=False),
        sa.Column("previous_status", sa.String(32)),
        sa.Column("note", sa.Text),
        sa.Column("coverage_at_decision", sa.String(16)),
        sa.Column("scorer_version", sa.String(32)),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisions_batch_candidate", "decisions", ["screening_batch_id", "candidate_id"])
    op.create_index("ix_decisions_created", "decisions", ["created_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", uid, primary_key=True),
        sa.Column("organization_id", uid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", uid, sa.ForeignKey("users.id")),
        sa.Column("actor_label", sa.String(255), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", uid),
        sa.Column("previous_value", postgresql.JSONB),
        sa.Column("new_value", postgresql.JSONB),
        sa.Column("scorer_version", sa.String(32)),
        sa.Column("note", sa.Text),
        sa.Column("request_id", sa.String(64)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("created_at", ts, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_org_created", "audit_logs", ["organization_id", "created_at"])
    op.create_index("ix_audit_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_action", "audit_logs", ["action"])


def downgrade() -> None:
    for table in (
        "audit_logs", "decisions", "screening_questions", "candidate_scores",
        "requirement_evidence", "screening_batches", "resume_chunks",
        "resume_documents", "candidate_identities", "candidates", "requirements",
        "job_descriptions", "password_reset_tokens", "refresh_tokens", "users",
        "organizations",
    ):
        op.drop_table(table)
    for enum_name in (
        "user_role", "job_status", "requirement_kind", "requirement_necessity",
        "requirement_weight", "document_status", "extraction_method",
        "batch_status", "evidence_verdict", "coverage_tier", "decision_action",
        "match_method",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
