"""Typed direct-message service."""

from __future__ import annotations

from samryetha.messages.repository import MessageRepository

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

from ..core.config import Settings
from ..authz import AuthorizationService
from ..core.errors import bad_request, forbidden, not_found
from ..core.ids import ConversationID, UserID
from .models import (
    ConversationRecord,
    ConversationSummary,
    DirectMessage,
    LastMessage,
    MessageList,
    MessageUser,
    MessageUserRecord,
    SendMessageResult,
)
from ..notifications.models import MessageCreatedPayload
from ..events.outbox import OutboxWriter
from ..users import make_handle, normalize_username

_settings_adapter = TypeAdapter(dict[str, object])


def _pair(first: UserID, second: UserID) -> tuple[UserID, UserID]:
    return (first, second) if first <= second else (second, first)


def _user(record: MessageUserRecord | None, fallback_id: UserID) -> MessageUser:
    if record is None:
        return MessageUser(
            id=fallback_id,
            username="deleted",
            handle="deleted",
            display_name="Deleted user",
        )
    return MessageUser(
        id=record.id,
        username=record.username,
        handle=make_handle(record.username, record.discriminator),
        display_name=record.display_name,
    )


def _dm_allowed(recipient: MessageUserRecord) -> bool:
    try:
        preferences = _settings_adapter.validate_json(recipient.settings or "{}")
    except ValidationError:
        return True
    value = preferences.get("direct_messages", True)
    return value if isinstance(value, bool) else True


class MessageService:
    """Application use cases within the caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._outbox = OutboxWriter(self._conn)
        self._repository = MessageRepository(self._conn)

    def _conversation_for_user(
        self,
        user_id: UserID,
        conversation_id: ConversationID,
    ) -> ConversationRecord:
        conversation = self._repository.get_conversation(conversation_id)
        if conversation is None or user_id not in (conversation.user_a_id, conversation.user_b_id):
            raise not_found("Conversation not found")
        return conversation

    def send(
        self,
        sender_id: UserID,
        recipient_username: str,
        body: str,
    ) -> SendMessageResult:
        recipient = self._repository.find_user(normalize_username(recipient_username))
        if recipient is None:
            raise not_found("User not found")
        if recipient.id == sender_id:
            raise bad_request("Cannot message yourself")
        if not _dm_allowed(recipient):
            raise forbidden("This user has disabled direct messages")

        user_a_id, user_b_id = _pair(sender_id, recipient.id)
        conversation_id = self._repository.ensure_conversation(user_a_id, user_b_id)
        self._repository.insert_message(conversation_id, sender_id, body)
        AuthorizationService(self._conn).assert_actor_current(sender_id)
        current_recipient = self._repository.find_user_by_id(recipient.id)
        if current_recipient is None:
            raise not_found("User not found")
        if not _dm_allowed(current_recipient):
            raise forbidden("This user has disabled direct messages")
        self._repository.touch_conversation(conversation_id)
        self._outbox.emit(
            "message.created",
            aggregate_type="conversation",
            aggregate_id=str(conversation_id),
            payload=MessageCreatedPayload(
                conversation_id=conversation_id, sender_id=sender_id, recipient_id=recipient.id
            ),
        )
        return SendMessageResult(conversation_id=conversation_id)

    def list_conversations(self, user_id: UserID) -> list[ConversationSummary]:
        conversation_records = self._repository.list_conversations(user_id)
        if not conversation_records:
            return []
        other_ids = [
            record.user_a_id if record.user_b_id == user_id else record.user_b_id for record in conversation_records
        ]
        users = self._repository.users_by_ids(other_ids)
        conversation_ids = [record.id for record in conversation_records]
        messages = self._repository.visible_messages_for_conversations(user_id, conversation_ids)
        last_messages: dict[ConversationID, LastMessage] = {}
        for message in messages:
            if message.conversation_id not in last_messages:
                last_messages[message.conversation_id] = LastMessage(
                    body=message.body,
                    sender_id=message.sender_id,
                    created_at=message.created_at,
                )
        unread = self._repository.unread_by_conversation(user_id, conversation_ids)
        return [
            ConversationSummary(
                id=record.id,
                other_user=_user(users.get(other_id), other_id),
                last_message=last_messages.get(record.id),
                unread_count=unread.get(record.id, 0),
                last_message_at=record.last_message_at,
            )
            for record in conversation_records
            for other_id in [record.user_a_id if record.user_b_id == user_id else record.user_b_id]
        ]

    def list_messages(
        self,
        user_id: UserID,
        conversation_id: ConversationID,
    ) -> MessageList:
        conversation = self._conversation_for_user(user_id, conversation_id)
        other_id = conversation.user_a_id if conversation.user_b_id == user_id else conversation.user_b_id
        messages = self._repository.list_messages(user_id, conversation_id)
        return MessageList(
            items=tuple(
                (
                    DirectMessage(
                        id=message.id,
                        sender_id=message.sender_id,
                        body=message.body,
                        source=message.source,
                        is_read=message.read_at is not None,
                        created_at=message.created_at,
                    )
                    for message in messages
                )
            ),
            other_user=_user(self._repository.find_user_by_id(other_id), other_id),
        )

    def mark_read(self, user_id: UserID, conversation_id: ConversationID) -> None:
        self._conversation_for_user(user_id, conversation_id)
        self._repository.mark_read(user_id, conversation_id)

    def unread_count(self, user_id: UserID) -> int:
        conversation_ids = [record.id for record in self._repository.list_conversations(user_id)]
        return 0 if not conversation_ids else self._repository.unread_count(user_id, conversation_ids)
