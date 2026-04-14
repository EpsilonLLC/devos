"""Integration tests — AC-F005: Task Status Tracking.

Covers PATCH /api/v1/tasks/{id}/status
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_status_update_valid_transition_updates_status_changed_at(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/status with a valid new status returns 200
    with task.status reflecting the new value and status_changed_at updated
    to current UTC time."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "done"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["status"] == "done"
    assert task["status_changed_at"] is not None


async def test_status_update_same_value_returns_200_no_db_write(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/status with the same status as currently
    stored returns 200 with unchanged task state and no database write."""
    task_id = created_task["id"]
    # created_task defaults to 'to-do'
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "to-do"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["status"] == "to-do"


async def test_status_update_invalid_enum_value_returns_400(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/status with status='blocked' or status='DONE'
    returns 400 VALIDATION_ERROR with valid enum options listed."""
    task_id = created_task["id"]

    for bad_value in ("blocked", "DONE", "in_progress", ""):
        response = await client.patch(
            f"/api/v1/tasks/{task_id}/status",
            json={"status": bad_value},
            headers=registered_user["headers"],
        )
        assert response.status_code == 400, f"Expected 400 for status={bad_value!r}"
        assert response.json()["code"] == "VALIDATION_ERROR"


async def test_status_update_forbidden_for_another_users_task(
    client: AsyncClient,
    registered_user: dict,
    second_user: dict,
    created_task: dict,
):
    """PATCH /api/v1/tasks/{id}/status for a task owned by a different user
    returns 403 FORBIDDEN and task status is not changed."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "done"},
        headers=second_user["headers"],
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    # Status unchanged for owner
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.json()["task"]["status"] == "to-do"


async def test_status_update_nonexistent_task_returns_404(
    client: AsyncClient, registered_user: dict
):
    """PATCH /api/v1/tasks/{id}/status for a task ID that does not exist
    returns 404 NOT_FOUND."""
    response = await client.patch(
        f"/api/v1/tasks/{uuid.uuid4()}/status",
        json={"status": "done"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_status_update_db_failure_returns_500_status_unchanged(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/status when the database write fails returns
    500 INTERNAL_ERROR and the task retains its previous status."""
    from devos.tasks.repository import TaskRepository

    task_id = created_task["id"]
    with patch.object(TaskRepository, "update_status", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("DB connection error")
        response = await client.patch(
            f"/api/v1/tasks/{task_id}/status",
            json={"status": "done"},
            headers=registered_user["headers"],
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_status_update_expired_session_returns_401(client: AsyncClient, created_task: dict):
    """PATCH /api/v1/tasks/{id}/status with an expired/invalid session token
    returns 401 UNAUTHORIZED."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "done"},
        headers={"Authorization": "Bearer expired.token.value"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_status_update_allows_reverse_transition_done_to_todo(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/status for an owned task allows 'done' → 'to-do'
    confirming no transition-order enforcement."""
    task_id = created_task["id"]

    # Move to done
    r1 = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "done"},
        headers=registered_user["headers"],
    )
    assert r1.status_code == 200
    assert r1.json()["task"]["status"] == "done"

    # Move back to to-do — must succeed
    r2 = await client.patch(
        f"/api/v1/tasks/{task_id}/status",
        json={"status": "to-do"},
        headers=registered_user["headers"],
    )
    assert r2.status_code == 200
    assert r2.json()["task"]["status"] == "to-do"
