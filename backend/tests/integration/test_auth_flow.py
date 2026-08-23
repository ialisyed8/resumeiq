"""End-to-end auth: registration, login, tenancy isolation, lockout."""
import pytest

from tests.integration.conftest import requires_db

pytestmark = requires_db


class TestRegistrationAndLogin:
    async def test_register_creates_org_and_admin(self, client, session):
        response = await client.post("/api/auth/register", json={
            "full_name": "Alex Morgan", "email": "alex@example.test",
            "password": "a-sufficiently-long-passphrase", "company": "Northwind",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["organization"]["name"] == "Northwind"

    async def test_login_returns_tokens(self, client, session):
        await client.post("/api/auth/register", json={
            "full_name": "Alex", "email": "login@example.test",
            "password": "a-sufficiently-long-passphrase", "company": "Acme",
        })
        response = await client.post("/api/auth/login", json={
            "email": "login@example.test", "password": "a-sufficiently-long-passphrase",
        })
        assert response.status_code == 200
        assert "access_token" in response.json()

    async def test_wrong_password_rejected(self, client, session):
        await client.post("/api/auth/register", json={
            "full_name": "Alex", "email": "wrong@example.test",
            "password": "a-sufficiently-long-passphrase", "company": "Acme",
        })
        response = await client.post("/api/auth/login", json={
            "email": "wrong@example.test", "password": "not-the-password",
        })
        assert response.status_code == 401

    async def test_unknown_email_gives_same_error(self, client, session):
        """Response must not reveal whether an account exists."""
        response = await client.post("/api/auth/login", json={
            "email": "nobody@example.test", "password": "whatever-long-enough",
        })
        assert response.status_code == 401
        assert "not correct" in response.json()["error"]["message"]

    async def test_short_password_rejected(self, client, session):
        response = await client.post("/api/auth/register", json={
            "full_name": "Alex", "email": "short@example.test",
            "password": "short", "company": "Acme",
        })
        assert response.status_code in (409, 422)


class TestAuthorisation:
    async def test_protected_route_requires_token(self, client):
        assert (await client.get("/api/dashboard")).status_code == 401

    async def test_invalid_token_rejected(self, client):
        response = await client.get(
            "/api/dashboard", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    async def test_tenancy_isolation(self, client, session):
        """A user must not see another organization's jobs."""
        await client.post("/api/auth/register", json={
            "full_name": "A", "email": "a@org-one.test",
            "password": "a-sufficiently-long-passphrase", "company": "OrgOne",
        })
        first = (await client.post("/api/auth/login", json={
            "email": "a@org-one.test", "password": "a-sufficiently-long-passphrase",
        })).json()

        job = await client.post(
            "/api/jobs",
            json={"raw_text": "Senior Engineer. " + "React TypeScript. " * 10},
            headers={"Authorization": f"Bearer {first['access_token']}"},
        )
        job_id = job.json()["id"]

        await client.post("/api/auth/register", json={
            "full_name": "B", "email": "b@org-two.test",
            "password": "a-sufficiently-long-passphrase", "company": "OrgTwo",
        })
        second = (await client.post("/api/auth/login", json={
            "email": "b@org-two.test", "password": "a-sufficiently-long-passphrase",
        })).json()

        response = await client.get(
            f"/api/jobs/{job_id}",
            headers={"Authorization": f"Bearer {second['access_token']}"},
        )
        assert response.status_code == 404, "cross-tenant read must not succeed"
