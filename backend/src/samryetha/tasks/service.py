"""Typed service layer for the admin task tracker."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from . import repository
from ..core.db import now_ms
from ..core.errors import not_found
from ..core.ids import TaskCommentID, TaskID, UserID
from .models import (
    TaskAuthorResponse, TaskCategoryCount, TaskCommentResponse, TaskCreate,
    TaskItemResponse, TaskListResponse, TaskPatch, TaskPriority, TaskStatus,
)
from .repository import AuthorRecord, CommentRecord, TaskRecord
from ..users import make_handle

DEFAULT_CATEGORY = "General"


def _category(value: str | None) -> str:
    return (value or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY


def _author(record: AuthorRecord) -> TaskAuthorResponse:
    return TaskAuthorResponse(
        id=record.id,
        username=record.username,
        handle=make_handle(record.username, record.discriminator),
        display_name=record.display_name,
    )


def _task(record: TaskRecord) -> TaskItemResponse:
    return TaskItemResponse(
        id=record.id, author=_author(record.author), category=record.category,
        title=record.title, notes=record.notes, priority=record.priority,
        status=record.status, done_at=record.done_at, created_at=record.created_at,
        updated_at=record.updated_at,
    )


def list_tasks(conn: Connection) -> TaskListResponse:
    items = [_task(record) for record in repository.all_tasks(conn)]
    counts: dict[str, tuple[int, int]] = {}
    for item in items:
        opened, done = counts.get(item.category, (0, 0))
        counts[item.category] = (opened + (item.status is TaskStatus.Open), done + (item.status is TaskStatus.Done))
    categories = [TaskCategoryCount(category=category, open=counts[category][0], done=counts[category][1]) for category in sorted(counts)]
    return TaskListResponse(items=items, categories=categories)


def create_task(conn: Connection, author_id: UserID, command: TaskCreate) -> TaskItemResponse:
    now = now_ms()
    status = command.status or TaskStatus.Open
    task_id = repository.insert_task(
        conn, author_id=author_id, category=_category(command.category), title=command.title,
        notes=command.notes or "", priority=command.priority or TaskPriority.Normal,
        status=status, done_at=now if status is TaskStatus.Done else None, now=now,
    )
    record = repository.task(conn, task_id)
    if record is None:
        raise RuntimeError("Created task could not be loaded")
    return _task(record)


def update_task(conn: Connection, task_id: TaskID, command: TaskPatch) -> TaskItemResponse:
    values: dict[str, object] = {"updated_at": now_ms()}
    fields = command.model_fields_set
    if "category" in fields and command.category is not None:
        values["category"] = _category(command.category)
    if "title" in fields and command.title is not None:
        values["title"] = command.title
    if "notes" in fields and command.notes is not None:
        values["notes"] = command.notes
    if "priority" in fields and command.priority is not None:
        values["priority"] = command.priority.value
    if not repository.update_task(conn, task_id, values):
        raise not_found("Task not found")
    record = repository.task(conn, task_id)
    if record is None:
        raise not_found("Task not found")
    return _task(record)


def set_task_status(conn: Connection, task_id: TaskID, status: TaskStatus) -> TaskItemResponse:
    now = now_ms()
    if not repository.update_task(conn, task_id, {"status": status.value, "done_at": now if status is TaskStatus.Done else None, "updated_at": now}):
        raise not_found("Task not found")
    record = repository.task(conn, task_id)
    if record is None:
        raise not_found("Task not found")
    return _task(record)


def delete_task(conn: Connection, task_id: TaskID) -> None:
    if not repository.delete_task(conn, task_id):
        raise not_found("Task not found")


def _comment(record: CommentRecord, author: AuthorRecord | None) -> TaskCommentResponse:
    return TaskCommentResponse(
        id=record.id, task_id=record.task_id, parent_comment_id=record.parent_comment_id,
        author=_author(author) if author is not None else None, body=record.body,
        is_deleted=record.deleted_at is not None, created_at=record.created_at,
        updated_at=record.updated_at,
    )


def list_comments(conn: Connection, task_id: TaskID) -> list[TaskCommentResponse]:
    if not repository.task_exists(conn, task_id):
        raise not_found("Task not found")
    records = repository.comments(conn, task_id)
    people = repository.authors(conn, (record.author_id for record in records))
    return [_comment(record, people.get(record.author_id)) for record in records]


def create_comment(
    conn: Connection, actor_id: UserID, task_id: TaskID, body: str,
    parent_comment_id: TaskCommentID | None,
) -> TaskCommentResponse:
    if not repository.task_exists(conn, task_id):
        raise not_found("Task not found")
    if parent_comment_id is not None and not repository.valid_parent(conn, task_id, parent_comment_id):
        raise not_found("Parent comment not found")
    comment_id = repository.insert_comment(
        conn, task_id=task_id, author_id=actor_id, parent_id=parent_comment_id,
        body=body, now=now_ms(),
    )
    record = repository.comment(conn, comment_id)
    if record is None:
        raise RuntimeError("Created task comment could not be loaded")
    author = repository.authors(conn, (record.author_id,)).get(record.author_id)
    return _comment(record, author)


def update_comment(conn: Connection, comment_id: TaskCommentID, body: str) -> TaskCommentResponse:
    if not repository.update_comment(conn, comment_id, body=body, now=now_ms()):
        raise not_found("Comment not found")
    record = repository.comment(conn, comment_id)
    if record is None:
        raise not_found("Comment not found")
    author = repository.authors(conn, (record.author_id,)).get(record.author_id)
    return _comment(record, author)


def delete_comment(conn: Connection, comment_id: TaskCommentID) -> None:
    if not repository.soft_delete_comment(conn, comment_id, now=now_ms()):
        raise not_found("Comment not found")
