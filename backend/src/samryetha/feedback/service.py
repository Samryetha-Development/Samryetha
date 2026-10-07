"""Typed Feedback domain service."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import cast

from sqlalchemy import delete, update
from sqlalchemy.engine import Connection

from . import repository
from ..db import now_ms
from ..errors import not_found
from .models import (
    AgentKeyResponse, AgentRole, AuthorRef, CommentAuthz, CommentResponse,
    FeedbackBody, FeedbackItemResponse, FeedbackListResponse, FeedbackPatch,
    FeedbackStatus, FeedbackUrgency, ItemAuthz, MemberInput, MemberResponse,
    MyProjectResponse, ProjectAdminResponse, ProjectAuthz, ProjectPatch,
)
from .repository import AuthorRecord, CommentRecord, ItemRecord, MemberRecord, ProjectRecord
from ..ids import FeedbackAPIKeyID, FeedbackCommentID, FeedbackItemID, FeedbackProjectID, UserID
from ..schema import feedback_api_keys, feedback_comments, feedback_items, feedback_projects
from ..users import make_handle

AGENT_KEY_PREFIX = "fb-agent:"
_KEY_PREFIX_SHOW = "fb_"


def _inserted_id(value: object, entity: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{entity} insert did not return an integer primary key")
    return value


def _author(record: AuthorRecord) -> AuthorRef:
    return AuthorRef(id=record.id, username=record.username, handle=make_handle(record.username, record.discriminator), display_name=record.display_name)


def _member(record: MemberRecord) -> MemberResponse:
    return MemberResponse(user_id=record.user_id, username=record.username, handle=make_handle(record.username, record.discriminator), display_name=record.display_name, is_programmer=record.is_programmer, joined_at=record.joined_at)


def _item(record: ItemRecord, author: AuthorRecord | None) -> FeedbackItemResponse:
    return FeedbackItemResponse(id=record.id, seq=record.seq, project_id=record.project_id, author=_author(author) if author else None, title=record.title, detail=record.detail, type=record.type, urgency=record.urgency, status=record.status, closed_at=record.closed_at, edited_at=record.edited_at, created_at=record.created_at, updated_at=record.updated_at)


def _comment(record: CommentRecord, author: AuthorRecord | None) -> CommentResponse:
    return CommentResponse(id=record.id, item_id=record.item_id, parent_comment_id=record.parent_comment_id, author=_author(author) if author else None, body=record.body, is_deleted=record.deleted_at is not None, created_at=record.created_at, updated_at=record.updated_at)


def members_of(conn: Connection, project_id: int) -> list[MemberResponse]:
    return [_member(record) for record in repository.members(conn, FeedbackProjectID(project_id))]


def _project_admin(conn: Connection, project: ProjectRecord) -> ProjectAdminResponse:
    return ProjectAdminResponse(id=project.id, name=project.name, description=project.description, members=members_of(conn, project.id), created_at=project.created_at)


def get_project_for_authz(conn: Connection, project_id: int) -> ProjectAuthz | None:
    record = repository.project(conn, FeedbackProjectID(project_id))
    return {"id": record.id, "projectId": record.id} if record else None


def list_my_projects(conn: Connection, viewer_id: int, is_admin: bool) -> list[MyProjectResponse]:
    result: list[MyProjectResponse] = []
    for project in repository.projects(conn):
        members = repository.members(conn, project.id)
        mine = next((member for member in members if member.user_id == viewer_id), None)
        if mine is None and not is_admin:
            continue
        result.append(MyProjectResponse(id=project.id, name=project.name, description=project.description, member_count=len(members), is_programmer=is_admin or bool(mine and mine.is_programmer), created_at=project.created_at))
    return result


def list_projects_for_admin(conn: Connection) -> list[ProjectAdminResponse]:
    return [_project_admin(conn, project) for project in repository.projects(conn)]


def create_project(conn: Connection, actor_id: int, name: str, description: str | None) -> ProjectAdminResponse:
    timestamp = now_ms()
    result = conn.execute(feedback_projects.insert().values(name=name, description=description or "", created_by_user_id=actor_id, created_at=timestamp, updated_at=timestamp))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("project insert did not return a primary key")
    project = repository.project(conn, FeedbackProjectID(_inserted_id(primary_key[0], "project")))
    if project is None:
        raise not_found("Project not found")
    return _project_admin(conn, project)


def update_project(conn: Connection, project_id: int, patch: ProjectPatch) -> None:
    typed_id = FeedbackProjectID(project_id)
    if repository.project(conn, typed_id) is None:
        raise not_found("Project not found")
    values: dict[str, object] = {"updated_at": now_ms()}
    if "name" in patch.model_fields_set:
        values["name"] = patch.name
    if "description" in patch.model_fields_set:
        values["description"] = patch.description or ""
    conn.execute(update(feedback_projects).where(feedback_projects.c.id == typed_id).values(**values))


def delete_project(conn: Connection, actor_id: int, project_id: int) -> None:
    typed_id = FeedbackProjectID(project_id)
    if repository.project(conn, typed_id) is None:
        raise not_found("Project not found")
    timestamp = now_ms()
    conn.execute(update(feedback_projects).where(feedback_projects.c.id == typed_id).values(deleted_at=timestamp, deleted_by=actor_id, updated_at=timestamp))
    conn.execute(update(feedback_items).where(feedback_items.c.project_id == typed_id).values(deleted_at=timestamp, deleted_by=actor_id, updated_at=timestamp))


def set_project_members(conn: Connection, project_id: int, members: list[MemberInput]) -> None:
    typed_id = FeedbackProjectID(project_id)
    if repository.project(conn, typed_id) is None:
        raise not_found("Project not found")
    repository.replace_members(conn, typed_id, [(UserID(member.user_id), member.is_programmer) for member in members])


def _items(conn: Connection, project_id: FeedbackProjectID | None = None) -> list[FeedbackItemResponse]:
    records = repository.items(conn, project_id)
    author_map = repository.authors(conn, {record.author_id for record in records})
    return [_item(record, author_map.get(record.author_id)) for record in records]


def item_by_id(conn: Connection, item_id: int) -> FeedbackItemResponse | None:
    record = repository.item(conn, FeedbackItemID(item_id))
    if record is None:
        return None
    return _item(record, repository.authors(conn, [record.author_id]).get(record.author_id))


def get_item_for_authz(conn: Connection, item_id: int) -> ItemAuthz | None:
    record = repository.item(conn, FeedbackItemID(item_id), include_deleted=True)
    return None if record is None else {"id": record.id, "projectId": record.project_id, "authorId": record.author_id, "deletedAt": record.deleted_at}


def get_comment_for_authz(conn: Connection, comment_id: int) -> CommentAuthz | None:
    record = repository.comment(conn, FeedbackCommentID(comment_id), include_deleted=True)
    if record is None:
        return None
    item = repository.item(conn, record.item_id, include_deleted=True)
    return {"id": record.id, "itemId": record.item_id, "projectId": item.project_id if item else None, "authorId": record.author_id, "deletedAt": record.deleted_at}


def list_feedback(conn: Connection, viewer_id: int, is_admin: bool, project_id: int) -> FeedbackListResponse:
    typed_id = FeedbackProjectID(project_id)
    member = next((value for value in repository.members(conn, typed_id) if value.user_id == viewer_id), None)
    return FeedbackListResponse(items=_items(conn, typed_id), can_manage=is_admin or bool(member and member.is_programmer))


def create_feedback(conn: Connection, actor_id: int, body: FeedbackBody) -> FeedbackItemResponse:
    timestamp = now_ms()
    project_id = FeedbackProjectID(body.project_id)
    result = conn.execute(feedback_items.insert().values(project_id=project_id, author_id=actor_id, seq=repository.next_seq(conn, project_id), title=body.title, detail=body.detail or "", type=body.type.value, urgency=(body.urgency or FeedbackUrgency.Normal).value, created_at=timestamp, updated_at=timestamp))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("feedback insert did not return a primary key")
    created = item_by_id(conn, _inserted_id(primary_key[0], "feedback"))
    if created is None:
        raise not_found("Feedback item not found")
    return created


def update_feedback(conn: Connection, item_id: int, patch: FeedbackPatch) -> FeedbackItemResponse:
    if repository.item(conn, FeedbackItemID(item_id)) is None:
        raise not_found("Feedback item not found")
    timestamp = now_ms()
    values: dict[str, object] = {"updated_at": timestamp}
    if "title" in patch.model_fields_set:
        values["title"] = patch.title
    if "detail" in patch.model_fields_set:
        values["detail"] = patch.detail or ""
    if "type" in patch.model_fields_set and patch.type is not None:
        values["type"] = patch.type.value
    if "urgency" in patch.model_fields_set and patch.urgency is not None:
        values["urgency"] = patch.urgency.value
    if patch.model_fields_set & {"title", "detail", "type", "urgency"}:
        values["edited_at"] = timestamp
    conn.execute(update(feedback_items).where(feedback_items.c.id == item_id).values(**values))
    updated = item_by_id(conn, item_id)
    if updated is None:
        raise not_found("Feedback item not found")
    return updated


def delete_feedback(conn: Connection, actor_id: int, item_id: int) -> None:
    if repository.item(conn, FeedbackItemID(item_id)) is None:
        raise not_found("Feedback item not found")
    timestamp = now_ms()
    conn.execute(update(feedback_items).where(feedback_items.c.id == item_id).values(deleted_at=timestamp, deleted_by=actor_id, updated_at=timestamp))


def set_feedback_status(conn: Connection, item_id: int, status: FeedbackStatus) -> FeedbackItemResponse:
    if repository.item(conn, FeedbackItemID(item_id)) is None:
        raise not_found("Feedback item not found")
    timestamp = now_ms()
    conn.execute(update(feedback_items).where(feedback_items.c.id == item_id).values(status=status.value, closed_at=None if status is FeedbackStatus.Open else timestamp, updated_at=timestamp))
    updated = item_by_id(conn, item_id)
    if updated is None:
        raise not_found("Feedback item not found")
    return updated


def list_feedback_for_agent(conn: Connection, project_id: int | None = None) -> list[FeedbackItemResponse]:
    return _items(conn, FeedbackProjectID(project_id) if project_id is not None else None)


def _comment_with_author(conn: Connection, comment_id: FeedbackCommentID) -> CommentResponse:
    record = repository.comment(conn, comment_id, include_deleted=True)
    if record is None:
        raise not_found("Comment not found")
    return _comment(record, repository.authors(conn, [record.author_id]).get(record.author_id))


def list_comments(conn: Connection, item_id: int) -> list[CommentResponse]:
    typed_id = FeedbackItemID(item_id)
    if repository.item(conn, typed_id) is None:
        raise not_found("Feedback item not found")
    records = repository.comments(conn, typed_id)
    author_map = repository.authors(conn, {record.author_id for record in records})
    return [_comment(record, author_map.get(record.author_id)) for record in records]


def create_comment(conn: Connection, actor_id: int, item_id: int, body: str, parent_comment_id: int | None) -> CommentResponse:
    typed_item_id = FeedbackItemID(item_id)
    if repository.item(conn, typed_item_id) is None:
        raise not_found("Feedback item not found")
    typed_parent = FeedbackCommentID(parent_comment_id) if parent_comment_id is not None else None
    if typed_parent is not None:
        parent = repository.comment(conn, typed_parent)
        if parent is None or parent.item_id != typed_item_id:
            raise not_found("Parent comment not found")
    timestamp = now_ms()
    result = conn.execute(feedback_comments.insert().values(item_id=typed_item_id, author_id=actor_id, parent_comment_id=typed_parent, body=body, created_at=timestamp, updated_at=timestamp))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("comment insert did not return a primary key")
    return _comment_with_author(conn, FeedbackCommentID(_inserted_id(primary_key[0], "comment")))


def update_comment(conn: Connection, comment_id: int, body: str) -> CommentResponse:
    typed_id = FeedbackCommentID(comment_id)
    if repository.comment(conn, typed_id) is None:
        raise not_found("Comment not found")
    conn.execute(update(feedback_comments).where(feedback_comments.c.id == typed_id).values(body=body, updated_at=now_ms()))
    return _comment_with_author(conn, typed_id)


def delete_comment(conn: Connection, comment_id: int) -> None:
    typed_id = FeedbackCommentID(comment_id)
    if repository.comment(conn, typed_id) is None:
        raise not_found("Comment not found")
    conn.execute(update(feedback_comments).where(feedback_comments.c.id == typed_id).values(deleted_at=now_ms(), updated_at=now_ms()))


def _hash_agent_key(key: str) -> str:
    return hashlib.sha256((AGENT_KEY_PREFIX + key).encode()).hexdigest()


def generate_agent_key() -> str:
    return _KEY_PREFIX_SHOW + secrets.token_hex(24)


def _parse_project_ids(raw: str) -> list[int]:
    try:
        data: object = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [value for value in cast(list[object], data) if isinstance(value, int) and not isinstance(value, bool)]


def _key(record: repository.APIKeyRecord) -> AgentKeyResponse:
    return AgentKeyResponse(id=record.id, name=record.name, prefix=record.key_prefix, role=record.role, project_ids=_parse_project_ids(record.project_ids_json), enabled=record.enabled, last_used_at=record.last_used_at, created_at=record.created_at)


def list_keys(conn: Connection) -> list[AgentKeyResponse]:
    return [_key(record) for record in repository.api_keys(conn)]


def create_key(conn: Connection, name: str, role: AgentRole, project_ids: list[int]) -> tuple[str, AgentKeyResponse]:
    raw_key = generate_agent_key()
    result = conn.execute(feedback_api_keys.insert().values(name=name, key_hash=_hash_agent_key(raw_key), key_prefix=raw_key[:8], role=role.value, project_ids=json.dumps(project_ids), enabled=1, created_at=now_ms()))
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("API key insert did not return a primary key")
    record = repository.api_key(conn, FeedbackAPIKeyID(_inserted_id(primary_key[0], "API key")))
    if record is None:
        raise not_found("API key not found")
    return raw_key, _key(record)


def set_key_enabled(conn: Connection, key_id: int, enabled: bool) -> None:
    typed_id = FeedbackAPIKeyID(key_id)
    if repository.api_key(conn, typed_id) is None:
        raise not_found("API key not found")
    conn.execute(update(feedback_api_keys).where(feedback_api_keys.c.id == typed_id).values(enabled=1 if enabled else 0))


def delete_key(conn: Connection, key_id: int) -> None:
    typed_id = FeedbackAPIKeyID(key_id)
    if repository.api_key(conn, typed_id) is None:
        raise not_found("API key not found")
    conn.execute(delete(feedback_api_keys).where(feedback_api_keys.c.id == typed_id))


def verify_key(conn: Connection, raw_key: str | None) -> AgentKeyResponse | None:
    if not raw_key:
        return None
    record = repository.api_key_by_hash(conn, _hash_agent_key(raw_key))
    if record is None:
        return None
    conn.execute(update(feedback_api_keys).where(feedback_api_keys.c.id == record.id).values(last_used_at=now_ms()))
    return _key(record)


def agent_can_access_project(agent: AgentKeyResponse, project_id: int) -> bool:
    return not agent.project_ids or project_id in agent.project_ids
