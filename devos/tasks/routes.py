import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.dependencies import get_current_user
from devos.auth.models import User
from devos.core.database import get_db
from devos.tasks.schemas import (
    TaskCreateRequest,
    TaskDeleteResponse,
    TaskEnvelope,
    TaskListEnvelope,
    TaskListItemResponse,
    TaskStatusUpdateRequest,
    TaskUpdateRequest,
)
from devos.tasks.service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _get_service(db: AsyncSession = Depends(get_db)) -> TaskService:
    return TaskService(db)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TaskEnvelope)
async def create_task(
    payload: TaskCreateRequest,
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> TaskEnvelope:
    task = await service.create_task(current_user, payload)
    return TaskEnvelope(task=task)


@router.get("", status_code=status.HTTP_200_OK, response_model=TaskListEnvelope)
async def list_tasks(
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> TaskListEnvelope:
    items: list[TaskListItemResponse] = await service.list_tasks(current_user)
    return TaskListEnvelope(tasks=items, total=len(items))


@router.get("/{task_id}", status_code=status.HTTP_200_OK, response_model=TaskEnvelope)
async def get_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> TaskEnvelope:
    task = await service.get_task(current_user, task_id)
    return TaskEnvelope(task=task)


@router.patch("/{task_id}", status_code=status.HTTP_200_OK, response_model=TaskEnvelope)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> TaskEnvelope:
    task = await service.update_task(current_user, task_id, payload)
    return TaskEnvelope(task=task)


@router.patch(
    "/{task_id}/status", status_code=status.HTTP_200_OK, response_model=TaskEnvelope
)
async def update_task_status(
    task_id: uuid.UUID,
    payload: TaskStatusUpdateRequest,
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> TaskEnvelope:
    task = await service.update_task_status(current_user, task_id, payload)
    return TaskEnvelope(task=task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: TaskService = Depends(_get_service),
) -> Response:
    await service.delete_task(current_user, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
