"""i18n 服务应用组装。

中间件（外到内）：
  CORS（信任 APP_ORIGIN + credentials）
  RequestIdMiddleware（每请求注入 request_id）

错误 envelope 与主站保持一致：
  { "error": { "code", "message", "requestId", "details"? } }
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from . import __version__
from .config import Settings, load_settings
from .db import AuthDatabase, Database
from .errors import ApiError, ErrorCode, build_error_body
from .routers.catalog import router as catalog_router
from .routers.health import router as health_router
from .routers.me import router as me_router
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
    app.include_router(me_router)
    app.include_router(catalog_router)
    app.include_router(source_router)
    app.include_router(submissions_router)

    _mount_site(app, settings)

    return app


def _mount_site(app: FastAPI, settings: Settings) -> None:
    """挂载翻译站静态产物（I18N_SITE_DIR），API 之外的路由 SPA fallback 到 index.html。

    API 路由（/api、/health 等）先注册先行匹配，静态托管只接管其余路径，
    因此翻译站与 API 同源部署（i18n.samryetha.com）时 site 请求也走本服务。
    留空 I18N_SITE_DIR 则仅提供 API。
    """
    site_dir = settings.site_dir.strip()
    if not site_dir or not os.path.isdir(site_dir):
        return

    root = os.path.realpath(site_dir)
    index_file = os.path.join(root, "index.html")

    assets_dir = os.path.join(root, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="site-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def site_spa(path: str) -> FileResponse:
        # API / 文档路径绝不能让 SPA fallback 吞掉：若接口不存在应返回 404 而不是
        # 200 + index.html（此前 site 调主站专属端点 getUser 时被误判成 200，得到
        # 000/undefined 而非用户 → 永久 loading）。
        top = path.split("/", 1)[0]
        if top in ("api", "health", "docs", "redoc") or path in ("openapi.json", "favicon.ico"):
            raise HTTPException(status_code=404)
        if path:
            candidate = os.path.realpath(os.path.join(root, path))
            try:
                inside_root = os.path.commonpath((root, candidate)) == root
            except ValueError:
                inside_root = False
            if not inside_root:
                raise HTTPException(status_code=404)
            if os.path.isfile(candidate):
                return FileResponse(candidate)
        return FileResponse(index_file)

    @app.get("/", include_in_schema=False)
    def site_home() -> FileResponse:
        return FileResponse(index_file)


def main() -> None:
    import uvicorn

    settings = load_settings()
    app = create_app(settings)
    app.state.db.create_schema()
    app.state.db.ensure_schema_drift()

    uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
