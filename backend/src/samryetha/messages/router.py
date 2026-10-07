"""Typed HTTP routes for direct messages."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from .. import messages as messages_service
from ..core.config import Settings
from ..core.deps import CurrentUser, DbConn, get_settings_dep, require_active_user
from ..core.ids import ConversationID, UserID
from ..messages.models import (
    ConversationListResponse,
    ConversationSummaryResponse,
    MessageListResponse,
    MessageOperationOkResponse,
    MessageUnreadCountResponse,
    SendMessageBody,
    SendMessageResponse,
)

router = APIRouter()

ConversationId = Annotated[int, Path(ge=1)]


@router.post("/api/messages", status_code=201, response_model=SendMessageResponse)
def send(
    body: SendMessageBody,
    conn: DbConn,
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(require_active_user),
) -> SendMessageResponse:
    result = messages_service.send(conn, UserID(user.id), body.username, body.body, settings)
    return SendMessageResponse.model_validate(result)


@router.get("/api/messages/conversations", response_model=ConversationListResponse)
def list_conversations(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> ConversationListResponse:
    items = messages_service.list_conversations(conn, UserID(user.id))
    return ConversationListResponse(
        items=[ConversationSummaryResponse.model_validate(item) for item in items]
    )


@router.get(
    "/api/messages/conversations/{conversation_id}",
    response_model=MessageListResponse,
)
def list_messages(
    conversation_id: ConversationId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> MessageListResponse:
    result = messages_service.list_messages(
        conn,
        UserID(user.id),
        ConversationID(conversation_id),
    )
    return MessageListResponse.model_validate(result)


@router.post(
    "/api/messages/conversations/{conversation_id}/read",
    response_model=MessageOperationOkResponse,
)
def mark_read(
    conversation_id: ConversationId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> MessageOperationOkResponse:
    messages_service.mark_read(conn, UserID(user.id), ConversationID(conversation_id))
    return MessageOperationOkResponse(ok=True)


@router.get("/api/messages/unread-count", response_model=MessageUnreadCountResponse)
def unread_count(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> MessageUnreadCountResponse:
    return MessageUnreadCountResponse(
        unread_count=messages_service.unread_count(conn, UserID(user.id))
    )
