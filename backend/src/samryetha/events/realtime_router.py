"""SSE 实时通道 — 镜像 backend/src/realtime/routes.ts。

GET /api/events：需要 active 用户（cookie 会话）。连上先发 `connected`，
随后订阅进程内总线的 `notification.created`，只把属于本用户的推送下来。
断线重连由客户端重拉通知兜底（瞬时通道，不保证可靠）。
"""

from __future__ import annotations

from ..auth.sessions import SessionService

import asyncio
import threading
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.db import Database, now_ms
from ..core.deps import CurrentUser, to_session_user
from ..events import EventBus
from ..core.errors import auth_required, banned, forbidden
from ..notifications.models import (
    ConnectedData,
    GapData,
    NotificationCreatedData,
    NotificationCreatedEvent,
    RealtimeEvent,
    RealtimeEventSchema,
)
from ..auth.security import SESSION_COOKIE

router = APIRouter()

_KEEPALIVE_SECONDS = 15.0

# 通知 seq：进程内单调计数器。重启归零——SSE 是瞬时通道，客户端重连后以通知
# 列表接口为准做全量对账，seq 只用于连接存续期内的"洞察"（gap 检测）。
_sse_seq = 0
_SSE_SEQ_LOCK = threading.Lock()


def _next_sse_seq() -> int:
    global _sse_seq
    with _SSE_SEQ_LOCK:
        _sse_seq += 1
        return _sse_seq


def _enqueue_notification_frame(queue: asyncio.Queue[str], data: NotificationCreatedData) -> None:
    """userId 过滤后的通知入队：带单调 seq；队列满时发 gap 控制帧而非静默丢。"""
    payload = data.model_copy(update={"seq": _next_sse_seq()})
    frame = "event: notification.created\ndata: %s\n\n" % payload.model_dump_json(by_alias=True)
    try:
        queue.put_nowait(frame)
        return
    except asyncio.QueueFull:
        pass
    # 队列满：丢最旧一帧腾位，补 gap 帧让客户端重拉补洞（仍满则放弃，本轮 keep-alive 会继续）。
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    gap = "event: gap\ndata: %s\n\n" % GapData(seq=_next_sse_seq()).model_dump_json(by_alias=True)
    try:
        queue.put_nowait(gap)
    except asyncio.QueueFull:
        pass


def _resolve_active_user(request: Request) -> CurrentUser:
    """短连接解析会话用户（active 才允许 SSE）。不持有连接。"""
    db: Database = request.app.state.db
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        with db.request_conn() as conn:
            row = SessionService(conn).get_session_user(token)
            if row is not None:
                user = to_session_user(row)
    if user is None:
        raise auth_required()
    if user.status == "banned":
        raise banned()
    if user.status != "active":
        raise forbidden("Your account is not active")
    return user


@router.get(
    "/api/events",
    response_model=None,
    responses={
        200: {
            "model": RealtimeEventSchema,
            "description": "Server-sent notification event payload schemas.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def events(request: Request) -> StreamingResponse:
    user = _resolve_active_user(request)
    user_id = user.id
    bus: EventBus = request.app.state.events

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)

        def deliver(ev: RealtimeEvent) -> None:
            # 只推属于该用户的通知（镜像 TS 的 d?.userId !== userId 过滤）
            if not isinstance(ev, NotificationCreatedEvent) or ev.data.user_id != user_id:
                return
            _enqueue_notification_frame(queue, ev.data)

        unsubscribe = bus.subscribe("notification.created", deliver)
        connected = ConnectedData(user_id=user_id, at=now_ms())
        try:
            yield f"event: connected\ndata: {connected.model_dump_json(by_alias=True)}\n\n"
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield frame
        finally:
            unsubscribe()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
