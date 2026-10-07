"""在线状态内存存储 — 镜像 infrastructure/presence/memory.ts。

客户端每 ~45s heartbeat 一次，TTL 60s；到期自动清除。线程安全（请求/心跳多线程访问）。
"""

from __future__ import annotations

import threading
import time


class MemoryPresenceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # user_id -> (last_seen_ms, expires_at_ms)
        self._store: dict[int, tuple[int, int]] = {}

    def _prune(self, now: int) -> None:
        for uid, (_seen, expires) in list(self._store.items()):
            if expires <= now:
                self._store.pop(uid, None)

    def heartbeat(self, user_id: int, ttl_ms: int) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            self._store[user_id] = (now, now + ttl_ms)

    def online_count(self) -> int:
        now = int(time.time() * 1000)
        with self._lock:
            self._prune(now)
            return len(self._store)

    def online_user_ids(self) -> list[int]:
        now = int(time.time() * 1000)
        with self._lock:
            self._prune(now)
            return list(self._store.keys())

    def last_seen(self, user_id: int) -> int | None:
        now = int(time.time() * 1000)
        with self._lock:
            entry = self._store.get(user_id)
            if entry is None:
                return None
            seen, expires = entry
            if expires <= now:
                self._store.pop(user_id, None)
                return None
            return seen
