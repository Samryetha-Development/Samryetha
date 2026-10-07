"""Typed SQLAlchemy persistence boundary for automatic moderation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from ..core.ids import ModerationQueueID
from ..core.records import require_int, require_str
from ..core.schema import (
    attachments,
    boards,
    direct_messages,
    discussions,
    moderation_queue,
    replies,
    users,
)


@dataclass(frozen=True, slots=True)
class ActorRecord:
    status: str
    role: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class ContentSnapshot:
    exists: bool
    text: str
    title: str | None
    is_public_board: bool


@dataclass(frozen=True, slots=True)
class QueueFinalizationRecord:
    id: ModerationQueueID
    content_type: str
    content_id: int


class AutomodRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def actor(self, user_id: int) -> ActorRecord | None:
        row = self._conn.execute(
            select(users.c.status, users.c.role, users.c.deleted_at).where(users.c.id == user_id)
        ).first()
        if row is None:
            return None
        return ActorRecord(status=row.status, role=row.role, deleted=row.deleted_at is not None)

    def has_discussion(self, author_id: int) -> bool:
        return (
            self._conn.execute(select(discussions.c.id).where(discussions.c.author_id == author_id).limit(1)).first()
            is not None
        )

    def supersede_content(self, *, content_type: str, content_id: int, now: int) -> None:
        self._conn.execute(
            moderation_queue.update()
            .where(
                moderation_queue.c.content_type == content_type,
                moderation_queue.c.content_id == content_id,
                moderation_queue.c.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )

    def insert_queue_item(self, values: dict[str, object]) -> ModerationQueueID:
        value = self._conn.execute(
            moderation_queue.insert().values(**values).returning(moderation_queue.c.id)
        ).scalar_one()
        return ModerationQueueID(require_int(value, "inserted queue id"))

    def update_content_status(self, *, content_type: str, content_id: int, status: str) -> None:
        tables = {"discussion": discussions, "reply": replies, "message": direct_messages}
        table = tables.get(content_type)
        if table is not None:
            self._conn.execute(table.update().where(table.c.id == content_id).values(moderation_status=status))

    def update_profile_status(self, user_id: int, status: str) -> None:
        self._conn.execute(users.update().where(users.c.id == user_id).values(profile_moderation_status=status))

    def orphan_attachment(self, attachment_id: int) -> None:
        self._conn.execute(attachments.update().where(attachments.c.id == attachment_id).values(state="orphaned"))

    def load_content(self, *, content_type: str, content_id: int) -> ContentSnapshot:
        if content_type == "discussion":
            row = self._conn.execute(
                select(discussions.c.title, discussions.c.body_md, boards.c.visibility)
                .select_from(discussions.join(boards, discussions.c.board_id == boards.c.id))
                .where(discussions.c.id == content_id)
            ).first()
            return (
                ContentSnapshot(False, "", None, True)
                if row is None
                else ContentSnapshot(
                    True,
                    row.body_md or "",
                    row.title,
                    row.visibility == "public",
                )
            )
        if content_type == "reply":
            row = self._conn.execute(
                select(replies.c.body_md, boards.c.visibility)
                .select_from(
                    replies.join(discussions, replies.c.discussion_id == discussions.c.id).join(
                        boards, discussions.c.board_id == boards.c.id
                    )
                )
                .where(replies.c.id == content_id)
            ).first()
            return (
                ContentSnapshot(False, "", None, True)
                if row is None
                else ContentSnapshot(
                    True,
                    row.body_md or "",
                    None,
                    row.visibility == "public",
                )
            )
        if content_type == "message":
            row = self._conn.execute(select(direct_messages.c.body).where(direct_messages.c.id == content_id)).first()
            return ContentSnapshot(row is not None, (row.body if row else "") or "", None, False)
        if content_type == "profile":
            row = self._conn.execute(
                select(users.c.display_name, users.c.bio, users.c.pending_display_name, users.c.pending_bio).where(
                    users.c.id == content_id
                )
            ).first()
            if row is None:
                return ContentSnapshot(False, "", None, True)
            display = row.pending_display_name or row.display_name
            bio = row.pending_bio if row.pending_bio is not None else row.bio
            return ContentSnapshot(True, f"{display}\n{bio or ''}", None, True)
        if content_type == "attachment":
            row = self._conn.execute(
                select(attachments.c.original_filename).where(attachments.c.id == content_id)
            ).first()
            return ContentSnapshot(row is not None, (row.original_filename if row else "") or "", None, True)
        return ContentSnapshot(False, "", None, True)

    def claim_finalization(
        self, queue_id: ModerationQueueID, *, resolution: str, resolved_at: int, recheck: str
    ) -> bool:
        result = self._conn.execute(
            moderation_queue.update()
            .where(
                moderation_queue.c.id == queue_id,
                moderation_queue.c.resolution.is_(None),
                moderation_queue.c.superseded_at.is_(None),
                moderation_queue.c.review_state == "pending",
            )
            .values(resolution=resolution, resolved_at=resolved_at, recheck=recheck)
        )
        return result.rowcount == 1

    def pending_finalizations(self, *, moment: int, limit: int) -> list[QueueFinalizationRecord]:
        rows = (
            self._conn.execute(
                select(moderation_queue.c.id, moderation_queue.c.content_type, moderation_queue.c.content_id)
                .where(
                    moderation_queue.c.review_state == "pending",
                    moderation_queue.c.resolution.is_(None),
                    moderation_queue.c.superseded_at.is_(None),
                    moderation_queue.c.hold_until.is_not(None),
                    moderation_queue.c.hold_until <= moment,
                )
                .order_by(moderation_queue.c.hold_until.asc(), moderation_queue.c.id.asc())
                .limit(limit)
            )
            .mappings()
            .all()
        )
        return [
            QueueFinalizationRecord(
                id=ModerationQueueID(require_int(row["id"], "queue id")),
                content_type=require_str(row["content_type"], "content type"),
                content_id=require_int(row["content_id"], "content id"),
            )
            for row in rows
        ]

    def queue_counts(self, *, blocked_resolutions: tuple[str, ...], ai_published: str) -> dict[str, int]:
        rows = self._conn.execute(
            select(moderation_queue.c.review_state, func.count())
            .where(moderation_queue.c.superseded_at.is_(None))
            .group_by(moderation_queue.c.review_state)
        ).all()
        counts = {"pending": 0, "approved": 0, "rejected": 0}
        for state, total in rows:
            if state in counts:
                counts[state] = int(total)

        def count(*conditions: ColumnElement[bool]) -> int:
            return int(
                self._conn.execute(select(func.count()).select_from(moderation_queue).where(*conditions)).scalar_one()
            )

        active = moderation_queue.c.superseded_at.is_(None)
        counts["awaiting"] = count(
            active, moderation_queue.c.review_state == "pending", moderation_queue.c.resolution.is_(None)
        )
        counts["aiPublished"] = count(
            active, moderation_queue.c.review_state == "pending", moderation_queue.c.resolution == ai_published
        )
        counts["aiBlocked"] = count(
            active, moderation_queue.c.resolution.in_(blocked_resolutions), moderation_queue.c.reviewer_id.is_(None)
        )
        counts["blocked"] = count(moderation_queue.c.resolution.in_(blocked_resolutions))
        return counts
