"""Typed SQLAlchemy persistence boundary for direct messages."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, RowMapping
from sqlalchemy.sql.elements import ColumnElement

from ..core.db import now_ms
from ..discussions.models import ModerationStatus
from ..core.ids import ConversationID, MessageID, UserID
from .models import ConversationRecord, MessageRecord, MessageUserRecord
from ..core.schema import conversations, direct_messages, users


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _optional_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _conversation(row: RowMapping) -> ConversationRecord:
    return ConversationRecord(
        id=ConversationID(_int(row["id"], "conversation id")),
        user_a_id=UserID(_int(row["user_a_id"], "user a id")),
        user_b_id=UserID(_int(row["user_b_id"], "user b id")),
        last_message_at=_int(row["last_message_at"], "last message at"),
        created_at=_int(row["created_at"], "created at"),
    )


def _message(row: RowMapping) -> MessageRecord:
    return MessageRecord(
        id=MessageID(_int(row["id"], "message id")),
        conversation_id=ConversationID(_int(row["conversation_id"], "conversation id")),
        sender_id=UserID(_int(row["sender_id"], "sender id")),
        body=_str(row["body"], "body"),
        source=_str(row["source"], "source"),
        moderation_status=ModerationStatus(_str(row["moderation_status"], "moderation status")),
        read_at=_optional_int(row["read_at"], "read at"),
        created_at=_int(row["created_at"], "created at"),
    )


def _user(row: RowMapping) -> MessageUserRecord:
    return MessageUserRecord(
        id=UserID(_int(row["id"], "user id")),
        username=_str(row["username"], "username"),
        display_name=_str(row["display_name"], "display name"),
        discriminator=_optional_int(row["discriminator"], "discriminator"),
        settings=_str(row["settings"], "settings"),
    )


def visible_predicate(user_id: UserID) -> ColumnElement[bool]:
    return or_(
        direct_messages.c.moderation_status == ModerationStatus.Approved.value,
        and_(
            direct_messages.c.moderation_status == ModerationStatus.Pending.value,
            direct_messages.c.sender_id == user_id,
        ),
    )


def find_user(conn: Connection, username: str) -> MessageUserRecord | None:
    row = conn.execute(
        select(users).where(users.c.username == username, users.c.deleted_at.is_(None))
    ).mappings().first()
    return None if row is None else _user(row)


def find_user_by_id(conn: Connection, user_id: UserID) -> MessageUserRecord | None:
    row = conn.execute(
        select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None))
    ).mappings().first()
    return None if row is None else _user(row)


def ensure_conversation(conn: Connection, user_a_id: UserID, user_b_id: UserID) -> ConversationID:
    condition = and_(
        conversations.c.user_a_id == user_a_id,
        conversations.c.user_b_id == user_b_id,
    )
    existing = conn.execute(select(conversations.c.id).where(condition)).scalar_one_or_none()
    if existing is not None:
        return ConversationID(_int(existing, "conversation id"))
    timestamp = now_ms()
    conn.execute(
        sqlite_insert(conversations)
        .values(
            user_a_id=user_a_id,
            user_b_id=user_b_id,
            last_message_at=timestamp,
            created_at=timestamp,
        )
        .on_conflict_do_nothing(index_elements=["user_a_id", "user_b_id"])
    )
    created = conn.execute(select(conversations.c.id).where(condition)).scalar_one()
    return ConversationID(_int(created, "conversation id"))


def get_conversation(conn: Connection, conversation_id: ConversationID) -> ConversationRecord | None:
    row = conn.execute(select(conversations).where(conversations.c.id == conversation_id)).mappings().first()
    return None if row is None else _conversation(row)


def insert_message(
    conn: Connection,
    conversation_id: ConversationID,
    sender_id: UserID,
    body: str,
) -> MessageID:
    value = conn.execute(
        direct_messages.insert()
        .values(
            conversation_id=conversation_id,
            sender_id=sender_id,
            body=body,
            source="user",
            created_at=now_ms(),
        )
        .returning(direct_messages.c.id)
    ).scalar_one()
    return MessageID(_int(value, "message id"))


def set_moderation_status(conn: Connection, message_id: MessageID, status: ModerationStatus) -> None:
    conn.execute(
        update(direct_messages)
        .where(direct_messages.c.id == message_id)
        .values(moderation_status=status.value)
    )


def touch_conversation(conn: Connection, conversation_id: ConversationID) -> None:
    conn.execute(
        update(conversations)
        .where(conversations.c.id == conversation_id)
        .values(last_message_at=now_ms())
    )


def list_conversations(conn: Connection, user_id: UserID) -> list[ConversationRecord]:
    rows = conn.execute(
        select(conversations)
        .where(or_(conversations.c.user_a_id == user_id, conversations.c.user_b_id == user_id))
        .order_by(conversations.c.last_message_at.desc())
    ).mappings()
    return [_conversation(row) for row in rows]


def users_by_ids(conn: Connection, user_ids: Sequence[UserID]) -> dict[UserID, MessageUserRecord]:
    rows = conn.execute(select(users).where(users.c.id.in_(user_ids))).mappings()
    records = [_user(row) for row in rows]
    return {record.id: record for record in records}


def visible_messages_for_conversations(
    conn: Connection,
    user_id: UserID,
    conversation_ids: Sequence[ConversationID],
) -> list[MessageRecord]:
    rows = conn.execute(
        select(direct_messages)
        .where(
            direct_messages.c.conversation_id.in_(conversation_ids),
            visible_predicate(user_id),
        )
        .order_by(direct_messages.c.id.desc())
    ).mappings()
    return [_message(row) for row in rows]


def unread_by_conversation(
    conn: Connection,
    user_id: UserID,
    conversation_ids: Sequence[ConversationID],
) -> dict[ConversationID, int]:
    rows = conn.execute(
        select(direct_messages.c.conversation_id, func.count().label("count"))
        .where(
            direct_messages.c.conversation_id.in_(conversation_ids),
            direct_messages.c.sender_id != user_id,
            direct_messages.c.read_at.is_(None),
            visible_predicate(user_id),
        )
        .group_by(direct_messages.c.conversation_id)
    ).mappings()
    return {
        ConversationID(_int(row["conversation_id"], "conversation id")): _int(row["count"], "count")
        for row in rows
    }


def list_messages(
    conn: Connection,
    user_id: UserID,
    conversation_id: ConversationID,
) -> list[MessageRecord]:
    rows = conn.execute(
        select(direct_messages)
        .where(
            direct_messages.c.conversation_id == conversation_id,
            visible_predicate(user_id),
        )
        .order_by(direct_messages.c.created_at)
    ).mappings()
    return [_message(row) for row in rows]


def mark_read(conn: Connection, user_id: UserID, conversation_id: ConversationID) -> None:
    conn.execute(
        update(direct_messages)
        .where(
            direct_messages.c.conversation_id == conversation_id,
            direct_messages.c.sender_id != user_id,
            direct_messages.c.read_at.is_(None),
        )
        .values(read_at=now_ms())
    )


def unread_count(conn: Connection, user_id: UserID, conversation_ids: Sequence[ConversationID]) -> int:
    value = conn.execute(
        select(func.count())
        .select_from(direct_messages)
        .where(
            direct_messages.c.conversation_id.in_(conversation_ids),
            direct_messages.c.sender_id != user_id,
            direct_messages.c.read_at.is_(None),
            visible_predicate(user_id),
        )
    ).scalar_one()
    return _int(value, "unread count")
