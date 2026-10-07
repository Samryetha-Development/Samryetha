"""Typed SQLAlchemy persistence boundary for attachments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import insert, or_, select, update
from sqlalchemy.engine import Connection, RowMapping

from .models import AttachmentRecord, AttachmentState, CreateAttachment
from ..core.db import now_ms
from ..core.ids import AttachmentID, DiscussionID, UserID
from ..core.schema import attachments, boards, draft_attachments, users
from ..core.records import opt_int, require_int, require_str


@dataclass(frozen=True, slots=True)
class BoardAccessRecord:
    id: int
    visibility: str
    posting_policy: str


def _record(row: RowMapping) -> AttachmentRecord:
    discussion_id = opt_int(row["discussion_id"], "discussion id")
    return AttachmentRecord(
        id=AttachmentID(require_int(row["id"], "attachment id")),
        uploader_id=UserID(require_int(row["uploader_id"], "uploader id")),
        discussion_id=DiscussionID(discussion_id) if discussion_id is not None else None,
        object_key=require_str(row["object_key"], "object key"),
        original_filename=require_str(row["original_filename"], "original filename"),
        mime_type=require_str(row["mime_type"], "mime type"),
        size_bytes=require_int(row["size_bytes"], "size bytes"),
        state=AttachmentState(require_str(row["state"], "state")),
        created_at=require_int(row["created_at"], "created at"),
    )


class AttachmentRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create_pending(
        self, uploader_id: UserID, command: CreateAttachment, object_key: str, safe_mime_type: str
    ) -> AttachmentID:
        value = self._conn.execute(
            insert(attachments)
            .values(
                uploader_id=uploader_id,
                object_key=object_key,
                original_filename=command.filename,
                mime_type=safe_mime_type,
                size_bytes=command.size_bytes,
                created_at=now_ms(),
            )
            .returning(attachments.c.id)
        ).scalar_one()
        return AttachmentID(require_int(value, "inserted attachment id"))

    def get(self, attachment_id: AttachmentID) -> AttachmentRecord | None:
        row = self._conn.execute(select(attachments).where(attachments.c.id == attachment_id)).mappings().first()
        return None if row is None else _record(row)

    def get_by_object_key(self, object_key: str) -> AttachmentRecord | None:
        row = self._conn.execute(select(attachments).where(attachments.c.object_key == object_key)).mappings().first()
        return None if row is None else _record(row)

    def list_attached(self, discussion_id: DiscussionID) -> list[AttachmentRecord]:
        rows = self._conn.execute(
            select(attachments)
            .where(
                (attachments.c.discussion_id == discussion_id) & (attachments.c.state == AttachmentState.Attached.value)
            )
            .order_by(attachments.c.id)
        ).mappings()
        return [_record(row) for row in rows]

    def user_status(self, user_id: UserID) -> str | None:
        value = self._conn.execute(select(users.c.status).where(users.c.id == user_id)).scalar_one_or_none()
        return None if value is None else require_str(value, "user status")

    def mark_uploaded(self, object_key: str) -> bool:
        result = self._conn.execute(
            update(attachments)
            .where((attachments.c.object_key == object_key) & (attachments.c.state == AttachmentState.Pending.value))
            .values(state=AttachmentState.Uploaded.value)
        )
        return (result.rowcount or 0) == 1

    def remove(self, attachment_id: AttachmentID) -> None:
        self._conn.execute(attachments.delete().where(attachments.c.id == attachment_id))

    def orphan_candidates(self, pending_cutoff: int, uploaded_cutoff: int) -> Sequence[tuple[AttachmentID, str]]:
        pending_rows = self._conn.execute(
            select(attachments.c.id, attachments.c.object_key).where(
                (attachments.c.created_at < pending_cutoff)
                & or_(
                    attachments.c.state == AttachmentState.Pending.value,
                    attachments.c.state == AttachmentState.Orphaned.value,
                )
            )
        ).all()
        uploaded_rows = self._conn.execute(
            select(attachments.c.id, attachments.c.object_key).where(
                (attachments.c.state == AttachmentState.Uploaded.value)
                & attachments.c.discussion_id.is_(None)
                & (attachments.c.created_at < uploaded_cutoff)
                & ~select(draft_attachments.c.attachment_id)
                .where(draft_attachments.c.attachment_id == attachments.c.id)
                .exists()
            )
        ).all()
        return [
            (AttachmentID(require_int(row.id, "attachment id")), require_str(row.object_key, "object key"))
            for row in (*pending_rows, *uploaded_rows)
        ]

    def active_board(self, board_id: int) -> BoardAccessRecord | None:
        row = (
            self._conn.execute(
                select(boards.c.id, boards.c.visibility, boards.c.posting_policy).where(
                    boards.c.id == board_id, boards.c.deleted_at.is_(None)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return BoardAccessRecord(
            id=require_int(row["id"], "board id"),
            visibility=require_str(row["visibility"], "board visibility"),
            posting_policy=require_str(row["posting_policy"], "board posting policy"),
        )
