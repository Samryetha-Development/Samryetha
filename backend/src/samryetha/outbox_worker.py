"""Transactional outbox 消费端 — 镜像 infrastructure/queue/worker.ts。

业务事务内 emit_event() 落 pending 行（见 outbox.py）；本模块的 worker 轮询：
原子 claim(pending→processing) → 顺序执行 dispatcher handler（通知/邮件/SSE 事件）
→ 成功置 done / 失败指数退避，超限转 failed。handler 通过返回的 publish 事件列表，
把"要广播到 SSE"的事件交给调用方在正确的线程 publish（事件总线见 events.py）。

poll_once() 是纯同步函数：测试可确定性调用；生产由 OutboxWorker 线程定时驱动。
"""

from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import and_, select, update

from . import notifications
from .db import Database, now_ms
from .errors import internal_error  # noqa: F401  (保留引用，handler 里区分 404 语义用)
from .mailer import ban_notification_text
from .schema import discussion_follows, discussions, notifications as notifications_table, outbox_events, replies, users

logger = logging.getLogger("samryetha.outbox")


# ---------------------------------------------------------------- dispatcher


class OutboxDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def on(self, event_type: str, handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def handlers_for(self, event_type: str) -> list:
        return self._handlers.get(event_type, [])


# ---------------------------------------------------------------- handlers
# handler 签名：(conn, payload: dict) -> list[dict]  # 返回要 publish 的事件


def _publish(user_id: int) -> list[dict]:
    return [{"type": "notification.created", "data": {"userId": user_id}}]


def _on_reply_created(conn, payload: dict) -> list[dict]:
    discussion_id = payload.get("discussionId")
    author_id = payload.get("authorId")
    title = payload.get("title") or ""
    disc = conn.execute(
        select(discussions.c.author_id, discussions.c.title).where(discussions.c.id == discussion_id)
    ).first()
    if disc is None or author_id is None:
        return []
    author_row = conn.execute(select(users).where(users.c.id == author_id)).first()
    actor_name = author_row.display_name if author_row else "Someone"
    follows = conn.execute(
        select(discussion_follows.c.user_id).where(discussion_follows.c.discussion_id == discussion_id)
    ).all()
    recipients = {disc.author_id}
    recipients.update(r.user_id for r in follows)
    # 嵌套回复：被回复的那条评论的作者也应收到通知
    # Nested reply: also notify the author of the parent reply being replied to
    parent_reply_id = payload.get("parentReplyId")
    if parent_reply_id:
        parent = conn.execute(select(replies.c.author_id).where(replies.c.id == parent_reply_id)).first()
        if parent is not None:
            recipients.add(parent.author_id)
    recipients.discard(author_id)
    body = f"{actor_name} 回复了「{title}」"
    out: list[dict] = []
    for uid in recipients:
        notifications.create(
            conn,
            user_id=uid,
            actor_user_id=author_id,
            type_="reply",
            discussion_id=discussion_id,
            reply_id=payload.get("replyId"),
            body=body,
        )
        out.extend(_publish(uid))
    return out


def _on_mention_created(conn, payload: dict) -> list[dict]:
    user_id = payload.get("mentionedUserId")
    author_id = payload.get("authorId")
    discussion_id = payload.get("discussionId")
    if not user_id or user_id == author_id or not discussion_id:
        return []
    existing = conn.execute(
        select(notifications_table.c.id).where(
            and_(
                notifications_table.c.user_id == user_id,
                notifications_table.c.actor_user_id == author_id,
                notifications_table.c.type == "mention",
                notifications_table.c.discussion_id == discussion_id,
                notifications_table.c.reply_id == payload.get("replyId"),
            )
        )
    ).first()
    if existing is not None:
        return []
    author = conn.execute(select(users).where(users.c.id == author_id)).first()
    name = author.display_name if author else "Someone"
    reply_text = "回复中" if payload.get("replyId") else "讨论中"
    notifications.create(
        conn,
        user_id=user_id,
        actor_user_id=author_id,
        type_="mention",
        discussion_id=discussion_id,
        reply_id=payload.get("replyId"),
        body=f"{name} 在{reply_text}提到了你",
    )
    return _publish(user_id)


def _on_message_created(conn, payload: dict) -> list[dict]:
    # 私信：通知收件方，让其未读徽标实时刷新
    # Direct message: notify the recipient so their unread badge refreshes in real time
    recipient_id = payload.get("recipientId")
    if not recipient_id:
        return []
    return _publish(recipient_id)


def _on_user_followed(conn, payload: dict) -> list[dict]:
    follower_id = payload.get("followerId")
    followee_id = payload.get("followeeId")
    if follower_id == followee_id:
        return []
    follower = conn.execute(select(users).where(users.c.id == follower_id)).first()
    if follower is None:
        return []
    notifications.create(
        conn,
        user_id=followee_id,
        actor_user_id=follower_id,
        type_="follow",
        body=f"{follower.display_name} 关注了你",
    )
    return _publish(followee_id)


def _on_user_banned(conn, payload: dict, mailer) -> list[dict]:
    """镜像 moderation/routes.ts user.banned handler：console 邮件 + 广播。"""
    user_id = payload.get("userId")
    user = conn.execute(select(users).where(users.c.id == user_id)).first()
    if user is None:
        return []
    reason = payload.get("reason")
    banned_until = payload.get("bannedUntil")  # ISO 字符串或 null
    mailer.send(
        to=user.email,
        subject="Samryetha 账号封禁通知",
        text=ban_notification_text(reason, banned_until),
    )
    return [{"type": "user.banned", "data": {"userId": user_id}}]


def register_outbox_handlers(dispatcher: OutboxDispatcher, mailer=None) -> None:
    if mailer is None:
        from .mailer import ConsoleMailer

        mailer = ConsoleMailer()
    dispatcher.on("reply.created", _on_reply_created)
    dispatcher.on("mention.created", _on_mention_created)
    dispatcher.on("message.created", _on_message_created)
    dispatcher.on("user.followed", _on_user_followed)
    dispatcher.on("user.banned", lambda conn, payload: _on_user_banned(conn, payload, mailer))


# ---------------------------------------------------------------- poll


def _parse_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def poll_once(db: Database, dispatcher: OutboxDispatcher, batch_size: int = 50, max_attempts: int = 10) -> list[dict]:
    """消费一批到期 pending 事件，返回要广播的事件列表。纯同步、可测试确定性调用。"""
    publishes: list[dict] = []
    with db.request_conn() as conn:
        rows = conn.execute(
            select(outbox_events)
            .where(
                (outbox_events.c.status == "pending")
                & (outbox_events.c.available_at <= now_ms())
            )
            .order_by(outbox_events.c.id)
            .limit(batch_size)
        ).all()
        if not rows:
            return []
        conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id.in_([r.id for r in rows]))
            .values(status="processing")
        )

    for row in rows:
        try:
            with db.request_conn() as conn:
                payload = _parse_payload(row.payload)
                for handler in dispatcher.handlers_for(row.event_type):
                    publishes.extend(handler(conn, payload) or [])
                conn.execute(
                    update(outbox_events)
                    .where(outbox_events.c.id == row.id)
                    .values(status="done", processed_at=now_ms())
                )
        except Exception as exc:  # noqa: BLE001 — 复刻 TS 逐事件失败处理
            attempts = (row.attempts or 0) + 1
            logger.warning(
                "[outbox] handler failed for %s (attempt %s): %s",
                row.event_type,
                attempts,
                exc,
                exc_info=True,
            )
            _record_failure(db, row.id, attempts, max_attempts)
    return publishes


def _record_failure(db: Database, row_id: int, attempts: int, max_attempts: int) -> None:
    with db.request_conn() as conn:
        if attempts >= max_attempts:
            conn.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row_id)
                .values(status="failed", attempts=attempts)
            )
        else:
            backoff_ms = min(30_000, 1000 * 2**attempts)
            conn.execute(
                update(outbox_events)
                .where(outbox_events.c.id == row_id)
                .values(status="pending", attempts=attempts, available_at=now_ms() + backoff_ms)
            )


def publish_once(db: Database, dispatcher: OutboxDispatcher, bus) -> int:
    """poll_once + 把事件广播到总线。返回处理/广播的事件数。测试用便捷入口。"""
    events = poll_once(db, dispatcher)
    for event in events:
        bus.publish(event)
    return len(events)


# ---------------------------------------------------------------- worker thread


class OutboxWorker:
    """后台线程每 interval_ms 轮询一次 outbox。仅生产 main() 启动，测试不用。"""

    def __init__(self, db: Database, dispatcher: OutboxDispatcher, bus, interval_ms: int = 500) -> None:
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
                publish_once(self.db, self.dispatcher, self.bus)
            except Exception:  # noqa: BLE001 — 轮询绝不能挂掉线程
                logger.exception("[outbox] poll error")
