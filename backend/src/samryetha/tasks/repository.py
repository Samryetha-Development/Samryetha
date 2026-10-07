"""Typed SQLAlchemy persistence boundary for tasks and task comments."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..ids import TaskCommentID, TaskID, UserID
from ..schema import task_comments, tasks, users
from .models import TaskPriority, TaskStatus


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _opt_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: TaskID
    author: AuthorRecord
    category: str
    title: str
    notes: str
    priority: TaskPriority
    status: TaskStatus
    done_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class CommentRecord:
    id: TaskCommentID
    task_id: TaskID
    author_id: UserID
    parent_comment_id: TaskCommentID | None
    body: str
    deleted_at: int | None
    created_at: int
    updated_at: int


_task_select = select(
    tasks.c.id, tasks.c.category, tasks.c.title, tasks.c.notes, tasks.c.priority,
    tasks.c.status, tasks.c.done_at, tasks.c.created_at, tasks.c.updated_at,
    users.c.id.label("author_user_id"), users.c.username.label("author_username"),
    users.c.discriminator.label("author_discriminator"), users.c.display_name.label("author_display_name"),
).join(users, users.c.id == tasks.c.author_id)


def _author(row: RowMapping) -> AuthorRecord:
    return AuthorRecord(
        UserID(_int(row["author_user_id"], "author id")),
        _str(row["author_username"], "author username"),
        _opt_int(row["author_discriminator"], "author discriminator"),
        _str(row["author_display_name"], "author display_name"),
    )


def _task(row: RowMapping) -> TaskRecord:
    return TaskRecord(
        TaskID(_int(row["id"], "task id")), _author(row), _str(row["category"], "category"),
        _str(row["title"], "title"), _str(row["notes"], "notes"),
        TaskPriority(_str(row["priority"], "priority")), TaskStatus(_str(row["status"], "status")),
        _opt_int(row["done_at"], "done_at"), _int(row["created_at"], "created_at"),
        _int(row["updated_at"], "updated_at"),
    )


def _comment(row: RowMapping) -> CommentRecord:
    parent = _opt_int(row["parent_comment_id"], "parent_comment_id")
    return CommentRecord(
        TaskCommentID(_int(row["id"], "comment id")), TaskID(_int(row["task_id"], "task_id")),
        UserID(_int(row["author_id"], "author_id")), TaskCommentID(parent) if parent is not None else None,
        _str(row["body"], "body"), _opt_int(row["deleted_at"], "deleted_at"),
        _int(row["created_at"], "created_at"), _int(row["updated_at"], "updated_at"),
    )


def inserted_id(value: object, entity: str) -> int:
    return _int(value, f"inserted {entity} id")


def task(conn: Connection, task_id: TaskID) -> TaskRecord | None:
    row = conn.execute(_task_select.where(tasks.c.id == task_id)).mappings().first()
    return _task(row) if row is not None else None


def all_tasks(conn: Connection) -> list[TaskRecord]:
    rows: Sequence[RowMapping] = conn.execute(_task_select.order_by(tasks.c.created_at.desc(), tasks.c.id.desc())).mappings().all()
    return [_task(row) for row in rows]


def insert_task(
    conn: Connection, *, author_id: UserID, category: str, title: str, notes: str,
    priority: TaskPriority, status: TaskStatus, done_at: int | None, now: int,
) -> TaskID:
    result = conn.execute(tasks.insert().values(author_id=author_id, category=category, title=title, notes=notes, priority=priority.value, status=status.value, done_at=done_at, created_at=now, updated_at=now))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("task insert did not return a primary key")
    return TaskID(inserted_id(primary_key[0], "task"))


def update_task(conn: Connection, task_id: TaskID, values: dict[str, object]) -> bool:
    return conn.execute(update(tasks).where(tasks.c.id == task_id).values(**values)).rowcount == 1


def delete_task(conn: Connection, task_id: TaskID) -> bool:
    deleted = conn.execute(delete(tasks).where(tasks.c.id == task_id)).rowcount == 1
    if deleted:
        conn.execute(delete(task_comments).where(task_comments.c.task_id == task_id))
    return deleted


def task_exists(conn: Connection, task_id: TaskID) -> bool:
    return conn.execute(select(tasks.c.id).where(tasks.c.id == task_id)).first() is not None


def comment(conn: Connection, comment_id: TaskCommentID) -> CommentRecord | None:
    row = conn.execute(select(task_comments).where(task_comments.c.id == comment_id)).mappings().first()
    return _comment(row) if row is not None else None


def comments(conn: Connection, task_id: TaskID) -> list[CommentRecord]:
    rows: Sequence[RowMapping] = conn.execute(select(task_comments).where(and_(task_comments.c.task_id == task_id, task_comments.c.deleted_at.is_(None))).order_by(task_comments.c.created_at)).mappings().all()
    return [_comment(row) for row in rows]


def authors(conn: Connection, ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
    values = tuple(set(ids))
    if not values:
        return {}
    rows: Sequence[RowMapping] = conn.execute(select(users.c.id.label("author_user_id"), users.c.username.label("author_username"), users.c.discriminator.label("author_discriminator"), users.c.display_name.label("author_display_name")).where(users.c.id.in_(values))).mappings().all()
    records = (_author(row) for row in rows)
    return {record.id: record for record in records}


def valid_parent(conn: Connection, task_id: TaskID, parent_id: TaskCommentID) -> bool:
    return conn.execute(select(task_comments.c.id).where(task_comments.c.id == parent_id, task_comments.c.task_id == task_id, task_comments.c.deleted_at.is_(None))).first() is not None


def insert_comment(conn: Connection, *, task_id: TaskID, author_id: UserID, parent_id: TaskCommentID | None, body: str, now: int) -> TaskCommentID:
    result = conn.execute(task_comments.insert().values(task_id=task_id, author_id=author_id, parent_comment_id=parent_id, body=body, created_at=now, updated_at=now))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("task comment insert did not return a primary key")
    return TaskCommentID(inserted_id(primary_key[0], "task comment"))


def update_comment(conn: Connection, comment_id: TaskCommentID, *, body: str, now: int) -> bool:
    return conn.execute(update(task_comments).where(task_comments.c.id == comment_id, task_comments.c.deleted_at.is_(None)).values(body=body, updated_at=now)).rowcount == 1


def soft_delete_comment(conn: Connection, comment_id: TaskCommentID, *, now: int) -> bool:
    return conn.execute(update(task_comments).where(task_comments.c.id == comment_id, task_comments.c.deleted_at.is_(None)).values(deleted_at=now, updated_at=now)).rowcount == 1
