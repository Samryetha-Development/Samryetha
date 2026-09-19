"""/api/presence — 镜像 backend/src/presence/routes.ts。

heartbeat 需 active 用户；列表公开但只返回在线人数（不回显在线用户名单，避免未认证枚举账号）。
TTL 60s（客户端 ~45s 上报）。
"""

from fastapi import APIRouter, Depends, Request

from ..deps import CurrentUser, require_active_user
from ..presence import MemoryPresenceStore

router = APIRouter()

_HEARTBEAT_TTL_MS = 60_000


@router.post("/api/presence/heartbeat")
def heartbeat(
    request: Request,
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    presence: MemoryPresenceStore = request.app.state.presence
    presence.heartbeat(user.id, _HEARTBEAT_TTL_MS)
    return {"onlineCount": presence.online_count()}


@router.get("/api/presence")
def online_users(request: Request) -> dict:
    """在线人数（公开）。不回显在线用户对象，避免未认证枚举账号/句柄。"""
    presence: MemoryPresenceStore = request.app.state.presence
    return {"onlineCount": presence.online_count()}
