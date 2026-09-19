"""GET /api/me — 当前登录用户（基于 samryetha_session cookie + auth DB）。

翻译站用它判断登录态。未登录返回 {"user": null}（200），登录返回 CurrentUser。
"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import CurrentUserDep

router = APIRouter(tags=["me"])


@router.get("/api/me")
def me(user: CurrentUserDep) -> dict:
    if user is None:
        return {"user": None}
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "status": user.status,
        }
    }