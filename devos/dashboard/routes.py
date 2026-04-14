"""FastAPI router for the personal task dashboard (F-006).

Endpoints owned by this module:

    GET /api/v1/tasks
        Returns all tasks for the authenticated user, sorted by status group
        then priority, with notes truncated to a preview.

    GET /api/v1/tasks/{task_id}
        Returns the full task record for a single task owned by the
        authenticated user (complete notes, all fields).

Both endpoints require a valid JWT session (get_current_user dependency).
Unauthenticated requests are rejected with 401 by the auth dependency
before this router's handler runs.

Dependencies:
    devos.auth.dependencies.get_current_user  (T-001 / T-002)
    devos.auth.schemas.UserOut                (T-001 / T-002)
    devos.core.database.get_db                (T-001)
    devos.tasks.repository.TaskRepository     (T-004)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.dependencies import get_current_user
from devos.auth.schemas import UserOut
from devos.core.database import get_db
from devos.tasks.repository import TaskRepository

from .schemas import DashboardResponse, TaskDetailResponse
from .service import DashboardService

router = APIRouter(prefix="/api/v1/tasks", tags=["dashboard"])


# ────────────────────────────────────────────────────────────────────────────
# Dependency factory
# ────────────────────────────────────────────────────────────────────────────


def _get_dashboard_service() -> DashboardService:
    """Construct a DashboardService with a fresh TaskRepository instance."""
    return DashboardService(task_repo=TaskRepository())


# ────────────────────────────────────────────────────────────────────────────
# Route handlers
# ────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    current_user: Annotated[UserOut, Depends(get_current_user)],
    service: Annotated[DashboardService, Depends(_get_dashboard_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardResponse:
    """Return all tasks for the authenticated user, sorted for the dashboard.

    Sort order: to-do → in-progress → done, then high → medium → low priority.
    Notes are truncated to 200 characters (notes_preview field).
    Returns tasks=[] and total=0 for users with no tasks (empty-state).

    Errors:
        401 UNAUTHORIZED  — no valid session token
        500 INTERNAL_ERROR — database query failed
    """
    return await service.get_dashboard_for_user(
        user_id=current_user.id,
        db=db,
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: uuid.UUID,
    current_user: Annotated[UserOut, Depends(get_current_user)],
    service: Annotated[DashboardService, Depends(_get_dashboard_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TaskDetailResponse:
    """Return the full task record for a single task owned by the caller.

    Returns complete notes content (up to 10,000 characters) unlike the
    dashboard list which only shows a 200-character preview.

    Errors:
        400 VALIDATION_ERROR — task_id is not a valid UUID (handled by FastAPI)
        401 UNAUTHORIZED     — no valid session token
        403 FORBIDDEN        — task exists but belongs to a different user
        404 NOT_FOUND        — task does not exist or has been soft-deleted
        500 INTERNAL_ERROR   — database query failed
    """
    detail = await service.get_task_detail_for_user(
        task_id=task_id,
        user_id=current_user.id,
        db=db,
    )
    return TaskDetailResponse(task=detail)
