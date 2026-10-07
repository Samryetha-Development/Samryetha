"""Typed commands and results for the admin task tracker."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import TaskCommentID, TaskID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class TaskPriority(StrEnum):
    Urgent = "urgent"
    Normal = "normal"


class TaskStatus(StrEnum):
    Open = "open"
    Done = "done"


class TaskModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class TaskRequest(TaskModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class TaskCreate(TaskRequest):
    category: Annotated[str, Field(max_length=40)] | None = None
    title: Annotated[str, Field(min_length=1, max_length=120)]
    notes: Annotated[str, Field(max_length=5000)] | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None


class TaskPatch(TaskRequest):
    category: Annotated[str, Field(max_length=40)] | None = None
    title: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    notes: Annotated[str, Field(max_length=5000)] | None = None
    priority: TaskPriority | None = None


class TaskStatusBody(TaskRequest):
    status: TaskStatus


class CommentBody(TaskRequest):
    body: Annotated[str, Field(min_length=1, max_length=5000)]
    parent_comment_id: Annotated[TaskCommentID, Field(ge=1)] | None = None


class CommentPatch(TaskRequest):
    body: Annotated[str, Field(min_length=1, max_length=5000)]


class TaskAuthorResponse(TaskModel):
    id: UserID
    username: str
    handle: str
    display_name: str


class TaskItemResponse(TaskModel):
    id: TaskID
    author: TaskAuthorResponse
    category: str
    title: str
    notes: str
    priority: TaskPriority
    status: TaskStatus
    done_at: int | None
    created_at: int
    updated_at: int


class TaskCategoryCount(TaskModel):
    category: str
    open: int
    done: int


class TaskListResponse(TaskModel):
    items: list[TaskItemResponse]
    categories: list[TaskCategoryCount]
    can_write: bool = True


class TaskCommentResponse(TaskModel):
    id: TaskCommentID
    task_id: TaskID
    parent_comment_id: TaskCommentID | None
    author: TaskAuthorResponse | None
    body: str
    is_deleted: bool
    created_at: int
    updated_at: int


class TaskCommentListResponse(TaskModel):
    items: list[TaskCommentResponse]


class TaskOperationOkResponse(TaskModel):
    ok: bool = True
