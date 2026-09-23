"""/api/notifications — 镜像 backend/src/notifications/routes.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from .. import notifications as notifications_service
from ..deps import CurrentUser, DbConn, require_active_user

router = APIRouter()

NotificationId = Annotated[int, Path(ge=1)]


@router.get("/api/notifications")
def list_notifications(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    unreadOnly: Literal["true", "false"] = Query(default="false"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return notifications_service.list(
        conn,
        user.id,
        unread_only=unreadOnly == "true",
        cursor=cursor,
        limit=limit,
    )


@router.get("/api/notifications/unread-count")
def unread_count(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return {"unreadCount": notifications_service.unread_count(conn, user.id)}


@router.post("/api/notifications/{id}/read")
def mark_read(
    id: NotificationId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    notifications_service.mark_read(conn, user.id, id)
    return {"ok": True}


@router.post("/api/notifications/read-all")
def mark_all_read(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    notifications_service.mark_all_read(conn, user.id)
    return {"ok": True}
