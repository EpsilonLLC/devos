"""Shared fixtures for integration tests.

Imports from the application modules produced by T-003/T-004/T-005:
  - devos.app            — FastAPI application instance
  - devos.core.database  — Base metadata + get_db_session dependency

The test client overrides get_db_session with an in-memory SQLite session so
every test starts from a clean schema and tears it down after.

To test against a real PostgreSQL instance, set:
  TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_db
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from devos.app import app
from devos.core.database import Base, get_db_session

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"
)

# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def client(db_engine):
    """Return an httpx AsyncClient wired to a clean in-memory database."""
    session_factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


@pytest_asyncio.fixture
async def registered_user(client: AsyncClient):
    """Sign up a user and return credentials + auth headers."""
    payload = {"email": _unique_email(), "password": "Password123"}
    response = await client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "email": payload["email"],
        "password": payload["password"],
        "user": data["user"],
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


@pytest_asyncio.fixture
async def second_user(client: AsyncClient):
    """A second independent user for cross-user ownership tests."""
    payload = {"email": _unique_email(), "password": "OtherPass456"}
    response = await client.post("/api/v1/auth/signup", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    return {
        "email": payload["email"],
        "password": payload["password"],
        "user": data["user"],
        "token": data["token"],
        "headers": {"Authorization": f"Bearer {data['token']}"},
    }


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def created_task(client: AsyncClient, registered_user: dict):
    """Create a minimal task owned by registered_user and return the task object."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "Fixture Task"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()["task"]
