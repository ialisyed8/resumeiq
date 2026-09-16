"""
Integration fixtures.

These require a live Postgres with pgvector. Start it with:

    docker compose -f docker-compose.test.yml up -d

Then run with the test database pointed at port 5433:

    TEST_DATABASE_URL=postgresql+asyncpg://resumeiq:resumeiq@localhost:5433/resumeiq_test \
      pytest tests/integration -v

The suite skips when TEST_DATABASE_URL is unset so a laptop without
infrastructure stays green — but CI sets it, and CI failing is what matters.
Skipping is a convenience for local work, never a way to retire a failing test.
"""

from __future__ import annotations

import os
import uuid

import email_validator
email_validator.TEST_ENVIRONMENT = True

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason=(
        "TEST_DATABASE_URL unset. Start docker-compose.test.yml and export it. "
        "CI sets this; a skip here is a local convenience, not a pass."
    ),
)


@pytest.fixture(scope="session")
def database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def engine(database_url):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models import Base

    engine = create_async_engine(database_url, poolclass=None)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def session(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s


@pytest.fixture
async def client(engine, monkeypatch):
    """
    HTTP client bound to the test database.

    The app's session factory is replaced so requests hit the test database
    rather than whatever DATABASE_URL happens to point at.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.db.session as db_session

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "SessionFactory", factory)

    async def override_get_session():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    from app.db.session import get_session
    from app.main import app

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unique_email():
    """Rate limits are keyed per principal; unique accounts keep tests independent."""
    def _make(prefix: str = "user") -> str:
        return f"{prefix}-{uuid.uuid4().hex[:10]}@example.test"
    return _make


@pytest.fixture
async def org_factory(client, unique_email):
    """Register an organisation and return its authenticated headers."""
    async def _make(company: str = "Acme"):
        email = unique_email(company.lower())
        password = "a-sufficiently-long-passphrase"
        await client.post("/api/auth/register", json={
            "full_name": f"{company} Admin", "email": email,
            "password": password, "company": company,
        })
        tokens = (await client.post("/api/auth/login", json={
            "email": email, "password": password,
        })).json()
        return {
            "email": email,
            "password": password,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
            "user": tokens["user"],
        }
    return _make
