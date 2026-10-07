"""/api/presence — 镜像 backend/src/presence/routes.ts。

heartbeat 需 active 用户；列表公开但只返回在线人数（不回显在线用户名单，避免未认证枚举账号）。
TTL 60s（客户端 ~45s 上报）。
"""

from fastapi import APIRouter, Depends, Request

from ..deps import CurrentUser, require_active_user
from ..adapters.presence import MemoryPresenceStore
from ..system.models import PresenceResponse

router = APIRouter()

_HEARTBEAT_TTL_MS = 60_000


@router.post("/api/presence/heartbeat", response_model=PresenceResponse)
def heartbeat(
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> PresenceResponse:
    presence: MemoryPresenceStore = request.app.state.presence
    presence.heartbeat(user.id, _HEARTBEAT_TTL_MS)
    return PresenceResponse(online_count=presence.online_count())


@router.get("/api/presence", response_model=PresenceResponse)
def online_users(request: Request) -> PresenceResponse:
    """在线人数（公开）。不回显在线用户对象，避免未认证枚举账号/句柄。"""
    presence: MemoryPresenceStore = request.app.state.presence
    return PresenceResponse(online_count=presence.online_count())
