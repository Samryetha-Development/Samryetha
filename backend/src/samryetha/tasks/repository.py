"""Typed SQLAlchemy persistence boundary for tasks and task comments."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import TaskCommentID, TaskID, UserID
from ..core.schema import task_comments, tasks, users
from .models import TaskPriority, TaskStatus
from ..core.records import opt_int, require_int, require_str


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
    tasks.c.id,
    tasks.c.category,
    tasks.c.title,
    tasks.c.notes,
    tasks.c.priority,
    tasks.c.status,
    tasks.c.done_at,
    tasks.c.created_at,
    tasks.c.updated_at,
    users.c.id.label("author_user_id"),
    users.c.username.label("author_username"),
    users.c.discriminator.label("author_discriminator"),
    users.c.display_name.label("author_display_name"),
).join(users, users.c.id == tasks.c.author_id)


def _author(row: RowMapping) -> AuthorRecord:
    return AuthorRecord(
        UserID(require_int(row["author_user_id"], "author id")),
        require_str(row["author_username"], "author username"),
        opt_int(row["author_discriminator"], "author discriminator"),
        require_str(row["author_display_name"], "author display_name"),
    )


def _task(row: RowMapping) -> TaskRecord:
    return TaskRecord(
        TaskID(require_int(row["id"], "task id")),
        _author(row),
        require_str(row["category"], "category"),
        require_str(row["title"], "title"),
        require_str(row["notes"], "notes"),
        TaskPriority(require_str(row["priority"], "priority")),
        TaskStatus(require_str(row["status"], "status")),
        opt_int(row["done_at"], "done_at"),
        require_int(row["created_at"], "created_at"),
        require_int(row["updated_at"], "updated_at"),
    )


def _comment(row: RowMapping) -> CommentRecord:
    parent = opt_int(row["parent_comment_id"], "parent_comment_id")
    return CommentRecord(
        TaskCommentID(require_int(row["id"], "comment id")),
        TaskID(require_int(row["task_id"], "task_id")),
        UserID(require_int(row["author_id"], "author_id")),
        TaskCommentID(parent) if parent is not None else None,
        require_str(row["body"], "body"),
        opt_int(row["deleted_at"], "deleted_at"),
        require_int(row["created_at"], "created_at"),
        require_int(row["updated_at"], "updated_at"),
    )


def inserted_id(value: object, entity: str) -> int:
    return require_int(value, f"inserted {entity} id")


class TaskRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def task(self, task_id: TaskID) -> TaskRecord | None:
        row = self._conn.execute(_task_select.where(tasks.c.id == task_id)).mappings().first()
        return _task(row) if row is not None else None

    def all_tasks(self) -> list[TaskRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(_task_select.order_by(tasks.c.created_at.desc(), tasks.c.id.desc())).mappings().all()
        )
        return [_task(row) for row in rows]

    def insert_task(
        self,
        *,
        author_id: UserID,
        category: str,
        title: str,
        notes: str,
        priority: TaskPriority,
        status: TaskStatus,
        done_at: int | None,
        now: int,
    ) -> TaskID:
        result = self._conn.execute(
            tasks.insert().values(
                author_id=author_id,
                category=category,
                title=title,
                notes=notes,
                priority=priority.value,
                status=status.value,
                done_at=done_at,
                created_at=now,
                updated_at=now,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("task insert did not return a primary key")
        return TaskID(inserted_id(primary_key[0], "task"))

    def update_task(self, task_id: TaskID, values: dict[str, object]) -> bool:
        return self._conn.execute(update(tasks).where(tasks.c.id == task_id).values(**values)).rowcount == 1

    def delete_task(self, task_id: TaskID) -> bool:
        deleted = self._conn.execute(delete(tasks).where(tasks.c.id == task_id)).rowcount == 1
        if deleted:
            self._conn.execute(delete(task_comments).where(task_comments.c.task_id == task_id))
        return deleted

    def task_exists(self, task_id: TaskID) -> bool:
        return self._conn.execute(select(tasks.c.id).where(tasks.c.id == task_id)).first() is not None

    def comment(self, comment_id: TaskCommentID) -> CommentRecord | None:
        row = self._conn.execute(select(task_comments).where(task_comments.c.id == comment_id)).mappings().first()
        return _comment(row) if row is not None else None

    def comments(self, task_id: TaskID) -> list[CommentRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(task_comments)
                .where(and_(task_comments.c.task_id == task_id, task_comments.c.deleted_at.is_(None)))
                .order_by(task_comments.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_comment(row) for row in rows]

    def authors(self, ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
        values = tuple(set(ids))
        if not values:
            return {}
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(
                    users.c.id.label("author_user_id"),
                    users.c.username.label("author_username"),
                    users.c.discriminator.label("author_discriminator"),
                    users.c.display_name.label("author_display_name"),
                ).where(users.c.id.in_(values))
            )
            .mappings()
            .all()
        )
        records = (_author(row) for row in rows)
        return {record.id: record for record in records}

    def valid_parent(self, task_id: TaskID, parent_id: TaskCommentID) -> bool:
        return (
            self._conn.execute(
                select(task_comments.c.id).where(
                    task_comments.c.id == parent_id,
                    task_comments.c.task_id == task_id,
                    task_comments.c.deleted_at.is_(None),
                )
            ).first()
            is not None
        )

    def insert_comment(
        self, *, task_id: TaskID, author_id: UserID, parent_id: TaskCommentID | None, body: str, now: int
    ) -> TaskCommentID:
        result = self._conn.execute(
            task_comments.insert().values(
                task_id=task_id,
                author_id=author_id,
                parent_comment_id=parent_id,
                body=body,
                created_at=now,
                updated_at=now,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("task comment insert did not return a primary key")
        return TaskCommentID(inserted_id(primary_key[0], "task comment"))

    def update_comment(self, comment_id: TaskCommentID, *, body: str, now: int) -> bool:
        return (
            self._conn.execute(
                update(task_comments)
                .where(task_comments.c.id == comment_id, task_comments.c.deleted_at.is_(None))
                .values(body=body, updated_at=now)
            ).rowcount
            == 1
        )

    def soft_delete_comment(self, comment_id: TaskCommentID, *, now: int) -> bool:
        return (
            self._conn.execute(
                update(task_comments)
                .where(task_comments.c.id == comment_id, task_comments.c.deleted_at.is_(None))
                .values(deleted_at=now, updated_at=now)
            ).rowcount
            == 1
        )
