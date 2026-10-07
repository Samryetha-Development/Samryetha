"""/api/users/:username/follow — 镜像 backend/src/follows/routes.ts。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from ..core.deps import CurrentUser, DbConn, require_active_user
from .follows import FollowService
from ..system.models import FollowResponse

router = APIRouter()

Username = Annotated[str, Path(min_length=1, max_length=30)]


@router.post("/api/users/{username}/follow", response_model=FollowResponse)
def follow(
    username: Username,
    conn: DbConn,
    actor: CurrentUser = Depends(require_active_user),
) -> FollowResponse:
    FollowService(conn).follow_username(actor, username)
    return FollowResponse(following=True)


@router.delete("/api/users/{username}/follow", response_model=FollowResponse)
def unfollow(
    username: Username,
    conn: DbConn,
    actor: CurrentUser = Depends(require_active_user),
) -> FollowResponse:
    FollowService(conn).unfollow_username(actor, username)
    return FollowResponse(following=False)
