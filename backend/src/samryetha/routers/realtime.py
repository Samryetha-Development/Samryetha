"""SSE 实时通道 — 镜像 backend/src/realtime/routes.ts。

GET /api/events：需要 active 用户（cookie 会话）。连上先发 `connected`，
随后订阅进程内总线的 `notification.created`，只把属于本用户的推送下来。
断线重连由客户端重拉通知兜底（瞬时通道，不保证可靠）。
"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..db import Database, now_ms
from ..deps import to_session_user
from ..events import EventBus
from ..errors import auth_required, banned, forbidden
from ..security import SESSION_COOKIE, get_session_user

router = APIRouter()

_KEEPALIVE_SECONDS = 15.0

# 通知 seq：进程内单调计数器。重启归零——SSE 是瞬时通道，客户端重连后以通知
# 列表接口为准做全量对账，seq 只用于连接存续期内的"洞察"（gap 检测）。
_SSE_SEQ = 0
_SSE_SEQ_LOCK = threading.Lock()


def _next_sse_seq() -> int:
    global _SSE_SEQ
    with _SSE_SEQ_LOCK:
        _SSE_SEQ += 1
        return _SSE_SEQ


def _enqueue_notification_frame(queue: asyncio.Queue, data: dict) -> None:
    """userId 过滤后的通知入队：带单调 seq；队列满时发 gap 控制帧而非静默丢。"""
    payload = dict(data)
    payload["seq"] = _next_sse_seq()
    frame = "event: notification.created\ndata: %s\n\n" % json.dumps(payload, ensure_ascii=False)
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
    gap = "event: gap\ndata: %s\n\n" % json.dumps({"seq": _next_sse_seq()}, ensure_ascii=False)
    try:
        queue.put_nowait(gap)
    except asyncio.QueueFull:
        pass


def _resolve_active_user(request: Request):
    """短连接解析会话用户（active 才允许 SSE）。不持有连接。"""
    db: Database = request.app.state.db
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        with db.request_conn() as conn:
            row = get_session_user(conn, token)
            if row is not None:
                user = to_session_user(row)
    if user is None:
        raise auth_required()
    if user.status == "banned":
        raise banned()
    if user.status != "active":
        raise forbidden("Your account is not active")
    return user


@router.get("/api/events")
async def events(request: Request):
    user = _resolve_active_user(request)
    user_id = user.id
    bus: EventBus = request.app.state.events

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        def deliver(ev: dict) -> None:
            data = ev.get("data")
            # 只推属于该用户的通知（镜像 TS 的 d?.userId !== userId 过滤）
            if not isinstance(data, dict) or data.get("userId") != user_id:
                return
            _enqueue_notification_frame(queue, data)

        unsubscribe = bus.subscribe("notification.created", deliver)
        connected = {"userId": user_id, "at": now_ms()}
        try:
            yield f"event: connected\ndata: {json.dumps(connected, ensure_ascii=False)}\n\n"
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
