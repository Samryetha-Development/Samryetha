"""Typed SQLAlchemy persistence boundary for attachments."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import insert, or_, select, update
from sqlalchemy.engine import Connection, RowMapping

from .models import AttachmentRecord, AttachmentState, CreateAttachment
from ..core.db import now_ms
from ..core.ids import AttachmentID, DiscussionID, UserID
from ..core.schema import attachments, draft_attachments, users
from ..core.records import opt_int, require_int, require_str

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

def create_pending(
    conn: Connection,
    uploader_id: UserID,
    command: CreateAttachment,
    object_key: str,
    safe_mime_type: str,
) -> AttachmentID:
    value = conn.execute(
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

def get(conn: Connection, attachment_id: AttachmentID) -> AttachmentRecord | None:
    row = conn.execute(select(attachments).where(attachments.c.id == attachment_id)).mappings().first()
    return None if row is None else _record(row)

def get_by_object_key(conn: Connection, object_key: str) -> AttachmentRecord | None:
    row = conn.execute(select(attachments).where(attachments.c.object_key == object_key)).mappings().first()
    return None if row is None else _record(row)

def list_attached(conn: Connection, discussion_id: DiscussionID) -> list[AttachmentRecord]:
    rows = conn.execute(
        select(attachments).where(
            (attachments.c.discussion_id == discussion_id)
            & (attachments.c.state == AttachmentState.Attached.value)
        ).order_by(attachments.c.id)
    ).mappings()
    return [_record(row) for row in rows]

def user_status(conn: Connection, user_id: UserID) -> str | None:
    value = conn.execute(select(users.c.status).where(users.c.id == user_id)).scalar_one_or_none()
    return None if value is None else require_str(value, "user status")

def mark_uploaded(conn: Connection, object_key: str) -> bool:
    result = conn.execute(
        update(attachments)
        .where(
            (attachments.c.object_key == object_key)
            & (attachments.c.state == AttachmentState.Pending.value)
        )
        .values(state=AttachmentState.Uploaded.value)
    )
    return (result.rowcount or 0) == 1

def remove(conn: Connection, attachment_id: AttachmentID) -> None:
    conn.execute(attachments.delete().where(attachments.c.id == attachment_id))

def orphan_candidates(
    conn: Connection,
    pending_cutoff: int,
    uploaded_cutoff: int,
) -> Sequence[tuple[AttachmentID, str]]:
    pending_rows = conn.execute(
        select(attachments.c.id, attachments.c.object_key).where(
            (attachments.c.created_at < pending_cutoff)
            & or_(
                attachments.c.state == AttachmentState.Pending.value,
                attachments.c.state == AttachmentState.Orphaned.value,
            )
        )
    ).all()
    uploaded_rows = conn.execute(
        select(attachments.c.id, attachments.c.object_key).where(
            (attachments.c.state == AttachmentState.Uploaded.value)
            & (attachments.c.discussion_id.is_(None))
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
