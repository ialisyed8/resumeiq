"""Least-privilege database roles.

Enforces two things by permission rather than by developer discipline:

  * The worker role cannot SELECT candidate_identities. Redaction is not
    something a future refactor can accidentally undo.
  * No role can UPDATE or DELETE audit_logs. The trail is append-only.

Run after creating the roles. The DO blocks make this idempotent and safe on a
database where the roles do not exist yet.

Revision ID: 0002
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "resumeiq_app"
WORKER_ROLE = "resumeiq_worker"


def upgrade() -> None:
    op.execute(f"""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{WORKER_ROLE}') THEN
            CREATE ROLE {WORKER_ROLE} NOLOGIN;
        END IF;
    END $$;
    """)

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {WORKER_ROLE}")

    # The audit trail is append-only for everyone.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {WORKER_ROLE}")

    # The scoring pipeline must not be able to read identity, ever.
    op.execute(f"REVOKE ALL ON candidate_identities FROM {WORKER_ROLE}")
    op.execute(f"GRANT INSERT ON candidate_identities TO {WORKER_ROLE}")

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}, {WORKER_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {WORKER_ROLE}")
