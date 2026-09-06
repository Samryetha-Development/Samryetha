"""/api/messages/* — 镜像 backend/src/messages/routes.ts。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import messages as messages_service
from ..deps import CurrentUser, DbConn, require_active_user

router = APIRouter()


class SendBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    body: Annotated[str, Field(min_length=1, max_length=5000)]


@router.post("/api/messages", status_code=201)
def send(body: SendBody, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return messages_service.send(conn, user.id, body.username, body.body)


@router.get("/api/messages/conversations")
def list_conversations(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return {"items": messages_service.list_conversations(conn, user.id)}


@router.get("/api/messages/conversations/{conversation_id}")
def list_messages(conversation_id: int, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return messages_service.list_messages(conn, user.id, conversation_id)


@router.post("/api/messages/conversations/{conversation_id}/read")
def mark_read(conversation_id: int, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    messages_service.mark_read(conn, user.id, conversation_id)
    return {"ok": True}


@router.get("/api/messages/unread-count")
def unread_count(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return {"unreadCount": messages_service.unread_count(conn, user.id)}
