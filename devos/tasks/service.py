import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.models import User
from devos.tasks.exceptions import TaskForbiddenError, TaskNotFoundError
from devos.tasks.models import Task, TaskTag
from devos.tasks.repository import TaskRepository, TaskTagRepository
from devos.tasks.schemas import (
    TaskCreateRequest,
    TaskListItemResponse,
    TaskResponse,
    TaskStatusUpdateRequest,
    TaskUpdateRequest,
)

_NOTES_PREVIEW_LEN = 200


def _task_to_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        user_id=task.user_id,
        tenant_id=task.tenant_id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        tags=[t.name for t in task.tags],
        notes=task.notes,
        status_changed_at=task.status_changed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _task_to_list_item(task: Task) -> TaskListItemResponse:
    return TaskListItemResponse(
        id=task.id,
        user_id=task.user_id,
        tenant_id=task.tenant_id,
        title=task.title,
        status=task.status,
        priority=task.priority,
        tags=[t.name for t in task.tags],
        notes_preview=task.notes[:_NOTES_PREVIEW_LEN],
        status_changed_at=task.status_changed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


_STATUS_ORDER = {"to-do": 0, "in-progress": 1, "done": 2}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _sort_key(item: TaskListItemResponse) -> tuple[int, int]:
    return (
        _STATUS_ORDER.get(item.status.value, 99),
        _PRIORITY_ORDER.get(item.priority.value, 99),
    )


class TaskService:
    def __init__(self, db: AsyncSession) -> None:
        self._task_repo = TaskRepository(db)
        self._tag_repo = TaskTagRepository(db)
        self._db = db

    async def create_task(self, user: User, payload: TaskCreateRequest) -> TaskResponse:
        now = datetime.now(timezone.utc)
        task = Task(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            user_id=user.id,
            title=payload.title,
            status=payload.status,
            priority=payload.priority,
            notes=payload.notes,
            status_changed_at=None,
            created_at=now,
            updated_at=now,
        )
        await self._task_repo.create(task)

        if payload.tags:
            tags = [
                TaskTag(
                    id=uuid.uuid4(),
                    task_id=task.id,
                    tenant_id=user.tenant_id,
                    name=name,
                    created_at=now,
                )
                for name in payload.tags
            ]
            await self._tag_repo.create_bulk(tags)

        await self._db.commit()
        await self._db.refresh(task)
        return _task_to_response(task)

    async def get_task(self, user: User, task_id: uuid.UUID) -> TaskResponse:
        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.user_id != user.id or task.tenant_id != user.tenant_id:
            raise TaskForbiddenError(str(task_id))
        return _task_to_response(task)

    async def list_tasks(self, user: User) -> list[TaskListItemResponse]:
        tasks = await self._task_repo.list_by_user(user.id, user.tenant_id)
        items = [_task_to_list_item(t) for t in tasks]
        items.sort(key=_sort_key)
        return items

    async def update_task(
        self, user: User, task_id: uuid.UUID, payload: TaskUpdateRequest
    ) -> TaskResponse:
        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.user_id != user.id or task.tenant_id != user.tenant_id:
            raise TaskForbiddenError(str(task_id))

        now = datetime.now(timezone.utc)

        if payload.title is not None:
            task.title = payload.title

        if payload.status is not None and payload.status != task.status:
            task.status = payload.status
            task.status_changed_at = now

        if payload.priority is not None:
            task.priority = payload.priority

        if payload.notes is not None:
            task.notes = payload.notes

        if payload.tags is not None:
            await self._tag_repo.soft_delete_by_task(task_id)
            if payload.tags:
                new_tags = [
                    TaskTag(
                        id=uuid.uuid4(),
                        task_id=task.id,
                        tenant_id=user.tenant_id,
                        name=name,
                        created_at=now,
                    )
                    for name in payload.tags
                ]
                await self._tag_repo.create_bulk(new_tags)

        task.updated_at = now
        await self._task_repo.update(task)
        await self._db.commit()
        await self._db.refresh(task)
        return _task_to_response(task)

    async def update_task_status(
        self, user: User, task_id: uuid.UUID, payload: TaskStatusUpdateRequest
    ) -> TaskResponse:
        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.user_id != user.id or task.tenant_id != user.tenant_id:
            raise TaskForbiddenError(str(task_id))

        if payload.status == task.status:
            return _task_to_response(task)

        now = datetime.now(timezone.utc)
        task.status = payload.status
        task.status_changed_at = now
        task.updated_at = now
        await self._task_repo.update(task)
        await self._db.commit()
        await self._db.refresh(task)
        return _task_to_response(task)

    async def delete_task(self, user: User, task_id: uuid.UUID) -> None:
        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        if task.user_id != user.id or task.tenant_id != user.tenant_id:
            raise TaskForbiddenError(str(task_id))

        await self._tag_repo.soft_delete_by_task(task_id)
        await self._task_repo.soft_delete(task)
        await self._db.commit()
