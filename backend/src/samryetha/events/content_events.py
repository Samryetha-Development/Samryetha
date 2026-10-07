"""Publish content side effects only after approval, once per created item."""

from samryetha.events.repository import EventRepository

from sqlalchemy.engine import Connection

from ..core.db import now_ms
from .outbox import OutboxWriter
from ..notifications.models import ReplyCreatedPayload


class ContentAwaitingReview(Exception):
    """Keep an existing outbox event held until its content becomes public."""


class ContentEventService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._outbox = OutboxWriter(self._conn)
        self._repository = EventRepository(self._conn)

    def _resume_held(self, discussion_id: int, reply_id: int | None = None) -> None:
        self._repository.resume_held(discussion_id, reply_id, available_at=now_ms())

    def _created_event_exists(self, event_type: str, discussion_id: int, reply_id: int | None = None) -> bool:
        return self._repository.created_event_exists(event_type, discussion_id, reply_id)

    def publish_content(self, content_type: str, content_id: int) -> None:
        """Called after a content write/review claim has acquired the write lock.

        The existing creation event is the durable idempotency marker. New held
        submissions emit nothing; legacy events deferred by the worker are resumed.
        Approving a parent also releases its already approved replies.
        """
        if content_type not in ("discussion", "reply"):
            return
        reply = None
        if content_type == "reply":
            reply = self._repository.reply(content_id)
            if reply is None or reply.deleted_at is not None or reply.moderation_status != "approved":
                return
            discussion_id = reply.discussion_id
        else:
            discussion_id = content_id
        disc = self._repository.discussion(discussion_id)
        if disc is None or disc.deleted_at is not None or disc.moderation_status != "approved":
            return
        self._resume_held(discussion_id, reply.id if reply is not None else None)
        from ..discussions import DiscussionService

        event_type = "reply.created" if reply is not None else "discussion.created"
        if not self._created_event_exists(event_type, discussion_id, reply.id if reply is not None else None):
            body = reply.body_md if reply is not None else disc.body_md
            author_id = reply.author_id if reply is not None else disc.author_id
            DiscussionService(self._conn).emit_mentions(
                body=body,
                author_id=author_id,
                discussion_id=discussion_id,
                reply_id=reply.id if reply is not None else None,
                title=disc.title,
            )
            if reply is not None:
                payload = ReplyCreatedPayload(
                    discussion_id=discussion_id,
                    author_id=author_id,
                    reply_id=reply.id,
                    parent_reply_id=reply.parent_reply_id,
                )
            else:
                payload = {
                    "discussionId": discussion_id,
                    "authorId": author_id,
                    "title": disc.title,
                    "boardId": disc.board_id,
                }
            self._outbox.emit(event_type, aggregate_type="discussion", aggregate_id=str(discussion_id), payload=payload)
        if reply is None:
            for approved_reply_id in self._repository.approved_reply_ids(discussion_id):
                self.publish_content("reply", approved_reply_id)
