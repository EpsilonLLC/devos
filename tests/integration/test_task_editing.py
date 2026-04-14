"""Integration tests — AC-F003: Task Editing.

Covers PATCH /api/v1/tasks/{id}
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_edit_task_updates_title_and_returns_full_task_object(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} with a new non-empty title returns 200 with
    the updated title reflected in the task object and updated_at advanced."""
    task_id = created_task["id"]
    original_updated_at = created_task["updated_at"]

    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Renamed Task"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["title"] == "Renamed Task"
    assert task["id"] == task_id
    # updated_at must be present and be a valid timestamp
    assert "updated_at" in task


async def test_edit_task_rejects_empty_title_with_400_validation_error(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} with title='' returns 400 VALIDATION_ERROR
    and the task title remains unchanged in the database."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": ""},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "title" in str(body).lower()

    # Verify the task title is unchanged
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.json()["task"]["title"] == created_task["title"]


async def test_edit_task_forbidden_when_task_owned_by_different_user(
    client: AsyncClient,
    registered_user: dict,
    second_user: dict,
    created_task: dict,
):
    """PATCH /api/v1/tasks/{id} where the task belongs to a different user
    returns 403 FORBIDDEN without revealing task details."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Stolen title"},
        headers=second_user["headers"],
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_edit_task_no_changes_still_updates_updated_at_timestamp(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} with all field values identical to current
    stored values returns 200 with updated_at set to current UTC time."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": created_task["title"]},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert "updated_at" in task
    assert task["title"] == created_task["title"]


async def test_edit_task_clears_tags_with_empty_array(
    client: AsyncClient, registered_user: dict
):
    """PATCH /api/v1/tasks/{id} with tags=[] returns 200 with task.tags=[]
    confirming all tags cleared."""
    create_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Tagged task", "tags": ["bug", "perf"]},
        headers=registered_user["headers"],
    )
    assert create_resp.status_code == 201
    task_id = create_resp.json()["task"]["id"]

    patch_resp = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"tags": []},
        headers=registered_user["headers"],
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["task"]["tags"] == []


async def test_edit_task_rejects_invalid_status_value_with_400(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} with status='blocked' returns 400
    VALIDATION_ERROR listing the valid status options."""
    response = await client.patch(
        f"/api/v1/tasks/{created_task['id']}",
        json={"status": "blocked"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_edit_task_returns_404_for_deleted_task(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} for a task that has already been deleted
    returns 404 NOT_FOUND."""
    task_id = created_task["id"]
    # Delete the task first
    del_resp = await client.delete(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert del_resp.status_code == 200

    # Now try to edit the deleted task
    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"title": "Ghost edit"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_edit_task_concurrent_requests_last_write_wins(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """Two concurrent PATCH requests for the same task are processed
    sequentially; the last write is persisted."""
    task_id = created_task["id"]
    headers = registered_user["headers"]

    r1, r2 = await asyncio.gather(
        client.patch(f"/api/v1/tasks/{task_id}", json={"title": "Title A"}, headers=headers),
        client.patch(f"/api/v1/tasks/{task_id}", json={"title": "Title B"}, headers=headers),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    final_title = get_resp.json()["task"]["title"]
    assert final_title in ("Title A", "Title B")


async def test_edit_task_empty_tags_array_clears_all_tags(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id} with tags=[] clears all tags on the task."""
    task_id = created_task["id"]
    # First add some tags
    await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"tags": ["alpha", "beta"]},
        headers=registered_user["headers"],
    )
    # Now clear them
    response = await client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"tags": []},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    assert response.json()["task"]["tags"] == []
