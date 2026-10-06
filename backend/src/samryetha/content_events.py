"""Publish content side effects only after approval, once per created item."""

from sqlalchemy import func, select, update

from .db import now_ms
from .outbox import emit_event
from .schema import discussions, outbox_events, replies


class ContentAwaitingReview(Exception):
    """Keep an existing outbox event held until its content becomes public."""


def _resume_held(conn, discussion_id, reply_id=None):
    conds = [
        outbox_events.c.status == "held",
        outbox_events.c.aggregate_type == "discussion",
        outbox_events.c.aggregate_id == str(discussion_id),
        outbox_events.c.event_type.in_(["discussion.created", "reply.created", "mention.created"]),
        func.json_extract(outbox_events.c.payload, "$.discussionId") == discussion_id,
    ]
    if reply_id is not None:
        conds.append(func.json_extract(outbox_events.c.payload, "$.replyId") == reply_id)
    conn.execute(update(outbox_events).where(*conds).values(status="pending", available_at=now_ms()))


def _created_event_exists(conn, event_type, discussion_id, reply_id=None):
    conds = [outbox_events.c.event_type == event_type,
             outbox_events.c.aggregate_type == "discussion", outbox_events.c.aggregate_id == str(discussion_id),
             func.json_extract(outbox_events.c.payload, "$.discussionId") == discussion_id]
    if reply_id is not None:
        conds.append(func.json_extract(outbox_events.c.payload, "$.replyId") == reply_id)
    return conn.execute(select(outbox_events.c.id).where(*conds).limit(1)).first() is not None


def publish_content(conn, content_type: str, content_id: int) -> None:
    """Called after a content write/review claim has acquired the write lock.

    The existing creation event is the durable idempotency marker. New held
    submissions emit nothing; legacy events deferred by the worker are resumed.
    Approving a parent also releases its already approved replies.
    """
    if content_type not in ("discussion", "reply"):
        return
    reply = None
    if content_type == "reply":
        reply = conn.execute(select(replies).where(replies.c.id == content_id)).first()
        if reply is None or reply.deleted_at is not None or reply.moderation_status != "approved":
            return
        discussion_id = reply.discussion_id
    else:
        discussion_id = content_id
    disc = conn.execute(select(discussions).where(discussions.c.id == discussion_id)).first()
    if disc is None or disc.deleted_at is not None or disc.moderation_status != "approved":
        return
    _resume_held(conn, discussion_id, reply.id if reply is not None else None)
    from .discussions import _emit_mentions

    event_type = "reply.created" if reply is not None else "discussion.created"
    if not _created_event_exists(conn, event_type, discussion_id, reply.id if reply is not None else None):
        body = reply.body_md if reply is not None else disc.body_md
        author_id = reply.author_id if reply is not None else disc.author_id
        _emit_mentions(conn, body=body, author_id=author_id, discussion_id=discussion_id,
                       reply_id=reply.id if reply is not None else None, title=disc.title)
        payload = {"discussionId": discussion_id, "authorId": author_id, "title": disc.title}
        if reply is not None:
            payload.update(replyId=reply.id, parentReplyId=reply.parent_reply_id)
        else:
            payload["boardId"] = disc.board_id
        emit_event(conn, event_type, aggregate_type="discussion", aggregate_id=str(discussion_id), payload=payload)
    if reply is None:
        approved_replies = conn.execute(select(replies.c.id).where(
            replies.c.discussion_id == discussion_id, replies.c.moderation_status == "approved",
            replies.c.deleted_at.is_(None),
        )).all()
        for row in approved_replies:
            publish_content(conn, "reply", row.id)
