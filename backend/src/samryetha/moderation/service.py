"""Typed reports, bans, moderation actions, and content restoration service."""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..authz import Abilities, Actor, assert_can
from ..core.db import now_ms
from ..core.errors import conflict, internal_error, not_found
from ..core.ids import DiscussionID, ModerationActionID, ReplyID, ReportID, UserID
from .models import (
    DiscussionReportTarget,
    ModerationActionListResponse,
    ModerationActionResponse,
    ModerationAuthorResponse,
    ReplyReportTarget,
    ReportListResponse,
    ReportResponse,
    ReportableType,
    ReportStatus,
    ReportTarget,
    RestoreTargetType,
    UserReportTarget,
)
from ..notifications.models import UserBannedPayload
from ..events.outbox import emit_event
from ..core.schema import attachments, bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from ..users import make_handle, normalize_username
from ..core.records import opt_str, require_int, require_str

@dataclass(frozen=True, slots=True)
class _Author:
    id: UserID
    username: str
    discriminator: int
    display_name: str

@dataclass(frozen=True, slots=True)
class _Report:
    id: ReportID
    reporter_user_id: UserID
    reportable_type: ReportableType
    reportable_id: int
    reason: str | None
    status: ReportStatus
    created_at: int

@dataclass(frozen=True, slots=True)
class _Action:
    id: ModerationActionID
    actor_user_id: UserID
    action: str
    target_type: str
    target_id: int
    reason: str | None
    created_at: int

def _author(row: RowMapping) -> _Author:
    return _Author(
        UserID(require_int(row["id"], "id")),
        require_str(row["username"], "username"),
        require_int(row["discriminator"], "discriminator"),
        require_str(row["display_name"], "display_name"),
    )

def _report(row: RowMapping) -> _Report:
    return _Report(
        ReportID(require_int(row["id"], "id")),
        UserID(require_int(row["reporter_user_id"], "reporter_user_id")),
        ReportableType(require_str(row["reportable_type"], "reportable_type")),
        require_int(row["reportable_id"], "reportable_id"),
        opt_str(row["reason"], "reason"),
        ReportStatus(require_str(row["status"], "status")),
        require_int(row["created_at"], "created_at"),
    )

def _action(row: RowMapping) -> _Action:
    return _Action(
        ModerationActionID(require_int(row["id"], "id")),
        UserID(require_int(row["actor_user_id"], "actor_user_id")),
        require_str(row["action"], "action"),
        require_str(row["target_type"], "target_type"),
        require_int(row["target_id"], "target_id"),
        opt_str(row["reason"], "reason"),
        require_int(row["created_at"], "created_at"),
    )

def _author_response(row: _Author) -> ModerationAuthorResponse:
    return ModerationAuthorResponse(
        id=row.id,
        username=row.username,
        handle=make_handle(row.username, row.discriminator),
        display_name=row.display_name,
    )

def _report_response(row: _Report, reporter: _Author | None, target: ReportTarget | None) -> ReportResponse:
    return ReportResponse(
        id=row.id,
        reporter=_author_response(reporter) if reporter else None,
        reportable_type=row.reportable_type,
        reportable_id=row.reportable_id,
        reason=row.reason,
        status=row.status,
        created_at=row.created_at,
        target=target,
    )

def _preview(md: str) -> str:
    flat = " ".join(md.split()).strip()
    return flat[:160] + "…" if len(flat) > 160 else flat

def _load_author(conn: Connection, user_id: UserID) -> _Author | None:
    row = conn.execute(select(users).where(users.c.id == int(user_id))).mappings().first()
    return _author(row) if row is not None else None

def create_report(
    conn: Connection, actor: Actor, reportable_type: ReportableType, reportable_id: int, reason: str | None
) -> ReportResponse:
    assert_can(actor, Abilities.REPORT_CREATE, None, conn)
    result = conn.execute(
        reports.insert().values(
            reporter_user_id=actor.id,
            reportable_type=reportable_type.value,
            reportable_id=reportable_id,
            reason=reason,
            status=ReportStatus.Open.value,
            created_at=now_ms(),
        )
    )
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise internal_error()
    inserted = require_int(primary_key[0], "inserted report id")
    raw = conn.execute(select(reports).where(reports.c.id == inserted)).mappings().first()
    if raw is None:
        raise internal_error()
    row = _report(raw)
    return _report_response(
        row, _load_author(conn, row.reporter_user_id), _target_for(conn, reportable_type, reportable_id)
    )

def _target_for(conn: Connection, kind: ReportableType, target_id: int) -> ReportTarget | None:
    if kind is ReportableType.Discussion:
        row = conn.execute(select(discussions).where(discussions.c.id == target_id)).mappings().first()
        if row is None:
            return None
        slug = conn.execute(select(boards.c.slug).where(boards.c.id == row["board_id"])).scalar_one_or_none()
        return DiscussionReportTarget(
            id=DiscussionID(require_int(row["id"], "id")),
            title=require_str(row["title"], "title"),
            board_slug=slug if isinstance(slug, str) else "",
        )
    if kind is ReportableType.Reply:
        row = conn.execute(select(replies).where(replies.c.id == target_id)).mappings().first()
        if row is None:
            return None
        return ReplyReportTarget(
            id=ReplyID(require_int(row["id"], "id")), discussion_id=DiscussionID(require_int(row["discussion_id"], "discussion_id"))
        )
    row = conn.execute(select(users).where(users.c.id == target_id)).mappings().first()
    if row is None:
        return None
    author = _author(row)
    return UserReportTarget(
        id=author.id,
        username=author.username,
        handle=make_handle(author.username, author.discriminator),
        display_name=author.display_name,
    )

def list_reports(
    conn: Connection, actor: Actor, status: ReportStatus | None, cursor: ReportID | None, limit: int = 20
) -> ReportListResponse:
    assert_can(actor, Abilities.MODERATION_VIEW, None, conn)
    conditions: list[ColumnElement[bool]] = []
    if status is not None:
        conditions.append(reports.c.status == status.value)
    if cursor is not None:
        conditions.append(reports.c.id < int(cursor))
    statement = select(reports)
    if conditions:
        statement = statement.where(and_(*conditions))
    records = [
        _report(row)
        for row in conn.execute(statement.order_by(reports.c.id.desc()).limit(min(limit, 50) + 1)).mappings()
    ]
    has_more = len(records) > limit
    page = records[:limit]
    reporter_ids = {row.reporter_user_id for row in page}
    reporters = (
        {
            author.id: author
            for author in (
                _author(raw)
                for raw in conn.execute(
                    select(users).where(users.c.id.in_([int(value) for value in reporter_ids]))
                ).mappings()
            )
        }
        if reporter_ids
        else {}
    )
    items = [
        _report_response(
            row, reporters.get(row.reporter_user_id), _target_for(conn, row.reportable_type, row.reportable_id)
        )
        for row in page
    ]
    return ReportListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)

def resolve_report(
    conn: Connection, actor: Actor, report_id: ReportID, status: ReportStatus, action: str | None, reason: str | None
) -> ReportResponse:
    assert_can(actor, Abilities.MODERATION_RESOLVE, None, conn)
    raw = conn.execute(select(reports).where(reports.c.id == int(report_id))).mappings().first()
    if raw is None:
        raise not_found("Report not found")
    row = _report(raw)
    conn.execute(update(reports).where(reports.c.id == int(report_id)).values(status=status.value))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action=action or f"report.{status.value}",
            target_type="report",
            target_id=int(report_id),
            reason=reason,
            created_at=now_ms(),
        )
    )
    updated = _Report(
        row.id, row.reporter_user_id, row.reportable_type, row.reportable_id, row.reason, status, row.created_at
    )
    return _report_response(
        updated, _load_author(conn, row.reporter_user_id), _target_for(conn, row.reportable_type, row.reportable_id)
    )

def ban_user(conn: Connection, actor: Actor, username: str, reason: str | None, duration_hours: int | None) -> None:
    assert_can(actor, Abilities.USER_BAN, None, conn)
    raw = (
        conn.execute(
            select(users).where((users.c.username == normalize_username(username)) & users.c.deleted_at.is_(None))
        )
        .mappings()
        .first()
    )
    if raw is None:
        raise not_found("User not found")
    target_id = UserID(require_int(raw["id"], "id"))
    target_role = require_str(raw["role"], "role")
    if int(target_id) == actor.id:
        raise conflict("Cannot ban yourself")
    if target_role == "admin":
        raise conflict("Cannot ban an admin")
    if target_role == "moderator" and actor.role != "admin":
        raise conflict("Cannot ban a moderator")
    timestamp = now_ms()
    banned_until = timestamp + duration_hours * 3_600_000 if duration_hours else None
    conn.execute(
        bans.insert().values(
            user_id=int(target_id),
            banned_by_user_id=actor.id,
            reason=reason,
            banned_until=banned_until,
            is_active=1,
            created_at=timestamp,
        )
    )
    conn.execute(update(users).where(users.c.id == int(target_id)).values(status="banned", updated_at=timestamp))
    conn.execute(delete(sessions).where(sessions.c.user_id == int(target_id)))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="user.ban",
            target_type="user",
            target_id=int(target_id),
            reason=reason,
            created_at=timestamp,
        )
    )
    emit_event(
        conn,
        "user.banned",
        aggregate_type="user",
        aggregate_id=str(int(target_id)),
        payload=UserBannedPayload(
            user_id=target_id,
            banned_by_user_id=UserID(actor.id),
            reason=reason,
            banned_until=_iso(banned_until) if banned_until is not None else None,
        ),
    )

def unban_user(conn: Connection, actor: Actor, username: str, reason: str | None) -> None:
    assert_can(actor, Abilities.MODERATION_UNBAN, None, conn)
    raw = (
        conn.execute(
            select(users.c.id).where((users.c.username == normalize_username(username)) & users.c.deleted_at.is_(None))
        )
        .mappings()
        .first()
    )
    if raw is None:
        raise not_found("User not found")
    target_id = UserID(require_int(raw["id"], "id"))
    conn.execute(update(bans).where((bans.c.user_id == int(target_id)) & (bans.c.is_active == 1)).values(is_active=0))
    conn.execute(update(users).where(users.c.id == int(target_id)).values(status="active", updated_at=now_ms()))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="user.unban",
            target_type="user",
            target_id=int(target_id),
            reason=reason,
            created_at=now_ms(),
        )
    )

def lift_ban_if_expired(conn: Connection, user_id: int) -> bool:
    timestamp = now_ms()
    active = conn.execute(
        select(bans.c.banned_until).where((bans.c.user_id == user_id) & (bans.c.is_active == 1))
    ).all()
    if not active or any(row.banned_until is None or row.banned_until > timestamp for row in active):
        return False
    conn.execute(update(bans).where((bans.c.user_id == user_id) & (bans.c.is_active == 1)).values(is_active=0))
    conn.execute(update(users).where(users.c.id == user_id).values(status="active", updated_at=now_ms()))
    return True

def list_actions(
    conn: Connection, actor: Actor, cursor: ModerationActionID | None, limit: int = 20
) -> ModerationActionListResponse:
    assert_can(actor, Abilities.MODERATION_VIEW, None, conn)
    statement = select(moderation_actions)
    if cursor is not None:
        statement = statement.where(moderation_actions.c.id < int(cursor))
    records = [
        _action(row)
        for row in conn.execute(statement.order_by(moderation_actions.c.id.desc()).limit(min(limit, 50) + 1)).mappings()
    ]
    has_more = len(records) > limit
    page = records[:limit]
    actor_ids = {row.actor_user_id for row in page}
    actors = (
        {
            author.id: author
            for author in (
                _author(raw)
                for raw in conn.execute(
                    select(users).where(users.c.id.in_([int(value) for value in actor_ids]))
                ).mappings()
            )
        }
        if actor_ids
        else {}
    )
    items = [
        ModerationActionResponse(
            id=row.id,
            actor=_author_response(actors[row.actor_user_id]) if row.actor_user_id in actors else None,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in page
    ]
    return ModerationActionListResponse(items=items, next_cursor=items[-1].id if has_more and items else None)

def restore_content(
    conn: Connection, actor: Actor, target_type: RestoreTargetType, target_id: int, reason: str | None
) -> None:
    assert_can(actor, Abilities.MODERATION_RESOLVE, None, conn)
    timestamp = now_ms()
    if target_type is RestoreTargetType.Discussion:
        row = conn.execute(
            select(discussions.c.id, discussions.c.deleted_at).where(discussions.c.id == target_id)
        ).first()
        if row is None:
            raise not_found("Discussion not found")
        was_deleted = row.deleted_at is not None
        conn.execute(
            update(discussions)
            .where(discussions.c.id == target_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=timestamp)
        )
        if was_deleted:
            conn.execute(
                update(attachments)
                .where((attachments.c.discussion_id == target_id) & (attachments.c.state == "orphaned"))
                .values(state="attached")
            )
    else:
        row = conn.execute(select(replies).where(replies.c.id == target_id)).first()
        if row is None:
            raise not_found("Reply not found")
        was_deleted = row.deleted_at is not None
        conn.execute(
            update(replies)
            .where(replies.c.id == target_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=timestamp)
        )
        if was_deleted:
            conn.execute(
                update(discussions)
                .where(discussions.c.id == row.discussion_id)
                .values(reply_count=discussions.c.reply_count + 1)
            )
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="content.restore",
            target_type=target_type.value,
            target_id=target_id,
            reason=reason,
            created_at=timestamp,
        )
    )

def _iso(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")

def preview_text(md: str) -> str:
    return _preview(md)
