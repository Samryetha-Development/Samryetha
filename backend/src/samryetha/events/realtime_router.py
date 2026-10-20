"""SSE state channel for authoritative unread counts and presence.

Each connection receives complete snapshots on startup. Domain events only wake
the stream; the payload is recalculated from current state, so reconnects,
duplicate events, and coalesced bursts cannot make the browser count drift.
"""

from __future__ import annotations

from ..auth.sessions import SessionService

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..adapters.presence import MemoryPresenceStore
from ..auth.security import SESSION_COOKIE
from ..core.db import Database, now_ms
from ..core.deps import CurrentUser, to_session_user
from ..core.errors import APIError, auth_required, banned, forbidden
from ..core.ids import UserID
from ..events import EventBus
from ..messages.service import MessageService
from ..notifications.models import (
    ConnectedData,
    NotificationCreatedEvent,
    PresenceChangedEvent,
    RealtimeEvent,
    RealtimeEventSchema,
    RealtimePresenceData,
    RealtimeUnreadCountData,
    UnreadChangedEvent,
)
from ..notifications.service import NotificationService

router = APIRouter()

_KEEPALIVE_SECONDS = 15.0


def _resolve_active_user(request: Request) -> CurrentUser:
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


def _frame(event: str, payload: str) -> str:
    return f"event: {event}\ndata: {payload}\n\n"


def _unread_frame(db: Database, user_id: int) -> str:
    with db.request_conn() as conn:
        notification_count = NotificationService(conn).unread_count(user_id)
        message_count = MessageService(conn).unread_count(UserID(user_id))
    data = RealtimeUnreadCountData(
        notification_unread_count=notification_count,
        message_unread_count=message_count,
        total=notification_count + message_count,
    )
    return _frame("unread-count", data.model_dump_json(by_alias=True))


def _presence_frame(presence: MemoryPresenceStore) -> str:
    data = RealtimePresenceData(online_count=presence.online_count())
    return _frame("presence", data.model_dump_json(by_alias=True))


def _enqueue(queue: asyncio.Queue[str], kind: str) -> None:
    """Queue state signals; on pressure, one full resync supersedes all deltas."""
    try:
        queue.put_nowait(kind)
    except asyncio.QueueFull:
        # A single full resync supersedes every queued state change.
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        queue.put_nowait("all")


@router.get(
    "/api/events",
    response_model=None,
    responses={
        200: {
            "model": RealtimeEventSchema,
            "description": "SSE snapshots for unread counts and online presence.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def events(request: Request) -> StreamingResponse:
    user = _resolve_active_user(request)
    user_id = user.id
    db: Database = request.app.state.db
    bus: EventBus = request.app.state.events
    presence: MemoryPresenceStore = request.app.state.presence

    def still_authorized() -> bool:
        try:
            return _resolve_active_user(request).id == user_id
        except APIError:
            return False

    async def event_stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)

        def deliver(event: RealtimeEvent) -> None:
            if isinstance(event, NotificationCreatedEvent):
                if event.data.user_id == user_id:
                    _enqueue(queue, "unread")
            elif isinstance(event, UnreadChangedEvent):
                if event.data.user_id == user_id:
                    _enqueue(queue, "unread")
            elif isinstance(event, PresenceChangedEvent):
                _enqueue(queue, "presence")

        changed, online_count = presence.connect(user_id)
        if changed:
            bus.publish(PresenceChangedEvent(data=RealtimePresenceData(online_count=online_count)))
        unsubscribe_notification = bus.subscribe("notification.created", deliver)
        unsubscribe_unread = bus.subscribe("unread.changed", deliver)
        unsubscribe_presence = bus.subscribe("presence.changed", deliver)
        connected = ConnectedData(user_id=user_id, at=now_ms())
        try:
            if not still_authorized():
                return
            yield _frame("connected", connected.model_dump_json(by_alias=True))
            if not still_authorized():
                return
            yield _unread_frame(db, user_id)
            if not still_authorized():
                return
            yield _presence_frame(presence)
            while True:
                try:
                    kind = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    if not still_authorized():
                        return
                    yield ": keep-alive\n\n"
                    continue
                if not still_authorized():
                    return
                if kind in {"unread", "all"}:
                    yield _unread_frame(db, user_id)
                if kind in {"presence", "all"}:
                    yield _presence_frame(presence)
        finally:
            unsubscribe_notification()
            unsubscribe_unread()
            unsubscribe_presence()
            changed, online_count = presence.disconnect(user_id)
            if changed:
                bus.publish(PresenceChangedEvent(data=RealtimePresenceData(online_count=online_count)))

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
