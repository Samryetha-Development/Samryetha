"""Typed HTTP routes for direct messages."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from .service import MessageService
from ..events.outbox import OutboxWriter
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
    result = MessageService(conn, settings).send(UserID(user.id), body.username, body.body)
    return SendMessageResponse.model_validate(result)


@router.get("/api/messages/conversations", response_model=ConversationListResponse)
def list_conversations(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> ConversationListResponse:
    items = MessageService(conn).list_conversations(UserID(user.id))
    return ConversationListResponse(items=[ConversationSummaryResponse.model_validate(item) for item in items])


@router.get(
    "/api/messages/conversations/{conversation_id}",
    response_model=MessageListResponse,
)
def list_messages(
    conversation_id: ConversationId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> MessageListResponse:
    result = MessageService(conn).list_messages(
        UserID(user.id),
        ConversationID(conversation_id),
        before=before, limit=limit,
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
    MessageService(conn).mark_read(UserID(user.id), ConversationID(conversation_id))
    OutboxWriter(conn).emit(
        "unread.changed", aggregate_type="user", aggregate_id=str(user.id), payload={"userId": user.id}
    )
    return MessageOperationOkResponse(ok=True)


@router.get("/api/messages/unread-count", response_model=MessageUnreadCountResponse)
def unread_count(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> MessageUnreadCountResponse:
    return MessageUnreadCountResponse(unread_count=MessageService(conn).unread_count(UserID(user.id)))
