"""/api/notifications — 镜像 backend/src/notifications/routes.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from .. import notifications as notifications_service
from ..core.deps import CurrentUser, DbConn, require_active_user
from ..core.ids import UserID
from ..notifications.models import (
    NotificationListResponse,
    NotificationResponse,
    OperationOkResponse,
    UnreadCountResponse,
)

router = APIRouter()

NotificationId = Annotated[int, Path(ge=1)]


@router.get("/api/notifications", response_model=NotificationListResponse)
def list_notifications(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    unreadOnly: Literal["true", "false"] = Query(default="false"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> NotificationListResponse:
    page = notifications_service.NotificationService(conn).list_notifications(
        UserID(user.id), unread_only=unreadOnly == "true", cursor=cursor, limit=limit
    )
    return NotificationListResponse(
        items=[
            NotificationResponse.model_validate(notifications_service.item_to_response_data(item))
            for item in page.items
        ],
        unread_count=page.unread_count,
        next_cursor=page.next_cursor,
    )


@router.get("/api/notifications/unread-count", response_model=UnreadCountResponse)
def unread_count(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> UnreadCountResponse:
    return UnreadCountResponse(unread_count=notifications_service.NotificationService(conn).unread_count(user.id))


@router.post("/api/notifications/{id}/read", response_model=OperationOkResponse)
def mark_read(
    id: NotificationId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    notifications_service.NotificationService(conn).mark_read(user.id, id)
    return OperationOkResponse()


@router.post("/api/notifications/read-all", response_model=OperationOkResponse)
def mark_all_read(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> OperationOkResponse:
    notifications_service.NotificationService(conn).mark_all_read(user.id)
    return OperationOkResponse()
