"""
Cross-tenant isolation.

The failure this suite exists to prevent is the one that ends a pilot: Company A
seeing Company B's candidates. Every endpoint that accepts an id in its path is
tested from the wrong organisation's perspective.

A 404 is the correct answer — not 403. Confirming that a resource exists but is
forbidden leaks the fact of its existence.
"""
import pytest

from tests.integration.conftest import requires_db

pytestmark = [requires_db, pytest.mark.anyio]

JD = "Senior Backend Engineer. " + "Python FastAPI PostgreSQL Kubernetes AWS. " * 8


@pytest.fixture
async def two_orgs(org_factory):
    return await org_factory("Alpha"), await org_factory("Beta")


@pytest.fixture
async def alpha_job(client, two_orgs):
    alpha, _ = two_orgs
    response = await client.post(
        "/api/jobs", json={"raw_text": JD}, headers=alpha["headers"]
    )
    assert response.status_code == 201
    return response.json()["id"]


class TestJobIsolation:
    async def test_cannot_read_another_orgs_job(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.get(f"/api/jobs/{alpha_job}", headers=beta["headers"])
        assert response.status_code == 404

    async def test_cannot_list_another_orgs_jobs(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.get("/api/jobs", headers=beta["headers"])
        assert response.status_code == 200
        assert alpha_job not in [j["id"] for j in response.json()["items"]]

    async def test_cannot_read_another_orgs_requirements(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.get(
            f"/api/jobs/{alpha_job}/requirements", headers=beta["headers"]
        )
        assert response.status_code == 404

    async def test_cannot_add_requirements_to_another_orgs_job(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.post(
            f"/api/jobs/{alpha_job}/requirements",
            json={"text": "Injected", "necessity": "must_have", "weight": "High"},
            headers=beta["headers"],
        )
        assert response.status_code == 404

    async def test_cannot_change_another_orgs_weights(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.patch(
            f"/api/jobs/{alpha_job}/weights",
            json={"skills": 100, "experience": 0, "projects": 0,
                  "education": 0, "certifications": 0},
            headers=beta["headers"],
        )
        assert response.status_code == 404

    async def test_cannot_archive_another_orgs_job(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.delete(f"/api/jobs/{alpha_job}", headers=beta["headers"])
        assert response.status_code == 404

    async def test_cannot_upload_into_another_orgs_job(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.post(
            f"/api/jobs/{alpha_job}/candidates",
            files={"files": ("cv.pdf", b"%PDF-1.7\n" + b"x" * 400, "application/pdf")},
            headers=beta["headers"],
        )
        assert response.status_code == 404

    async def test_cannot_start_a_screening_on_another_orgs_job(self, client, two_orgs, alpha_job):
        _, beta = two_orgs
        response = await client.post(
            f"/api/jobs/{alpha_job}/screenings", json={}, headers=beta["headers"]
        )
        assert response.status_code == 404


class TestScreeningIsolation:
    @pytest.fixture
    async def alpha_batch(self, client, two_orgs, alpha_job, session):
        """A batch row created directly — the pipeline is not under test here."""
        import uuid as _uuid
        from app.models import ScreeningBatch
        from app.scoring.engine import SCORER_VERSION

        alpha, _ = two_orgs
        batch = ScreeningBatch(
            organization_id=_uuid.UUID(alpha["user"]["organization_id"]),
            job_description_id=_uuid.UUID(alpha_job),
            name="Alpha batch", total_documents=0,
            scorer_version=SCORER_VERSION,
            created_by=_uuid.UUID(alpha["user"]["id"]),
        )
        session.add(batch)
        await session.commit()
        return str(batch.id)

    @pytest.mark.parametrize("path", [
        "", "/results", "/quarantine", "/report", "/export/csv", "/audit",
    ])
    async def test_cannot_read_another_orgs_screening(
        self, client, two_orgs, alpha_batch, path
    ):
        _, beta = two_orgs
        response = await client.get(
            f"/api/screenings/{alpha_batch}{path}", headers=beta["headers"]
        )
        assert response.status_code == 404, f"leak on {path or '/'}"

    async def test_cannot_rescore_another_orgs_screening(self, client, two_orgs, alpha_batch):
        _, beta = two_orgs
        response = await client.post(
            f"/api/screenings/{alpha_batch}/rescore",
            json={"skills": 40, "experience": 30, "projects": 15,
                  "education": 10, "certifications": 5},
            headers=beta["headers"],
        )
        assert response.status_code == 404

    async def test_own_org_can_read_its_screening(self, client, two_orgs, alpha_batch):
        """Guard against over-tightening: isolation must not break normal access."""
        alpha, _ = two_orgs
        response = await client.get(
            f"/api/screenings/{alpha_batch}", headers=alpha["headers"]
        )
        assert response.status_code == 200


class TestAuthenticationRequired:
    @pytest.mark.parametrize("method,path", [
        ("get", "/api/dashboard"),
        ("get", "/api/jobs"),
        ("get", "/api/screenings"),
        ("post", "/api/jobs"),
    ])
    async def test_no_token_is_rejected(self, client, method, path):
        response = await getattr(client, method)(path) if method == "get" else \
            await client.post(path, json={})
        assert response.status_code == 401

    async def test_garbage_token_rejected(self, client):
        response = await client.get(
            "/api/dashboard", headers={"Authorization": "Bearer not-a-token"}
        )
        assert response.status_code == 401


class TestErrorsDoNotLeakExistence:
    async def test_wrong_org_and_nonexistent_are_indistinguishable(
        self, client, two_orgs, alpha_job
    ):
        """
        Both must return the same status and message. A different response for
        "exists but forbidden" tells an attacker the id is real.
        """
        import uuid as _uuid
        _, beta = two_orgs
        real = await client.get(f"/api/jobs/{alpha_job}", headers=beta["headers"])
        fake = await client.get(f"/api/jobs/{_uuid.uuid4()}", headers=beta["headers"])
        assert real.status_code == fake.status_code == 404
        assert real.json()["error"]["message"] == fake.json()["error"]["message"]
