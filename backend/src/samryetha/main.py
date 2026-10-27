"""生产入口 — 组装后台 worker/scheduler 后启动 uvicorn。

App 工厂在 ``core.app.create_app``；这里只负责生产侧长驻线程（outbox / 备份）。
"""

from __future__ import annotations

from .core.app import create_app
from .core.config import load_settings

__all__ = ["create_app", "main"]


def main() -> None:
    import uvicorn

    settings = load_settings()
    app = create_app(settings)
    from .events.outbox_worker import OutboxWorker

    # 全新库建表：create_all 幂等，只创建缺失的表，既有库不受影响
    # Create tables for a fresh database: create_all is idempotent and skips existing tables
    app.state.db.create_schema()
    # 既有库补列：create_all 只建表，不改已存在表；这里幂等补齐 schema.py 新声明但库里缺的列
    app.state.db.ensure_schema_drift()
    # 一次性回填内建身份标记 is_builtin（F07）：必须在补列之后、ensure_builtin_accounts 之前
    app.state.db.mark_builtin_identity()
    app.state.db.retire_content_review()

    # 启动时幂等确保内建 admin/dev（镜像 TS main 的 ensureBuiltInAccounts）
    from .auth import AuthService
    from .auth.moderator_roles import report_legacy_moderator_accounts

    with app.state.db.request_conn() as conn:
        auth = AuthService(conn, settings)
        auth.ensure_builtin_accounts()
        # 存量 moderator 只报告、不提升：静默提权已移除，改角色走 scripts/migrate_moderator_roles.py。
        # Legacy moderators are only reported, never promoted: the silent escalation is gone and role
        # changes go through scripts/migrate_moderator_roles.py.
        report_legacy_moderator_accounts(conn, source="main")

    # 生产入口才启动 outbox worker（测试用 app.state.flush_outbox 确定性消费）
    worker = OutboxWorker(
        app.state.db,
        app.state.dispatcher,
        app.state.events,
        interval_ms=settings.outbox_poll_interval_ms,
    )
    from .feedback.backup import BackupScheduler

    backup_scheduler = BackupScheduler(app.state.db)
    app.state.backup_scheduler = backup_scheduler
    backup_scheduler.start()
    worker.start()
    try:
        from .core.logging import production_logging_config

        uvicorn.run(app, host="127.0.0.1", port=settings.port, log_level="info", log_config=production_logging_config())
    finally:
        worker.stop()
        backup_scheduler.stop()


if __name__ == "__main__":
    main()
