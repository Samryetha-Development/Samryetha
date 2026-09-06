"""管理后台 service — 镜像 backend/src/admin/service.ts。

stats / 用户管理 / 删除内容清单。only admins（ability）。onlineNow 需 presence 存储。
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can
from .db import now_ms
from .security import delete_user_sessions, hash_password
from .errors import conflict, internal_error, not_found
from .moderation import preview_text
from .schema import bans, boards, discussions, moderation_actions, replies, reports, sessions, users
from .users import make_handle

_EMAIL = "samryetha.local"


def _start_of_today_ms() -> int:
    now = datetime.now(timezone.utc).astimezone()  # local tz
    local_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)


def _author_ref(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
    }


def _user_dto(row: dict, ban_active: bool, report_count: int) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "status": row["status"],
        "emailVerified": row.get("email_verified_at") is not None,
        "createdAt": row["created_at"],
        "lastSeenAt": row["last_seen_at"],
        "banActive": ban_active,
        "reportCount": report_count,
    }


def _load_user_full(conn: Connection, user_id: int) -> dict | None:
    row = conn.execute(select(users).where(users.c.id == user_id)).first()
    if row is None:
        return None
    row_d = dict(row._mapping)
    ban = conn.execute(
        select(bans.c.id).where((bans.c.user_id == user_id) & (bans.c.is_active == 1))
    ).first()
    report_count = conn.execute(
        select(func.count())
        .select_from(reports)
        .where((reports.c.reportable_type == "user") & (reports.c.reportable_id == user_id))
    ).scalar() or 0
    return _user_dto(row_d, ban is not None, report_count)


# ---------------------------------------------------------------- stats

def stats(conn: Connection, actor, presence) -> dict:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    today = _start_of_today_ms()
    status_rows = conn.execute(select(users.c.status, func.count()).group_by(users.c.status)).all()
    dist = {"total": 0, "pending": 0, "active": 0, "banned": 0, "deactivated": 0}
    for row in status_rows:
        dist[row[0]] = row[1]
        dist["total"] += row[1]

    def count(tbl, *extra):
        stmt = select(func.count()).select_from(tbl)
        if extra:
            stmt = stmt.where(and_(*extra))
        return conn.execute(stmt).scalar() or 0

    discussions_live = discussions.c.deleted_at.is_(None)
    replies_live = replies.c.deleted_at.is_(None)
    boards_live = boards.c.deleted_at.is_(None)
    active_bans = bans.c.is_active == 1
    open_reports = reports.c.status.in_(["open", "in_progress"])

    authors_today = set()
    for col in (discussions.c.author_id, replies.c.author_id):
        tbl = discussions if col is discussions.c.author_id else replies
        live = discussions_live if col is discussions.c.author_id else replies_live
        rows = conn.execute(select(col).where(and_(tbl.c.created_at > today, live))).all()
        authors_today.update(r[0] for r in rows)

    return {
        "users": dist,
        "content": {
            "discussions": count(discussions, discussions_live),
            "replies": count(replies, replies_live),
            "boards": count(boards, boards_live),
        },
        "moderation": {
            "openReports": count(reports, open_reports),
            "activeBans": count(bans, active_bans),
        },
        "activity": {
            "activeToday": len(authors_today),
            "newUsersToday": count(users, users.c.created_at > today),
            "newDiscussionsToday": count(discussions, and_(discussions.c.created_at > today, discussions_live)),
            "newRepliesToday": count(replies, and_(replies.c.created_at > today, replies_live)),
            "onlineNow": presence.online_count(),
        },
    }


# ---------------------------------------------------------------- users list

def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_users(conn: Connection, actor, q: str | None, status: str | None, role: str | None, cursor: int | None, limit: int = 20) -> dict:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    limit = min(limit, 50)
    conds = [users.c.deleted_at.is_(None)]
    if q and q.strip():
        qs = _escape_like(q.strip()[:100])
        pat = f"%{qs}%"
        conds.append(or_(users.c.username.like(pat, escape="\\"), users.c.display_name.like(pat, escape="\\"), users.c.email.like(pat, escape="\\")))
    if status:
        conds.append(users.c.status == status)
    if role:
        conds.append(users.c.role == role)
    if cursor is not None:
        conds.append(users.c.id < cursor)
    rows = conn.execute(
        select(users).where(and_(*conds)).order_by(users.c.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    ids = [r.id for r in page]
    ban_ids = set()
    report_counts: dict[int, int] = {}
    if ids:
        for r in conn.execute(select(bans.c.user_id).where((bans.c.user_id.in_(ids)) & (bans.c.is_active == 1))).all():
            ban_ids.add(r[0])
        for r in conn.execute(
            select(reports.c.reportable_id, func.count())
            .where((reports.c.reportable_type == "user") & (reports.c.reportable_id.in_(ids)))
            .group_by(reports.c.reportable_id)
        ).all():
            report_counts[r[0]] = r[1]
    items = [_user_dto(dict(r._mapping), r.id in ban_ids, report_counts.get(r.id, 0)) for r in page]
    return {"items": items, "nextCursor": items[-1]["id"] if has_more and items else None}


# ---------------------------------------------------------------- actions

def _log_action(conn: Connection, actor, action: str, target_type: str, target_id: int, reason: str | None) -> None:
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            created_at=now_ms(),
        )
    )


def change_role(conn: Connection, actor, target_id: int, role: str, reason: str | None) -> dict:
    assert_can(actor, Abilities.ADMIN_USER_ROLE_UPDATE, None, conn)
    target = conn.execute(select(users).where(users.c.id == target_id)).first()
    if target is None:
        raise not_found("User not found")
    if target.id == actor.id:
        raise conflict("Cannot change your own role")
    if target.role == role:
        raise conflict("Role is already set")
    if target.role == "admin" and role != "admin":
        admin_count = conn.execute(select(func.count()).select_from(users).where(users.c.role == "admin")).scalar() or 0
        if admin_count <= 1:
            raise conflict("Cannot demote the last admin")
    conn.execute(update(users).where(users.c.id == target_id).values(role=role, updated_at=now_ms()))
    _log_action(conn, actor, "user.role.change", "user", target_id, reason or f"{target.role}->{role}")
    return _load_user_full(conn, target_id) or {}


def change_status(conn: Connection, actor, target_id: int, status: str, reason: str | None) -> dict:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    target = conn.execute(select(users).where(users.c.id == target_id)).first()
    if target is None:
        raise not_found("User not found")
    if target.status == "banned":
        raise conflict("Banned users must be unbanned first")
    if target.id == actor.id:
        raise conflict("Cannot change your own status")
    if target.status == status:
        raise conflict("Status is already set")
    _now = now_ms()
    conn.execute(update(users).where(users.c.id == target_id).values(status=status, updated_at=_now))
    action = "user.deactivate" if status == "deactivated" else "user.reactivate"
    _log_action(conn, actor, action, "user", target_id, reason)
    if status == "deactivated":
        conn.execute(delete(sessions).where(sessions.c.user_id == target_id))
    return _load_user_full(conn, target_id) or {}


def reset_password(conn: Connection, actor, target_id: int) -> dict:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    target = conn.execute(select(users).where(users.c.id == target_id)).first()
    if target is None:
        raise not_found("User not found")
    if target.status == "banned":
        raise conflict("Banned users must be unbanned first")
    temporary_password = secrets.token_urlsafe(12)
    conn.execute(
        update(users)
        .where(users.c.id == target_id)
        .values(password_hash=hash_password(temporary_password), updated_at=now_ms())
    )
    delete_user_sessions(conn, target_id)
    _log_action(conn, actor, "user.password.reset", "user", target_id, "admin reset")
    return {"temporaryPassword": temporary_password}


def verify_user(conn: Connection, actor, target_id: int) -> dict:
    assert_can(actor, Abilities.ADMIN_USER_STATUS_UPDATE, None, conn)
    target = conn.execute(select(users).where(users.c.id == target_id)).first()
    if target is None:
        raise not_found("User not found")
    if target.status == "banned":
        raise conflict("Banned users must be unbanned first")
    if target.status == "active" and target.email_verified_at is not None:
        raise conflict("User already verified")
    _now = now_ms()
    conn.execute(
        update(users)
        .where(users.c.id == target_id)
        .values(status="active", email_verified_at=target.email_verified_at or _now, updated_at=_now)
    )
    _log_action(conn, actor, "user.verify", "user", target_id, None)
    return _load_user_full(conn, target_id) or {}


def delete_user(conn: Connection, actor, target_id: int, reason: str | None) -> None:
    assert_can(actor, Abilities.ADMIN_USER_DELETE, None, conn)
    target = conn.execute(select(users).where(users.c.id == target_id)).first()
    if target is None:
        raise not_found("User not found")
    if target.id == actor.id:
        raise conflict("Cannot delete your own account")
    if target.deleted_at is not None:
        raise conflict("User is already deleted")
    _now = now_ms()
    conn.execute(
        update(users)
        .where(users.c.id == target_id)
        .values(
            deleted_at=_now,
            status="deactivated",
            username=f"deleted-{target_id}",
            email=f"deleted-{target_id}@{_EMAIL}",
            display_name="Deleted user",
            bio="",
            avatar_object_key=None,
            updated_at=_now,
        )
    )
    conn.execute(delete(sessions).where(sessions.c.user_id == target_id))
    _log_action(conn, actor, "user.delete", "user", target_id, reason)


# ---------------------------------------------------------------- deleted content

def list_deleted_content(conn: Connection, actor, discussion_cursor: int | None, reply_cursor: int | None, limit: int = 20) -> dict:
    assert_can(actor, Abilities.ADMIN_VIEW, None, conn)
    limit = min(limit, 50)

    d_conds = [discussions.c.deleted_at.is_not(None)]
    if discussion_cursor is not None:
        d_conds.append(discussions.c.id < discussion_cursor)
    r_conds = [replies.c.deleted_at.is_not(None)]
    if reply_cursor is not None:
        r_conds.append(replies.c.id < reply_cursor)

    d_rows = conn.execute(select(discussions).where(and_(*d_conds)).order_by(discussions.c.id.desc()).limit(limit + 1)).all()
    r_rows = conn.execute(select(replies).where(and_(*r_conds)).order_by(replies.c.id.desc()).limit(limit + 1)).all()

    def slice_rows(rows):
        more = len(rows) > limit
        return (rows[:limit], more)

    d_page, d_more = slice_rows(d_rows)
    r_page, r_more = slice_rows(r_rows)

    board_slugs = {}
    if d_page:
        ids = list({r.board_id for r in d_page})
        for b in conn.execute(select(boards.c.id, boards.c.slug).where(boards.c.id.in_(ids))).all():
            board_slugs[b.id] = b.slug
    deleter_ids = list({r.deleted_by for r in list(d_page) + list(r_page) if r.deleted_by is not None})
    deleters = {}
    if deleter_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(deleter_ids))).all():
            deleters[u.id] = dict(u._mapping)
    parent_titles = {}
    if r_page:
        pids = list({r.discussion_id for r in r_page})
        for d in conn.execute(select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(pids))).all():
            parent_titles[d.id] = d.title

    discussions_out = [
        {
            "id": r.id,
            "boardSlug": board_slugs.get(r.board_id, ""),
            "title": r.title,
            "preview": preview_text(r.body_md),
            "deletedBy": _author_ref(deleters[r.deleted_by]) if r.deleted_by and r.deleted_by in deleters else None,
            "deletedAt": r.deleted_at,
            "reason": r.deletion_reason,
        }
        for r in d_page
    ]
    replies_out = [
        {
            "id": r.id,
            "discussionId": r.discussion_id,
            "discussionTitle": parent_titles.get(r.discussion_id, ""),
            "preview": preview_text(r.body_md),
            "deletedBy": _author_ref(deleters[r.deleted_by]) if r.deleted_by and r.deleted_by in deleters else None,
            "deletedAt": r.deleted_at,
            "reason": r.deletion_reason,
        }
        for r in r_page
    ]
    return {
        "discussions": discussions_out,
        "replies": replies_out,
        "nextDiscussionCursor": discussions_out[-1]["id"] if d_more and discussions_out else None,
        "nextReplyCursor": replies_out[-1]["id"] if r_more and replies_out else None,
    }
