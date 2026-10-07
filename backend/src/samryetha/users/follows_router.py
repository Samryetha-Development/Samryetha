"""/api/users/:username/follow — 镜像 backend/src/follows/routes.ts。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ..deps import CurrentUser, DbConn, require_active_user
from ..errors import internal_error
from ..users.follows import follow_user, get_user_id_by_username, unfollow_user
from ..system.models import FollowResponse

router = APIRouter()

Username = Annotated[str, Path(min_length=1, max_length=30)]


@router.post("/api/users/{username}/follow", response_model=FollowResponse)
def follow(
    username: Username,
    conn: DbConn,
    actor: CurrentUser = Depends(require_active_user),
) -> FollowResponse:
    target_id = get_user_id_by_username(conn, username)
    if target_id is None:
        # TS 原样：目标不存在在 route 层抛普通 Error → 500
        raise internal_error()
    follow_user(conn, actor, target_id)
    return FollowResponse(following=True)


@router.delete("/api/users/{username}/follow", response_model=FollowResponse)
def unfollow(
    username: Username,
    conn: DbConn,
    actor: CurrentUser = Depends(require_active_user),
) -> FollowResponse:
    target_id = get_user_id_by_username(conn, username)
    if target_id is None:
        raise internal_error()
    unfollow_user(conn, actor, target_id)
    return FollowResponse(following=False)
