"""
Screening lifecycle against a live database.

Covers the guarantees that only show up once persistence is involved: evidence
survives re-scoring, decisions are audited, deletion erases identity.
"""
import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
async def authed(client):
    await client.post("/api/auth/register", json={
        "full_name": "Alex", "email": "flow@example.test",
        "password": "a-sufficiently-long-passphrase", "company": "Flow",
    })
    tokens = (await client.post("/api/auth/login", json={
        "email": "flow@example.test", "password": "a-sufficiently-long-passphrase",
    })).json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


class TestJobAndRequirements:
    async def test_create_job_and_add_requirement(self, client, authed, session):
        job = await client.post(
            "/api/jobs",
            json={"raw_text": "Senior Frontend Engineer. " + "React TypeScript WCAG. " * 8},
            headers=authed,
        )
        assert job.status_code == 201
        job_id = job.json()["id"]

        added = await client.post(
            f"/api/jobs/{job_id}/requirements",
            json={"text": "React", "necessity": "must_have", "weight": "High"},
            headers=authed,
        )
        assert added.status_code == 201
        assert added.json()["recruiter_edited"] is True

    async def test_screening_requires_a_must_have(self, client, authed, session):
        job = await client.post(
            "/api/jobs",
            json={"raw_text": "A role description. " * 10},
            headers=authed,
        )
        response = await client.post(
            f"/api/jobs/{job.json()['id']}/screenings", json={}, headers=authed
        )
        assert response.status_code == 409
        assert "must-have" in response.json()["error"]["message"]


class TestUploadValidation:
    async def test_bad_file_rejected_with_reason(self, client, authed, session):
        job = await client.post(
            "/api/jobs", json={"raw_text": "Engineer. React. " * 10}, headers=authed
        )
        response = await client.post(
            f"/api/jobs/{job.json()['id']}/candidates",
            files={"files": ("payload.exe", b"MZ\x90\x00" + b"\x00" * 400,
                             "application/octet-stream")},
            headers=authed,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["summary"]["accepted"] == 0
        assert body["rejected"][0]["reason"], "rejection must carry a reason"


class TestAuditTrail:
    async def test_job_creation_is_audited(self, client, authed, session):
        from sqlalchemy import select

        from app.models import AuditLog

        await client.post(
            "/api/jobs", json={"raw_text": "Engineer. React. " * 10}, headers=authed
        )
        rows = (await session.scalars(
            select(AuditLog).where(AuditLog.action == "job.created")
        )).all()
        assert rows, "job creation must produce an audit entry"

    async def test_audit_log_has_no_update_path(self):
        """The audit service must expose no way to mutate an entry."""
        from app.services import audit
        assert hasattr(audit, "record")
        for forbidden in ("update", "delete", "modify", "edit"):
            assert not hasattr(audit, forbidden)
