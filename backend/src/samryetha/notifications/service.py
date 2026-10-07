"""Typed notification business operations."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from ..errors import not_found
from ..ids import DiscussionID, NotificationID, OutboxEventID, ReplyID, UserID
from ..users import make_handle
from . import repository
from .models import (
    CreateNotification,
    NotificationItem,
    NotificationPage,
    NotificationType,
)


def create_notification(conn: Connection, command: CreateNotification) -> NotificationID:
    return repository.insert_notification(conn, command)


def create(
    conn: Connection,
    *,
    user_id: int,
    type_: NotificationType | str,
    actor_user_id: int | None = None,
    discussion_id: int | None = None,
    reply_id: int | None = None,
    body: str | None = None,
    source_event_id: int | None = None,
) -> NotificationID:
    """Compatibility entrypoint for existing typed-at-the-boundary callers."""
    return create_notification(
        conn,
        CreateNotification(
            user_id=UserID(user_id),
            actor_user_id=UserID(actor_user_id) if actor_user_id is not None else None,
            type=type_ if isinstance(type_, NotificationType) else NotificationType(type_),
            discussion_id=DiscussionID(discussion_id) if discussion_id is not None else None,
            reply_id=ReplyID(reply_id) if reply_id is not None else None,
            body=body,
            source_event_id=OutboxEventID(source_event_id) if source_event_id is not None else None,
        ),
    )


def can_receive_content(
    conn: Connection,
    user_id: int,
    discussion_id: int,
    reply_id: int | None = None,
) -> bool:
    return repository.can_receive_content(
        conn,
        UserID(user_id),
        DiscussionID(discussion_id),
        ReplyID(reply_id) if reply_id is not None else None,
    )


def list_notifications(
    conn: Connection,
    user_id: UserID,
    *,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> NotificationPage:
    bounded_limit = min(limit, 50)
    before_id: NotificationID | None = None
    if cursor is not None:
        try:
            before_id = NotificationID(int(cursor))
        except (TypeError, ValueError):
            before_id = None
    records, has_more = repository.list_records(
        conn,
        user_id,
        unread_only=unread_only,
        before_id=before_id,
        limit=bounded_limit,
    )
    actor_ids = [record.actor_user_id for record in records if record.actor_user_id is not None]
    actors = repository.actor_map(conn, actor_ids)
    items = tuple(
        NotificationItem(
            id=record.id,
            type=record.type,
            actor=actors.get(record.actor_user_id) if record.actor_user_id is not None else None,
            body=record.body,
            discussion_id=record.discussion_id,
            reply_id=record.reply_id,
            is_read=record.is_read,
            created_at=record.created_at,
        )
        for record in records
    )
    next_cursor = str(items[-1].id) if has_more and items else None
    return NotificationPage(items=items, unread_count=repository.count_unread(conn, user_id), next_cursor=next_cursor)


def list(
    conn: Connection,
    user_id: int,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> NotificationPage:
    return list_notifications(conn, UserID(user_id), unread_only=unread_only, cursor=cursor, limit=limit)


def unread_count(conn: Connection, user_id: int) -> int:
    return repository.count_unread(conn, UserID(user_id))


def mark_read(conn: Connection, user_id: int, notification_id: int) -> None:
    typed_user_id = UserID(user_id)
    typed_notification_id = NotificationID(notification_id)
    if not repository.owns_notification(conn, typed_user_id, typed_notification_id):
        raise not_found("Notification not found")
    repository.mark_read(conn, typed_notification_id)


def mark_all_read(conn: Connection, user_id: int) -> None:
    repository.mark_all_read(conn, UserID(user_id))


def item_to_response_data(item: NotificationItem) -> dict[str, object]:
    actor = item.actor
    return {
        "id": item.id,
        "type": item.type,
        "actor": (
            {
                "id": actor.id,
                "username": actor.username,
                "handle": make_handle(actor.username, actor.discriminator),
                "displayName": actor.display_name,
            }
            if actor is not None
            else None
        ),
        "body": item.body,
        "discussionId": item.discussion_id,
        "replyId": item.reply_id,
        "isRead": item.is_read,
        "createdAt": item.created_at,
    }
