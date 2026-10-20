"""应用组装 — 镜像 backend/src/app/server.ts。

中间件（由外到内）：
- CORS（只信任 APP_ORIGIN + credentials）
- RequestId（每请求注入 ``scope['state']['request_id']``，SSE 安全）
- Guard（CSRF 同源校验 + 全局限频，可提前短路，SSE 安全）
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .. import __version__
from ..adapters.docpreview import PreviewCache, build_converter
from ..adapters.mailer import build_mailer
from ..adapters.storage import Storage
from ..system.health_router import router as health_router
from .config import Settings, load_settings
from .db import Database
from .handlers import install_error_handlers
from .middleware import GuardMiddleware, RequestIdMiddleware, SlidingWindowLimiter

logger = logging.getLogger("samryetha.app")


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
        db.retire_content_review()
        from ..attachments import AttachmentService
        from ..auth import AuthService
        from ..files import FileService

        with db.request_conn() as conn:
            AuthService(conn).merge_moderator_roles()
            FileService(conn, storage=_app.state.storage).ensure_seed_categories()
            FileService(conn, storage=_app.state.storage).reap_orphan_objects()
            AttachmentService(conn, storage=_app.state.storage).reap_orphans()
        if settings.node_env != "test":
            app.state.scheduled_post_worker.start()
        try:
            yield
        finally:
            app.state.scheduled_post_worker.stop()
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
    # 可变数据目录启动自检：确保上传目录存在，并把**实际解析到的路径**打到日志，便于一眼
    # 看出数据是否被配回仓库内（配回仓库内会被 git clean/reset 更新清空；config 里会打 WARNING）。
    # 数据库父目录由 Database.__init__ 负责创建，这里只需保证上传目录存在。
    # Startup self-check for mutable data: ensure the upload directory exists and log the
    # *resolved* paths so it is obvious whether data was pointed back inside the repo (which any
    # git clean/reset update would wipe; config.py emits a WARNING for that case). The database
    # parent directory is created by Database.__init__ itself.
    os.makedirs(settings.upload_dir, exist_ok=True)
    logger.info(
        "storage paths — data_dir=%s upload_dir=%s database_url=%s",
        settings.samryetha_data_dir,
        settings.upload_dir,
        settings.database_url,
    )
    app.state.storage = Storage(settings.upload_dir, settings.storage_secret)
    # No unsandboxed fallback. The capability and endpoint share this readiness result.
    app.state.doc_preview = build_converter(
        settings.preview_converter,
        settings.soffice_bin,
        settings.preview_timeout_seconds,
    )
    app.state.doc_preview_cache = PreviewCache(
        settings.preview_cache_dir,
        concurrency=settings.preview_max_conversions,
        version=settings.preview_cache_version,
        max_bytes=settings.preview_cache_max_bytes,
    )
    from ..scheduled_posts.worker import ScheduledPostWorker

    app.state.scheduled_post_worker = ScheduledPostWorker(db, settings, app.state.storage)

    def reap_attachment_orphans(older_than_ms: int = 24 * 3600 * 1000) -> int:
        from ..attachments import AttachmentService

        with db.request_conn() as conn:
            return AttachmentService(conn, storage=app.state.storage).reap_orphans(older_than_ms)

    def reap_file_orphans(older_than_ms: int = 24 * 3600 * 1000) -> int:
        from ..files import FileService
        with db.request_conn() as conn:
            return FileService(conn, storage=app.state.storage).reap_orphan_objects(older_than_ms)

    app.state.reap_file_orphans = reap_file_orphans
    app.state.reap_attachment_orphans = reap_attachment_orphans

    # S4 实时/社交基础设施（单例，挂在 app.state 供路由/worker/测试取用）
    from ..events import EventBus
    from ..events.outbox_worker import OutboxDispatcher, OutboxDeliveryService, register_outbox_handlers
    from ..adapters.presence import MemoryPresenceStore

    app.state.events = EventBus()
    app.state.presence = MemoryPresenceStore()
    dispatcher = OutboxDispatcher()
    register_outbox_handlers(dispatcher, mailer=app.state.mailer)
    app.state.dispatcher = dispatcher

    def flush_outbox() -> int:
        """消费当前所有 pending outbox 事件并广播（测试用；生产走 OutboxWorker 线程）。"""
        return OutboxDeliveryService(db, dispatcher=dispatcher, bus=app.state.events).publish_once()

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
    from ..files.router import router as files_router
    app.include_router(health_router)
    from ..auth.router import router as auth_router
    from ..users.router import router as users_router
    from ..users.follows_router import router as follows_router
    from ..boards.router import router as boards_router
    from ..discussions.router import router as discussions_router
    from ..drafts.router import router as drafts_router
    from ..scheduled_posts.router import router as scheduled_posts_router
    from ..attachments.router import router as attachments_router
    from ..search.router import router as search_router
    from ..notifications.router import router as notifications_router
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
    app.include_router(scheduled_posts_router)
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
    app.include_router(files_router)
    return app
