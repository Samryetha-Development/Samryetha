"""进程内事件总线 — 镜像 infrastructure/events/memory.ts。

瞬时通道：SSE / presence 订阅，outbox worker 处理完成后 publish。
发布方可能在工作线程（outbox worker）也可能在事件循环线程（SSE/请求），
故按订阅时所在的 loop 用 call_soon_threadsafe 投递，保证每个订阅回调跑在正确的线程。
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable

from ..notifications.models import RealtimeEvent


class _Subscription:
    __slots__ = ("cb", "loop")

    def __init__(self, cb: Callable[[RealtimeEvent], None], loop: asyncio.AbstractEventLoop | None):
        self.cb = cb
        self.loop = loop


class EventBus:
    """{type: {data?}} 事件；subscribe 返回取消函数。publish 对每个订阅者尽力投递。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, set[_Subscription]] = {}

    def subscribe(self, event_type: str, cb: Callable[[RealtimeEvent], None]) -> Callable[[], None]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        sub = _Subscription(cb, loop)
        with self._lock:
            self._subs.setdefault(event_type, set()).add(sub)

        def unsubscribe() -> None:
            with self._lock:
                s = self._subs.get(event_type)
                if s is not None:
                    s.discard(sub)
                    if not s:
                        self._subs.pop(event_type, None)

        return unsubscribe

    def publish(self, event: RealtimeEvent) -> None:
        event_type = event.type
        with self._lock:
            subs = list(self._subs.get(event_type, ()))
        for sub in subs:
            self._deliver(sub, event)

    @staticmethod
    def _deliver(sub: _Subscription, event: RealtimeEvent) -> None:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        try:
            if sub.loop is not None and current is not sub.loop:
                sub.loop.call_soon_threadsafe(sub.cb, event)
            else:
                sub.cb(event)
        except Exception:
            # 订阅者出错不影响其他订阅者/主流程（镜像 TS memory.ts catch）
            import logging

            logging.getLogger("samryetha.events").exception("eventbus handler error")
