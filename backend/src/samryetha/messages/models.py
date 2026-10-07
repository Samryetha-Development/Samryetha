"""Typed domain records and HTTP contracts for direct messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..core.ids import ConversationID, MessageID, UserID


def _to_camel(name: str) -> str:
    head, *tail = name.split("_")
    return head + "".join(part.capitalize() for part in tail)


@dataclass(frozen=True, slots=True)
class MessageUserRecord:
    id: UserID
    username: str
    display_name: str
    discriminator: int | None
    settings: str


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    id: ConversationID
    user_a_id: UserID
    user_b_id: UserID
    last_message_at: int
    created_at: int


@dataclass(frozen=True, slots=True)
class MessageRecord:
    id: MessageID
    conversation_id: ConversationID
    sender_id: UserID
    body: str
    source: str
    read_at: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class MessageUser:
    id: UserID
    username: str
    handle: str
    display_name: str


@dataclass(frozen=True, slots=True)
class LastMessage:
    body: str
    sender_id: UserID
    created_at: int


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: ConversationID
    other_user: MessageUser
    last_message: LastMessage | None
    unread_count: int
    last_message_at: int


@dataclass(frozen=True, slots=True)
class DirectMessage:
    id: MessageID
    sender_id: UserID
    body: str
    source: str
    is_read: bool
    created_at: int


@dataclass(frozen=True, slots=True)
class MessageList:
    items: tuple[DirectMessage, ...]
    other_user: MessageUser


@dataclass(frozen=True, slots=True)
class SendMessageResult:
    conversation_id: ConversationID


class MessageHttpModel(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, from_attributes=True)


class MessageRequest(MessageHttpModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True, extra="ignore")


class SendMessageBody(MessageRequest):
    username: Annotated[str, Field(min_length=1, max_length=30)]
    body: Annotated[str, Field(min_length=1, max_length=5000)]


class MessageUserResponse(MessageHttpModel):
    id: UserID
    username: str
    handle: str
    display_name: str


class LastMessageResponse(MessageHttpModel):
    body: str
    sender_id: UserID
    created_at: int


class ConversationSummaryResponse(MessageHttpModel):
    id: ConversationID
    other_user: MessageUserResponse
    last_message: LastMessageResponse | None
    unread_count: int
    last_message_at: int


class ConversationListResponse(MessageHttpModel):
    items: list[ConversationSummaryResponse]


class DirectMessageResponse(MessageHttpModel):
    id: MessageID
    sender_id: UserID
    body: str
    source: str
    is_read: bool
    created_at: int


class MessageListResponse(MessageHttpModel):
    items: list[DirectMessageResponse]
    other_user: MessageUserResponse


class SendMessageResponse(MessageHttpModel):
    conversation_id: ConversationID


class MessageOperationOkResponse(MessageHttpModel):
    ok: bool


class MessageUnreadCountResponse(MessageHttpModel):
    unread_count: int
