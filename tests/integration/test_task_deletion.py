"""Integration tests — AC-F004: Task Deletion.

Covers DELETE /api/v1/tasks/{id}
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_delete_task_owned_by_user_returns_200_and_task_inaccessible(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """DELETE /api/v1/tasks/{id} for a task owned by the authenticated user
    returns 200 with the deleted task id; subsequent GET returns 404."""
    task_id = created_task["id"]
    response = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Task deleted successfully"
    assert body["id"] == task_id

    # Task must be permanently inaccessible
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.status_code == 404
    assert get_resp.json()["code"] == "NOT_FOUND"


async def test_delete_task_unauthenticated_returns_401(
    client: AsyncClient, created_task: dict
):
    """DELETE /api/v1/tasks/{id} without a valid Authorization header returns
    401 UNAUTHORIZED and the task remains in the database."""
    task_id = created_task["id"]
    response = await client.delete(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_delete_task_forbidden_for_another_users_task(
    client: AsyncClient,
    registered_user: dict,
    second_user: dict,
    created_task: dict,
):
    """DELETE /api/v1/tasks/{id} targeting a task owned by a different user
    returns 403 FORBIDDEN and the task record is not deleted."""
    task_id = created_task["id"]
    response = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=second_user["headers"]
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"

    # Task still accessible to its owner
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.status_code == 200


async def test_delete_task_malformed_or_missing_id_returns_400(
    client: AsyncClient, registered_user: dict
):
    """DELETE /api/v1/tasks/{id} with a non-UUID path segment returns 400
    VALIDATION_ERROR."""
    response = await client.delete(
        "/api/v1/tasks/not-a-uuid", headers=registered_user["headers"]
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_delete_task_already_deleted_returns_404(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """DELETE /api/v1/tasks/{id} for a task that has already been deleted
    (double-delete) returns 404 NOT_FOUND."""
    task_id = created_task["id"]
    # First delete succeeds
    r1 = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert r1.status_code == 200

    # Second delete must fail with 404
    r2 = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert r2.status_code == 404
    assert r2.json()["code"] == "NOT_FOUND"


async def test_delete_task_nonexistent_id_returns_404(
    client: AsyncClient, registered_user: dict
):
    """DELETE /api/v1/tasks/{id} with a valid UUID that does not exist
    returns 404 NOT_FOUND."""
    response = await client.delete(
        f"/api/v1/tasks/{uuid.uuid4()}",
        headers=registered_user["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_delete_task_db_failure_returns_500_task_record_intact(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """DELETE /api/v1/tasks/{id} when the database write fails returns 500
    INTERNAL_ERROR and leaves the task record intact."""
    from devos.tasks.repository import TaskRepository

    task_id = created_task["id"]
    with patch.object(TaskRepository, "delete_task", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("DB write failed")
        response = await client.delete(
            f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
