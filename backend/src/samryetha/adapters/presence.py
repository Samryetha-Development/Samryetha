"""在线状态内存存储 — 镜像 infrastructure/presence/memory.ts。

SSE 连接是主在线租约；旧 heartbeat API 仍保留兼容。多标签页按用户去重。
线程安全（请求、SSE 事件循环和 worker 线程均可访问）。
"""

from __future__ import annotations

import threading
import time


class MemoryPresenceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # user_id -> (last_seen_ms, expires_at_ms)
        self._store: dict[int, tuple[int, int]] = {}
        # SSE 连接引用计数：同一用户多个标签页仍只算一个在线用户。
        self._connections: dict[int, int] = {}

    def _prune(self, now: int) -> None:
        for uid, (_seen, expires) in list(self._store.items()):
            if expires <= now:
                self._store.pop(uid, None)

    def _online_count_locked(self, now: int) -> int:
        self._prune(now)
        return len(set(self._store) | set(self._connections))

    def heartbeat(self, user_id: int, ttl_ms: int) -> tuple[bool, int]:
        now = int(time.time() * 1000)
        with self._lock:
            before = self._online_count_locked(now)
            self._store[user_id] = (now, now + ttl_ms)
            after = self._online_count_locked(now)
            return after != before, after

    def connect(self, user_id: int) -> tuple[bool, int]:
        now = int(time.time() * 1000)
        with self._lock:
            before = self._online_count_locked(now)
            self._connections[user_id] = self._connections.get(user_id, 0) + 1
            after = self._online_count_locked(now)
            return after != before, after

    def disconnect(self, user_id: int) -> tuple[bool, int]:
        now = int(time.time() * 1000)
        with self._lock:
            before = self._online_count_locked(now)
            count = self._connections.get(user_id, 0)
            if count <= 1:
                self._connections.pop(user_id, None)
            else:
                self._connections[user_id] = count - 1
            after = self._online_count_locked(now)
            return after != before, after

    def online_count(self) -> int:
        now = int(time.time() * 1000)
        with self._lock:
            return self._online_count_locked(now)

    def online_user_ids(self) -> list[int]:
        now = int(time.time() * 1000)
        with self._lock:
            self._prune(now)
            return list(set(self._store) | set(self._connections))

    def last_seen(self, user_id: int) -> int | None:
        now = int(time.time() * 1000)
        with self._lock:
            entry = self._store.get(user_id)
            if user_id in self._connections:
                return entry[0] if entry is not None else now
            if entry is None:
                return None
            seen, expires = entry
            if expires <= now:
                self._store.pop(user_id, None)
                return None
            return seen
