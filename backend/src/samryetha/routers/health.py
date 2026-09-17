"""GET /api/health — 镜像 server.ts 的 health route。

对外只暴露 status，不泄漏 uptime / db 连通细节。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict:
    try:
        db = request.app.state.db
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return {"status": "error"}
    return {"status": "ok"}
