"""Integration tests — AC-F001: User Authentication.

Covers:
  POST /api/v1/auth/signup
  POST /api/v1/auth/login
  GET  /api/v1/auth/session
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# AC-F001 behavioural tests
# ---------------------------------------------------------------------------


async def test_signup_valid_credentials_returns_user_and_token(client: AsyncClient):
    """POST /api/v1/auth/signup with a unique email and password ≥8 chars returns
    200 with user.id UUID, user.email matching input, and a non-empty token string."""
    payload = {"email": "alice@example.com", "password": "securepass"}
    response = await client.post("/api/v1/auth/signup", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "user" in data
    assert "token" in data

    user = data["user"]
    assert uuid.UUID(user["id"])  # valid UUID
    assert user["email"] == payload["email"]
    assert "created_at" in user

    token = data["token"]
    assert isinstance(token, str) and len(token) > 0


async def test_signup_duplicate_email_returns_409_conflict(client: AsyncClient):
    """POST /api/v1/auth/signup with an already-registered email returns 409
    with code EMAIL_ALREADY_EXISTS and no new user record created."""
    payload = {"email": "bob@example.com", "password": "password1"}
    # First signup succeeds
    r1 = await client.post("/api/v1/auth/signup", json=payload)
    assert r1.status_code == 200

    # Second signup with same email must fail
    r2 = await client.post("/api/v1/auth/signup", json=payload)
    assert r2.status_code == 409
    body = r2.json()
    assert body["code"] == "EMAIL_ALREADY_EXISTS"


async def test_login_correct_credentials_returns_token(client: AsyncClient):
    """POST /api/v1/auth/login with a known email and correct password returns
    200 with a non-empty token string and valid user object."""
    email, password = "carol@example.com", "mypassword"
    await client.post("/api/v1/auth/signup", json={"email": email, "password": password})

    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    data = response.json()
    assert data["token"] and len(data["token"]) > 0
    assert data["user"]["email"] == email


async def test_session_endpoint_rejects_missing_auth_header(client: AsyncClient):
    """GET /api/v1/auth/session without an Authorization header returns 401
    with code UNAUTHORIZED."""
    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# AC-F001 edge cases
# ---------------------------------------------------------------------------


async def test_login_wrong_password_returns_401_invalid_credentials(client: AsyncClient):
    """POST /api/v1/auth/login with correct email but wrong password returns
    401 with code INVALID_CREDENTIALS."""
    email = "dave@example.com"
    await client.post("/api/v1/auth/signup", json={"email": email, "password": "rightpass"})

    response = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrongpass"})
    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_signup_and_login_empty_fields_return_400_validation_error(client: AsyncClient):
    """POST /api/v1/auth/signup and /login with empty email or password field
    return 400 VALIDATION_ERROR."""
    cases = [
        {"email": "", "password": "password1"},
        {"email": "user@example.com", "password": ""},
        {"password": "password1"},   # missing email key
        {"email": "user@example.com"},  # missing password key
    ]
    for payload in cases:
        r = await client.post("/api/v1/auth/signup", json=payload)
        assert r.status_code == 400, f"Expected 400 for payload {payload}, got {r.status_code}"
        assert r.json()["code"] == "VALIDATION_ERROR"

    for payload in cases:
        r = await client.post("/api/v1/auth/login", json=payload)
        assert r.status_code == 400, f"Expected 400 for payload {payload}, got {r.status_code}"
        assert r.json()["code"] == "VALIDATION_ERROR"


async def test_signup_short_password_returns_400_validation_error(client: AsyncClient):
    """POST /api/v1/auth/signup with a password shorter than 8 characters returns
    400 VALIDATION_ERROR."""
    response = await client.post(
        "/api/v1/auth/signup",
        json={"email": "eve@example.com", "password": "short"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_signup_db_unreachable_returns_500_internal_error(client: AsyncClient):
    """POST /api/v1/auth/signup when the database write fails returns
    500 INTERNAL_ERROR."""
    from devos.auth.repository import UserRepository

    with patch.object(UserRepository, "create_user", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("connection refused")
        response = await client.post(
            "/api/v1/auth/signup",
            json={"email": "frank@example.com", "password": "password1"},
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_session_valid_token_allows_dashboard_access(client: AsyncClient):
    """GET /api/v1/auth/session with a valid Bearer token returns 200 with
    user.id, user.email, user.created_at."""
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": "grace@example.com", "password": "password1"},
    )
    token = signup.json()["token"]

    response = await client.get(
        "/api/v1/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    user = response.json()["user"]
    assert "id" in user
    assert user["email"] == "grace@example.com"
    assert "created_at" in user


async def test_session_missing_token_redirects_to_login(client: AsyncClient):
    """GET /api/v1/auth/session with no Authorization header returns 401
    UNAUTHORIZED (API equivalent of redirect-to-login)."""
    response = await client.get("/api/v1/auth/session")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
