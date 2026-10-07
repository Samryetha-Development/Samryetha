"""应用组装 — 镜像 backend/src/app/server.ts。

中间件（由外到内）：
- CORS（只信任 APP_ORIGIN + credentials）
- RequestId（每请求注入 ``scope['state']['request_id']``，SSE 安全）
- Guard（CSRF 同源校验 + 全局限频，可提前短路，SSE 安全）
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .. import __version__
from ..adapters.mailer import build_mailer
from ..adapters.storage import Storage
from ..automod import FinalizationResult
from ..system.health_router import router as health_router
from .config import Settings, load_settings
from .db import Database
from .handlers import install_error_handlers
from .middleware import GuardMiddleware, RequestIdMiddleware, SlidingWindowLimiter


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    # 若有待恢复备份标记，在打开引擎前换库（镜像 container.applyPendingRestore）
    from ..feedback.backup import apply_pending_restore

    apply_pending_restore(settings)
    db = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        # 无迁移框架：对已存在的运行库，启动时按 schema.py 幂等补齐缺失列/新表（对最新库是 no-op）。
        db.create_schema()
        db.ensure_schema_drift()
        from ..attachments import reap_orphans
        from ..auth import merge_moderator_roles

        with db.request_conn() as conn:
            merge_moderator_roles(conn)
            reap_orphans(conn, _app.state.storage)
        yield
        db.close()

    app = FastAPI(title="Samryetha API", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    # 按 SMTP_URL 选 SMTP 或 Console；未接线 SMTP 时生产打印告警（密码重置令牌绝不进日志）
    app.state.mailer = build_mailer(settings.smtp_url, settings.smtp_from, is_production=settings.is_production)
    from ..auth.oidc import OidcClient

    app.state.oidc = OidcClient(settings) if settings.oidc_enabled else None
    # 登录/注册 per-route 限流（防暴力破解/批量注册；测试放宽以免拖慢测试套件，镜像 auth/routes.ts）
    app.state.auth_limiter = SlidingWindowLimiter(
        max_hits=1_000_000 if settings.node_env == "test" else 10,
        window_seconds=60,
    )
    # 附件本地磁盘存储（上传根目录启动时确保存在）
    os.makedirs(settings.upload_dir, exist_ok=True)
    app.state.storage = Storage(settings.upload_dir, settings.storage_secret)

    def reap_attachment_orphans(older_than_ms: int = 24 * 3600 * 1000) -> int:
        from ..attachments import reap_orphans

        with db.request_conn() as conn:
            return reap_orphans(conn, app.state.storage, older_than_ms)

    app.state.reap_attachment_orphans = reap_attachment_orphans

    def finalize_overdue_moderation(now: int | None = None) -> list[FinalizationResult]:
        """跑一轮"逾期未确认 → AI 复审落定"（测试与运维用；生产走 ModerationWorker 线程）。"""
        from ..automod.worker import finalize_once

        return finalize_once(db, settings, now=now)

    app.state.finalize_moderation = finalize_overdue_moderation

    # S4 实时/社交基础设施（单例，挂在 app.state 供路由/worker/测试取用）
    from ..events import EventBus
    from ..events.outbox_worker import OutboxDispatcher, publish_once, register_outbox_handlers
    from ..adapters.presence import MemoryPresenceStore

    app.state.events = EventBus()
    app.state.presence = MemoryPresenceStore()
    dispatcher = OutboxDispatcher()
    register_outbox_handlers(dispatcher, mailer=app.state.mailer)
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
        allow_origins=settings.browser_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------ error handlers
    install_error_handlers(app)

    # ------------------------------------------------------------ routes
    app.include_router(health_router)
    from ..auth.router import router as auth_router
    from ..users.router import router as users_router
    from ..users.follows_router import router as follows_router
    from ..boards.router import router as boards_router
    from ..discussions.router import router as discussions_router
    from ..drafts.router import router as drafts_router
    from ..attachments.router import router as attachments_router
    from ..search.router import router as search_router
    from ..notifications.router import router as notifications_router
    from ..review_queue.router import router as review_queue_router
    from ..messages.router import router as messages_router
    from ..system.presence_router import router as presence_router
    from ..events.realtime_router import router as realtime_router
    from ..moderation.router import router as moderation_router
    from ..admin.router import router as admin_router
    from ..feedback.router import router as feedback_router
    from ..tasks.router import router as tasks_router

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(follows_router)
    app.include_router(boards_router)
    app.include_router(discussions_router)
    app.include_router(drafts_router)
    app.include_router(attachments_router)
    app.include_router(search_router)
    app.include_router(notifications_router)
    app.include_router(review_queue_router)
    app.include_router(messages_router)
    app.include_router(presence_router)
    app.include_router(realtime_router)
    app.include_router(moderation_router)
    app.include_router(admin_router)
    app.include_router(feedback_router)
    app.include_router(tasks_router)
    return app
