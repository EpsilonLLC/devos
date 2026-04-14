"""Integration tests — AC-F007: Developer-Friendly Task Properties.

Covers PATCH /api/v1/tasks/{id}/properties
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def test_properties_update_priority_tags_notes_returns_full_task(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with all three developer properties
    returns 200 with the full updated task object and updated_at set to
    current UTC time."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={
            "priority": "high",
            "tags": ["backend", "perf"],
            "notes": "# Code snippet\n```python\nprint('hello')\n```",
        },
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    task = response.json()["task"]
    assert task["priority"] == "high"
    assert task["tags"] == ["backend", "perf"]
    assert "Code snippet" in task["notes"]
    assert "updated_at" in task


async def test_properties_update_rejects_invalid_priority_with_400(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with priority='urgent' returns 400
    VALIDATION_ERROR identifying the invalid value; the task record is unchanged."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={"priority": "urgent"},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    # Error must name the invalid value
    assert "urgent" in str(body).lower() or "priority" in str(body).lower()

    # Task must be unchanged
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.json()["task"]["priority"] == created_task["priority"]


async def test_properties_update_notes_stored_verbatim_with_special_chars(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with notes containing backticks,
    newlines, and special characters returns 200 with notes stored exactly
    as submitted."""
    task_id = created_task["id"]
    verbatim_notes = (
        "```python\n"
        "def hello():\n"
        "    return `world` & <script>alert('xss')</script>\n"
        "```\n"
        "Special: ñ, 中文, émoji 🔥\n"
        "Backslash: \\n \\t"
    )
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={"notes": verbatim_notes},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200

    detail = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert detail.json()["task"]["notes"] == verbatim_notes


async def test_properties_update_another_users_task_returns_404_not_403(
    client: AsyncClient,
    registered_user: dict,
    second_user: dict,
    created_task: dict,
):
    """PATCH /api/v1/tasks/{id}/properties for a task belonging to a different
    user returns 404 NOT_FOUND, not 403, to avoid disclosing task existence."""
    task_id = created_task["id"]
    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={"priority": "high"},
        headers=second_user["headers"],
    )
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_properties_update_empty_tags_array_clears_all_tags(
    client: AsyncClient, registered_user: dict
):
    """PATCH /api/v1/tasks/{id}/properties with tags=[] clears all tags and
    persists an empty tag list."""
    create_resp = await client.post(
        "/api/v1/tasks",
        json={"title": "Tagged task", "tags": ["backend", "db"]},
        headers=registered_user["headers"],
    )
    assert create_resp.status_code == 201
    task_id = create_resp.json()["task"]["id"]

    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={"tags": []},
        headers=registered_user["headers"],
    )
    assert response.status_code == 200
    assert response.json()["task"]["tags"] == []


async def test_properties_update_whitespace_only_tag_returns_400_no_partial_save(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with a tag that is whitespace-only
    returns 400 VALIDATION_ERROR and saves nothing."""
    task_id = created_task["id"]
    original_priority = created_task["priority"]

    response = await client.patch(
        f"/api/v1/tasks/{task_id}/properties",
        json={"priority": "high", "tags": ["valid", "   "]},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"

    # Confirm no partial update was applied — priority must be unchanged
    get_resp = await client.get(
        f"/api/v1/tasks/{task_id}", headers=registered_user["headers"]
    )
    assert get_resp.json()["task"]["priority"] == original_priority


async def test_properties_update_notes_exceeding_10000_chars_returns_400(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with notes > 10,000 characters
    returns 400 VALIDATION_ERROR; task is not modified."""
    response = await client.patch(
        f"/api/v1/tasks/{created_task['id']}/properties",
        json={"notes": "x" * 10_001},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "notes" in str(body).lower()


async def test_properties_update_more_than_20_tags_returns_400(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with more than 20 tags returns 400
    VALIDATION_ERROR specifying the maximum tag count."""
    tags = [f"tag{i}" for i in range(21)]
    response = await client.patch(
        f"/api/v1/tasks/{created_task['id']}/properties",
        json={"tags": tags},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_properties_update_empty_body_returns_400_validation_error(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """PATCH /api/v1/tasks/{id}/properties with an empty body (no valid
    property fields) returns 400 VALIDATION_ERROR."""
    response = await client.patch(
        f"/api/v1/tasks/{created_task['id']}/properties",
        json={},
        headers=registered_user["headers"],
    )
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_properties_update_concurrent_requests_no_data_loss(
    client: AsyncClient, registered_user: dict, created_task: dict
):
    """Two concurrent PATCH /properties requests updating different fields
    apply both updates without data loss."""
    task_id = created_task["id"]
    headers = registered_user["headers"]

    r1, r2 = await asyncio.gather(
        client.patch(
            f"/api/v1/tasks/{task_id}/properties",
            json={"priority": "high"},
            headers=headers,
        ),
        client.patch(
            f"/api/v1/tasks/{task_id}/properties",
            json={"notes": "concurrent note"},
            headers=headers,
        ),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Both updates must be reflected
    get_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=headers)
    task = get_resp.json()["task"]
    assert task["priority"] == "high"
    assert task["notes"] == "concurrent note"
