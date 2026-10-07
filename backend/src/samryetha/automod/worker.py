"""逾期复审 worker：把过了人工确认窗口、仍无人定案的内容交给 AI 复审并落定。

设计跟 `OutboxWorker` 一致：

- 单轮执行由 `ModerationWorker.finalize_once()` 协调事务，测试无需启动线程；
- 生产由 `ModerationWorker` 线程按 `AUTOMOD_FINALIZE_INTERVAL_MS` 轮询；
- worker 自己开事务（`db.request_conn()`），**不依赖任何请求事务**。

开关：总开关 `AUTOMOD_ENABLED` + `AUTOMOD_AUTO_FINALIZE`。两者任一关闭时
`finalize_pending` 直接返回空，所以即使 worker 起来了也不会动数据。
"""

from __future__ import annotations

import logging
import threading

from .service import AutomodService, FinalizationResult
from ..core.config import Settings
from ..core.db import Database

logger = logging.getLogger("samryetha.automod")


class ModerationWorker:
    """周期复审驱动器；单轮执行也可直接调用，不启动线程。"""

    def __init__(self, db: Database, settings: Settings, interval_ms: int = 5_000) -> None:
        self.db = db
        self.settings = settings
        self.interval_ms = max(interval_ms, 200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def finalize_once(self, *, now: int | None = None, notify: bool = True) -> list[FinalizationResult]:
        """Use a fresh transaction for the actual AutomodService use case."""
        with self.db.request_conn() as conn:
            return AutomodService(conn, self.settings).finalize_pending(now=now, notify=notify)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="automod-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_ms / 1000.0):
            try:
                finalized = self.finalize_once()
                if finalized:
                    logger.info("[automod] finalized %d overdue item(s)", len(finalized))
            except Exception:  # noqa: BLE001 — 轮询绝不能挂掉线程
                logger.exception("[automod] finalize poll error")
