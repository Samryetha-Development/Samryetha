"""Typed domain, HTTP, outbox, and realtime contracts for notifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..ids import DiscussionID, NotificationID, OutboxEventID, ReplyID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


class NotificationType(StrEnum):
    Reply = "reply"
    Mention = "mention"
    Follow = "follow"
    System = "system"
    Moderation = "moderation"
    Ban = "ban"


class UserRole(StrEnum):
    Student = "student"
    Moderator = "moderator"
    Admin = "admin"


class UserStatus(StrEnum):
    Pending = "pending"
    Active = "active"
    Banned = "banned"
    Deactivated = "deactivated"


@dataclass(frozen=True, slots=True)
class NotificationViewer:
    id: UserID
    role: UserRole
    status: UserStatus


@dataclass(frozen=True, slots=True)
class NotificationActor:
    id: UserID
    username: str
    discriminator: int | None
    display_name: str


@dataclass(frozen=True, slots=True)
class NotificationRecord:
    id: NotificationID
    user_id: UserID
    actor_user_id: UserID | None
    type: NotificationType
    discussion_id: DiscussionID | None
    reply_id: ReplyID | None
    body: str | None
    source_event_id: OutboxEventID | None
    is_read: bool
    read_at: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class NotificationItem:
    id: NotificationID
    type: NotificationType
    actor: NotificationActor | None
    body: str | None
    discussion_id: DiscussionID | None
    reply_id: ReplyID | None
    is_read: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class NotificationPage:
    items: tuple[NotificationItem, ...]
    unread_count: int
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class CreateNotification:
    user_id: UserID
    type: NotificationType
    actor_user_id: UserID | None = None
    discussion_id: DiscussionID | None = None
    reply_id: ReplyID | None = None
    body: str | None = None
    source_event_id: OutboxEventID | None = None


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
        use_enum_values=True,
    )


class NotificationActorResponse(CamelModel):
    id: int
    username: str
    handle: str
    display_name: str


class NotificationResponse(CamelModel):
    id: int
    type: NotificationType
    actor: NotificationActorResponse | None
    body: str | None
    discussion_id: int | None
    reply_id: int | None
    is_read: bool
    created_at: int


class NotificationListResponse(CamelModel):
    items: list[NotificationResponse]
    unread_count: int
    next_cursor: str | None


class UnreadCountResponse(CamelModel):
    unread_count: int


class OperationOkResponse(CamelModel):
    ok: bool = True


class OutboxPayload(CamelModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class ReplyCreatedPayload(OutboxPayload):
    discussion_id: int
    author_id: int
    reply_id: int
    parent_reply_id: int | None = None


class MentionCreatedPayload(OutboxPayload):
    mentioned_user_id: int
    author_id: int
    discussion_id: int
    reply_id: int | None = None


class MessageCreatedPayload(OutboxPayload):
    conversation_id: int
    sender_id: int
    recipient_id: int


class UserFollowedPayload(OutboxPayload):
    follower_id: int
    followee_id: int


class UserBannedPayload(OutboxPayload):
    user_id: int
    banned_by_user_id: int
    reason: str | None = None
    banned_until: str | None = None


class NotificationCreatedData(CamelModel):
    user_id: int
    seq: int | None = None


class ConnectedData(CamelModel):
    user_id: int
    at: int


class GapData(CamelModel):
    seq: int


class RealtimeEventSchema(CamelModel):
    notification: NotificationCreatedData | None = None
    connected: ConnectedData | None = None
    gap: GapData | None = None


class NotificationCreatedEvent(BaseModel):
    type: Literal["notification.created"] = "notification.created"
    data: NotificationCreatedData


class UserBannedData(CamelModel):
    user_id: int


class UserBannedEvent(BaseModel):
    type: Literal["user.banned"] = "user.banned"
    data: UserBannedData


type RealtimeEvent = NotificationCreatedEvent | UserBannedEvent
