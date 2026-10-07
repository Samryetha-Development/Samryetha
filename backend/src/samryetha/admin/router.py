"""Typed admin HTTP boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from .. import admin as service
from ..admin.models import (
    AdminAssignableRole,
    AdminOperationOkResponse,
    AdminStatsResponse,
    AdminUserListResponse,
    AdminUserResponse,
    ChangeRoleBody,
    ChangeStatusBody,
    DeletedContentResponse,
    TemporaryPasswordResponse,
)
from ..deps import CurrentUser, DbConn, require_admin
from ..ids import DiscussionID, ReplyID, UserID
from ..adapters.presence import MemoryPresenceStore
from ..users.models import AccountStatus

router = APIRouter()
UserIDPath = Annotated[UserID, Path(ge=1)]


@router.get("/api/admin/stats", response_model=AdminStatsResponse)
def stats(request: Request, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> AdminStatsResponse:
    presence: MemoryPresenceStore = request.app.state.presence
    return service.stats(conn, user, presence)


@router.get("/api/admin/users", response_model=AdminUserListResponse)
def list_users(
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
    q: str | None = Query(default=None, max_length=100),
    status: AccountStatus | None = Query(default=None),
    role: AdminAssignableRole | None = Query(default=None),
    exclude_pending: bool = Query(default=False, alias="excludePending"),
    cursor: UserID | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> AdminUserListResponse:
    return service.list_users(conn, user, q, status, role, cursor, limit, exclude_pending)


@router.delete("/api/admin/users/{id}", response_model=AdminOperationOkResponse)
def delete_user(id: UserIDPath, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> AdminOperationOkResponse:
    service.delete_user(conn, user, id, None)
    return AdminOperationOkResponse()


@router.patch("/api/admin/users/{id}/role", response_model=AdminUserResponse)
def change_role(
    id: UserIDPath, body: ChangeRoleBody, conn: DbConn, user: CurrentUser = Depends(require_admin)
) -> AdminUserResponse:
    return service.change_role(conn, user, id, body.role, body.reason)


@router.patch("/api/admin/users/{id}/status", response_model=AdminUserResponse)
def change_status(
    id: UserIDPath, body: ChangeStatusBody, conn: DbConn, user: CurrentUser = Depends(require_admin)
) -> AdminUserResponse:
    return service.change_status(conn, user, id, body.status, body.reason)


@router.post("/api/admin/users/{id}/reset-password", response_model=TemporaryPasswordResponse)
def reset_password(
    id: UserIDPath, conn: DbConn, user: CurrentUser = Depends(require_admin)
) -> TemporaryPasswordResponse:
    return service.reset_password(conn, user, id)


@router.post("/api/admin/users/{id}/verify", response_model=AdminUserResponse)
def verify_user(id: UserIDPath, conn: DbConn, user: CurrentUser = Depends(require_admin)) -> AdminUserResponse:
    return service.verify_user(conn, user, id)


@router.get("/api/admin/moderation/deleted", response_model=DeletedContentResponse)
def list_deleted(
    conn: DbConn,
    user: CurrentUser = Depends(require_admin),
    discussion_cursor: DiscussionID | None = Query(default=None, ge=1, alias="discussionCursor"),
    reply_cursor: ReplyID | None = Query(default=None, ge=1, alias="replyCursor"),
    limit: int = Query(default=20, ge=1, le=50),
) -> DeletedContentResponse:
    return service.list_deleted_content(conn, user, discussion_cursor, reply_cursor, limit)
