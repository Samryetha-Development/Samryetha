"""In-memory sliding-window rate limiter.

Single-process only (one limiter per worker). If Lako ever runs behind
multiple replicas, replace with a shared store (Redis) — the call sites
(`check`) stay the same. Tests reset via `reset_all()`.
"""

from __future__ import annotations

import asyncio
import time

from app.common.errors import ApiError

_windows: dict[str, list[float]] = {}
_lock = asyncio.Lock()


async def check(key: str, max_hits: int, window_seconds: float = 60.0) -> None:
    """Raise 429 when `key` exceeded `max_hits` in the trailing window."""
    now = time.monotonic()
    async with _lock:
        hits = [t for t in _windows.get(key, []) if now - t < window_seconds]
        if len(hits) >= max_hits:
            retry_after = max(1, int(window_seconds - (now - hits[0])))
            raise ApiError(429, "RATE_LIMITED", f"Too many requests, retry in {retry_after}s")
        hits.append(now)
        _windows[key] = hits


async def reset_all() -> None:
    async with _lock:
        _windows.clear()
