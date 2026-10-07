"""Typed SQLAlchemy persistence boundary for content and outbox event processing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import DiscussionID, ReplyID, UserID
from ..core.records import opt_int, opt_str, require_int, require_str
from ..core.schema import (
    discussion_follows,
    discussions,
    notifications,
    outbox_events,
    replies,
    users,
)


@dataclass(frozen=True, slots=True)
class DiscussionRecord:
    id: DiscussionID
    author_id: UserID
    board_id: int
    title: str
    body_md: str
    moderation_status: str
    deleted_at: int | None


@dataclass(frozen=True, slots=True)
class ReplyRecord:
    id: ReplyID
    discussion_id: DiscussionID
    author_id: UserID
    parent_reply_id: ReplyID | None
    body_md: str
    moderation_status: str
    deleted_at: int | None


@dataclass(frozen=True, slots=True)
class UserContactRecord:
    id: UserID
    display_name: str
    email: str


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: int
    event_type: str
    payload: str | None
    attempts: int


def _outbox_record(row: RowMapping) -> OutboxRecord:
    return OutboxRecord(
        id=require_int(row["id"], "outbox id"),
        event_type=require_str(row["event_type"], "event type"),
        payload=opt_str(row["payload"], "payload"),
        attempts=require_int(row["attempts"], "attempts"),
    )


class EventRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def discussion(self, discussion_id: int) -> DiscussionRecord | None:
        row = self._conn.execute(select(discussions).where(discussions.c.id == discussion_id)).mappings().first()
        if row is None:
            return None
        return DiscussionRecord(
            id=DiscussionID(require_int(row["id"], "discussion id")),
            author_id=UserID(require_int(row["author_id"], "author id")),
            board_id=require_int(row["board_id"], "board id"),
            title=require_str(row["title"], "title"),
            body_md=require_str(row["body_md"], "body"),
            moderation_status=require_str(row["moderation_status"], "moderation status"),
            deleted_at=opt_int(row["deleted_at"], "deleted at"),
        )

    def reply(self, reply_id: int, discussion_id: int | None = None) -> ReplyRecord | None:
        statement = select(replies).where(replies.c.id == reply_id)
        if discussion_id is not None:
            statement = statement.where(replies.c.discussion_id == discussion_id)
        row = self._conn.execute(statement).mappings().first()
        if row is None:
            return None
        parent = opt_int(row["parent_reply_id"], "parent reply id")
        return ReplyRecord(
            id=ReplyID(require_int(row["id"], "reply id")),
            discussion_id=DiscussionID(require_int(row["discussion_id"], "discussion id")),
            author_id=UserID(require_int(row["author_id"], "author id")),
            parent_reply_id=ReplyID(parent) if parent is not None else None,
            body_md=require_str(row["body_md"], "body"),
            moderation_status=require_str(row["moderation_status"], "moderation status"),
            deleted_at=opt_int(row["deleted_at"], "deleted at"),
        )

    def approved_reply_ids(self, discussion_id: int) -> list[ReplyID]:
        values = (
            self._conn.execute(
                select(replies.c.id).where(
                    replies.c.discussion_id == discussion_id,
                    replies.c.moderation_status == "approved",
                    replies.c.deleted_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        return [ReplyID(require_int(value, "reply id")) for value in values]

    def resume_held(self, discussion_id: int, reply_id: int | None, *, available_at: int) -> None:
        conditions = [
            outbox_events.c.status == "held",
            outbox_events.c.aggregate_type == "discussion",
            outbox_events.c.aggregate_id == str(discussion_id),
            outbox_events.c.event_type.in_(["discussion.created", "reply.created", "mention.created"]),
            func.json_extract(outbox_events.c.payload, "$.discussionId") == discussion_id,
        ]
        if reply_id is not None:
            conditions.append(func.json_extract(outbox_events.c.payload, "$.replyId") == reply_id)
        self._conn.execute(update(outbox_events).where(*conditions).values(status="pending", available_at=available_at))

    def created_event_exists(self, event_type: str, discussion_id: int, reply_id: int | None) -> bool:
        conditions = [
            outbox_events.c.event_type == event_type,
            outbox_events.c.aggregate_type == "discussion",
            outbox_events.c.aggregate_id == str(discussion_id),
            func.json_extract(outbox_events.c.payload, "$.discussionId") == discussion_id,
        ]
        if reply_id is not None:
            conditions.append(func.json_extract(outbox_events.c.payload, "$.replyId") == reply_id)
        return self._conn.execute(select(outbox_events.c.id).where(*conditions).limit(1)).first() is not None

    def already_notified(self, user_id: UserID, event_id: int) -> bool:
        return (
            self._conn.execute(
                select(notifications.c.id).where(
                    and_(notifications.c.user_id == user_id, notifications.c.source_event_id == event_id)
                )
            ).first()
            is not None
        )

    def user_contact(self, user_id: int) -> UserContactRecord | None:
        row = (
            self._conn.execute(select(users.c.id, users.c.display_name, users.c.email).where(users.c.id == user_id))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return UserContactRecord(
            id=UserID(require_int(row["id"], "user id")),
            display_name=require_str(row["display_name"], "display name"),
            email=require_str(row["email"], "email"),
        )

    def discussion_follower_ids(self, discussion_id: int) -> set[UserID]:
        return {
            UserID(require_int(value, "user id"))
            for value in self._conn.execute(
                select(discussion_follows.c.user_id).where(discussion_follows.c.discussion_id == discussion_id)
            ).scalars()
        }

    def reply_author_id(self, reply_id: int) -> UserID | None:
        value = self._conn.execute(select(replies.c.author_id).where(replies.c.id == reply_id)).scalar_one_or_none()
        return UserID(require_int(value, "author id")) if value is not None else None

    def reclaim_stale_processing(self, *, cutoff: int) -> int:
        result = self._conn.execute(
            update(outbox_events)
            .where(
                outbox_events.c.status == "processing",
                outbox_events.c.processing_at.is_not(None),
                outbox_events.c.processing_at <= cutoff,
            )
            .values(status="pending", processing_at=None)
        )
        return result.rowcount or 0

    def pending_batch(self, *, available_at: int, limit: int) -> list[OutboxRecord]:
        rows: Sequence[RowMapping] = (
            self._conn.execute(
                select(outbox_events)
                .where(outbox_events.c.status == "pending", outbox_events.c.available_at <= available_at)
                .order_by(outbox_events.c.id)
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [_outbox_record(row) for row in rows]

    def mark_processing(self, event_ids: list[int], *, processing_at: int) -> None:
        self._conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id.in_(event_ids))
            .values(status="processing", processing_at=processing_at)
        )

    def mark_done(self, event_id: int, *, processed_at: int) -> None:
        self._conn.execute(
            update(outbox_events).where(outbox_events.c.id == event_id).values(status="done", processed_at=processed_at)
        )

    def mark_held(self, event_id: int) -> None:
        self._conn.execute(
            update(outbox_events).where(outbox_events.c.id == event_id).values(status="held", processing_at=None)
        )

    def release_held_result(self, event_id: int, *, pending: bool, available_at: int) -> None:
        self._conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id == event_id)
            .values(status="pending" if pending else "done", available_at=available_at)
        )

    def record_failure(self, event_id: int, *, attempts: int, failed: bool, available_at: int | None = None) -> None:
        values: dict[str, object] = {"status": "failed" if failed else "pending", "attempts": attempts}
        if available_at is not None:
            values["available_at"] = available_at
        self._conn.execute(update(outbox_events).where(outbox_events.c.id == event_id).values(**values))
