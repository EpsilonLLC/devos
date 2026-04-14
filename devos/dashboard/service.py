"""DashboardService — read-only aggregation layer for F-006.

This service:
  - fetches the authenticated user's tasks via TaskRepository
  - applies sort_and_group_tasks() for the dashboard list
  - applies truncate_notes_preview() for dashboard card previews
  - handles per-task ownership checks for the detail endpoint

The service never writes to or deletes task records.  All mutation
logic lives in tasks/service.py.

Dependency: tasks/repository.py (T-004).  This module cannot be imported
until T-004 is complete.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from devos.tasks.repository import TaskRepository

from .schemas import DashboardResponse, DashboardTaskDetail, DashboardTaskPreview
from .sorting import sort_and_group_tasks
from .truncation import truncate_notes_preview

if TYPE_CHECKING:
    pass  # kept for future type-only imports


class DashboardService:
    """Aggregates and presents task data for the personal dashboard."""

    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo

    # ------------------------------------------------------------------ #
    # Dashboard list                                                        #
    # ------------------------------------------------------------------ #

    async def get_dashboard_for_user(
        self,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> DashboardResponse:
        """Return all tasks for *user_id*, sorted and with notes truncated.

        Returns an empty DashboardResponse (tasks=[], total=0) when the user
        has no tasks — this is the empty-state scenario required by F-006.

        Raises:
            HTTPException(500): propagated from repository on DB failure.
        """
        tasks = await self._task_repo.get_all_for_user(user_id=user_id, db=db)
        sorted_tasks = sort_and_group_tasks(tasks)

        previews = [
            DashboardTaskPreview(
                id=task.id,
                user_id=task.user_id,
                tenant_id=task.tenant_id,
                title=task.title,
                status=task.status,
                priority=task.priority,
                tags=task.tags,
                notes_preview=truncate_notes_preview(task.notes or ""),
                status_changed_at=task.status_changed_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            for task in sorted_tasks
        ]
        return DashboardResponse(tasks=previews, total=len(previews))

    # ------------------------------------------------------------------ #
    # Task detail                                                           #
    # ------------------------------------------------------------------ #

    async def get_task_detail_for_user(
        self,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        db: AsyncSession,
    ) -> DashboardTaskDetail:
        """Return the full task record if *user_id* owns *task_id*.

        Two-step ownership check (per AC-F006):
          1. Fetch task by task_id only — returns 404 if the task does not
             exist or has been soft-deleted.
          2. Compare task.user_id with the authenticated user — returns 403
             if the task belongs to a different user.

        Note: step 1 queries without a user_id filter so that we can
        distinguish NOT_FOUND (404) from FORBIDDEN (403).  No task data is
        returned to an unauthorised caller.

        Raises:
            HTTPException(404): task does not exist.
            HTTPException(403): task exists but belongs to another user.
            HTTPException(500): propagated from repository on DB failure.
        """
        task = await self._task_repo.get_by_id(task_id=task_id, db=db)

        if task is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "Task not found",
                    "code": "NOT_FOUND",
                    "detail": "task_id",
                },
            )

        if str(task.user_id) != str(user_id):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Access forbidden",
                    "code": "FORBIDDEN",
                    "detail": "task_id",
                },
            )

        return DashboardTaskDetail.model_validate(task)
