"""通知 service — 镜像 backend/src/notifications/service.ts。

notifications 表：type 存 reply|mention|follow|system|moderation|ban；
is_read 0/1 int；read_at/created_at 毫秒 int。actor 可空，DTO 输出 camelCase。
"""

from __future__ import annotations

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import not_found
from .schema import notifications, users
from .users import make_handle


def create(
    conn: Connection,
    *,
    user_id: int,
    actor_user_id: int | None = None,
    type_: str,
    discussion_id: int | None = None,
    reply_id: int | None = None,
    body: str | None = None,
    source_event_id: int | None = None,
) -> int:
    res = conn.execute(
        notifications.insert().values(
            user_id=user_id,
            actor_user_id=actor_user_id,
            type=type_,
            discussion_id=discussion_id,
            reply_id=reply_id,
            body=body,
            source_event_id=source_event_id,
            is_read=0,
            created_at=now_ms(),
        )
    )
    return res.inserted_primary_key[0]


def _actor_map(conn: Connection, actor_ids: list[int]) -> dict[int, dict]:
    if not actor_ids:
        return {}
    rows = conn.execute(select(users).where(users.c.id.in_(actor_ids))).all()
    return {r.id: dict(r._mapping) for r in rows}


def _dto(row: dict, actor_row: dict | None) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "actor": (
            {
                "id": actor_row["id"],
                "username": actor_row["username"],
                "handle": make_handle(actor_row["username"], actor_row["discriminator"]),
                "displayName": actor_row["display_name"],
            }
            if actor_row is not None
            else None
        ),
        "body": row["body"],
        "discussionId": row["discussion_id"],
        "replyId": row["reply_id"],
        "isRead": row["is_read"] == 1,
        "createdAt": row["created_at"],
    }


def list(
    conn: Connection,
    user_id: int,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> dict:
    limit = min(limit, 50)
    conds = [notifications.c.user_id == user_id]
    if unread_only:
        conds.append(notifications.c.is_read == 0)
    cursor_id: int | None = None
    if cursor is not None:
        try:
            cursor_id = int(cursor)
        except (TypeError, ValueError):
            cursor_id = None
        if cursor_id is not None:
            conds.append(notifications.c.id < cursor_id)

    rows = conn.execute(
        select(
            notifications.c.id,
            notifications.c.type,
            notifications.c.actor_user_id,
            notifications.c.body,
            notifications.c.discussion_id,
            notifications.c.reply_id,
            notifications.c.is_read,
            notifications.c.created_at,
        )
        .where(and_(*conds))
        .order_by(notifications.c.id.desc())
        .limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit]

    actor_ids = [r.actor_user_id for r in page if r.actor_user_id is not None]
    actors = _actor_map(conn, actor_ids)
    items = [
        _dto(dict(r._mapping), actors.get(r.actor_user_id) if r.actor_user_id is not None else None)
        for r in page
    ]
    next_cursor = (str(items[-1]["id"]) if has_more and items else None)
    return {"items": items, "unreadCount": unread_count(conn, user_id), "nextCursor": next_cursor}


def unread_count(conn: Connection, user_id: int) -> int:
    return (
        conn.execute(
            select(func.count())
            .select_from(notifications)
            .where(and_(notifications.c.user_id == user_id, notifications.c.is_read == 0))
        ).scalar()
        or 0
    )


def mark_read(conn: Connection, user_id: int, notification_id: int) -> None:
    row = conn.execute(
        select(notifications.c.id).where(
            and_(notifications.c.id == notification_id, notifications.c.user_id == user_id)
        )
    ).first()
    if row is None:
        raise not_found("Notification not found")
    conn.execute(
        update(notifications)
        .where(notifications.c.id == notification_id)
        .values(is_read=1, read_at=now_ms())
    )


def mark_all_read(conn: Connection, user_id: int) -> None:
    conn.execute(
        update(notifications)
        .where(notifications.c.user_id == user_id)
        .values(is_read=1, read_at=now_ms())
    )
