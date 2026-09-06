"""用户路由 — 镜像 backend/src/users/routes.ts（posts/replies/saved 三个 feed 在 S2 随 discussion feed 一起挂）。"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import discussions as discussions_service
from .. import users as users_service
from ..deps import (
    CurrentUser,
    DbConn,
    get_current_user,
    require_active_user,
)
from ..errors import not_found, validation_failed
from ..users import get_public_profile, get_by_username, update_profile

router = APIRouter()

Username = Annotated[str, Path(min_length=1, max_length=30)]

_ALLOWED_PROFILE_KEYS = {"displayName", "username", "recoveryEmail", "bio", "avatarObjectKey", "settings"}


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    displayName: str | None = Field(default=None, min_length=1, max_length=50)
    username: str | None = Field(default=None, min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")
    recoveryEmail: str | None = Field(default=None, min_length=3, max_length=200)
    bio: str | None = Field(default=None, max_length=500)
    avatarObjectKey: str | None = None
    settings: dict | None = None

    @field_validator("displayName", "username", mode="before")
    @classmethod
    def _strip_fields(cls, v: Any) -> Any:
        return _strip(v)


# 非空值字段显式传 null → 422（zod string 不接受 null）；仅 avatarObjectKey 可空
_NULLABLE_STRING_KEYS = ("displayName", "username", "recoveryEmail", "bio")


def _reject_null(body: ProfileBody, provided: set[str]) -> None:
    for key in _NULLABLE_STRING_KEYS:
        if key in provided and getattr(body, key) is None:
            raise validation_failed([{"field": key, "message": "Expected string, received null", "code": "invalid_type"}])


@router.get("/api/users/{username}")
def get_profile(
    username: Username,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
) -> dict:
    viewer_id = viewer.id if viewer else None
    return get_public_profile(conn, viewer_id, username)


@router.patch("/api/me/profile")
def patch_profile(
    body: ProfileBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    provided = body.model_fields_set & _ALLOWED_PROFILE_KEYS
    if not provided:
        raise validation_failed([{"field": "", "message": "No fields to update", "code": "custom"}])
    _reject_null(body, provided)
    patch: dict = {}
    if "displayName" in provided:
        patch["displayName"] = body.displayName
    if "username" in provided:
        patch["username"] = body.username
    if "recoveryEmail" in provided:
        patch["recoveryEmail"] = body.recoveryEmail
    if "bio" in provided:
        patch["bio"] = body.bio
    if "avatarObjectKey" in provided:
        patch["avatarObjectKey"] = body.avatarObjectKey
    if "settings" in provided and body.settings is not None:
        patch["settings"] = body.settings
    dto = update_profile(conn, user.id, patch)
    return {"user": dto}


@router.get("/api/users/{username}/posts")
def user_posts(
    username: Username,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    user = get_by_username(conn, username)
    if user is None:
        raise not_found("User not found")
    return discussions_service.list_by_author(conn, viewer, user["id"], {"cursor": cursor, "limit": limit})


@router.get("/api/users/{username}/replies")
def user_replies(
    username: Username,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    user = get_by_username(conn, username)
    if user is None:
        raise not_found("User not found")
    return discussions_service.list_replies_by_author(conn, viewer, user["id"], {"cursor": cursor, "limit": limit})


@router.get("/api/users/{username}/saved")
def user_saved(
    username: Username,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict:
    user = get_by_username(conn, username)
    if user is None:
        raise not_found("User not found")
    return discussions_service.list_saved(conn, viewer, user["id"], {"cursor": cursor, "limit": limit})
