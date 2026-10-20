"""纯 ASGI 中间件 — 镜像 backend/src/app/server.ts。

- RequestId（每请求注入 ``scope['state']['request_id']``，SSE 安全）
- Guard（CSRF 同源校验 + 全局限频，可提前短路，SSE 安全）
"""

from __future__ import annotations

import time
import uuid
import threading
from collections.abc import Mapping, MutableMapping
from typing import cast

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.exceptions import HTTPException

from .config import Settings
from .errors import ErrorCode, build_error_body


def _scope_state(scope: Scope) -> MutableMapping[str, object]:
    raw_state: object = scope.get("state")
    if isinstance(raw_state, MutableMapping):
        return cast(MutableMapping[str, object], raw_state)
    state: dict[str, object] = {}
    scope["state"] = state
    return state


class RequestIdMiddleware:
    """纯 ASGI：注入 request_id，不缓冲 body，兼容 SSE 流。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            state = _scope_state(scope)
            state["request_id"] = "req_" + uuid.uuid4().hex[:8]
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def secure_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [(k, v) for k, v in headers if k.lower() not in (b"x-content-type-options", b"referrer-policy")]
                headers += [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin")]
                if scope.get("path", "").startswith("/api/auth/"):
                    headers = [(k, v) for k, v in headers if k.lower() != b"cache-control"]
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, secure_send)


class SlidingWindowLimiter:
    """内存滑动窗口限频（镜像 @fastify/rate-limit max=300/min）。"""

    def __init__(self, max_hits: int = 300, window_seconds: float = 60.0) -> None:
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._next_sweep = 0.0

    def allow(self, key: str) -> tuple[bool, float]:
        """返回 (allowed, retry_after_seconds)。"""
        with self._lock:
            return self._allow(key)

    def _allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        if now >= self._next_sweep:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
            self._next_sweep = now + self.window
        # Fail closed at the bounded bucket capacity; never evict a live limiter.
        if key not in self._hits and len(self._hits) >= 10_000:
            return False, self.window
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

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.limiter = SlidingWindowLimiter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        json_body = method not in self._SAFE and headers.get("content-type", "").split(";")[0].strip().lower() == "application/json"
        max_json_bytes = 1024 * 1024
        if json_body:
            try:
                declared_length = int(headers.get("content-length", "0"))
            except ValueError:
                declared_length = 0
            if declared_length > max_json_bytes:
                await self._error(scope, send, 413, ErrorCode.BadRequest, "JSON request body is too large", retry_after=None)
                return
        received = 0

        async def bounded_receive() -> Message:
            nonlocal received
            message = await receive()
            if json_body and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_json_bytes:
                    raise HTTPException(413, "JSON request body is too large")
            return message

        if method not in self._SAFE and headers.get("sec-fetch-site") == "cross-site":
            await self._error(scope, send, 403, ErrorCode.Forbidden, "Cross-site request rejected", retry_after=None)
            return

        # CSRF：非安全方法带 Origin 时必须同源
        if method not in self._SAFE and origin is not None:
            if origin.rstrip("/") not in self.settings.browser_origin_list:
                await self._error(
                    scope,
                    send,
                    403,
                    ErrorCode.Forbidden,
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
                ErrorCode.RateLimited,
                "Too many requests",
                retry_after=retry_after,
            )
            return

        await self.app(scope, bounded_receive, send)

    def _client_ip(self, scope: Scope, headers: Headers) -> str:
        if self.settings.trust_proxy:
            fwd = headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        client: object = scope.get("client")
        if isinstance(client, tuple) and client and isinstance(client[0], str):
            return client[0]
        return "unknown"

    async def _error(
        self,
        scope: Scope,
        send: Send,
        status: int,
        code: ErrorCode,
        message: str,
        retry_after: float | None,
    ) -> None:
        request_id_value = _scope_state(scope).get("request_id")
        request_id = request_id_value if isinstance(request_id_value, str) else "req_" + uuid.uuid4().hex[:8]
        if status == 429 and retry_after is not None:
            details = {"retryAfterMs": int(retry_after * 1000)}
            body = build_error_body(code, message, request_id, details)
        else:
            body = build_error_body(code, message, request_id)
        await send_json(scope, send, status, body, {"retry-after": str(int(retry_after))} if retry_after else None)


async def send_json(
    _scope: Scope,
    send: Send,
    status: int,
    payload: Mapping[str, object],
    extra_headers: Mapping[str, str] | None = None,
) -> None:
    import json

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8")]
    if extra_headers:
        headers += [(k.encode("latin-1"), v.encode("latin-1")) for k, v in extra_headers.items()]
    start_message: Message = {"type": "http.response.start", "status": status, "headers": headers}
    body_message: Message = {"type": "http.response.body", "body": body}
    await send(start_message)
    await send(body_message)
