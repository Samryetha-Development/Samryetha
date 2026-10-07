"""Typed HTTP boundary for user profiles and user-owned feeds."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from ..core.config import Settings
from ..core.deps import CurrentUser, DbConn, get_current_user, get_settings_dep, require_active_user
from ..discussions.models import AuthoredReplyListResponse, DiscussionListResponse, PageQuery
from ..core.errors import validation_failed
from ..users.models import ProfileBody, ProfilePatch, PublicProfileResponse, UserEnvelopeResponse
from ..users import UserService

router = APIRouter()

Username = Annotated[str, Path(min_length=1, max_length=30)]
ViewerDep = Annotated[CurrentUser | None, Depends(get_current_user)]
ActiveUserDep = Annotated[CurrentUser, Depends(require_active_user)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
_allowed_profile_keys = {"display_name", "username", "recovery_email", "bio", "avatar_object_key", "settings"}
_non_nullable_keys = ("display_name", "username", "recovery_email", "bio")


def _reject_null(body: ProfileBody, provided: set[str]) -> None:
    for key in _non_nullable_keys:
        if key in provided and getattr(body, key) is None:
            wire_key = {"display_name": "displayName", "recovery_email": "recoveryEmail"}.get(key, key)
            raise validation_failed(
                [{"field": wire_key, "message": "Expected string, received null", "code": "invalid_type"}]
            )


@router.get("/api/users/{username}", response_model=PublicProfileResponse)
def get_profile(username: Username, conn: DbConn, viewer: ViewerDep) -> PublicProfileResponse:
    return UserService(conn).get_public_profile(viewer.id if viewer else None, username)


@router.patch("/api/me/profile", response_model=UserEnvelopeResponse)
def patch_profile(body: ProfileBody, conn: DbConn, settings: SettingsDep, user: ActiveUserDep) -> UserEnvelopeResponse:
    provided = body.model_fields_set & _allowed_profile_keys
    if not provided:
        raise validation_failed([{"field": "", "message": "No fields to update", "code": "custom"}])
    _reject_null(body, provided)
    patch: ProfilePatch = {}
    if "display_name" in provided and body.display_name is not None:
        patch["displayName"] = body.display_name
    if "username" in provided and body.username is not None:
        patch["username"] = body.username
    if "recovery_email" in provided and body.recovery_email is not None:
        patch["recoveryEmail"] = body.recovery_email
    if "bio" in provided and body.bio is not None:
        patch["bio"] = body.bio
    if "avatar_object_key" in provided:
        patch["avatarObjectKey"] = body.avatar_object_key
    if "settings" in provided and body.settings is not None:
        patch["settings"] = body.settings
    return UserEnvelopeResponse(user=UserService(conn, settings=settings).update_profile(user.id, patch))


@router.get("/api/users/{username}/posts", response_model=DiscussionListResponse)
def user_posts(
    username: Username,
    conn: DbConn,
    viewer: ViewerDep,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DiscussionListResponse:
    return UserService(conn).posts(
        viewer,
        username,
        PageQuery(cursor=cursor, limit=limit),
    )


@router.get("/api/users/{username}/replies", response_model=AuthoredReplyListResponse)
def user_replies(
    username: Username,
    conn: DbConn,
    viewer: ViewerDep,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> AuthoredReplyListResponse:
    return UserService(conn).replies(
        viewer,
        username,
        PageQuery(cursor=cursor, limit=limit),
    )


@router.get("/api/users/{username}/saved", response_model=DiscussionListResponse)
def user_saved(
    username: Username,
    conn: DbConn,
    viewer: ViewerDep,
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> DiscussionListResponse:
    return UserService(conn).saved(
        viewer,
        username,
        PageQuery(cursor=cursor, limit=limit),
    )
