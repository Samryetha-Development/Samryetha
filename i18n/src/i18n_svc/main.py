"""i18n 服务应用组装。

中间件（外到内）：
  CORS（信任 APP_ORIGIN + credentials）
  RequestIdMiddleware（每请求注入 request_id）

错误 envelope 与主站保持一致：
  { "error": { "code", "message", "requestId", "details"? } }
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, load_settings
from .db import AuthDatabase, Database
from .errors import ApiError, ErrorCode, build_error_body
from .routers.health import router as health_router
from .routers.catalog import router as catalog_router
from .routers.source import router as source_router
from .routers.submissions import router as submissions_router

logger = logging.getLogger("i18n_svc")


# ---------------------------------------------------------------- request id

def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = "req_" + uuid.uuid4().hex[:8]
    return rid


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state["request_id"] = "req_" + uuid.uuid4().hex[:8]
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- app factory

def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Samryetha i18n Service",
        version=__version__,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    app.state.auth_db = AuthDatabase(settings.effective_auth_db_url)

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=exc.status,
            content=build_error_body(exc.code, exc.message, _request_id(request), exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        from fastapi.responses import JSONResponse
        details = [
            {"field": ".".join(str(loc) for loc in e["loc"]), "message": e["msg"]}
            for e in exc.errors()
        ]
        summary = "; ".join(f"{d['field']}: {d['message']}" for d in details)
        return JSONResponse(
            status_code=422,
            content=build_error_body(
                ErrorCode.VALIDATION_ERROR,
                f"Validation failed — {summary}" if summary else "Validation failed",
                _request_id(request),
                details,
            ),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception):
        from fastapi.responses import JSONResponse
        logger.error("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=build_error_body(ErrorCode.INTERNAL_ERROR, "Internal server error", _request_id(request)),
        )

    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(source_router)
    app.include_router(submissions_router)

    return app


def main() -> None:
    import uvicorn

    settings = load_settings()
    app = create_app(settings)
    app.state.db.create_schema()
    app.state.db.ensure_schema_drift()

    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
