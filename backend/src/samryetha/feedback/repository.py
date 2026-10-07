"""Typed SQLAlchemy persistence boundary for Feedback."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Connection, RowMapping

from ..core.db import now_ms
from .models import AgentRole, FeedbackStatus, FeedbackType, FeedbackUrgency
from ..core.ids import FeedbackAPIKeyID, FeedbackCommentID, FeedbackItemID, FeedbackProjectID, UserID
from ..core.schema import feedback_api_keys, feedback_comments, feedback_items, feedback_project_members, feedback_projects, users


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


def inserted_id(value: object, entity: str) -> int:
    return _int(value, f"inserted {entity} id")


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
    return AuthorRecord(UserID(_int(row["id"], "user id")), _str(row["username"], "username"), _opt_int(row["discriminator"], "discriminator"), _str(row["display_name"], "display_name"))


def _project(row: RowMapping) -> ProjectRecord:
    creator = _opt_int(row["created_by_user_id"], "created_by_user_id")
    return ProjectRecord(FeedbackProjectID(_int(row["id"], "project id")), _str(row["name"], "name"), _str(row["description"], "description"), UserID(creator) if creator is not None else None, _opt_int(row["deleted_at"], "deleted_at"), _int(row["created_at"], "created_at"), _int(row["updated_at"], "updated_at"))


def _member(row: RowMapping) -> MemberRecord:
    return MemberRecord(FeedbackProjectID(_int(row["project_id"], "project_id")), UserID(_int(row["user_id"], "user_id")), _str(row["username"], "username"), _opt_int(row["discriminator"], "discriminator"), _str(row["display_name"], "display_name"), _int(row["is_programmer"], "is_programmer") == 1, _int(row["joined_at"], "joined_at"))


def _item(row: RowMapping) -> ItemRecord:
    return ItemRecord(FeedbackItemID(_int(row["id"], "item id")), FeedbackProjectID(_int(row["project_id"], "project_id")), UserID(_int(row["author_id"], "author_id")), _int(row["seq"], "seq"), _str(row["title"], "title"), _str(row["detail"], "detail"), FeedbackType(_str(row["type"], "type")), FeedbackUrgency(_str(row["urgency"], "urgency")), FeedbackStatus(_str(row["status"], "status")), _opt_int(row["closed_at"], "closed_at"), _opt_int(row["edited_at"], "edited_at"), _opt_int(row["deleted_at"], "deleted_at"), _int(row["created_at"], "created_at"), _int(row["updated_at"], "updated_at"))


def _comment(row: RowMapping) -> CommentRecord:
    parent = _opt_int(row["parent_comment_id"], "parent_comment_id")
    return CommentRecord(FeedbackCommentID(_int(row["id"], "comment id")), FeedbackItemID(_int(row["item_id"], "item_id")), UserID(_int(row["author_id"], "author_id")), FeedbackCommentID(parent) if parent is not None else None, _str(row["body"], "body"), _opt_int(row["deleted_at"], "deleted_at"), _int(row["created_at"], "created_at"), _int(row["updated_at"], "updated_at"))


def _api_key(row: RowMapping) -> APIKeyRecord:
    return APIKeyRecord(FeedbackAPIKeyID(_int(row["id"], "key id")), _str(row["name"], "name"), _str(row["key_hash"], "key_hash"), _str(row["key_prefix"], "key_prefix"), AgentRole(_str(row["role"], "role")), _str(row["project_ids"], "project_ids"), _int(row["enabled"], "enabled") == 1, _opt_int(row["last_used_at"], "last_used_at"), _int(row["created_at"], "created_at"))


def project(conn: Connection, project_id: FeedbackProjectID, *, include_deleted: bool = False) -> ProjectRecord | None:
    stmt = select(feedback_projects).where(feedback_projects.c.id == project_id)
    if not include_deleted:
        stmt = stmt.where(feedback_projects.c.deleted_at.is_(None))
    row = conn.execute(stmt).mappings().first()
    return _project(row) if row is not None else None


def projects(conn: Connection) -> list[ProjectRecord]:
    rows: Sequence[RowMapping] = conn.execute(select(feedback_projects).where(feedback_projects.c.deleted_at.is_(None)).order_by(feedback_projects.c.name)).mappings().all()
    return [_project(row) for row in rows]


def members(conn: Connection, project_id: FeedbackProjectID) -> list[MemberRecord]:
    rows: Sequence[RowMapping] = conn.execute(select(feedback_project_members.c.project_id, feedback_project_members.c.user_id, users.c.username, users.c.discriminator, users.c.display_name, feedback_project_members.c.is_programmer, feedback_project_members.c.joined_at).select_from(feedback_project_members).join(users, users.c.id == feedback_project_members.c.user_id).where(feedback_project_members.c.project_id == project_id)).mappings().all()
    return [_member(row) for row in rows]


def authors(conn: Connection, ids: Iterable[UserID]) -> dict[UserID, AuthorRecord]:
    values = tuple(ids)
    if not values:
        return {}
    rows: Sequence[RowMapping] = conn.execute(select(users.c.id, users.c.username, users.c.discriminator, users.c.display_name).where(users.c.id.in_(values))).mappings().all()
    records = (_author(row) for row in rows)
    return {record.id: record for record in records}


def item(conn: Connection, item_id: FeedbackItemID, *, include_deleted: bool = False) -> ItemRecord | None:
    stmt = select(feedback_items).where(feedback_items.c.id == item_id)
    if not include_deleted:
        stmt = stmt.where(feedback_items.c.deleted_at.is_(None))
    row = conn.execute(stmt).mappings().first()
    return _item(row) if row is not None else None


def items(conn: Connection, project_id: FeedbackProjectID | None = None) -> list[ItemRecord]:
    stmt = select(feedback_items).where(feedback_items.c.deleted_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(feedback_items.c.project_id == project_id)
    rows: Sequence[RowMapping] = conn.execute(stmt.order_by(feedback_items.c.created_at)).mappings().all()
    return [_item(row) for row in rows]


def comment(conn: Connection, comment_id: FeedbackCommentID, *, include_deleted: bool = False) -> CommentRecord | None:
    stmt = select(feedback_comments).where(feedback_comments.c.id == comment_id)
    if not include_deleted:
        stmt = stmt.where(feedback_comments.c.deleted_at.is_(None))
    row = conn.execute(stmt).mappings().first()
    return _comment(row) if row is not None else None


def comments(conn: Connection, item_id: FeedbackItemID) -> list[CommentRecord]:
    rows: Sequence[RowMapping] = conn.execute(select(feedback_comments).where(feedback_comments.c.item_id == item_id, feedback_comments.c.deleted_at.is_(None)).order_by(feedback_comments.c.created_at)).mappings().all()
    return [_comment(row) for row in rows]


def api_key(conn: Connection, key_id: FeedbackAPIKeyID) -> APIKeyRecord | None:
    row = conn.execute(select(feedback_api_keys).where(feedback_api_keys.c.id == key_id)).mappings().first()
    return _api_key(row) if row is not None else None


def api_keys(conn: Connection) -> list[APIKeyRecord]:
    rows: Sequence[RowMapping] = conn.execute(select(feedback_api_keys).order_by(feedback_api_keys.c.created_at)).mappings().all()
    return [_api_key(row) for row in rows]


def api_key_by_hash(conn: Connection, key_hash: str) -> APIKeyRecord | None:
    row = conn.execute(select(feedback_api_keys).where(feedback_api_keys.c.key_hash == key_hash, feedback_api_keys.c.enabled == 1)).mappings().first()
    return _api_key(row) if row is not None else None


def next_seq(conn: Connection, project_id: FeedbackProjectID) -> int:
    value = conn.execute(select(func.max(feedback_items.c.seq)).where(feedback_items.c.project_id == project_id)).scalar()
    return (_int(value, "max seq") if value is not None else 0) + 1


def replace_members(conn: Connection, project_id: FeedbackProjectID, values: Sequence[tuple[UserID, bool]]) -> None:
    conn.execute(delete(feedback_project_members).where(feedback_project_members.c.project_id == project_id))
    if values:
        joined_at = now_ms()
        conn.execute(feedback_project_members.insert().values([{"project_id": project_id, "user_id": user_id, "is_programmer": 1 if programmer else 0, "joined_at": joined_at} for user_id, programmer in values]))
