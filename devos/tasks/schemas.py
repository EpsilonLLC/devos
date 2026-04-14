import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from devos.tasks.enums import Priority, TaskStatus

_TAG_MAX_LEN = 50
_TAGS_MAX_COUNT = 20
_NOTES_MAX_LEN = 10_000


def _clean_tags(raw: list[str]) -> list[str]:
    """Strip whitespace, drop empty strings, deduplicate while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in raw:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1)
    status: TaskStatus = TaskStatus.todo
    priority: Priority = Priority.medium
    tags: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=_NOTES_MAX_LEN)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be empty or whitespace-only")
        return stripped

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        cleaned = _clean_tags(v)
        for tag in cleaned:
            if len(tag) > _TAG_MAX_LEN:
                raise ValueError(f"each tag must be at most {_TAG_MAX_LEN} characters")
        if len(cleaned) > _TAGS_MAX_COUNT:
            raise ValueError(f"maximum {_TAGS_MAX_COUNT} tags allowed")
        return cleaned


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    status: TaskStatus | None = None
    priority: Priority | None = None
    tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=_NOTES_MAX_LEN)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str | None) -> str | None:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError("title must not be empty or whitespace-only")
        return stripped

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        cleaned = _clean_tags(v)
        for tag in cleaned:
            if len(tag) > _TAG_MAX_LEN:
                raise ValueError(f"each tag must be at most {_TAG_MAX_LEN} characters")
        if len(cleaned) > _TAGS_MAX_COUNT:
            raise ValueError(f"maximum {_TAGS_MAX_COUNT} tags allowed")
        return cleaned

    @model_validator(mode="after")
    def at_least_one_field(self) -> "TaskUpdateRequest":
        if all(
            v is None
            for v in (self.title, self.status, self.priority, self.tags, self.notes)
        ):
            raise ValueError("request body must include at least one editable field")
        return self


class TaskStatusUpdateRequest(BaseModel):
    status: TaskStatus


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class TaskResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    status: TaskStatus
    priority: Priority
    tags: list[str]
    notes: str
    status_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskListItemResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    title: str
    status: TaskStatus
    priority: Priority
    tags: list[str]
    notes_preview: str
    status_changed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskEnvelope(BaseModel):
    task: TaskResponse


class TaskListEnvelope(BaseModel):
    tasks: list[TaskListItemResponse]
    total: int


class TaskDeleteResponse(BaseModel):
    message: str
    id: uuid.UUID
