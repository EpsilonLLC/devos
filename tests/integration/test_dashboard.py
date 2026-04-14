"""Integration tests — AC-F006: Personal Task Dashboard.

Covers:
  GET /api/v1/tasks        — list (with notes_preview, sorted)
  GET /api/v1/tasks/{id}  — detail (full notes)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create(client, headers, **fields):
    r = await client.post("/api/v1/tasks", json={"title": "Task", **fields}, headers=headers)
    assert r.status_code == 201
    return r.json()["task"]


# ---------------------------------------------------------------------------
# AC-F006 behavioural tests
# ---------------------------------------------------------------------------


async def test_dashboard_tasks_sorted_by_status_then_priority(
    client: AsyncClient, registered_user: dict
):
    """GET /api/v1/tasks returns 200 with tasks ordered to-do before
    in-progress before done, and within each group high priority before
    medium before low."""
    h = registered_user["headers"]
    await _create(client, h, title="done-low",       status="done",        priority="low")
    await _create(client, h, title="todo-low",        status="to-do",       priority="low")
    await _create(client, h, title="inprog-high",     status="in-progress", priority="high")
    await _create(client, h, title="todo-high",       status="to-do",       priority="high")
    await _create(client, h, title="inprog-medium",   status="in-progress", priority="medium")
    await _create(client, h, title="done-high",       status="done",        priority="high")

    response = await client.get("/api/v1/tasks", headers=h)
    assert response.status_code == 200
    data = response.json()
    tasks = data["tasks"]
    assert data["total"] == len(tasks)

    STATUS_ORDER = {"to-do": 0, "in-progress": 1, "done": 2}
    PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    for i in range(len(tasks) - 1):
        a, b = tasks[i], tasks[i + 1]
        sa, sb = STATUS_ORDER[a["status"]], STATUS_ORDER[b["status"]]
        assert sa <= sb, f"Status out of order: {a['status']} before {b['status']}"
        if sa == sb:
            pa = PRIORITY_ORDER.get(a["priority"], 99)
            pb = PRIORITY_ORDER.get(b["priority"], 99)
            assert pa <= pb, f"Priority out of order within status {a['status']}"


async def test_dashboard_empty_state_returns_empty_tasks_array(
    client: AsyncClient, registered_user: dict
):
    """GET /api/v1/tasks for a user with no tasks returns 200 with tasks=[]
    and total=0."""
    response = await client.get("/api/v1/tasks", headers=registered_user["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["tasks"] == []
    assert data["total"] == 0


async def test_dashboard_task_detail_returns_full_notes_not_truncated(
    client: AsyncClient, registered_user: dict
):
    """GET /api/v1/tasks/{id} returns 200 with full notes string whereas
    GET /api/v1/tasks returns only a notes_preview truncated at 200 chars."""
    long_notes = "A" * 500  # 500 chars — well above the 200-char preview limit
    task = await _create(client, registered_user["headers"], title="Notes task", notes=long_notes)
    task_id = task["id"]

    # List endpoint: notes_preview must be truncated to ≤ 200 chars
    list_resp = await client.get("/api/v1/tasks", headers=registered_user["headers"])
    assert list_resp.status_code == 200
    list_task = next(t for t in list_resp.json()["tasks"] if t["id"] == task_id)
    assert "notes_preview" in list_task
    assert len(list_task["notes_preview"]) <= 200
    assert "notes" not in list_task  # full notes must NOT appear in the list

    # Detail endpoint: full notes returned
    detail_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=registered_user["headers"])
    assert detail_resp.status_code == 200
    detail_task = detail_resp.json()["task"]
    assert detail_task["notes"] == long_notes


async def test_dashboard_unauthenticated_request_returns_401(client: AsyncClient):
    """GET /api/v1/tasks without a valid Authorization header returns
    401 UNAUTHORIZED."""
    response = await client.get("/api/v1/tasks")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# AC-F006 edge cases
# ---------------------------------------------------------------------------


async def test_dashboard_db_failure_returns_500_internal_error(
    client: AsyncClient, registered_user: dict
):
    """GET /api/v1/tasks when the data store query fails returns 500
    INTERNAL_ERROR."""
    from devos.dashboard.service import DashboardService

    with patch.object(DashboardService, "list_tasks", new_callable=AsyncMock) as mock:
        mock.side_effect = Exception("DB timeout")
        response = await client.get("/api/v1/tasks", headers=registered_user["headers"])

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


async def test_dashboard_task_without_priority_sorted_last_within_status_group(
    client: AsyncClient, registered_user: dict
):
    """A task with no priority value (None) is sorted after all tasks with an
    explicit priority within its status group."""
    h = registered_user["headers"]
    await _create(client, h, title="todo-low",    status="to-do", priority="low")
    await _create(client, h, title="todo-noprio", status="to-do")

    response = await client.get("/api/v1/tasks", headers=h)
    tasks = response.json()["tasks"]
    todo_tasks = [t for t in tasks if t["status"] == "to-do"]

    # The task with explicit priority must appear before the one without
    titles = [t["title"] for t in todo_tasks]
    assert titles.index("todo-low") < titles.index("todo-noprio")


async def test_dashboard_task_with_no_tags_returns_empty_tags_array(
    client: AsyncClient, registered_user: dict
):
    """A task created without tags returns tags=[] in both list and detail
    responses — no placeholder label."""
    task = await _create(client, registered_user["headers"], title="No-tag task")

    list_resp = await client.get("/api/v1/tasks", headers=registered_user["headers"])
    list_task = next(t for t in list_resp.json()["tasks"] if t["id"] == task["id"])
    assert list_task["tags"] == []

    detail_resp = await client.get(
        f"/api/v1/tasks/{task['id']}", headers=registered_user["headers"]
    )
    assert detail_resp.json()["task"]["tags"] == []


async def test_dashboard_expired_session_returns_401_on_navigation(client: AsyncClient):
    """GET /api/v1/tasks with an expired/invalid token returns 401
    UNAUTHORIZED (API equivalent of redirect-to-login)."""
    response = await client.get(
        "/api/v1/tasks",
        headers={"Authorization": "Bearer expired.or.invalid"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_dashboard_detail_forbidden_for_different_users_task(
    client: AsyncClient,
    registered_user: dict,
    second_user: dict,
    created_task: dict,
):
    """GET /api/v1/tasks/{id} for a task belonging to a different user returns
    403 FORBIDDEN."""
    task_id = created_task["id"]
    response = await client.get(
        f"/api/v1/tasks/{task_id}", headers=second_user["headers"]
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


async def test_dashboard_list_returns_all_required_fields(
    client: AsyncClient, registered_user: dict
):
    """GET /api/v1/tasks response includes all required fields per the API
    contract: id, user_id, tenant_id, title, status, priority, tags,
    notes_preview, status_changed_at, created_at, updated_at, total."""
    await _create(client, registered_user["headers"], title="Complete task")

    response = await client.get("/api/v1/tasks", headers=registered_user["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "tasks" in data
    assert "total" in data
    assert data["total"] == 1

    task = data["tasks"][0]
    for field in ("id", "user_id", "tenant_id", "title", "status", "priority",
                  "tags", "notes_preview", "created_at", "updated_at"):
        assert field in task, f"Missing field: {field}"
