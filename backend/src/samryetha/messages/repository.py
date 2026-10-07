"""Typed SQLAlchemy persistence boundary for direct messages."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, RowMapping

from ..core.db import now_ms
from ..core.ids import ConversationID, MessageID, UserID
from .models import ConversationRecord, MessageRecord, MessageUserRecord
from ..core.schema import conversations, direct_messages, users
from ..core.records import opt_int, require_int, require_str


def _conversation(row: RowMapping) -> ConversationRecord:
    return ConversationRecord(
        id=ConversationID(require_int(row["id"], "conversation id")),
        user_a_id=UserID(require_int(row["user_a_id"], "user a id")),
        user_b_id=UserID(require_int(row["user_b_id"], "user b id")),
        last_message_at=require_int(row["last_message_at"], "last message at"),
        created_at=require_int(row["created_at"], "created at"),
    )


def _message(row: RowMapping) -> MessageRecord:
    return MessageRecord(
        id=MessageID(require_int(row["id"], "message id")),
        conversation_id=ConversationID(require_int(row["conversation_id"], "conversation id")),
        sender_id=UserID(require_int(row["sender_id"], "sender id")),
        body=require_str(row["body"], "body"),
        source=require_str(row["source"], "source"),
        read_at=opt_int(row["read_at"], "read at"),
        created_at=require_int(row["created_at"], "created at"),
    )


def _user(row: RowMapping) -> MessageUserRecord:
    return MessageUserRecord(
        id=UserID(require_int(row["id"], "user id")),
        username=require_str(row["username"], "username"),
        display_name=require_str(row["display_name"], "display name"),
        discriminator=opt_int(row["discriminator"], "discriminator"),
        settings=require_str(row["settings"], "settings"),
    )


class MessageRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def find_user(self, username: str) -> MessageUserRecord | None:
        row = (
            self._conn.execute(select(users).where(users.c.username == username, users.c.deleted_at.is_(None)))
            .mappings()
            .first()
        )
        return None if row is None else _user(row)

    def find_user_by_id(self, user_id: UserID) -> MessageUserRecord | None:
        row = (
            self._conn.execute(select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None)))
            .mappings()
            .first()
        )
        return None if row is None else _user(row)

    def ensure_conversation(self, user_a_id: UserID, user_b_id: UserID) -> ConversationID:
        condition = and_(
            conversations.c.user_a_id == user_a_id,
            conversations.c.user_b_id == user_b_id,
        )
        existing = self._conn.execute(select(conversations.c.id).where(condition)).scalar_one_or_none()
        if existing is not None:
            return ConversationID(require_int(existing, "conversation id"))
        timestamp = now_ms()
        self._conn.execute(
            sqlite_insert(conversations)
            .values(user_a_id=user_a_id, user_b_id=user_b_id, last_message_at=timestamp, created_at=timestamp)
            .on_conflict_do_nothing(index_elements=["user_a_id", "user_b_id"])
        )
        created = self._conn.execute(select(conversations.c.id).where(condition)).scalar_one()
        return ConversationID(require_int(created, "conversation id"))

    def get_conversation(self, conversation_id: ConversationID) -> ConversationRecord | None:
        row = self._conn.execute(select(conversations).where(conversations.c.id == conversation_id)).mappings().first()
        return None if row is None else _conversation(row)

    def insert_message(self, conversation_id: ConversationID, sender_id: UserID, body: str) -> MessageID:
        value = self._conn.execute(
            direct_messages.insert()
            .values(conversation_id=conversation_id, sender_id=sender_id, body=body, source="user", created_at=now_ms())
            .returning(direct_messages.c.id)
        ).scalar_one()
        return MessageID(require_int(value, "message id"))

    def touch_conversation(self, conversation_id: ConversationID) -> None:
        self._conn.execute(
            update(conversations).where(conversations.c.id == conversation_id).values(last_message_at=now_ms())
        )

    def list_conversations(self, user_id: UserID) -> list[ConversationRecord]:
        rows = self._conn.execute(
            select(conversations)
            .where(or_(conversations.c.user_a_id == user_id, conversations.c.user_b_id == user_id))
            .order_by(conversations.c.last_message_at.desc())
        ).mappings()
        return [_conversation(row) for row in rows]

    def users_by_ids(self, user_ids: Sequence[UserID]) -> dict[UserID, MessageUserRecord]:
        rows = self._conn.execute(select(users).where(users.c.id.in_(user_ids))).mappings()
        records = [_user(row) for row in rows]
        return {record.id: record for record in records}

    def visible_messages_for_conversations(
        self, user_id: UserID, conversation_ids: Sequence[ConversationID]
    ) -> list[MessageRecord]:
        rows = self._conn.execute(
            select(direct_messages)
            .where(direct_messages.c.conversation_id.in_(conversation_ids))
            .order_by(direct_messages.c.id.desc())
        ).mappings()
        return [_message(row) for row in rows]

    def unread_by_conversation(
        self, user_id: UserID, conversation_ids: Sequence[ConversationID]
    ) -> dict[ConversationID, int]:
        rows = self._conn.execute(
            select(direct_messages.c.conversation_id, func.count().label("count"))
            .where(
                direct_messages.c.conversation_id.in_(conversation_ids),
                direct_messages.c.sender_id != user_id,
                direct_messages.c.read_at.is_(None),
            )
            .group_by(direct_messages.c.conversation_id)
        ).mappings()
        return {
            ConversationID(require_int(row["conversation_id"], "conversation id")): require_int(row["count"], "count")
            for row in rows
        }

    def list_messages(self, user_id: UserID, conversation_id: ConversationID) -> list[MessageRecord]:
        rows = self._conn.execute(
            select(direct_messages)
            .where(direct_messages.c.conversation_id == conversation_id)
            .order_by(direct_messages.c.created_at)
        ).mappings()
        return [_message(row) for row in rows]

    def mark_read(self, user_id: UserID, conversation_id: ConversationID) -> None:
        self._conn.execute(
            update(direct_messages)
            .where(
                direct_messages.c.conversation_id == conversation_id,
                direct_messages.c.sender_id != user_id,
                direct_messages.c.read_at.is_(None),
            )
            .values(read_at=now_ms())
        )

    def unread_count(self, user_id: UserID, conversation_ids: Sequence[ConversationID]) -> int:
        value = self._conn.execute(
            select(func.count())
            .select_from(direct_messages)
            .where(
                direct_messages.c.conversation_id.in_(conversation_ids),
                direct_messages.c.sender_id != user_id,
                direct_messages.c.read_at.is_(None),
            )
        ).scalar_one()
        return require_int(value, "unread count")
