"""治理 service — 镜像 backend/src/moderation/service.ts。

举报 / 报告处理 / 封禁 / 操作日志 / 内容恢复。所有时间戳毫秒 int。
user.banned 在事务内 emit_event（S4 worker 消费→发信+广播）。
"""

from __future__ import annotations

from sqlalchemy import and_, delete, select, update
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can
from .db import now_ms
from .errors import conflict, internal_error, not_found
from .outbox import emit_event
from .schema import bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from .users import make_handle, normalize_username

_REPORT_STATUSES = {"open", "in_progress", "resolved", "dismissed"}
_TARGET_TYPES = {"discussion", "reply", "user"}


def _author_ref(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
    }


def _report_dto(row: dict, reporter: dict | None, target: dict | None) -> dict:
    dto = {
        "id": row["id"],
        "reporter": _author_ref(reporter) if reporter else None,
        "reportableType": row["reportable_type"],
        "reportableId": row["reportable_id"],
        "reason": row["reason"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }
    if target is not None:
        dto["target"] = target
    return dto


def _preview(md: str) -> str:
    flat = " ".join(md.split()).strip()
    return flat[:160] + "…" if len(flat) > 160 else flat


# ---------------------------------------------------------------- reports

def create_report(conn: Connection, actor, reportable_type: str, reportable_id: int, reason: str | None) -> dict:
    if actor is None:
        raise internal_error()
    if reportable_type not in _TARGET_TYPES:
        raise internal_error()  # 路由 zod 已挡，防御
    assert_can(actor, Abilities.REPORT_CREATE, None, conn)
    _now = now_ms()
    res = conn.execute(
        reports.insert().values(
            reporter_user_id=actor.id,
            reportable_type=reportable_type,
            reportable_id=reportable_id,
            reason=reason,
            status="open",
            created_at=_now,
        )
    )
    row = conn.execute(select(reports).where(reports.c.id == res.inserted_primary_key[0])).first()
    row_d = dict(row._mapping)
    reporter = dict(
        conn.execute(select(users).where(users.c.id == row_d["reporter_user_id"])).first()._mapping
    )
    return _report_dto(row_d, reporter, _target_for(conn, reportable_type, reportable_id))


def _target_for(conn: Connection, reportable_type: str, reportable_id: int) -> dict | None:
    if reportable_type == "discussion":
        d = conn.execute(select(discussions).where(discussions.c.id == reportable_id)).first()
        if d is None:
            return None
        board = conn.execute(select(boards.c.slug).where(boards.c.id == d.board_id)).first()
        return {"type": "discussion", "id": d.id, "title": d.title, "boardSlug": board[0] if board else ""}
    if reportable_type == "reply":
        r = conn.execute(select(replies).where(replies.c.id == reportable_id)).first()
        if r is None:
            return None
        return {"type": "reply", "id": r.id, "discussionId": r.discussion_id}
    if reportable_type == "user":
        u = conn.execute(select(users).where(users.c.id == reportable_id)).first()
        if u is None:
            return None
        return {
            "type": "user",
            "id": u.id,
            "username": u.username,
            "handle": make_handle(u.username, u.discriminator),
            "displayName": u.display_name,
        }
    return None


def list_reports(conn: Connection, actor, status: str | None, cursor: int | None, limit: int = 20) -> dict:
    assert_can(actor, Abilities.MODERATION_VIEW, None, conn)
    limit = min(limit, 50)
    conds = []
    if status:
        conds.append(reports.c.status == status)
    if cursor is not None:
        conds.append(reports.c.id < cursor)
    stmt = select(reports)
    if conds:
        stmt = stmt.where(and_(*conds))
    rows = conn.execute(stmt.order_by(reports.c.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    reporter_ids = [r.reporter_user_id for r in page]
    reporters = {}
    if reporter_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(reporter_ids))).all():
            reporters[u.id] = dict(u._mapping)
    items = [
        _report_dto(
            dict(r._mapping),
            reporters.get(r.reporter_user_id),
            _target_for(conn, r.reportable_type, r.reportable_id),
        )
        for r in page
    ]
    return {"items": items, "nextCursor": items[-1]["id"] if has_more and items else None}


def resolve_report(conn: Connection, actor, report_id: int, status: str, action: str | None, reason: str | None) -> dict:
    assert_can(actor, Abilities.MODERATION_RESOLVE, None, conn)
    row = conn.execute(select(reports).where(reports.c.id == report_id)).first()
    if row is None:
        raise not_found("Report not found")
    row_d = dict(row._mapping)
    conn.execute(update(reports).where(reports.c.id == report_id).values(status=status))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action=action or f"report.{status}",
            target_type="report",
            target_id=report_id,
            reason=reason,
            created_at=now_ms(),
        )
    )
    reporter = dict(
        conn.execute(select(users).where(users.c.id == row_d["reporter_user_id"])).first()._mapping
    )
    return _report_dto(
        {**row_d, "status": status},
        reporter,
        _target_for(conn, row_d["reportable_type"], row_d["reportable_id"]),
    )


# ---------------------------------------------------------------- bans

def ban_user(conn: Connection, actor, username: str, reason: str | None, duration_hours: int | None) -> None:
    assert_can(actor, Abilities.USER_BAN, None, conn)
    wanted = normalize_username(username)
    target = conn.execute(
        select(users).where((users.c.username == wanted) & (users.c.deleted_at.is_(None)))
    ).first()
    if target is None:
        raise not_found("User not found")
    if target.id == actor.id:
        raise conflict("Cannot ban yourself")
    if target.role == "admin":
        raise conflict("Cannot ban an admin")
    # 角色层级：只有 admin 能封 moderator（解封仅 admin），防恶意 moderator 横向清掉同僚
    if target.role == "moderator" and actor.role != "admin":
        raise conflict("Cannot ban a moderator")
    _now = now_ms()
    banned_until = _now + duration_hours * 3600 * 1000 if duration_hours else None
    banned_until_iso = _iso(banned_until) if banned_until else None
    conn.execute(
        bans.insert().values(
            user_id=target.id,
            banned_by_user_id=actor.id,
            reason=reason,
            banned_until=banned_until,
            is_active=1,
            created_at=_now,
        )
    )
    conn.execute(update(users).where(users.c.id == target.id).values(status="banned", updated_at=_now))
    conn.execute(delete(sessions).where(sessions.c.user_id == target.id))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="user.ban",
            target_type="user",
            target_id=target.id,
            reason=reason,
            created_at=_now,
        )
    )
    emit_event(
        conn,
        "user.banned",
        aggregate_type="user",
        aggregate_id=str(target.id),
        payload={
            "userId": target.id,
            "bannedByUserId": actor.id,
            "reason": reason,
            "bannedUntil": banned_until_iso,
        },
    )


def unban_user(conn: Connection, actor, username: str, reason: str | None) -> None:
    assert_can(actor, Abilities.MODERATION_UNBAN, None, conn)
    wanted = normalize_username(username)
    target = conn.execute(
        select(users).where((users.c.username == wanted) & (users.c.deleted_at.is_(None)))
    ).first()
    if target is None:
        raise not_found("User not found")
    conn.execute(
        update(bans)
        .where((bans.c.user_id == target.id) & (bans.c.is_active == 1))
        .values(is_active=0)
    )
    conn.execute(update(users).where(users.c.id == target.id).values(status="active", updated_at=now_ms()))
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="user.unban",
            target_type="user",
            target_id=target.id,
            reason=reason,
            created_at=now_ms(),
        )
    )


def lift_ban_if_expired(conn: Connection, user_id: int) -> bool:
    """用户处于 banned 状态时调用：若所有 active 封禁都已到期(仅临时、无未到期/永久项)，
    自动解封(is_active=0 + status=active)。返回 True 表示已恢复 active；False 维持封禁。

    临时封禁的过期由这里惰性判定——登录 / 会话 gate 在 banned 分支调用即可，
    无需定时任务。调用方在返回 True 后要把内存里的 status 视为 active。
    """
    _now = now_ms()
    active = conn.execute(
        select(bans.c.banned_until).where((bans.c.user_id == user_id) & (bans.c.is_active == 1))
    ).all()
    if not active:
        return False
    # 存在任一"仍在生效"的封禁(永久 banned_until IS NULL，或还未到期) → 不解封
    if any(r.banned_until is None or r.banned_until > _now for r in active):
        return False
    conn.execute(
        update(bans)
        .where((bans.c.user_id == user_id) & (bans.c.is_active == 1))
        .values(is_active=0)
    )
    conn.execute(update(users).where(users.c.id == user_id).values(status="active", updated_at=now_ms()))
    return True


# ---------------------------------------------------------------- actions log

def _action_dto(row: dict, actor_row: dict | None) -> dict:
    return {
        "id": row["id"],
        "actor": _author_ref(actor_row) if actor_row else None,
        "action": row["action"],
        "targetType": row["target_type"],
        "targetId": row["target_id"],
        "reason": row["reason"],
        "createdAt": row["created_at"],
    }


def list_actions(conn: Connection, actor, cursor: int | None, limit: int = 20) -> dict:
    assert_can(actor, Abilities.MODERATION_VIEW, None, conn)
    limit = min(limit, 50)
    stmt = select(moderation_actions)
    if cursor is not None:
        stmt = stmt.where(moderation_actions.c.id < cursor)
    rows = conn.execute(stmt.order_by(moderation_actions.c.id.desc()).limit(limit + 1)).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    actor_ids = [r.actor_user_id for r in page]
    actors = {}
    if actor_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(actor_ids))).all():
            actors[u.id] = dict(u._mapping)
    items = [_action_dto(dict(r._mapping), actors.get(r.actor_user_id)) for r in page]
    return {"items": items, "nextCursor": items[-1]["id"] if has_more and items else None}


# ---------------------------------------------------------------- restore

def restore_content(conn: Connection, actor, target_type: str, target_id: int, reason: str | None) -> None:
    assert_can(actor, Abilities.MODERATION_RESOLVE, None, conn)
    _now = now_ms()
    if target_type == "discussion":
        d = conn.execute(select(discussions.c.id).where(discussions.c.id == target_id)).first()
        if d is None:
            raise not_found("Discussion not found")
        conn.execute(
            update(discussions)
            .where(discussions.c.id == target_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=_now)
        )
    elif target_type == "reply":
        r = conn.execute(select(replies.c.id).where(replies.c.id == target_id)).first()
        if r is None:
            raise not_found("Reply not found")
        conn.execute(
            update(replies)
            .where(replies.c.id == target_id)
            .values(deleted_at=None, deleted_by=None, deletion_reason=None, updated_at=_now)
        )
    else:
        raise conflict("Unsupported target type")
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action="content.restore",
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            created_at=_now,
        )
    )


def _iso(ms: int) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat().replace("+00:00", "Z")


# 供 admin list_deleted 复用
def preview_text(md: str) -> str:
    return _preview(md)
