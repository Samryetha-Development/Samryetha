"""GET /api/health — 镜像 server.ts 的 health route。

对外只暴露 status，不泄漏 uptime / db 连通细节。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import text

from ..core.db import Database
from ..system.models import HealthResponse

router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request, response: Response) -> HealthResponse:
    try:
        db: Database = request.app.state.db
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = 503
        return HealthResponse(status="error")
    return HealthResponse(status="ok")
