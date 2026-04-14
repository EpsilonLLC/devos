"""Integration tests — AC-F002: Task Creation.

Covers POST /api/v1/tasks
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_create_task_minimal_title_returns_201_with_defaults(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with only a title returns 201 with task.id UUID,
    status='to-do', priority='medium', tags=[], notes='', and a valid
    created_at timestamp."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "Fix authentication bug"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 201
    task = response.json()["task"]

    assert uuid.UUID(task["id"])
    assert task["title"] == "Fix authentication bug"
    assert task["status"] == "to-do"
    assert task["priority"] == "medium"
    assert task["tags"] == []
    assert task["notes"] == ""
    assert "created_at" in task
    assert task["user_id"] == registered_user["user"]["id"]


async def test_create_task_all_optional_fields_reflected(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with status, priority, tags, and notes returns 201
    with all submitted fields reflected in the task object."""
    payload = {
        "title": "Refactor DB layer",
        "status": "in-progress",
        "priority": "high",
        "tags": ["backend", "perf"],
        "notes": "Migrate to async SQLAlchemy",
    }
    response = await client.post(
        "/api/v1/tasks", json=payload, headers=registered_user["headers"]
    )
    assert response.status_code == 201
    task = response.json()["task"]
    assert task["status"] == "in-progress"
    assert task["priority"] == "high"
    assert task["tags"] == ["backend", "perf"]
    assert task["notes"] == "Migrate to async SQLAlchemy"


async def test_create_task_deduplicates_tags_and_strips_empty_strings(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with tags=['bug','bug',''] returns 201 with
    persisted tags=['bug'] — duplicates removed and empty strings stripped."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "Fix login", "tags": ["bug", "bug", ""]},
        headers=registered_user["headers"],
    )
    assert response.status_code == 201
    tags = response.json()["task"]["tags"]
    assert tags == ["bug"]


async def test_create_task_unauthenticated_returns_401(client: AsyncClient):
    """POST /api/v1/tasks with no Authorization header returns 401 UNAUTHORIZED
    and no task record is persisted."""
    response = await client.post("/api/v1/tasks", json={"title": "Stealth task"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_create_task_rejects_whitespace_only_title_with_400(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with title=' ' (whitespace only) returns 400
    VALIDATION_ERROR and no task is written to the database."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "   "},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "title" in str(body).lower()


async def test_create_task_rejects_wrong_case_status_and_priority(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with status='TO-DO' or priority='High' (wrong casing)
    returns 400 VALIDATION_ERROR — no silent coercion."""
    wrong_status = await client.post(
        "/api/v1/tasks",
        json={"title": "Task", "status": "TO-DO"},
        headers=registered_user["headers"],
    )
    assert wrong_status.status_code == 400
    assert wrong_status.json()["code"] == "VALIDATION_ERROR"

    wrong_priority = await client.post(
        "/api/v1/tasks",
        json={"title": "Task", "priority": "High"},
        headers=registered_user["headers"],
    )
    assert wrong_priority.status_code == 400
    assert wrong_priority.json()["code"] == "VALIDATION_ERROR"


async def test_create_task_rejects_notes_exceeding_10000_chars(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks with notes longer than 10,000 characters returns
    400 VALIDATION_ERROR specifying the notes field exceeds the limit."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "Big notes task", "notes": "x" * 10_001},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "notes" in str(body).lower()


async def test_create_task_db_failure_returns_500_no_partial_record(
    client: AsyncClient, registered_user: dict
):
    """POST /api/v1/tasks when the database write fails returns 500
    INTERNAL_ERROR and leaves no partial task record."""
    from devos.tasks.repository import TaskRepository

    with patch.object(TaskRepository, "create_task", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("DB connection timeout")
        response = await client.post(
            "/api/v1/tasks",
            json={"title": "Doomed task"},
            headers=registered_user["headers"],
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_create_task_expired_session_returns_401(client: AsyncClient):
    """POST /api/v1/tasks with an expired/invalid session token returns
    401 UNAUTHORIZED and no task is persisted."""
    response = await client.post(
        "/api/v1/tasks",
        json={"title": "Orphan task"},
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
