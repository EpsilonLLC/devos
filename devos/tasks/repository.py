import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from devos.tasks.models import Task, TaskTag


class TaskRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        result = await self._db.execute(
            select(Task)
            .options(selectinload(Task.tags))
            .where(Task.id == task_id, Task.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> list[Task]:
        result = await self._db.execute(
            select(Task)
            .options(selectinload(Task.tags))
            .where(
                Task.user_id == user_id,
                Task.tenant_id == tenant_id,
                Task.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def create(self, task: Task) -> Task:
        self._db.add(task)
        await self._db.flush()
        await self._db.refresh(task)
        return task

    async def update(self, task: Task) -> Task:
        await self._db.flush()
        await self._db.refresh(task)
        return task

    async def soft_delete(self, task: Task) -> None:
        task.deleted_at = datetime.now(UTC)
        await self._db.flush()


class TaskTagRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_bulk(self, tags: list[TaskTag]) -> None:
        for tag in tags:
            self._db.add(tag)
        await self._db.flush()

    async def soft_delete_by_task(self, task_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            update(TaskTag)
            .where(TaskTag.task_id == task_id, TaskTag.deleted_at.is_(None))
            .values(deleted_at=now)
        )
