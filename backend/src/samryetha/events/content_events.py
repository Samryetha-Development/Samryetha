"""Publish content side effects after writes, once per created item."""

from samryetha.events.repository import EventRepository

from sqlalchemy.engine import Connection

from .outbox import OutboxWriter
from ..notifications.models import ReplyCreatedPayload


class ContentEventService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._outbox = OutboxWriter(self._conn)
        self._repository = EventRepository(self._conn)

    def _created_event_exists(self, event_type: str, discussion_id: int, reply_id: int | None = None) -> bool:
        return self._repository.created_event_exists(event_type, discussion_id, reply_id)

    def publish_content(self, content_type: str, content_id: int) -> None:
        """Emit creation side effects once, within the content write transaction."""
        if content_type not in ("discussion", "reply"):
            return
        reply = None
        if content_type == "reply":
            reply = self._repository.reply(content_id)
            if reply is None or reply.deleted_at is not None:
                return
            discussion_id = reply.discussion_id
        else:
            discussion_id = content_id
        disc = self._repository.discussion(discussion_id)
        if disc is None or disc.deleted_at is not None:
            return
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
            for reply_id in self._repository.active_reply_ids(discussion_id):
                self.publish_content("reply", reply_id)
