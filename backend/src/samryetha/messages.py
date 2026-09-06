"""站内信（私信）service — 镜像 backend/src/messages/service.ts。

conversations：两人一个会话（user_a < user_b 归一，避免重复会话）。
direct_messages：会话内消息，source 字段预留其他平台接入。
"""

from __future__ import annotations

import json

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import bad_request, forbidden, not_found
from .outbox import emit_event
from .schema import conversations, direct_messages, users
from .users import make_handle, normalize_username


def _pair(a: int, b: int) -> tuple[int, int]:
    # 归一化参与者：a 恒为较小 id，避免同一对用户产生两条会话
    return (a, b) if a <= b else (b, a)


def _other_user(row) -> dict:
    return {
        "id": row.id,
        "username": row.username,
        "handle": make_handle(row.username, row.discriminator),
        "displayName": row.display_name,
    }


def _find_or_create_conversation(conn: Connection, a: int, b: int) -> int:
    row = conn.execute(
        select(conversations.c.id).where(
            and_(conversations.c.user_a_id == a, conversations.c.user_b_id == b)
        )
    ).first()
    if row is not None:
        return row[0]
    _now = now_ms()
    # 并发安全：on_conflict_do_nothing 处理双方同时首发导致的唯一约束冲突，再重查拿回会话 id
    # Concurrency-safe: on_conflict_do_nothing absorbs the unique-constraint race, then re-query for the id
    conn.execute(
        sqlite_insert(conversations)
        .values(user_a_id=a, user_b_id=b, last_message_at=_now, created_at=_now)
        .on_conflict_do_nothing(index_elements=["user_a_id", "user_b_id"])
    )
    row = conn.execute(
        select(conversations.c.id).where(
            and_(conversations.c.user_a_id == a, conversations.c.user_b_id == b)
        )
    ).first()
    return row[0]


def _get_conversation(conn: Connection, user_id: int, conversation_id: int):
    row = conn.execute(select(conversations).where(conversations.c.id == conversation_id)).first()
    if row is None or (row.user_a_id != user_id and row.user_b_id != user_id):
        raise not_found("Conversation not found")
    return row


def _dm_allowed(recip) -> bool:
    # settings 是 JSON 字符串；direct_messages 缺省视为允许
    # settings is a JSON string; direct_messages defaults to allowed when absent
    try:
        prefs = json.loads(recip.settings or "{}")
    except (TypeError, ValueError):
        prefs = {}
    return prefs.get("direct_messages", True)


def send(conn: Connection, sender_id: int, recipient_username: str, body: str) -> dict:
    wanted = normalize_username(recipient_username)
    recip = conn.execute(
        select(users).where(
            and_(users.c.username == wanted, users.c.deleted_at.is_(None))
        )
    ).first()
    if recip is None:
        raise not_found("User not found")
    if recip.id == sender_id:
        # 参数不正确（对象设为自己不合法）→ 400，而非 404/409
        # Self-message is an invalid argument → 400, not 404/409
        raise bad_request("Cannot message yourself")
    # 校验收件方隐私开关：settings.direct_messages=false 时拒绝
    # Respect recipient privacy: reject when settings.direct_messages is false
    if not _dm_allowed(recip):
        raise forbidden("This user has disabled direct messages")
    a, b = _pair(sender_id, recip.id)
    conversation_id = _find_or_create_conversation(conn, a, b)
    conn.execute(
        direct_messages.insert().values(
            conversation_id=conversation_id,
            sender_id=sender_id,
            body=body,
            source="user",
            created_at=now_ms(),
        )
    )
    conn.execute(
        update(conversations)
        .where(conversations.c.id == conversation_id)
        .values(last_message_at=now_ms())
    )
    # 广播 message.created 事件，让收件方未读徽标实时刷新
    # Emit message.created so the recipient's unread badge refreshes in real time
    emit_event(
        conn,
        "message.created",
        aggregate_type="conversation",
        aggregate_id=str(conversation_id),
        payload={
            "conversationId": conversation_id,
            "senderId": sender_id,
            "recipientId": recip.id,
        },
    )
    return {"conversationId": conversation_id}


def list_conversations(conn: Connection, user_id: int) -> list[dict]:
    rows = conn.execute(
        select(conversations)
        .where(or_(conversations.c.user_a_id == user_id, conversations.c.user_b_id == user_id))
        .order_by(conversations.c.last_message_at.desc())
    ).all()
    if not rows:
        return []

    other_ids = [r.user_a_id if r.user_b_id == user_id else r.user_b_id for r in rows]
    other_map = {u.id: u for u in conn.execute(select(users).where(users.c.id.in_(other_ids))).all()}

    conv_ids = [r.id for r in rows]
    last_rows = conn.execute(
        select(direct_messages)
        .where(direct_messages.c.conversation_id.in_(conv_ids))
        .order_by(direct_messages.c.id.desc())
    ).all()
    last_map: dict[int, dict] = {}
    for m in last_rows:
        if m.conversation_id not in last_map:
            last_map[m.conversation_id] = m

    unread_rows = conn.execute(
        select(direct_messages.c.conversation_id, func.count())
        .where(
            and_(
                direct_messages.c.conversation_id.in_(conv_ids),
                direct_messages.c.sender_id != user_id,
                direct_messages.c.read_at.is_(None),
            )
        )
        .group_by(direct_messages.c.conversation_id)
    ).all()
    unread_map = {r[0]: r[1] for r in unread_rows}

    out = []
    for r in rows:
        other_id = r.user_a_id if r.user_b_id == user_id else r.user_b_id
        other = other_map.get(other_id)
        last = last_map.get(r.id)
        out.append(
            {
                "id": r.id,
                "otherUser": _other_user(other) if other else {"id": other_id, "username": "deleted", "handle": "deleted", "displayName": "Deleted user"},
                "lastMessage": {"body": last.body, "senderId": last.sender_id, "createdAt": last.created_at} if last else None,
                "unreadCount": unread_map.get(r.id, 0),
                "lastMessageAt": r.last_message_at,
            }
        )
    return out


def list_messages(conn: Connection, user_id: int, conversation_id: int) -> dict:
    conv = _get_conversation(conn, user_id, conversation_id)
    other_id = conv.user_a_id if conv.user_b_id == user_id else conv.user_b_id
    other = conn.execute(select(users).where(users.c.id == other_id)).first()
    rows = conn.execute(
        select(direct_messages)
        .where(direct_messages.c.conversation_id == conversation_id)
        .order_by(direct_messages.c.created_at)
    ).all()
    return {
        "items": [
            {
                "id": m.id,
                "senderId": m.sender_id,
                "body": m.body,
                "source": m.source,
                "isRead": m.read_at is not None,
                "createdAt": m.created_at,
            }
            for m in rows
        ],
        "otherUser": _other_user(other) if other else {"id": other_id, "username": "deleted", "handle": "deleted", "displayName": "Deleted user"},
    }


def mark_read(conn: Connection, user_id: int, conversation_id: int) -> None:
    conv = _get_conversation(conn, user_id, conversation_id)
    conn.execute(
        update(direct_messages)
        .where(
            and_(
                direct_messages.c.conversation_id == conv.id,
                direct_messages.c.sender_id != user_id,
                direct_messages.c.read_at.is_(None),
            )
        )
        .values(read_at=now_ms())
    )


def unread_count(conn: Connection, user_id: int) -> int:
    convs = conn.execute(
        select(conversations.c.id).where(
            or_(conversations.c.user_a_id == user_id, conversations.c.user_b_id == user_id)
        )
    ).all()
    if not convs:
        return 0
    ids = [c[0] for c in convs]
    return (
        conn.execute(
            select(func.count())
            .select_from(direct_messages)
            .where(
                and_(
                    direct_messages.c.conversation_id.in_(ids),
                    direct_messages.c.sender_id != user_id,
                    direct_messages.c.read_at.is_(None),
                )
            )
        ).scalar()
        or 0
    )
