"""应用组装 — 镜像 backend/src/app/server.ts。

中间件（由外到内）：
- CORS（只信任 APP_ORIGIN + credentials）
- RequestId（每请求注入 ``scope['state']['request_id']``，SSE 安全）
- Guard（CSRF 同源校验 + 全局限频，可提前短路，SSE 安全）

错误序列化走 FastAPI exception handler，包络与 Fastify error handler 一致：
{error:{code,message,requestId,details?}}，含 422(验证)/400(空 body)/429(限频)/500 分支。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.middleware.cors import CORSMiddleware

from .config import Settings, load_settings
from .db import Database
from .errors import (
    ApiError,
    ErrorCode,
    build_error_body,
)
from .routers.health import router as health_router
from .storage import Storage
from .mailer import ConsoleMailer

logger = logging.getLogger("samryetha")


# ---------------------------------------------------------------- request id


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = "req_" + uuid.uuid4().hex[:8]
    return rid


class RequestIdMiddleware:
    """纯 ASGI：注入 request_id，不缓冲 body，兼容 SSE 流。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state["request_id"] = "req_" + uuid.uuid4().hex[:8]
        await self.app(scope, receive, send)


# ---------------------------------------------------------------- rate limit


class SlidingWindowLimiter:
    """内存滑动窗口限频（镜像 @fastify/rate-limit max=300/min）。"""

    def __init__(self, max_hits: int = 300, window_seconds: float = 60.0) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> tuple[bool, float]:
        """返回 (allowed, retry_after_seconds)。"""
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) < self.max_hits:
            hits.append(now)
            self._hits[key] = hits
            return True, 0.0
        self._hits[key] = hits
        return False, self.window - (now - hits[0])


class GuardMiddleware:
    """纯 ASGI：CSRF 同源校验 + 全局限频，命中直接短路成 error envelope。"""

    _SAFE = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings
        self.limiter = SlidingWindowLimiter()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"]
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        origin = headers.get(b"origin")

        # CSRF：非安全方法带 Origin 时必须同源
        if method not in self._SAFE and origin is not None:
            if origin.decode("utf-8", "replace") != self.settings.app_origin:
                await self._error(
                    scope,
                    send,
                    403,
                    ErrorCode.FORBIDDEN,
                    "Cross-origin request rejected",
                    retry_after=None,
                )
                return

        # 全局限频：仅当配置 TRUST_PROXY=true（位于受控反代后）才信任 X-Forwarded-For，
        # 否则直连公网时 XFF 可被伪造 → 绕过限流（镜像 app/server.ts 的 trustProxy 修复）
        client = self._client_ip(scope, headers)
        allowed, retry_after = self.limiter.allow(client)
        if not allowed:
            await self._error(
                scope,
                send,
                429,
                ErrorCode.RATE_LIMITED,
                "Too many requests",
                retry_after=retry_after,
            )
            return

        await self.app(scope, receive, send)

    def _client_ip(self, scope, headers) -> str:
        if self.settings.trust_proxy:
            fwd = headers.get(b"x-forwarded-for")
            if fwd:
                return fwd.decode("utf-8", "replace").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def _error(self, scope, send, status, code, message, retry_after):
        state = scope.get("state", {})
        request_id = state.get("request_id", "req_" + uuid.uuid4().hex[:8])
        if status == 429 and retry_after is not None:
            details = {"retryAfterMs": int(retry_after * 1000)}
            body = build_error_body(code, message, request_id, details)
        else:
            body = build_error_body(code, message, request_id)
        await send_json(scope, send, status, body, {"retry-after": str(int(retry_after))} if retry_after else None)


async def send_json(scope, send, status: int, payload: dict, extra_headers: dict[str, str] | None = None) -> None:
    import json

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8")]
    if extra_headers:
        headers += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in extra_headers.items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------- pydantic → zod 形状


_VALIDATION_CODE_MAP = {
    "missing": "invalid_type",
    "int_type": "invalid_type",
    "string_type": "invalid_type",
    "float_type": "invalid_type",
    "bool_type": "invalid_type",
    "model_attributes_type": "invalid_type",
    "int_parsing": "invalid_type",
    "float_parsing": "invalid_type",
    "string_parsing": "invalid_type",
    "json_invalid": "invalid_json",
    "string_too_short": "too_small",
    "string_too_long": "too_big",
    "string_pattern_mismatch": "invalid_string",
    "greater_than": "too_small",
    "greater_than_equal": "too_small",
    "less_than": "too_big",
    "less_than_equal": "too_big",
    "literal_error": "invalid_enum_value",
    "enum": "invalid_enum_value",
    "value_error": "custom",
}


def _validation_detail(item: dict) -> dict:
    loc = [str(x) for x in item.get("loc", ())]
    if loc and loc[0] in ("body", "path", "query"):
        loc = loc[1:]
    field = ".".join(loc) if loc else "body"
    etype = item.get("type") or ""
    return {
        "field": field,
        "message": item.get("msg") or "Invalid value",
        "code": _VALIDATION_CODE_MAP.get(etype, "custom"),
    }


# ---------------------------------------------------------------- app factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    # 若有待恢复备份标记，在打开引擎前换库（镜像 container.applyPendingRestore）
    from .feedback_backup import apply_pending_restore

    apply_pending_restore(settings)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 无迁移框架：对已存在的运行库，启动时按 schema.py 幂等补齐缺失列/新表（对最新库是 no-op）。
        db.create_schema()
        db.ensure_schema_drift()
        yield
        db.close()

    app = FastAPI(title="Samryetha API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.mailer = ConsoleMailer()
    # 登录/注册 per-route 限流（防暴力破解/批量注册；测试放宽以免拖慢测试套件，镜像 auth/routes.ts）
    app.state.auth_limiter = SlidingWindowLimiter(
        max_hits=1_000_000 if settings.node_env == "test" else 10,
        window_seconds=60,
    )
    # 附件本地磁盘存储（上传根目录启动时确保存在）
    import os

    os.makedirs(settings.upload_dir, exist_ok=True)
    app.state.storage = Storage(settings.upload_dir, settings.storage_secret)

    # S4 实时/社交基础设施（单例，挂在 app.state 供路由/worker/测试取用）
    from .events import EventBus
    from .outbox_worker import OutboxDispatcher, publish_once, register_outbox_handlers
    from .presence import MemoryPresenceStore

    app.state.events = EventBus()
    app.state.presence = MemoryPresenceStore()
    dispatcher = OutboxDispatcher()
    register_outbox_handlers(dispatcher)
    app.state.dispatcher = dispatcher

    def flush_outbox() -> int:
        """消费当前所有 pending outbox 事件并广播（测试用；生产走 OutboxWorker 线程）。"""
        return publish_once(db, dispatcher, app.state.events)

    app.state.flush_outbox = flush_outbox

    # 中间件（add_middleware 是前插，后加的在外层 → 想让 CORS 最外层，最后加）
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(GuardMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.app_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------ error handlers

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError):
        return JSONEnvelope(exc.status, build_error_body(exc.code, exc.message, _request_id(request), exc.details))

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # 空 JSON body → 400（复刻 Fastify FST_ERR_CTP_EMPTY_JSON_BODY）
        if request.headers.get("content-type", "").lower().startswith("application/json"):
            try:
                raw = await request.body()
            except Exception:
                raw = b""
            if not raw.strip():
                return JSONEnvelope(
                    400,
                    build_error_body(ErrorCode.BAD_REQUEST, "Request body must not be empty", _request_id(request)),
                )
        details = [_validation_detail(e) for e in exc.errors()]
        # 把具体字段错误拼进 message，避免只返回笼统的 "Validation failed"
        summary = "; ".join(f"{d['field']}: {d['message']}" for d in details)
        return JSONEnvelope(
            422,
            build_error_body(
                ErrorCode.VALIDATION_ERROR,
                f"Validation failed — {summary}" if summary else "Validation failed",
                _request_id(request),
                details,
            ),
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception):
        logger.error("unhandled error", exc_info=exc)
        return JSONEnvelope(
            500,
            build_error_body(ErrorCode.INTERNAL_ERROR, "Internal server error", _request_id(request)),
        )

    # ------------------------------------------------------------ routes
    app.include_router(health_router)
    from .routers.auth import router as auth_router
    from .routers.users import router as users_router
    from .routers.follows import router as follows_router
    from .routers.boards import router as boards_router
    from .routers.discussions import router as discussions_router
    from .routers.attachments import router as attachments_router
    from .routers.search import router as search_router
    from .routers.notifications import router as notifications_router
    from .routers.messages import router as messages_router
    from .routers.presence import router as presence_router
    from .routers.realtime import router as realtime_router
    from .routers.moderation import router as moderation_router
    from .routers.admin import router as admin_router
    from .routers.feedback import router as feedback_router
    from .routers.tasks import router as tasks_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(follows_router)
    app.include_router(boards_router)
    app.include_router(discussions_router)
    app.include_router(attachments_router)
    app.include_router(search_router)
    app.include_router(notifications_router)
    app.include_router(messages_router)
    app.include_router(presence_router)
    app.include_router(realtime_router)
    app.include_router(moderation_router)
    app.include_router(admin_router)
    app.include_router(feedback_router)
    app.include_router(tasks_router)
    return app


def JSONEnvelope(status: int, payload: dict):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content=payload)


def main() -> None:
    import uvicorn

    settings = load_settings()
    app = create_app(settings)
    from .outbox_worker import OutboxWorker

    # 全新库建表：create_all 幂等，只创建缺失的表，既有库不受影响
    # Create tables for a fresh database: create_all is idempotent and skips existing tables
    app.state.db.create_schema()
    # 既有库补列：create_all 只建表，不改已存在表；这里幂等补齐 schema.py 新声明但库里缺的列
    app.state.db.ensure_schema_drift()

    # 启动时幂等确保内建 admin/dev（镜像 TS main 的 ensureBuiltInAccounts）
    from .auth import ensure_builtin_accounts, merge_moderator_roles

    with app.state.db.request_conn() as conn:
        ensure_builtin_accounts(conn, settings)
        merge_moderator_roles(conn)

    # 生产入口才启动 outbox worker（测试用 app.state.flush_outbox 确定性消费）
    worker = OutboxWorker(
        app.state.db,
        app.state.dispatcher,
        app.state.events,
        interval_ms=settings.outbox_poll_interval_ms,
    )
    from .feedback_backup import BackupScheduler

    backup_scheduler = BackupScheduler(app.state.db)
    app.state.backup_scheduler = backup_scheduler
    backup_scheduler.start()
    worker.start()
    try:
        uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")
    finally:
        worker.stop()
        backup_scheduler.stop()


if __name__ == "__main__":
    main()
