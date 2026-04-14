from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class DashboardTaskPreview(BaseModel):
    """Serialised task card shown on the dashboard list view.

    Notes are truncated to a short preview; full content is only available
    via the task detail endpoint.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    status: str
    priority: str
    tags: list[str]
    notes_preview: str
    status_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DashboardResponse(BaseModel):
    """Response envelope for GET /api/v1/tasks (dashboard list)."""

    tasks: list[DashboardTaskPreview]
    total: int


class DashboardTaskDetail(BaseModel):
    """Full task record returned by the task detail endpoint.

    Contains the complete notes field (up to 10,000 characters), unlike
    the dashboard list which only shows a truncated preview.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    status: str
    priority: str
    tags: list[str]
    notes: str
    status_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskDetailResponse(BaseModel):
    """Response envelope for GET /api/v1/tasks/{task_id}."""

    task: DashboardTaskDetail
