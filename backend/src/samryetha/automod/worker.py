"""逾期复审 worker：把过了人工确认窗口、仍无人定案的内容交给 AI 复审并落定。

设计跟 `OutboxWorker` 一致：

- 核心逻辑是**纯同步函数** `finalize_once`——测试可以直接调用，不需要线程、不需要 sleep；
- 生产由 `ModerationWorker` 线程按 `AUTOMOD_FINALIZE_INTERVAL_MS` 轮询；
- worker 自己开事务（`db.request_conn()`），**不依赖任何请求事务**。

开关：总开关 `AUTOMOD_ENABLED` + `AUTOMOD_AUTO_FINALIZE`。两者任一关闭时
`finalize_pending` 直接返回空，所以即使 worker 起来了也不会动数据。
"""

from __future__ import annotations

import logging
import threading

from .service import FinalizationResult, finalize_pending
from ..core.config import Settings
from ..core.db import Database

logger = logging.getLogger("samryetha.automod")


def finalize_once(
    db: Database,
    settings: Settings,
    *,
    now: int | None = None,
    notify: bool = True,
) -> list[FinalizationResult]:
    """跑一轮逾期复审，返回本轮落定的条目。

    `now` 可注入：测试里把时钟推到窗口之后即可确定性地触发，不必真的等 1 分钟。
    """
    with db.request_conn() as conn:
        return finalize_pending(conn, settings, now=now, notify=notify)


class ModerationWorker:
    """周期跑逾期复审的后台线程。仅生产 `main()` 启动，测试用 `finalize_once`。"""

    def __init__(self, db: Database, settings: Settings, interval_ms: int = 5_000) -> None:
        self.db = db
        self.settings = settings
        self.interval_ms = max(interval_ms, 200)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
                finalized = finalize_once(self.db, self.settings)
                if finalized:
                    logger.info("[automod] finalized %d overdue item(s)", len(finalized))
            except Exception:  # noqa: BLE001 — 轮询绝不能挂掉线程
                logger.exception("[automod] finalize poll error")
