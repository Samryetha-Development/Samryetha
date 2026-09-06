"""/api/admin — 镜像 backend/src/admin/routes.ts。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import admin as service
from ..deps import CurrentUser, DbConn, require_active_user

router = APIRouter()

UserId = Annotated[int, Path(ge=1)]


class ChangeRoleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["student", "admin"]
    reason: str | None = Field(default=None, max_length=1000)


class ChangeStatusBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["active", "deactivated"]
    reason: str | None = Field(default=None, max_length=1000)


@router.get("/api/admin/stats")
def stats(
    request: Request,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.stats(conn, user, request.app.state.presence)


@router.get("/api/admin/users")
def list_users(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    q: str | None = Query(default=None, max_length=100),
    status: Literal["pending", "active", "banned", "deactivated"] | None = Query(default=None),
    role: Literal["student", "admin"] | None = Query(default=None),
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return service.list_users(conn, user, q, status, role, cursor, limit)


@router.delete("/api/admin/users/{id}")
def delete_user(
    id: UserId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    service.delete_user(conn, user, id, None)
    return {"ok": True}


@router.patch("/api/admin/users/{id}/role")
def change_role(
    id: UserId,
    body: ChangeRoleBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.change_role(conn, user, id, body.role, body.reason)


@router.patch("/api/admin/users/{id}/status")
def change_status(
    id: UserId,
    body: ChangeStatusBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.change_status(conn, user, id, body.status, body.reason)


@router.post("/api/admin/users/{id}/reset-password")
def reset_password(
    id: UserId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.reset_password(conn, user, id)


@router.post("/api/admin/users/{id}/verify")
def verify_user(
    id: UserId,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.verify_user(conn, user, id)


@router.get("/api/admin/moderation/deleted")
def list_deleted(
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
    discussionCursor: int | None = Query(default=None, ge=1),
    replyCursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    return service.list_deleted_content(conn, user, discussionCursor, replyCursor, limit)
