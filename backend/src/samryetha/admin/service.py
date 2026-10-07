"""Typed admin statistics, user management, and deleted-content service."""

from __future__ import annotations

from samryetha.admin.repository import AdminRepository

import json
import secrets
from datetime import datetime, timezone
from typing import Protocol

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

from .models import (
    ActivityStats,
    AdminAssignableRole,
    AdminMutableStatus,
    AdminStatsResponse,
    AdminUserListResponse,
    AdminUserResponse,
    ContentStats,
    DeletedContentResponse,
    DeletedDiscussionResponse,
    DeletedReplyResponse,
    ModerationStats,
    TemporaryPasswordResponse,
    UserDistribution,
)
from ..authz import Abilities, Actor, AuthorizationService
from ..core.db import now_ms
from ..core.errors import conflict, not_found
from ..core.ids import DiscussionID, ReplyID, UserID
from ..moderation import preview_text
from ..moderation.models import ModerationAuthorResponse
from ..auth.security import hash_password
from ..users.models import AccountRole, AccountStatus, UserRow
from ..users import make_handle

_EMAIL = "samryetha.local"
_SETTINGS = TypeAdapter(dict[str, object])


class Presence(Protocol):
    def online_count(self) -> int: ...


def _start_of_today_ms() -> int:
    local_midnight = datetime.now(timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)


def _author(row: UserRow) -> ModerationAuthorResponse:
    return ModerationAuthorResponse(
        id=UserID(row["id"]),
        username=row["username"],
        handle=make_handle(row["username"], row["discriminator"]),
        display_name=row["display_name"],
    )


def _admin_user(row: UserRow, ban_active: bool, report_count: int) -> AdminUserResponse:
    return AdminUserResponse(
        id=UserID(row["id"]),
        username=row["username"],
        handle=make_handle(row["username"], row["discriminator"]),
        display_name=row["display_name"],
        email=row["email"],
        role=AccountRole(row["role"]),
        status=AccountStatus(row["status"]),
        email_verified=row["email_verified_at"] is not None,
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        ban_active=ban_active,
        report_count=report_count,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class AdminService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = AdminRepository(self._conn)

    def _load_user_full(self, user_id: UserID) -> AdminUserResponse:
        record = self._repository.user_admin_record(user_id)
        if record is None:
            raise not_found("User not found")
        return _admin_user(record.user, record.ban_active, record.report_count)

    def stats(self, actor: Actor, presence: Presence) -> AdminStatsResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_VIEW, None)
        today = _start_of_today_ms()
        record = self._repository.stats(today=today)
        distribution = record.distribution
        return AdminStatsResponse(
            users=UserDistribution(
                total=sum(distribution.values()),
                pending=distribution[AccountStatus.Pending],
                active=distribution[AccountStatus.Active],
                banned=distribution[AccountStatus.Banned],
                deactivated=distribution[AccountStatus.Deactivated],
            ),
            content=ContentStats(
                discussions=record.discussions,
                replies=record.replies,
                boards=record.boards,
            ),
            moderation=ModerationStats(
                open_reports=record.open_reports,
                active_bans=record.active_bans,
            ),
            activity=ActivityStats(
                active_today=record.active_today,
                new_users_today=record.new_users_today,
                new_discussions_today=record.new_discussions_today,
                new_replies_today=record.new_replies_today,
                online_now=presence.online_count(),
            ),
        )

    def list_users(
        self,
        actor: Actor,
        q: str | None,
        status: AccountStatus | None,
        role: AdminAssignableRole | None,
        cursor: UserID | None,
        limit: int = 20,
        exclude_pending: bool = False,
    ) -> AdminUserListResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_VIEW, None)
        page_limit = min(limit, 50)
        records = self._repository.user_page(
            query_pattern=f"%{_escape_like(q.strip()[:100])}%" if q and q.strip() else None,
            status=status,
            role=role,
            cursor=cursor,
            limit=page_limit,
            exclude_pending=exclude_pending,
        )
        has_more = len(records) > page_limit
        page = records[:page_limit]
        items = [_admin_user(record.user, record.ban_active, record.report_count) for record in page]
        return AdminUserListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)

    def _log_action(self, actor: Actor, action: str, target_id: UserID, reason: str | None) -> None:
        self._repository.log_action(
            actor_id=UserID(actor.id), action=action, target_id=target_id, reason=reason, created_at=now_ms()
        )

    def change_role(
        self, actor: Actor, target_id: UserID, role: AdminAssignableRole, reason: str | None
    ) -> AdminUserResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_USER_ROLE_UPDATE, None)
        target = self._repository.user(target_id)
        if target is None:
            raise not_found("User not found")
        if target["id"] == actor.id:
            raise conflict("Cannot change your own role")
        if target["role"] == role.value:
            raise conflict("Role is already set")
        if target["role"] == AccountRole.Admin.value and role is not AdminAssignableRole.Admin:
            if self._repository.admin_count() <= 1:
                raise conflict("Cannot demote the last admin")
        try:
            settings = _SETTINGS.validate_json(target["settings"] or "{}")
        except ValidationError:
            settings = {}
        settings["role_source"] = "local"
        self._repository.update_user(
            target_id,
            {"role": role.value, "settings": json.dumps(settings, ensure_ascii=False), "updated_at": now_ms()},
        )
        self._log_action(actor, "user.role.change", target_id, reason or f"{target['role']}->{role.value}")
        return self._load_user_full(target_id)

    def change_status(
        self, actor: Actor, target_id: UserID, status: AdminMutableStatus, reason: str | None
    ) -> AdminUserResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None)
        target = self._repository.user(target_id)
        if target is None:
            raise not_found("User not found")
        if target["status"] == AccountStatus.Banned.value:
            raise conflict("Banned users must be unbanned first")
        if target["id"] == actor.id:
            raise conflict("Cannot change your own status")
        if target["status"] == status.value:
            raise conflict("Status is already set")
        self._repository.update_user(target_id, {"status": status.value, "updated_at": now_ms()})
        self._log_action(
            actor,
            "user.deactivate" if status is AdminMutableStatus.Deactivated else "user.reactivate",
            target_id,
            reason,
        )
        if status is AdminMutableStatus.Deactivated:
            self._repository.delete_user_sessions(target_id)
        return self._load_user_full(target_id)

    def reset_password(self, actor: Actor, target_id: UserID) -> TemporaryPasswordResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None)
        target = self._repository.user(target_id)
        if target is None:
            raise not_found("User not found")
        if target["status"] == AccountStatus.Banned.value:
            raise conflict("Banned users must be unbanned first")
        temporary_password = secrets.token_urlsafe(12)
        self._repository.update_user(
            target_id, {"password_hash": hash_password(temporary_password), "updated_at": now_ms()}
        )
        self._repository.delete_user_sessions(target_id)
        self._log_action(actor, "user.password.reset", target_id, "admin reset")
        return TemporaryPasswordResponse(temporary_password=temporary_password)

    def verify_user(self, actor: Actor, target_id: UserID) -> AdminUserResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None)
        target = self._repository.user(target_id)
        if target is None:
            raise not_found("User not found")
        if target["status"] == AccountStatus.Banned.value:
            raise conflict("Banned users must be unbanned first")
        if target["status"] == AccountStatus.Active.value and target["email_verified_at"] is not None:
            raise conflict("User already verified")
        timestamp = now_ms()
        self._repository.update_user(
            target_id,
            {
                "status": AccountStatus.Active.value,
                "email_verified_at": target["email_verified_at"] or timestamp,
                "updated_at": timestamp,
            },
        )
        self._log_action(actor, "user.verify", target_id, None)
        return self._load_user_full(target_id)

    def delete_user(self, actor: Actor, target_id: UserID, reason: str | None) -> None:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_USER_DELETE, None)
        target = self._repository.user(target_id)
        if target is None:
            raise not_found("User not found")
        if target["id"] == actor.id:
            raise conflict("Cannot delete your own account")
        if target["deleted_at"] is not None:
            raise conflict("User is already deleted")
        timestamp = now_ms()
        self._repository.update_user(
            target_id,
            {
                "deleted_at": timestamp,
                "status": AccountStatus.Deactivated.value,
                "username": f"deleted-{int(target_id)}",
                "email": f"deleted-{int(target_id)}@{_EMAIL}",
                "display_name": "Deleted user",
                "bio": "",
                "avatar_object_key": None,
                "updated_at": timestamp,
            },
        )
        self._repository.delete_user_sessions(target_id)
        self._log_action(actor, "user.delete", target_id, reason)

    def list_deleted_content(
        self, actor: Actor, discussion_cursor: DiscussionID | None, reply_cursor: ReplyID | None, limit: int = 20
    ) -> DeletedContentResponse:
        AuthorizationService(self._conn).assert_can(actor, Abilities.ADMIN_VIEW, None)
        page_limit = min(limit, 50)
        d_rows = self._repository.deleted_discussions(discussion_cursor, limit=page_limit)
        r_rows = self._repository.deleted_replies(reply_cursor, limit=page_limit)
        d_more, r_more = len(d_rows) > page_limit, len(r_rows) > page_limit
        d_page, r_page = d_rows[:page_limit], r_rows[:page_limit]
        board_slugs = self._repository.board_slugs((row.board_id for row in d_page))
        deleters = self._repository.users_by_ids(
            (row.deleted_by for row in [*d_page, *r_page] if row.deleted_by is not None)
        )
        titles = self._repository.discussion_titles((row.discussion_id for row in r_page))
        discussions_out = [
            DeletedDiscussionResponse(
                id=row.id,
                board_slug=board_slugs.get(row.board_id, ""),
                title=row.title,
                preview=preview_text(row.body_md),
                deleted_by=_author(deleters[row.deleted_by])
                if row.deleted_by is not None and row.deleted_by in deleters
                else None,
                deleted_at=row.deleted_at,
                reason=row.reason,
            )
            for row in d_page
        ]
        replies_out = [
            DeletedReplyResponse(
                id=row.id,
                discussion_id=row.discussion_id,
                discussion_title=titles.get(row.discussion_id, ""),
                preview=preview_text(row.body_md),
                deleted_by=_author(deleters[row.deleted_by])
                if row.deleted_by is not None and row.deleted_by in deleters
                else None,
                deleted_at=row.deleted_at,
                reason=row.reason,
            )
            for row in r_page
        ]
        return DeletedContentResponse(
            discussions=discussions_out,
            replies=replies_out,
            next_discussion_cursor=discussions_out[-1].id if d_more and discussions_out else None,
            next_reply_cursor=replies_out[-1].id if r_more and replies_out else None,
        )
