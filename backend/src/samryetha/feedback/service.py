"""Typed Feedback domain service."""

from __future__ import annotations

from samryetha.feedback.repository import FeedbackRepository

import hashlib
import json
import secrets
from typing import cast

from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, AuthorizationService
from . import repository
from ..core.db import now_ms
from ..core.errors import forbidden, not_found
from .models import (
    AgentKeyResponse,
    AgentRole,
    AgentProjectResponse,
    AgentProjectsResponse,
    AgentSummaryResponse,
    AgentTasksResponse,
    AuthorRef,
    CommentAuthz,
    CommentResponse,
    FeedbackBody,
    FeedbackItemResponse,
    FeedbackListResponse,
    FeedbackPatch,
    FeedbackStatus,
    FeedbackUrgency,
    ItemAuthz,
    MemberInput,
    MemberResponse,
    MyProjectResponse,
    ProjectAdminResponse,
    ProjectAuthz,
    ProjectPatch,
)
from .repository import AuthorRecord, CommentRecord, ItemRecord, MemberRecord, ProjectRecord
from ..core.ids import FeedbackAPIKeyID, FeedbackCommentID, FeedbackItemID, FeedbackProjectID, UserID
from ..users import make_handle

AGENT_KEY_PREFIX = "fb-agent:"
_KEY_PREFIX_SHOW = "fb_"


def _author(record: AuthorRecord) -> AuthorRef:
    return AuthorRef(
        id=record.id,
        username=record.username,
        handle=make_handle(record.username, record.discriminator),
        display_name=record.display_name,
    )


def _member(record: MemberRecord) -> MemberResponse:
    return MemberResponse(
        user_id=record.user_id,
        username=record.username,
        handle=make_handle(record.username, record.discriminator),
        display_name=record.display_name,
        is_programmer=record.is_programmer,
        joined_at=record.joined_at,
    )


def _item(record: ItemRecord, author: AuthorRecord | None) -> FeedbackItemResponse:
    return FeedbackItemResponse(
        id=record.id,
        seq=record.seq,
        project_id=record.project_id,
        author=_author(author) if author else None,
        title=record.title,
        detail=record.detail,
        type=record.type,
        urgency=record.urgency,
        status=record.status,
        closed_at=record.closed_at,
        edited_at=record.edited_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _comment(record: CommentRecord, author: AuthorRecord | None) -> CommentResponse:
    return CommentResponse(
        id=record.id,
        item_id=record.item_id,
        parent_comment_id=record.parent_comment_id,
        author=_author(author) if author else None,
        body=record.body,
        is_deleted=record.deleted_at is not None,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


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
    return AgentKeyResponse(
        id=record.id,
        name=record.name,
        prefix=record.key_prefix,
        role=record.role,
        project_ids=_parse_project_ids(record.project_ids_json),
        enabled=record.enabled,
        last_used_at=record.last_used_at,
        created_at=record.created_at,
    )


def agent_can_access_project(agent: AgentKeyResponse, project_id: int) -> bool:
    return not agent.project_ids or project_id in agent.project_ids


class FeedbackService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = FeedbackRepository(self._conn)

    def _authorize_project(self, actor: Actor, ability: str, project_id: int) -> None:
        project = self.get_project_for_authz(project_id)
        if project is None:
            raise not_found("Project not found")
        AuthorizationService(self._conn).assert_can(actor, ability, {"type": "feedbackProject", **project})

    def _require_item(self, item_id: int) -> ItemAuthz:
        item = self.get_item_for_authz(item_id)
        if item is None:
            raise not_found("Feedback item not found")
        return item

    def _authorize_item(self, actor: Actor, ability: str, item_id: int) -> None:
        item = self._require_item(item_id)
        AuthorizationService(self._conn).assert_can(actor, ability, {"type": "feedbackItem", **item})

    def _authorize_comment(self, actor: Actor, ability: str, comment_id: int) -> None:
        comment = self.get_comment_for_authz(comment_id)
        if comment is None:
            raise not_found("Comment not found")
        AuthorizationService(self._conn).assert_can(actor, ability, {"type": "feedbackComment", **comment})

    def agent_projects(self, key: AgentKeyResponse) -> AgentProjectsResponse:
        return AgentProjectsResponse(
            items=[
                AgentProjectResponse(id=project.id, name=project.name, description=project.description)
                for project in self.list_projects_for_admin()
                if agent_can_access_project(key, project.id)
            ]
        )

    def agent_tasks(
        self, key: AgentKeyResponse, *, project_id: int | None, status: str | None, type_: str | None
    ) -> AgentTasksResponse:
        if project_id is not None and not agent_can_access_project(key, project_id):
            raise forbidden("This API key cannot access this project")
        items = [
            item for item in self.list_feedback_for_agent(project_id) if agent_can_access_project(key, item.project_id)
        ]
        if status:
            items = [item for item in items if item.status.value == status]
        if type_:
            items = [item for item in items if item.type.value == type_]
        return AgentTasksResponse(
            items=items,
            summary=AgentSummaryResponse(
                open=sum(item.status is FeedbackStatus.Open for item in items),
                done=sum(item.status is FeedbackStatus.Done for item in items),
                expired=sum(item.status is FeedbackStatus.Expired for item in items),
            ),
        )

    def agent_task(self, key: AgentKeyResponse, item_id: int) -> FeedbackItemResponse:
        item = self.item_by_id(item_id)
        if item is None:
            raise not_found("Task not found")
        if not agent_can_access_project(key, item.project_id):
            raise forbidden("This API key cannot access this project")
        return item

    def agent_set_status(self, key: AgentKeyResponse, item_id: int, status: FeedbackStatus) -> FeedbackItemResponse:
        self.agent_task(key, item_id)
        return self._set_feedback_status(item_id, status)

    def members_of(self, project_id: int) -> list[MemberResponse]:
        return [_member(record) for record in self._repository.members(FeedbackProjectID(project_id))]

    def _project_admin(self, project: ProjectRecord) -> ProjectAdminResponse:
        return ProjectAdminResponse(
            id=project.id,
            name=project.name,
            description=project.description,
            members=self.members_of(project.id),
            created_at=project.created_at,
        )

    def get_project_for_authz(self, project_id: int) -> ProjectAuthz | None:
        record = self._repository.project(FeedbackProjectID(project_id))
        return {"id": record.id, "projectId": record.id} if record else None

    def list_my_projects(self, viewer_id: int, is_admin: bool) -> list[MyProjectResponse]:
        result: list[MyProjectResponse] = []
        for project in self._repository.projects():
            members = self._repository.members(project.id)
            mine = next((member for member in members if member.user_id == viewer_id), None)
            if mine is None and not is_admin:
                continue
            result.append(
                MyProjectResponse(
                    id=project.id,
                    name=project.name,
                    description=project.description,
                    member_count=len(members),
                    is_programmer=is_admin or bool(mine and mine.is_programmer),
                    created_at=project.created_at,
                )
            )
        return result

    def list_projects_for_admin(self) -> list[ProjectAdminResponse]:
        return [self._project_admin(project) for project in self._repository.projects()]

    def create_project(self, actor_id: int, name: str, description: str | None) -> ProjectAdminResponse:
        timestamp = now_ms()
        project_id = self._repository.insert_project(
            actor_id=UserID(actor_id), name=name, description=description or "", now=timestamp
        )
        project = self._repository.project(project_id)
        if project is None:
            raise not_found("Project not found")
        return self._project_admin(project)

    def update_project(self, project_id: int, patch: ProjectPatch) -> None:
        typed_id = FeedbackProjectID(project_id)
        if self._repository.project(typed_id) is None:
            raise not_found("Project not found")
        values: dict[str, object] = {"updated_at": now_ms()}
        if "name" in patch.model_fields_set:
            values["name"] = patch.name
        if "description" in patch.model_fields_set:
            values["description"] = patch.description or ""
        self._repository.update_project(typed_id, values)

    def delete_project(self, actor_id: int, project_id: int) -> None:
        typed_id = FeedbackProjectID(project_id)
        if self._repository.project(typed_id) is None:
            raise not_found("Project not found")
        timestamp = now_ms()
        self._repository.soft_delete_project(typed_id, actor_id=UserID(actor_id), now=timestamp)

    def set_project_members(self, project_id: int, members: list[MemberInput]) -> None:
        typed_id = FeedbackProjectID(project_id)
        if self._repository.project(typed_id) is None:
            raise not_found("Project not found")
        self._repository.replace_members(
            typed_id, [(UserID(member.user_id), member.is_programmer) for member in members]
        )

    def _items(self, project_id: FeedbackProjectID | None = None) -> list[FeedbackItemResponse]:
        records = self._repository.items(project_id)
        author_map = self._repository.authors({record.author_id for record in records})
        return [_item(record, author_map.get(record.author_id)) for record in records]

    def item_by_id(self, item_id: int) -> FeedbackItemResponse | None:
        record = self._repository.item(FeedbackItemID(item_id))
        if record is None:
            return None
        return _item(record, self._repository.authors([record.author_id]).get(record.author_id))

    def get_item_for_authz(self, item_id: int) -> ItemAuthz | None:
        record = self._repository.item(FeedbackItemID(item_id), include_deleted=True)
        return (
            None
            if record is None
            else {
                "id": record.id,
                "projectId": record.project_id,
                "authorId": record.author_id,
                "deletedAt": record.deleted_at,
            }
        )

    def get_comment_for_authz(self, comment_id: int) -> CommentAuthz | None:
        record = self._repository.comment(FeedbackCommentID(comment_id), include_deleted=True)
        if record is None:
            return None
        item = self._repository.item(record.item_id, include_deleted=True)
        return {
            "id": record.id,
            "itemId": record.item_id,
            "projectId": item.project_id if item else None,
            "authorId": record.author_id,
            "deletedAt": record.deleted_at,
        }

    def list_feedback(self, actor: Actor, project_id: int) -> FeedbackListResponse:
        self._authorize_project(actor, Abilities.FEEDBACK_VIEW, project_id)
        typed_id = FeedbackProjectID(project_id)
        member = next((value for value in self._repository.members(typed_id) if value.user_id == actor.id), None)
        return FeedbackListResponse(
            items=self._items(typed_id), can_manage=actor.role == "admin" or bool(member and member.is_programmer)
        )

    def create_feedback(self, actor: Actor, body: FeedbackBody) -> FeedbackItemResponse:
        self._authorize_project(actor, Abilities.FEEDBACK_CREATE, body.project_id)
        timestamp = now_ms()
        project_id = FeedbackProjectID(body.project_id)
        item_id = self._repository.insert_item(
            project_id=project_id,
            author_id=UserID(actor.id),
            seq=self._repository.next_seq(project_id),
            title=body.title,
            detail=body.detail or "",
            type_value=body.type.value,
            urgency=body.urgency or FeedbackUrgency.Normal,
            now=timestamp,
        )
        created = self.item_by_id(item_id)
        if created is None:
            raise not_found("Feedback item not found")
        return created

    def update_feedback(self, actor: Actor, item_id: int, patch: FeedbackPatch) -> FeedbackItemResponse:
        self._authorize_item(actor, Abilities.FEEDBACK_UPDATE, item_id)
        if self._repository.item(FeedbackItemID(item_id)) is None:
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
        self._repository.update_item(FeedbackItemID(item_id), values)
        updated = self.item_by_id(item_id)
        if updated is None:
            raise not_found("Feedback item not found")
        return updated

    def delete_feedback(self, actor: Actor, item_id: int) -> None:
        self._authorize_item(actor, Abilities.FEEDBACK_DELETE, item_id)
        if self._repository.item(FeedbackItemID(item_id)) is None:
            raise not_found("Feedback item not found")
        timestamp = now_ms()
        self._repository.update_item(
            FeedbackItemID(item_id), {"deleted_at": timestamp, "deleted_by": actor.id, "updated_at": timestamp}
        )

    def set_feedback_status(self, actor: Actor, item_id: int, status: FeedbackStatus) -> FeedbackItemResponse:
        self._authorize_item(actor, Abilities.FEEDBACK_MANAGE, item_id)
        return self._set_feedback_status(item_id, status)

    def _set_feedback_status(self, item_id: int, status: FeedbackStatus) -> FeedbackItemResponse:
        if self._repository.item(FeedbackItemID(item_id)) is None:
            raise not_found("Feedback item not found")
        timestamp = now_ms()
        self._repository.update_item(
            FeedbackItemID(item_id),
            {
                "status": status.value,
                "closed_at": None if status is FeedbackStatus.Open else timestamp,
                "updated_at": timestamp,
            },
        )
        updated = self.item_by_id(item_id)
        if updated is None:
            raise not_found("Feedback item not found")
        return updated

    def list_feedback_for_agent(self, project_id: int | None = None) -> list[FeedbackItemResponse]:
        return self._items(FeedbackProjectID(project_id) if project_id is not None else None)

    def _comment_with_author(self, comment_id: FeedbackCommentID) -> CommentResponse:
        record = self._repository.comment(comment_id, include_deleted=True)
        if record is None:
            raise not_found("Comment not found")
        return _comment(record, self._repository.authors([record.author_id]).get(record.author_id))

    def list_comments(self, actor: Actor, item_id: int) -> list[CommentResponse]:
        self._authorize_item(actor, Abilities.FEEDBACK_VIEW, item_id)
        typed_id = FeedbackItemID(item_id)
        if self._repository.item(typed_id) is None:
            raise not_found("Feedback item not found")
        records = self._repository.comments(typed_id)
        author_map = self._repository.authors({record.author_id for record in records})
        return [_comment(record, author_map.get(record.author_id)) for record in records]

    def create_comment(self, actor: Actor, item_id: int, body: str, parent_comment_id: int | None) -> CommentResponse:
        item = self._require_item(item_id)
        AuthorizationService(self._conn).assert_can(
            actor, Abilities.FEEDBACK_COMMENT_CREATE, {"type": "feedbackComment", "projectId": item["projectId"]}
        )
        typed_item_id = FeedbackItemID(item_id)
        if self._repository.item(typed_item_id) is None:
            raise not_found("Feedback item not found")
        typed_parent = FeedbackCommentID(parent_comment_id) if parent_comment_id is not None else None
        if typed_parent is not None:
            parent = self._repository.comment(typed_parent)
            if parent is None or parent.item_id != typed_item_id:
                raise not_found("Parent comment not found")
        timestamp = now_ms()
        comment_id = self._repository.insert_comment(
            item_id=typed_item_id, author_id=UserID(actor.id), parent_comment_id=typed_parent, body=body, now=timestamp
        )
        return self._comment_with_author(comment_id)

    def update_comment(self, actor: Actor, comment_id: int, body: str) -> CommentResponse:
        self._authorize_comment(actor, Abilities.FEEDBACK_COMMENT_UPDATE, comment_id)
        typed_id = FeedbackCommentID(comment_id)
        if self._repository.comment(typed_id) is None:
            raise not_found("Comment not found")
        self._repository.update_comment(typed_id, {"body": body, "updated_at": now_ms()})
        return self._comment_with_author(typed_id)

    def delete_comment(self, actor: Actor, comment_id: int) -> None:
        self._authorize_comment(actor, Abilities.FEEDBACK_COMMENT_DELETE, comment_id)
        typed_id = FeedbackCommentID(comment_id)
        if self._repository.comment(typed_id) is None:
            raise not_found("Comment not found")
        timestamp = now_ms()
        self._repository.update_comment(typed_id, {"deleted_at": timestamp, "updated_at": timestamp})

    def list_keys(self) -> list[AgentKeyResponse]:
        return [_key(record) for record in self._repository.api_keys()]

    def create_key(self, name: str, role: AgentRole, project_ids: list[int]) -> tuple[str, AgentKeyResponse]:
        valid = {project.id for project in self.list_projects_for_admin()}
        project_ids = [value for value in project_ids if value in valid]
        raw_key = generate_agent_key()
        key_id = self._repository.insert_api_key(
            name=name,
            key_hash=_hash_agent_key(raw_key),
            key_prefix=raw_key[:8],
            role=role,
            project_ids_json=json.dumps(project_ids),
            created_at=now_ms(),
        )
        record = self._repository.api_key(key_id)
        if record is None:
            raise not_found("API key not found")
        return raw_key, _key(record)

    def set_key_enabled(self, key_id: int, enabled: bool) -> None:
        typed_id = FeedbackAPIKeyID(key_id)
        if self._repository.api_key(typed_id) is None:
            raise not_found("API key not found")
        self._repository.set_api_key_enabled(typed_id, enabled)

    def delete_key(self, key_id: int) -> None:
        typed_id = FeedbackAPIKeyID(key_id)
        if self._repository.api_key(typed_id) is None:
            raise not_found("API key not found")
        self._repository.delete_api_key(typed_id)

    def verify_key(self, raw_key: str | None) -> AgentKeyResponse | None:
        if not raw_key:
            return None
        record = self._repository.api_key_by_hash(_hash_agent_key(raw_key))
        if record is None:
            return None
        self._repository.touch_api_key(record.id, last_used_at=now_ms())
        return _key(record)
