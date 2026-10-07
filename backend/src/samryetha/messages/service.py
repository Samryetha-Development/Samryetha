"""Typed direct-message service."""

from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.engine import Connection

from . import repository
from ..automod import assert_author_current
from ..automod.rules import Verdict
from ..config import Settings
from ..discussions.models import ModerationStatus
from ..errors import bad_request, forbidden, not_found
from ..ids import ConversationID, MessageID, UserID
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
from ..events.outbox import emit_event
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


def _conversation_for_user(
    conn: Connection,
    user_id: UserID,
    conversation_id: ConversationID,
) -> ConversationRecord:
    conversation = repository.get_conversation(conn, conversation_id)
    if conversation is None or user_id not in (conversation.user_a_id, conversation.user_b_id):
        raise not_found("Conversation not found")
    return conversation


def _moderate_message(
    conn: Connection,
    settings: Settings | None,
    message_id: MessageID,
    sender_id: UserID,
    body: str,
    verdict: Verdict | None,
) -> None:
    if verdict is None or settings is None:
        return
    from ..automod import CONTENT_MESSAGE, held_status, submit as submit_for_review

    submit_for_review(
        conn,
        settings,
        content_type=CONTENT_MESSAGE,
        content_id=message_id,
        author_id=sender_id,
        text=body,
        verdict=verdict,
    )
    if verdict.decision == "allow":
        return
    status = held_status(settings, verdict)
    if status is not None:
        repository.set_moderation_status(conn, message_id, ModerationStatus(status))


def send(
    conn: Connection,
    sender_id: UserID,
    recipient_username: str,
    body: str,
    settings: Settings | None = None,
) -> SendMessageResult:
    recipient = repository.find_user(conn, normalize_username(recipient_username))
    if recipient is None:
        raise not_found("User not found")
    if recipient.id == sender_id:
        raise bad_request("Cannot message yourself")
    if not _dm_allowed(recipient):
        raise forbidden("This user has disabled direct messages")

    user_a_id, user_b_id = _pair(sender_id, recipient.id)
    verdict: Verdict | None = None
    if settings is not None and settings.automod_enabled:
        from ..automod import prepare_submission

        verdict = prepare_submission(
            conn,
            settings,
            author_id=sender_id,
            text=body,
            context="direct message",
        )

    conversation_id = repository.ensure_conversation(conn, user_a_id, user_b_id)
    message_id = repository.insert_message(conn, conversation_id, sender_id, body)
    assert_author_current(conn, sender_id)
    current_recipient = repository.find_user_by_id(conn, recipient.id)
    if current_recipient is None:
        raise not_found("User not found")
    if not _dm_allowed(current_recipient):
        raise forbidden("This user has disabled direct messages")
    _moderate_message(conn, settings, message_id, sender_id, body, verdict)
    repository.touch_conversation(conn, conversation_id)
    emit_event(
        conn,
        "message.created",
        aggregate_type="conversation",
        aggregate_id=str(conversation_id),
        payload=MessageCreatedPayload(
            conversation_id=conversation_id,
            sender_id=sender_id,
            recipient_id=recipient.id,
        ),
    )
    return SendMessageResult(conversation_id=conversation_id)


def list_conversations(conn: Connection, user_id: UserID) -> list[ConversationSummary]:
    conversation_records = repository.list_conversations(conn, user_id)
    if not conversation_records:
        return []
    other_ids = [
        record.user_a_id if record.user_b_id == user_id else record.user_b_id for record in conversation_records
    ]
    users = repository.users_by_ids(conn, other_ids)
    conversation_ids = [record.id for record in conversation_records]
    messages = repository.visible_messages_for_conversations(conn, user_id, conversation_ids)
    last_messages: dict[ConversationID, LastMessage] = {}
    for message in messages:
        if message.conversation_id not in last_messages:
            last_messages[message.conversation_id] = LastMessage(
                body=message.body,
                sender_id=message.sender_id,
                created_at=message.created_at,
            )
    unread = repository.unread_by_conversation(conn, user_id, conversation_ids)
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
    conn: Connection,
    user_id: UserID,
    conversation_id: ConversationID,
) -> MessageList:
    conversation = _conversation_for_user(conn, user_id, conversation_id)
    other_id = conversation.user_a_id if conversation.user_b_id == user_id else conversation.user_b_id
    messages = repository.list_messages(conn, user_id, conversation_id)
    return MessageList(
        items=tuple(
            DirectMessage(
                id=message.id,
                sender_id=message.sender_id,
                body=message.body,
                source=message.source,
                is_read=message.read_at is not None,
                created_at=message.created_at,
            )
            for message in messages
        ),
        other_user=_user(repository.find_user_by_id(conn, other_id), other_id),
    )


def mark_read(conn: Connection, user_id: UserID, conversation_id: ConversationID) -> None:
    _conversation_for_user(conn, user_id, conversation_id)
    repository.mark_read(conn, user_id, conversation_id)


def unread_count(conn: Connection, user_id: UserID) -> int:
    conversation_ids = [record.id for record in repository.list_conversations(conn, user_id)]
    return 0 if not conversation_ids else repository.unread_count(conn, user_id, conversation_ids)
