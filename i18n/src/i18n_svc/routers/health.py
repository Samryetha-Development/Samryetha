"""健康检查端点。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
