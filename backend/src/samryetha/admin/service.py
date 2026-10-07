"""Typed admin statistics, user management, and deleted-content service."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Protocol

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Table, and_, delete, func, or_, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

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
from ..authz import Abilities, Actor, assert_can
from ..db import now_ms
from ..errors import conflict, not_found
from ..ids import DiscussionID, ReplyID, UserID
from ..moderation import preview_text
from ..moderation.models import ModerationAuthorResponse
from ..schema import bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from ..auth.security import delete_user_sessions, hash_password
from ..users.models import AccountRole, AccountStatus, UserRow
from ..users import make_handle, user_row_from_mapping

_EMAIL = "samryetha.local"
_SETTINGS = TypeAdapter(dict[str, object])


class Presence(Protocol):
    def online_count(self) -> int: ...


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_str(value: object, name: str) -> str | None:
    return None if value is None else _str(value, name)


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


def _load_user_full(conn: Connection, user_id: UserID) -> AdminUserResponse:
    raw = conn.execute(select(users).where(users.c.id == int(user_id))).mappings().first()
    if raw is None:
        raise not_found("User not found")
    ban_active = (
        conn.execute(select(bans.c.id).where((bans.c.user_id == int(user_id)) & (bans.c.is_active == 1))).first()
        is not None
    )
    report_count = conn.execute(
        select(func.count())
        .select_from(reports)
        .where((reports.c.reportable_type == "user") & (reports.c.reportable_id == int(user_id)))
    ).scalar_one()
    return _admin_user(user_row_from_mapping(raw), ban_active, int(report_count))


def _count(conn: Connection, table: Table, condition: ColumnElement[bool] | None = None) -> int:
    statement = select(func.count()).select_from(table)
    if condition is not None:
        statement = statement.where(condition)
    return int(conn.execute(statement).scalar_one())


def stats(conn: Connection, actor: Actor, presence: Presence) -> AdminStatsResponse:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    today = _start_of_today_ms()
    distribution = {status: 0 for status in AccountStatus}
    total = 0
    for raw_status, raw_count in conn.execute(select(users.c.status, func.count()).group_by(users.c.status)):
        status = AccountStatus(_str(raw_status, "status"))
        count = _int(raw_count, "count")
        distribution[status] = count
        total += count
    authors_today: set[UserID] = set()
    for raw_id in conn.execute(
        select(discussions.c.author_id).where((discussions.c.created_at > today) & discussions.c.deleted_at.is_(None))
    ).scalars():
        authors_today.add(UserID(_int(raw_id, "author_id")))
    for raw_id in conn.execute(
        select(replies.c.author_id).where((replies.c.created_at > today) & replies.c.deleted_at.is_(None))
    ).scalars():
        authors_today.add(UserID(_int(raw_id, "author_id")))
    return AdminStatsResponse(
        users=UserDistribution(
            total=total,
            pending=distribution[AccountStatus.Pending],
            active=distribution[AccountStatus.Active],
            banned=distribution[AccountStatus.Banned],
            deactivated=distribution[AccountStatus.Deactivated],
        ),
        content=ContentStats(
            discussions=_count(conn, discussions, discussions.c.deleted_at.is_(None)),
            replies=_count(conn, replies, replies.c.deleted_at.is_(None)),
            boards=_count(conn, boards, boards.c.deleted_at.is_(None)),
        ),
        moderation=ModerationStats(
            open_reports=_count(conn, reports, reports.c.status.in_(["open", "in_progress"])),
            active_bans=_count(conn, bans, bans.c.is_active == 1),
        ),
        activity=ActivityStats(
            active_today=len(authors_today),
            new_users_today=_count(conn, users, users.c.created_at > today),
            new_discussions_today=_count(
                conn, discussions, (discussions.c.created_at > today) & discussions.c.deleted_at.is_(None)
            ),
            new_replies_today=_count(conn, replies, (replies.c.created_at > today) & replies.c.deleted_at.is_(None)),
            online_now=presence.online_count(),
        ),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_users(
    conn: Connection,
    actor: Actor,
    q: str | None,
    status: AccountStatus | None,
    role: AdminAssignableRole | None,
    cursor: UserID | None,
    limit: int = 20,
    exclude_pending: bool = False,
) -> AdminUserListResponse:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    conditions: list[ColumnElement[bool]] = [users.c.deleted_at.is_(None)]
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip()[:100])}%"
        conditions.append(
            or_(
                users.c.username.like(pattern, escape="\\"),
                users.c.display_name.like(pattern, escape="\\"),
                users.c.email.like(pattern, escape="\\"),
            )
        )
    if status is not None:
        conditions.append(users.c.status == status.value)
    if exclude_pending:
        conditions.append(users.c.status != AccountStatus.Pending.value)
    if role is not None:
        conditions.append(users.c.role == role.value)
    if cursor is not None:
        conditions.append(users.c.id < int(cursor))
    rows = [
        user_row_from_mapping(raw)
        for raw in conn.execute(
            select(users).where(and_(*conditions)).order_by(users.c.id.desc()).limit(min(limit, 50) + 1)
        ).mappings()
    ]
    has_more = len(rows) > limit
    page = rows[:limit]
    ids = [row["id"] for row in page]
    banned_ids: set[UserID] = (
        {
            UserID(_int(value, "user_id"))
            for value in conn.execute(
                select(bans.c.user_id).where(bans.c.user_id.in_(ids) & (bans.c.is_active == 1))
            ).scalars()
        }
        if ids
        else set()
    )
    report_counts: dict[UserID, int] = {}
    if ids:
        for raw_id, raw_count in conn.execute(
            select(reports.c.reportable_id, func.count())
            .where((reports.c.reportable_type == "user") & reports.c.reportable_id.in_(ids))
            .group_by(reports.c.reportable_id)
        ):
            report_counts[UserID(_int(raw_id, "reportable_id"))] = _int(raw_count, "count")
    items = [_admin_user(row, UserID(row["id"]) in banned_ids, report_counts.get(UserID(row["id"]), 0)) for row in page]
    return AdminUserListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)


def _log_action(conn: Connection, actor: Actor, action: str, target_id: UserID, reason: str | None) -> None:
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action=action,
            target_type="user",
            target_id=int(target_id),
            reason=reason,
            created_at=now_ms(),
        )
    )


def change_role(
    conn: Connection, actor: Actor, target_id: UserID, role: AdminAssignableRole, reason: str | None
) -> AdminUserResponse:
    assert_can(actor, Abilities.ADMIN_USER_ROLE_UPDATE, None, conn)
    raw = conn.execute(select(users).where(users.c.id == int(target_id))).mappings().first()
    if raw is None:
        raise not_found("User not found")
    target = user_row_from_mapping(raw)
    if target["id"] == actor.id:
        raise conflict("Cannot change your own role")
    if target["role"] == role.value:
        raise conflict("Role is already set")
    if target["role"] == AccountRole.Admin.value and role is not AdminAssignableRole.Admin:
        if _count(conn, users, users.c.role == AccountRole.Admin.value) <= 1:
            raise conflict("Cannot demote the last admin")
    try:
        settings = _SETTINGS.validate_json(target["settings"] or "{}")
    except ValidationError:
        settings = {}
    settings["role_source"] = "local"
    conn.execute(
        update(users)
        .where(users.c.id == int(target_id))
        .values(role=role.value, settings=json.dumps(settings, ensure_ascii=False), updated_at=now_ms())
    )
    _log_action(conn, actor, "user.role.change", target_id, reason or f"{target['role']}->{role.value}")
    return _load_user_full(conn, target_id)


def change_status(
    conn: Connection, actor: Actor, target_id: UserID, status: AdminMutableStatus, reason: str | None
) -> AdminUserResponse:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    raw = conn.execute(select(users).where(users.c.id == int(target_id))).mappings().first()
    if raw is None:
        raise not_found("User not found")
    target = user_row_from_mapping(raw)
    if target["status"] == AccountStatus.Banned.value:
        raise conflict("Banned users must be unbanned first")
    if target["id"] == actor.id:
        raise conflict("Cannot change your own status")
    if target["status"] == status.value:
        raise conflict("Status is already set")
    conn.execute(update(users).where(users.c.id == int(target_id)).values(status=status.value, updated_at=now_ms()))
    _log_action(
        conn,
        actor,
        "user.deactivate" if status is AdminMutableStatus.Deactivated else "user.reactivate",
        target_id,
        reason,
    )
    if status is AdminMutableStatus.Deactivated:
        conn.execute(delete(sessions).where(sessions.c.user_id == int(target_id)))
    return _load_user_full(conn, target_id)


def reset_password(conn: Connection, actor: Actor, target_id: UserID) -> TemporaryPasswordResponse:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    raw = conn.execute(select(users.c.status).where(users.c.id == int(target_id))).scalar_one_or_none()
    if raw is None:
        raise not_found("User not found")
    if _str(raw, "status") == AccountStatus.Banned.value:
        raise conflict("Banned users must be unbanned first")
    temporary_password = secrets.token_urlsafe(12)
    conn.execute(
        update(users)
        .where(users.c.id == int(target_id))
        .values(password_hash=hash_password(temporary_password), updated_at=now_ms())
    )
    delete_user_sessions(conn, int(target_id))
    _log_action(conn, actor, "user.password.reset", target_id, "admin reset")
    return TemporaryPasswordResponse(temporary_password=temporary_password)


def verify_user(conn: Connection, actor: Actor, target_id: UserID) -> AdminUserResponse:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    raw = conn.execute(select(users).where(users.c.id == int(target_id))).mappings().first()
    if raw is None:
        raise not_found("User not found")
    target = user_row_from_mapping(raw)
    if target["status"] == AccountStatus.Banned.value:
        raise conflict("Banned users must be unbanned first")
    if target["status"] == AccountStatus.Active.value and target["email_verified_at"] is not None:
        raise conflict("User already verified")
    timestamp = now_ms()
    conn.execute(
        update(users)
        .where(users.c.id == int(target_id))
        .values(
            status=AccountStatus.Active.value,
            email_verified_at=target["email_verified_at"] or timestamp,
            updated_at=timestamp,
        )
    )
    _log_action(conn, actor, "user.verify", target_id, None)
    return _load_user_full(conn, target_id)


def delete_user(conn: Connection, actor: Actor, target_id: UserID, reason: str | None) -> None:
    assert_can(actor, Abilities.ADMIN_USER_DELETE, None, conn)
    raw = conn.execute(select(users).where(users.c.id == int(target_id))).mappings().first()
    if raw is None:
        raise not_found("User not found")
    target = user_row_from_mapping(raw)
    if target["id"] == actor.id:
        raise conflict("Cannot delete your own account")
    if target["deleted_at"] is not None:
        raise conflict("User is already deleted")
    timestamp = now_ms()
    conn.execute(
        update(users)
        .where(users.c.id == int(target_id))
        .values(
            deleted_at=timestamp,
            status=AccountStatus.Deactivated.value,
            username=f"deleted-{int(target_id)}",
            email=f"deleted-{int(target_id)}@{_EMAIL}",
            display_name="Deleted user",
            bio="",
            avatar_object_key=None,
            updated_at=timestamp,
        )
    )
    conn.execute(delete(sessions).where(sessions.c.user_id == int(target_id)))
    _log_action(conn, actor, "user.delete", target_id, reason)


def _deleted_author(row: RowMapping | None) -> ModerationAuthorResponse | None:
    return _author(user_row_from_mapping(row)) if row is not None else None


def list_deleted_content(
    conn: Connection,
    actor: Actor,
    discussion_cursor: DiscussionID | None,
    reply_cursor: ReplyID | None,
    limit: int = 20,
) -> DeletedContentResponse:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    d_conditions: list[ColumnElement[bool]] = [discussions.c.deleted_at.is_not(None)]
    r_conditions: list[ColumnElement[bool]] = [replies.c.deleted_at.is_not(None)]
    if discussion_cursor is not None:
        d_conditions.append(discussions.c.id < int(discussion_cursor))
    if reply_cursor is not None:
        r_conditions.append(replies.c.id < int(reply_cursor))
    page_limit = min(limit, 50)
    d_rows = list(
        conn.execute(
            select(discussions).where(and_(*d_conditions)).order_by(discussions.c.id.desc()).limit(page_limit + 1)
        ).mappings()
    )
    r_rows = list(
        conn.execute(
            select(replies).where(and_(*r_conditions)).order_by(replies.c.id.desc()).limit(page_limit + 1)
        ).mappings()
    )
    d_more, r_more = len(d_rows) > page_limit, len(r_rows) > page_limit
    d_page, r_page = d_rows[:page_limit], r_rows[:page_limit]
    board_ids = {_int(row["board_id"], "board_id") for row in d_page}
    board_slugs = (
        {
            _int(row["id"], "id"): _str(row["slug"], "slug")
            for row in conn.execute(select(boards.c.id, boards.c.slug).where(boards.c.id.in_(board_ids))).mappings()
        }
        if board_ids
        else {}
    )
    deleter_ids = {_int(row["deleted_by"], "deleted_by") for row in [*d_page, *r_page] if row["deleted_by"] is not None}
    deleters = (
        {
            _int(row["id"], "id"): row
            for row in conn.execute(select(users).where(users.c.id.in_(deleter_ids))).mappings()
        }
        if deleter_ids
        else {}
    )
    parent_ids = {_int(row["discussion_id"], "discussion_id") for row in r_page}
    titles = (
        {
            _int(row["id"], "id"): _str(row["title"], "title")
            for row in conn.execute(
                select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(parent_ids))
            ).mappings()
        }
        if parent_ids
        else {}
    )
    discussions_out = [
        DeletedDiscussionResponse(
            id=DiscussionID(_int(row["id"], "id")),
            board_slug=board_slugs.get(_int(row["board_id"], "board_id"), ""),
            title=_str(row["title"], "title"),
            preview=preview_text(_str(row["body_md"], "body_md")),
            deleted_by=_deleted_author(deleters.get(_int(row["deleted_by"], "deleted_by")))
            if row["deleted_by"] is not None
            else None,
            deleted_at=_int(row["deleted_at"], "deleted_at"),
            reason=_optional_str(row["deletion_reason"], "deletion_reason"),
        )
        for row in d_page
    ]
    replies_out = [
        DeletedReplyResponse(
            id=ReplyID(_int(row["id"], "id")),
            discussion_id=DiscussionID(_int(row["discussion_id"], "discussion_id")),
            discussion_title=titles.get(_int(row["discussion_id"], "discussion_id"), ""),
            preview=preview_text(_str(row["body_md"], "body_md")),
            deleted_by=_deleted_author(deleters.get(_int(row["deleted_by"], "deleted_by")))
            if row["deleted_by"] is not None
            else None,
            deleted_at=_int(row["deleted_at"], "deleted_at"),
            reason=_optional_str(row["deletion_reason"], "deletion_reason"),
        )
        for row in r_page
    ]
    return DeletedContentResponse(
        discussions=discussions_out,
        replies=replies_out,
        next_discussion_cursor=discussions_out[-1].id if d_more and discussions_out else None,
        next_reply_cursor=replies_out[-1].id if r_more and replies_out else None,
    )
