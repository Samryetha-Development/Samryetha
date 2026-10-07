"""Transactional outbox 消费端 — 镜像 infrastructure/queue/worker.ts。

业务事务内 OutboxWriter.emit() 落 pending 行（见 outbox.py）；本模块的 worker 轮询：
原子 claim(pending→processing) → 顺序执行 dispatcher handler（通知/邮件/SSE 事件）
→ 成功置 done / 失败指数退避，超限转 failed。handler 通过返回的 publish 事件列表，
把"要广播到 SSE"的事件交给调用方在正确的线程 publish（事件总线见 events.py）。

poll_once() 是纯同步函数：测试可确定性调用；生产由 OutboxWorker 线程定时驱动。
"""

from __future__ import annotations

from samryetha.events.repository import EventRepository

import logging
import threading
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel, TypeAdapter
from sqlalchemy.engine import Connection

from .. import notifications
from ..core.db import Database, now_ms
from ..adapters.mailer import ban_notification_email, ban_notification_text
from ..notifications.models import (
    MentionCreatedPayload,
    MessageCreatedPayload,
    NotificationCreatedData,
    NotificationCreatedEvent,
    RealtimeEvent,
    ReplyCreatedPayload,
    UserBannedData,
    UserBannedEvent,
    UserBannedPayload,
    UserFollowedPayload,
)
from ..core.ids import UserID

logger = logging.getLogger("samryetha.outbox")
_event_id: ContextVar[int] = ContextVar("outbox_event_id")


# ---------------------------------------------------------------- dispatcher


type RawPayload = dict[str, object]
type RawHandler = Callable[[Connection, RawPayload], list[RealtimeEvent]]
PayloadT = TypeVar("PayloadT", bound=BaseModel)


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, text: str, html: str) -> None: ...


class EventPublisher(Protocol):
    def publish(self, event: RealtimeEvent) -> None: ...


class OutboxDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, list[RawHandler]] = {}

    def on(self, event_type: str, handler: RawHandler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def on_typed(
        self,
        event_type: str,
        payload_model: type[PayloadT],
        handler: Callable[[Connection, PayloadT], list[RealtimeEvent]],
    ) -> None:
        def validated(conn: Connection, payload: RawPayload) -> list[RealtimeEvent]:
            return handler(conn, payload_model.model_validate(payload))

        self.on(event_type, validated)

    def handlers_for(self, event_type: str) -> list[RawHandler]:
        return self._handlers.get(event_type, [])


# ---------------------------------------------------------------- handlers
# handler 签名：(conn, payload: dict) -> list[dict]  # 返回要 publish 的事件


def _publish(user_id: int) -> list[RealtimeEvent]:
    return [NotificationCreatedEvent(data=NotificationCreatedData(user_id=user_id))]


@dataclass(frozen=True, slots=True)
class PublicContent:
    title: str
    author_id: int


def register_outbox_handlers(dispatcher: OutboxDispatcher, mailer: Mailer | None = None) -> None:
    if mailer is None:
        from ..adapters.mailer import ConsoleMailer

        mailer = ConsoleMailer()
    dispatcher.on_typed(
        "reply.created", ReplyCreatedPayload, lambda conn, payload: OutboxEventService(conn).on_reply_created(payload)
    )
    dispatcher.on_typed(
        "mention.created",
        MentionCreatedPayload,
        lambda conn, payload: OutboxEventService(conn).on_mention_created(payload),
    )
    dispatcher.on_typed(
        "message.created",
        MessageCreatedPayload,
        lambda conn, payload: OutboxEventService(conn).on_message_created(payload),
    )
    dispatcher.on_typed(
        "user.followed", UserFollowedPayload, lambda conn, payload: OutboxEventService(conn).on_user_followed(payload)
    )
    dispatcher.on_typed(
        "user.banned", UserBannedPayload, lambda conn, payload: OutboxEventService(conn, mailer).on_user_banned(payload)
    )


# ---------------------------------------------------------------- poll


_PAYLOAD_ADAPTER = TypeAdapter(dict[str, object])


def _parse_payload(raw: str | None) -> RawPayload:
    if not raw:
        return {}
    try:
        return _PAYLOAD_ADAPTER.validate_json(raw)
    except ValueError:
        return {}


# processing 租约：超过此时长仍未 done/failed，视为 worker 崩溃，扫回 pending 重做。
# Processing lease: a row stuck in processing longer than this is assumed orphaned
# (worker crashed between claim and done) and swept back to pending.
PROCESSING_TIMEOUT_MS = 5 * 60 * 1000


# ---------------------------------------------------------------- worker thread


class OutboxWorker:
    """后台线程每 interval_ms 轮询一次 outbox。仅生产 main() 启动，测试不用。"""

    def __init__(self, db: Database, dispatcher: OutboxDispatcher, bus: EventPublisher, interval_ms: int = 500) -> None:
        self.db = db
        self.dispatcher = dispatcher
        self.bus = bus
        self.interval_ms = max(interval_ms, 50)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="outbox-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.interval_ms / 1000.0):
            try:
                OutboxDeliveryService(self.db, dispatcher=self.dispatcher, bus=self.bus).publish_once()
            except Exception:  # noqa: BLE001 — 轮询绝不能挂掉线程
                logger.exception("[outbox] poll error")


class OutboxEventService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, mailer: Mailer | None = None) -> None:
        self._conn = conn
        self._mailer = mailer
        self._repository = EventRepository(self._conn)

    def _already_notified(self, user_id: int) -> bool:
        return self._repository.already_notified(UserID(user_id), _event_id.get())

    def public_content(self, discussion_id: int, reply_id: int | None = None) -> PublicContent | None:
        """Never use a stale event title or send side effects for held content."""
        disc = self._repository.discussion(discussion_id)
        if disc is None or disc.deleted_at is not None:
            return None
        if reply_id is not None:
            reply = self._repository.reply(reply_id, discussion_id)
            if reply is None or reply.deleted_at is not None:
                return None
        return PublicContent(title=disc.title, author_id=disc.author_id)

    def on_reply_created(self, payload: ReplyCreatedPayload) -> list[RealtimeEvent]:
        discussion_id = payload.discussion_id
        author_id = payload.author_id
        reply_id = payload.reply_id
        disc = self.public_content(discussion_id, reply_id)
        if disc is None:
            return []
        author_row = self._repository.user_contact(author_id)
        actor_name = author_row.display_name if author_row else "Someone"
        recipients = {disc.author_id}
        recipients.update(self._repository.discussion_follower_ids(discussion_id))
        # 嵌套回复：被回复的那条评论的作者也应收到通知
        # Nested reply: also notify the author of the parent reply being replied to
        parent_reply_id = payload.parent_reply_id
        if parent_reply_id:
            parent_author_id = self._repository.reply_author_id(parent_reply_id)
            if parent_author_id is not None:
                recipients.add(parent_author_id)
        recipients.discard(author_id)
        body = f"{actor_name} 回复了「{disc.title}」"
        out: list[RealtimeEvent] = []
        for uid in recipients:
            if not notifications.NotificationService(self._conn).can_receive_content(uid, discussion_id, reply_id):
                continue
            if self._already_notified(uid):
                continue
            notifications.NotificationService(self._conn).create(
                user_id=uid,
                actor_user_id=author_id,
                type_="reply",
                discussion_id=discussion_id,
                reply_id=reply_id,
                body=body,
                source_event_id=_event_id.get(),
            )
            out.extend(_publish(uid))
        return out

    def on_mention_created(self, payload: MentionCreatedPayload) -> list[RealtimeEvent]:
        user_id = payload.mentioned_user_id
        author_id = payload.author_id
        discussion_id = payload.discussion_id
        if user_id == author_id:
            return []
        if self.public_content(discussion_id, payload.reply_id) is None:
            return []
        if not notifications.NotificationService(self._conn).can_receive_content(
            user_id, discussion_id, payload.reply_id
        ):
            return []
        if self._already_notified(user_id):
            return []
        author = self._repository.user_contact(author_id)
        name = author.display_name if author else "Someone"
        notifications.NotificationService(self._conn).create(
            user_id=user_id,
            actor_user_id=author_id,
            type_="mention",
            discussion_id=discussion_id,
            reply_id=payload.reply_id,
            body=f"{name} 在{('回复中' if payload.reply_id else '讨论中')}提到了你",
            source_event_id=_event_id.get(),
        )
        return _publish(user_id)

    def on_message_created(self, payload: MessageCreatedPayload) -> list[RealtimeEvent]:
        return _publish(payload.recipient_id)

    def on_user_followed(self, payload: UserFollowedPayload) -> list[RealtimeEvent]:
        follower_id = payload.follower_id
        followee_id = payload.followee_id
        if follower_id == followee_id:
            return []
        follower = self._repository.user_contact(follower_id)
        if follower is None:
            return []
        if self._already_notified(followee_id):
            return []
        notifications.NotificationService(self._conn).create(
            user_id=followee_id,
            actor_user_id=follower_id,
            type_="follow",
            body=f"{follower.display_name} 关注了你",
            source_event_id=_event_id.get(),
        )
        return _publish(followee_id)

    def on_user_banned(self, payload: UserBannedPayload) -> list[RealtimeEvent]:
        """镜像 moderation/routes.ts user.banned handler：console 邮件 + 广播。

        租约回收会重放事件：以事件 ID 对应的 notifications 行为幂等标记，
        仅在新建该行时发信，避免重复邮件。
        """
        user_id = payload.user_id
        user = self._repository.user_contact(user_id)
        if user is None:
            return []
        reason = payload.reason
        banned_until = payload.banned_until
        body = ban_notification_text(reason, banned_until)
        if not self._already_notified(user_id):
            notifications.NotificationService(self._conn).create(
                user_id=user_id,
                actor_user_id=payload.banned_by_user_id,
                type_="ban",
                body=body,
                source_event_id=_event_id.get(),
            )
            if self._mailer is None:
                raise RuntimeError("Ban event delivery requires mailer")
            self._mailer.send(
                to=user.email,
                subject="Samryetha 账号封禁通知",
                text=body,
                html=ban_notification_email(reason=reason, banned_until_iso=banned_until),
            )
        return [UserBannedEvent(data=UserBannedData(user_id=user_id))]

    def reclaim_stale_processing(self) -> int:
        """把超时的 processing 行扫回 pending。返回回收行数。"""
        cutoff = now_ms() - PROCESSING_TIMEOUT_MS
        try:
            return self._repository.reclaim_stale_processing(cutoff=cutoff)
        except Exception as exc:
            # 极旧运行库尚无 processing_at 列（drift 补列前）：不挡正常消费，下次补列后生效。
            if "processing_at" not in str(exc):
                raise
            logger.warning("[outbox] reclaim skipped (missing processing_at column): %s", exc)
            return 0


class OutboxDeliveryService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, db: Database, dispatcher: OutboxDispatcher, bus: EventPublisher | None = None) -> None:
        self._db = db
        self._bus = bus
        self._dispatcher = dispatcher

    def _require_bus(self) -> EventPublisher:
        if self._bus is None:
            raise RuntimeError("OutboxDeliveryService operation requires bus")
        return self._bus

    def poll_once(self, batch_size: int = 50, max_attempts: int = 10) -> list[RealtimeEvent]:
        """消费一批到期 pending 事件，返回要广播的事件列表。纯同步、可测试确定性调用。"""
        dispatcher = self._dispatcher
        publishes: list[RealtimeEvent] = []
        with self._db.request_conn() as conn:
            OutboxEventService(conn).reclaim_stale_processing()
            rows = EventRepository(conn).pending_batch(available_at=now_ms(), limit=batch_size)
            if not rows:
                return []
            EventRepository(conn).mark_processing([row.id for row in rows], processing_at=now_ms())

        for row in rows:
            payload: RawPayload = {}
            try:
                with self._db.request_conn() as conn:
                    payload = _parse_payload(row.payload)
                    token = _event_id.set(row.id)
                    try:
                        for handler in dispatcher.handlers_for(row.event_type):
                            publishes.extend(handler(conn, payload) or [])
                    finally:
                        _event_id.reset(token)
                    EventRepository(conn).mark_done(row.id, processed_at=now_ms())
            except Exception as exc:  # noqa: BLE001 — 复刻 TS 逐事件失败处理
                attempts = row.attempts + 1
                logger.warning(
                    "[outbox] handler failed for %s (attempt %s): %s",
                    row.event_type,
                    attempts,
                    exc,
                    exc_info=True,
                )
                self._record_failure(row.id, attempts, max_attempts)
        return publishes

    def _record_failure(self, row_id: int, attempts: int, max_attempts: int) -> None:
        with self._db.request_conn() as conn:
            if attempts >= max_attempts:
                EventRepository(conn).record_failure(row_id, attempts=attempts, failed=True)
            else:
                backoff_ms = min(30_000, 1000 * 2**attempts)
                EventRepository(conn).record_failure(
                    row_id, attempts=attempts, failed=False, available_at=now_ms() + backoff_ms
                )

    def publish_once(self) -> int:
        """poll_once + 把事件广播到总线。返回处理/广播的事件数。测试用便捷入口。"""
        bus = self._require_bus()
        events = self.poll_once()
        for event in events:
            bus.publish(event)
        return len(events)
