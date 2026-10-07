"""授权矩阵 — 镜像 backend/src/authz/can.ts。

唯一授权入口：业务代码不散落 ``user.role ==`` 判断，一律走 :func:`can` / :func:`assert_can`。
resource 用鸭子类型对象：带 ``type`` 及所需字段（见各 ability 分支）。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.engine import Connection

from ..errors import forbidden
from ..schema import board_members, feedback_project_members


class Abilities:
    BOARD_CREATE = "board.create"
    BOARD_UPDATE = "board.update"
    BOARD_DELETE = "board.delete"
    BOARD_MANAGE_MEMBERS = "board.manage_members"
    BOARD_JOIN = "board.join"
    DISCUSSION_CREATE = "discussion.create"
    DISCUSSION_READ = "discussion.read"
    DISCUSSION_UPDATE = "discussion.update"
    DISCUSSION_DELETE = "discussion.delete"
    DISCUSSION_PIN = "discussion.pin"
    DISCUSSION_LOCK = "discussion.lock"
    REPLY_CREATE = "reply.create"
    REPLY_UPDATE = "reply.update"
    REPLY_DELETE = "reply.delete"
    USER_UPDATE_SELF = "user.update.self"
    USER_FOLLOW = "user.follow"
    REPORT_CREATE = "report.create"
    ATTACHMENT_CREATE = "attachment.create"
    ATTACHMENT_DELETE = "attachment.delete"
    # 读写他人附件（取证/清理）：仅全局管理员，service 层与 owner 条件组合使用。
    ATTACHMENT_MODERATE = "attachment.moderate"
    PRESENCE_HEARTBEAT = "presence.heartbeat"
    MODERATION_VIEW = "moderation.view"
    MODERATION_RESOLVE = "moderation.resolve"
    USER_BAN = "user.ban"
    MODERATION_UNBAN = "moderation.unban"
    ADMIN_VIEW = "admin.view"
    ADMIN_USER_ROLE_UPDATE = "admin.user.role.update"
    ADMIN_USER_STATUS_UPDATE = "admin.user.status.update"
    ADMIN_USER_DELETE = "admin.user.delete"
    FEEDBACK_VIEW = "feedback.view"
    FEEDBACK_CREATE = "feedback.create"
    FEEDBACK_UPDATE = "feedback.update"
    FEEDBACK_DELETE = "feedback.delete"
    FEEDBACK_MANAGE = "feedback.manage"
    FEEDBACK_PROJECT_MANAGE = "feedback.project.manage"
    FEEDBACK_COMMENT_CREATE = "feedback.comment.create"
    FEEDBACK_COMMENT_UPDATE = "feedback.comment.update"
    FEEDBACK_COMMENT_DELETE = "feedback.comment.delete"


class Actor(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def role(self) -> str: ...

    @property
    def status(self) -> str: ...


class AuthorizationResource(BaseModel):
    """Normalized, typed authorization input accepted from records and wire-shaped mappings."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True, extra="ignore")

    type: str = ""
    id: int = 0
    visibility: str = ""
    posting_policy: str = Field(default="", alias="postingPolicy")
    author_id: int = Field(default=0, alias="authorId")
    deleted_at: int | None = Field(default=None, alias="deletedAt")
    board_id: int = Field(default=0, alias="boardId")
    is_locked: bool = Field(default=False, alias="isLocked")
    uploader_id: int = Field(default=0, alias="uploaderId")
    project_id: int = Field(default=0, alias="projectId")


def is_active(actor: Actor | None) -> bool:
    return actor is not None and actor.status == "active"


def is_global_mod(actor: Actor | None) -> bool:
    # 全局角色已合并：moderator 并入 admin，仅 admin 为全局管理角色
    # Global roles merged: moderator folded into admin; only admin is the global privileged role
    return actor is not None and actor.role == "admin"


def _is_board_member(conn: Connection, actor: Actor | None, board_id: int) -> bool:
    if actor is None:
        return False
    row = conn.execute(
        select(board_members.c.board_id).where(
            (board_members.c.board_id == board_id) & (board_members.c.user_id == actor.id)
        )
    ).first()
    return row is not None


def _is_board_mod(conn: Connection, actor: Actor | None, board_id: int) -> bool:
    if actor is None:
        return False
    row = conn.execute(
        select(board_members.c.board_id).where(
            (board_members.c.board_id == board_id)
            & (board_members.c.user_id == actor.id)
            & (board_members.c.role == "moderator")
        )
    ).first()
    return row is not None


def _is_project_member(conn: Connection, actor: Actor | None, project_id: int) -> bool:
    if actor is None:
        return False
    row = conn.execute(
        select(feedback_project_members.c.project_id).where(
            (feedback_project_members.c.project_id == project_id) & (feedback_project_members.c.user_id == actor.id)
        )
    ).first()
    return row is not None


def _is_project_programmer(conn: Connection, actor: Actor | None, project_id: int) -> bool:
    if actor is None:
        return False
    row = conn.execute(
        select(feedback_project_members.c.project_id).where(
            (feedback_project_members.c.project_id == project_id)
            & (feedback_project_members.c.user_id == actor.id)
            & (feedback_project_members.c.is_programmer == 1)
        )
    ).first()
    return row is not None


def can(
    actor: Actor | None,
    ability: str,
    resource: object | None,
    conn: Connection,
) -> bool:
    if actor is not None and actor.status == "banned":
        return False

    # 不依赖 resource 的能力
    if ability == Abilities.BOARD_CREATE:
        return actor is not None and actor.role == "admin"
    if ability == Abilities.BOARD_DELETE:
        return actor is not None and actor.role == "admin"
    if ability in (Abilities.ATTACHMENT_CREATE, Abilities.PRESENCE_HEARTBEAT, Abilities.REPORT_CREATE):
        return is_active(actor)
    if ability in (
        Abilities.MODERATION_VIEW,
        Abilities.MODERATION_RESOLVE,
        Abilities.USER_BAN,
        Abilities.ATTACHMENT_MODERATE,
    ):
        return is_global_mod(actor)
    if ability == Abilities.MODERATION_UNBAN:
        return actor is not None and actor.role == "admin"
    if ability in (
        Abilities.ADMIN_VIEW,
        Abilities.ADMIN_USER_ROLE_UPDATE,
        Abilities.ADMIN_USER_STATUS_UPDATE,
        Abilities.ADMIN_USER_DELETE,
        Abilities.FEEDBACK_PROJECT_MANAGE,
    ):
        return actor is not None and actor.role == "admin"

    if resource is None:
        return False

    normalized = AuthorizationResource.model_validate(resource)
    rtype = normalized.type

    if ability in (Abilities.BOARD_UPDATE, Abilities.BOARD_MANAGE_MEMBERS):
        return rtype == "board" and (
            (actor is not None and actor.role == "admin") or _is_board_mod(conn, actor, normalized.id)
        )

    if ability == Abilities.BOARD_JOIN:
        return is_active(actor) and rtype == "board" and normalized.visibility != "public"

    if ability == Abilities.DISCUSSION_CREATE:
        if not is_active(actor):
            return False
        if normalized.posting_policy == "everyone":
            return True
        if normalized.posting_policy == "moderators":
            return is_global_mod(actor) or _is_board_mod(conn, actor, normalized.id)
        return is_global_mod(actor) or _is_board_member(conn, actor, normalized.id)

    if ability == Abilities.DISCUSSION_READ:
        if normalized.visibility == "public":
            return True
        if actor is None:
            return False
        if is_global_mod(actor):
            return True
        return _is_board_member(conn, actor, normalized.id)

    if ability in (Abilities.DISCUSSION_UPDATE, Abilities.DISCUSSION_DELETE):
        if is_global_mod(actor):
            return True
        if actor is None:
            return False
        if normalized.author_id != actor.id:
            return False
        if normalized.deleted_at is not None and ability == Abilities.DISCUSSION_UPDATE:
            return False
        return True

    if ability in (Abilities.DISCUSSION_PIN, Abilities.DISCUSSION_LOCK):
        if is_global_mod(actor):
            return True
        return actor is not None and _is_board_mod(conn, actor, normalized.board_id)

    if ability == Abilities.REPLY_CREATE:
        if not is_active(actor):
            return False
        return not normalized.is_locked and normalized.deleted_at is None

    if ability in (Abilities.REPLY_UPDATE, Abilities.REPLY_DELETE):
        if is_global_mod(actor):
            return True
        return actor is not None and normalized.author_id == actor.id

    if ability == Abilities.USER_UPDATE_SELF:
        return actor is not None and rtype == "user" and normalized.id == actor.id

    if ability == Abilities.USER_FOLLOW:
        return actor is not None and is_active(actor) and rtype == "user" and normalized.id != actor.id

    if ability == Abilities.ATTACHMENT_DELETE:
        return actor is not None and rtype == "attachment" and normalized.uploader_id == actor.id

    if ability in (Abilities.FEEDBACK_VIEW, Abilities.FEEDBACK_CREATE):
        if actor is None or actor.status != "active":
            return False
        if actor.role == "admin":
            return True
        return _is_project_member(conn, actor, normalized.project_id)

    if ability in (Abilities.FEEDBACK_UPDATE, Abilities.FEEDBACK_DELETE):
        if actor is not None and actor.role == "admin":
            return True
        if actor is None or actor.status != "active":
            return False
        if normalized.author_id == actor.id:
            if ability == Abilities.FEEDBACK_DELETE:
                return True
            return normalized.deleted_at is None
        return _is_project_programmer(conn, actor, normalized.project_id)

    if ability == Abilities.FEEDBACK_MANAGE:
        if actor is not None and actor.role == "admin":
            return True
        return (
            actor is not None
            and actor.status == "active"
            and _is_project_programmer(conn, actor, normalized.project_id)
        )

    if ability == Abilities.FEEDBACK_COMMENT_CREATE:
        # 评论创建：与 FEEDBACK_CREATE 一致——项目成员或 admin
        # Comment create: same as FEEDBACK_CREATE — project member or admin
        if actor is None or actor.status != "active":
            return False
        if actor.role == "admin":
            return True
        return _is_project_member(conn, actor, normalized.project_id)

    if ability in (Abilities.FEEDBACK_COMMENT_UPDATE, Abilities.FEEDBACK_COMMENT_DELETE):
        # 评论编辑/删除：作者、admin、或项目 programmer
        # Comment update/delete: author, admin, or project programmer
        if actor is not None and actor.role == "admin":
            return True
        if actor is None or actor.status != "active":
            return False
        if normalized.author_id == actor.id:
            return True
        return _is_project_programmer(conn, actor, normalized.project_id)

    return False


def assert_can(actor: Actor | None, ability: str, resource: object | None, conn: Connection) -> None:
    if not can(actor, ability, resource, conn):
        raise forbidden()
