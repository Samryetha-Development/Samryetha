"""Typed SQLAlchemy persistence boundary for Feedback."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.db import now_ms
from ..core.db import Database
from .models import AgentRole, FeedbackStatus, FeedbackType, FeedbackUrgency
from ..core.ids import FeedbackAPIKeyID, FeedbackCommentID, FeedbackItemID, FeedbackProjectID, UserID
from ..core.schema import (
    app_settings,
    feedback_api_keys,
    feedback_comments,
    feedback_items,
    feedback_project_members,
    feedback_projects,
    users,
)
from ..core.records import opt_int, require_int, require_str


def inserted_id(value: object, entity: str) -> int:
    return require_int(value, f"inserted {entity} id")


def _result_id(primary_key: Sequence[object] | None, entity: str) -> int:
    if primary_key is None:
        raise RuntimeError(f"{entity} insert did not return a primary key")
    return inserted_id(primary_key[0], entity)


@dataclass(frozen=True, slots=True)
class AuthorRecord:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: FeedbackProjectID
    name: str
    description: str
    created_by_user_id: UserID | None
    deleted_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class MemberRecord:
    project_id: FeedbackProjectID
    user_id: UserID
    username: str
    discriminator: int | None
    display_name: str
    is_programmer: bool
    joined_at: int


@dataclass(frozen=True, slots=True)
class ItemRecord:
    id: FeedbackItemID
    project_id: FeedbackProjectID
    author_id: UserID
    seq: int
    title: str
    detail: str
    type: FeedbackType
    urgency: FeedbackUrgency
    status: FeedbackStatus
    closed_at: int | None
    edited_at: int | None
    deleted_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class CommentRecord:
    id: FeedbackCommentID
    item_id: FeedbackItemID
    author_id: UserID
    parent_comment_id: FeedbackCommentID | None
    body: str
    deleted_at: int | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class APIKeyRecord:
    id: FeedbackAPIKeyID
    name: str
    key_hash: str
    key_prefix: str
    role: AgentRole
    project_ids_json: str
    enabled: bool
    last_used_at: int | None
    created_at: int


def _author(row: RowMapping) -> AuthorRecord:
    return AuthorRecord(
        UserID(require_int(row["id"], "user id")),
        require_str(row["username"], "username"),
        opt_int(row["discriminator"], "discriminator"),
        require_str(row["display_name"], "display_name"),
    )


def _project(row: RowMapping) -> ProjectRecord:
    creator = opt_int(row["created_by_user_id"], "created_by_user_id")
    return ProjectRecord(
        FeedbackProjectID(require_int(row["id"], "project id")),
        require_str(row["name"], "name"),
        require_str(row["description"], "description"),
        UserID(creator) if creator is not None else None,
        opt_int(row["deleted_at"], "deleted_at"),
        require_int(row["created_at"], "created_at"),
        require_int(row["updated_at"], "updated_at"),
    )


def _member(row: RowMapping) -> MemberRecord:
    return MemberRecord(
        FeedbackProjectID(require_int(row["project_id"], "project_id")),
        UserID(require_int(row["user_id"], "user_id")),
        require_str(row["username"], "username"),
        opt_int(row["discriminator"], "discriminator"),
        require_str(row["display_name"], "display_name"),
        require_int(row["is_programmer"], "is_programmer") == 1,
        require_int(row["joined_at"], "joined_at"),
    )


def _item(row: RowMapping) -> ItemRecord:
    return ItemRecord(
        FeedbackItemID(require_int(row["id"], "item id")),
        FeedbackProjectID(require_int(row["project_id"], "project_id")),
        UserID(require_int(row["author_id"], "author_id")),
        require_int(row["seq"], "seq"),
        require_str(row["title"], "title"),
        require_str(row["detail"], "detail"),
        FeedbackType(require_str(row["type"], "type")),
        FeedbackUrgency(require_str(row["urgency"], "urgency")),
        FeedbackStatus(require_str(row["status"], "status")),
        opt_int(row["closed_at"], "closed_at"),
        opt_int(row["edited_at"], "edited_at"),
        opt_int(row["deleted_at"], "deleted_at"),
        require_int(row["created_at"], "created_at"),
        require_int(row["updated_at"], "updated_at"),
    )


def _comment(row: RowMapping) -> CommentRecord:
    parent = opt_int(row["parent_comment_id"], "parent_comment_id")
    return CommentRecord(
        FeedbackCommentID(require_int(row["id"], "comment id")),
        FeedbackItemID(require_int(row["item_id"], "item_id")),
        UserID(require_int(row["author_id"], "author_id")),
        FeedbackCommentID(parent) if parent is not None else None,
        require_str(row["body"], "body"),
        opt_int(row["deleted_at"], "deleted_at"),
        require_int(row["created_at"], "created_at"),
        require_int(row["updated_at"], "updated_at"),
    )


def _api_key(row: RowMapping) -> APIKeyRecord:
    return APIKeyRecord(
        FeedbackAPIKeyID(require_int(row["id"], "key id")),
        require_str(row["name"], "name"),
        require_str(row["key_hash"], "key_hash"),
        require_str(row["key_prefix"], "key_prefix"),
        AgentRole(require_str(row["role"], "role")),
        require_str(row["project_ids"], "project_ids"),
        require_int(row["enabled"], "enabled") == 1,
        opt_int(row["last_used_at"], "last_used_at"),
        require_int(row["created_at"], "created_at"),
    )


class DatabaseSnapshotRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, target: str) -> None:
        """SQLite VACUUM requires a dedicated autocommit connection, not a request transaction."""
        escaped = target.replace("'", "''")
        conn = self._db.engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            conn.exec_driver_sql(f"VACUUM INTO '{escaped}'")
        finally:
            conn.close()


class FeedbackRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def project(self, project_id: FeedbackProjectID, *, include_deleted: bool = False) -> ProjectRecord | None:
        stmt = select(feedback_projects).where(feedback_projects.c.id == project_id)
        if not include_deleted:
            stmt = stmt.where(feedback_projects.c.deleted_at.is_(None))
        row = self._conn.execute(stmt).mappings().first()
        return _project(row) if row is not None else None

    def projects(self) -> list[ProjectRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(feedback_projects)
                .where(feedback_projects.c.deleted_at.is_(None))
                .order_by(feedback_projects.c.name)
            )
            .mappings()
            .all()
        )
        return [_project(row) for row in rows]

    def members(self, project_id: FeedbackProjectID) -> list[MemberRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(
                    feedback_project_members.c.project_id,
                    feedback_project_members.c.user_id,
                    users.c.username,
                    users.c.discriminator,
                    users.c.display_name,
                    feedback_project_members.c.is_programmer,
                    feedback_project_members.c.joined_at,
                )
                .select_from(feedback_project_members)
                .join(users, users.c.id == feedback_project_members.c.user_id)
                .where(feedback_project_members.c.project_id == project_id)
            )
            .mappings()
            .all()
        )
        return [_member(row) for row in rows]

    def authors(self, ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
        values = tuple(ids)
        if not values:
            return {}
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(users.c.id, users.c.username, users.c.discriminator, users.c.display_name).where(
                    users.c.id.in_(values)
                )
            )
            .mappings()
            .all()
        )
        records = (_author(row) for row in rows)
        return {record.id: record for record in records}

    def item(self, item_id: FeedbackItemID, *, include_deleted: bool = False) -> ItemRecord | None:
        stmt = select(feedback_items).where(feedback_items.c.id == item_id)
        if not include_deleted:
            stmt = stmt.where(feedback_items.c.deleted_at.is_(None))
        row = self._conn.execute(stmt).mappings().first()
        return _item(row) if row is not None else None

    def items(self, project_id: FeedbackProjectID | None = None) -> list[ItemRecord]:
        stmt = select(feedback_items).where(feedback_items.c.deleted_at.is_(None))
        if project_id is not None:
            stmt = stmt.where(feedback_items.c.project_id == project_id)
        rows: Sequence[RowMapping] = self._conn.execute(stmt.order_by(feedback_items.c.created_at)).mappings().all()
        return [_item(row) for row in rows]

    def comment(self, comment_id: FeedbackCommentID, *, include_deleted: bool = False) -> CommentRecord | None:
        stmt = select(feedback_comments).where(feedback_comments.c.id == comment_id)
        if not include_deleted:
            stmt = stmt.where(feedback_comments.c.deleted_at.is_(None))
        row = self._conn.execute(stmt).mappings().first()
        return _comment(row) if row is not None else None

    def comments(self, item_id: FeedbackItemID) -> list[CommentRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(feedback_comments)
                .where(feedback_comments.c.item_id == item_id, feedback_comments.c.deleted_at.is_(None))
                .order_by(feedback_comments.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_comment(row) for row in rows]

    def api_key(self, key_id: FeedbackAPIKeyID) -> APIKeyRecord | None:
        row = self._conn.execute(select(feedback_api_keys).where(feedback_api_keys.c.id == key_id)).mappings().first()
        return _api_key(row) if row is not None else None

    def api_keys(self) -> list[APIKeyRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(select(feedback_api_keys).order_by(feedback_api_keys.c.created_at)).mappings().all()
        )
        return [_api_key(row) for row in rows]

    def api_key_by_hash(self, key_hash: str) -> APIKeyRecord | None:
        row = (
            self._conn.execute(
                select(feedback_api_keys).where(
                    feedback_api_keys.c.key_hash == key_hash, feedback_api_keys.c.enabled == 1
                )
            )
            .mappings()
            .first()
        )
        return _api_key(row) if row is not None else None

    def next_seq(self, project_id: FeedbackProjectID) -> int:
        value = self._conn.execute(
            select(func.max(feedback_items.c.seq)).where(feedback_items.c.project_id == project_id)
        ).scalar()
        return (require_int(value, "max seq") if value is not None else 0) + 1

    def replace_members(self, project_id: FeedbackProjectID, values: Sequence[tuple[UserID, bool]]) -> None:
        self._conn.execute(delete(feedback_project_members).where(feedback_project_members.c.project_id == project_id))
        if values:
            joined_at = now_ms()
            self._conn.execute(
                feedback_project_members.insert().values(
                    [
                        {
                            "project_id": project_id,
                            "user_id": user_id,
                            "is_programmer": 1 if programmer else 0,
                            "joined_at": joined_at,
                        }
                        for user_id, programmer in values
                    ]
                )
            )

    def insert_project(self, *, actor_id: UserID, name: str, description: str, now: int) -> FeedbackProjectID:
        result = self._conn.execute(
            feedback_projects.insert().values(
                name=name, description=description, created_by_user_id=actor_id, created_at=now, updated_at=now
            )
        )
        return FeedbackProjectID(_result_id(result.inserted_primary_key, "project"))

    def update_project(self, project_id: FeedbackProjectID, values: dict[str, object]) -> None:
        self._conn.execute(update(feedback_projects).where(feedback_projects.c.id == project_id).values(**values))

    def soft_delete_project(self, project_id: FeedbackProjectID, *, actor_id: UserID, now: int) -> None:
        self._conn.execute(
            update(feedback_projects)
            .where(feedback_projects.c.id == project_id)
            .values(deleted_at=now, deleted_by=actor_id, updated_at=now)
        )
        self._conn.execute(
            update(feedback_items)
            .where(feedback_items.c.project_id == project_id)
            .values(deleted_at=now, deleted_by=actor_id, updated_at=now)
        )

    def insert_item(
        self,
        *,
        project_id: FeedbackProjectID,
        author_id: UserID,
        seq: int,
        title: str,
        detail: str,
        type_value: str,
        urgency: FeedbackUrgency,
        now: int,
    ) -> FeedbackItemID:
        result = self._conn.execute(
            feedback_items.insert().values(
                project_id=project_id,
                author_id=author_id,
                seq=seq,
                title=title,
                detail=detail,
                type=type_value,
                urgency=urgency.value,
                created_at=now,
                updated_at=now,
            )
        )
        return FeedbackItemID(_result_id(result.inserted_primary_key, "feedback"))

    def update_item(self, item_id: FeedbackItemID, values: dict[str, object]) -> None:
        self._conn.execute(update(feedback_items).where(feedback_items.c.id == item_id).values(**values))

    def insert_comment(
        self,
        *,
        item_id: FeedbackItemID,
        author_id: UserID,
        parent_comment_id: FeedbackCommentID | None,
        body: str,
        now: int,
    ) -> FeedbackCommentID:
        result = self._conn.execute(
            feedback_comments.insert().values(
                item_id=item_id,
                author_id=author_id,
                parent_comment_id=parent_comment_id,
                body=body,
                created_at=now,
                updated_at=now,
            )
        )
        return FeedbackCommentID(_result_id(result.inserted_primary_key, "comment"))

    def update_comment(self, comment_id: FeedbackCommentID, values: dict[str, object]) -> None:
        self._conn.execute(update(feedback_comments).where(feedback_comments.c.id == comment_id).values(**values))

    def insert_api_key(
        self, *, name: str, key_hash: str, key_prefix: str, role: AgentRole, project_ids_json: str, created_at: int
    ) -> FeedbackAPIKeyID:
        result = self._conn.execute(
            feedback_api_keys.insert().values(
                name=name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                role=role.value,
                project_ids=project_ids_json,
                enabled=1,
                created_at=created_at,
            )
        )
        return FeedbackAPIKeyID(_result_id(result.inserted_primary_key, "API key"))

    def set_api_key_enabled(self, key_id: FeedbackAPIKeyID, enabled: bool) -> None:
        self._conn.execute(
            update(feedback_api_keys).where(feedback_api_keys.c.id == key_id).values(enabled=1 if enabled else 0)
        )

    def delete_api_key(self, key_id: FeedbackAPIKeyID) -> None:
        self._conn.execute(delete(feedback_api_keys).where(feedback_api_keys.c.id == key_id))

    def touch_api_key(self, key_id: FeedbackAPIKeyID, *, last_used_at: int) -> None:
        self._conn.execute(
            update(feedback_api_keys).where(feedback_api_keys.c.id == key_id).values(last_used_at=last_used_at)
        )

    def setting(self, key: str) -> str | None:
        value = self._conn.execute(select(app_settings.c.value).where(app_settings.c.key == key)).scalar_one_or_none()
        return require_str(value, "setting value") if value is not None else None

    def set_setting(self, key: str, value: str) -> None:
        if self._conn.execute(select(app_settings.c.key).where(app_settings.c.key == key)).first() is None:
            self._conn.execute(app_settings.insert().values(key=key, value=value))
        else:
            self._conn.execute(update(app_settings).where(app_settings.c.key == key).values(value=value))
