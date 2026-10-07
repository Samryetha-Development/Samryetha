"""Typed attachment service — upload sessions, visibility, and cleanup."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.engine import Connection

from . import repository
from .models import (
    AttachmentDetail,
    AttachmentRecord,
    AttachmentState,
    AttachmentView,
    CreateAttachment,
    PresignResult,
)
from ..authz import Abilities, Actor, assert_can, can
from ..db import now_ms
from ..errors import APIError, internal_error, not_found
from ..ids import AttachmentID, DiscussionID, UserID
from ..schema import boards
from ..adapters.storage import Storage, content_type_for_object_key

logger = logging.getLogger("samryetha.attachments")


def presign(
    conn: Connection,
    actor: Actor | None,
    command: CreateAttachment,
    storage: Storage,
) -> PresignResult:
    if actor is None:
        raise internal_error()
    assert_can(actor, Abilities.ATTACHMENT_CREATE, None, conn)
    object_key = storage.create_upload_session(
        uploader_id=actor.id,
        original_filename=command.filename,
        mime_type=command.mime_type,
        size_bytes=command.size_bytes,
    )
    safe_mime_type = content_type_for_object_key(object_key)
    attachment_id = repository.create_pending(
        conn,
        UserID(actor.id),
        command,
        object_key,
        safe_mime_type,
    )
    upload = storage.generate_upload_url(object_key, content_type=safe_mime_type)
    return PresignResult(
        attachment_id=attachment_id,
        object_key=object_key,
        upload_url=upload["url"],
        upload_method=upload["method"],
        upload_headers=upload["headers"],
    )


def to_attachment(record: AttachmentRecord, storage: Storage) -> AttachmentView:
    mime = content_type_for_object_key(record.object_key)
    return AttachmentView(
        id=record.id,
        object_key=record.object_key,
        original_filename=record.original_filename,
        mime_type=mime,
        size_bytes=record.size_bytes,
        is_image=mime.startswith("image/"),
        download_url=storage.generate_download_url(record.object_key),
    )


def _can_read(actor: Actor | None, record: AttachmentRecord, conn: Connection) -> bool:
    if actor is not None and record.uploader_id == actor.id:
        return True
    return can(actor, Abilities.ATTACHMENT_MODERATE, None, conn)


def downloadable(conn: Connection, actor: Actor | None, record: AttachmentRecord) -> bool:
    """A signature never overrides the current parent moderation state."""
    if record.state is AttachmentState.Orphaned:
        return False
    if record.discussion_id is None:
        return True

    from ..discussions import assert_content_visible, get_discussion_row

    parent = get_discussion_row(conn, record.discussion_id)
    if parent is None or parent.deleted_at is not None:
        return False
    if parent.moderation_status.value == "approved":
        return True
    try:
        assert_content_visible(parent, actor)
    except APIError:
        return False
    board = conn.execute(
        select(boards).where(
            boards.c.id == parent.board_id,
            boards.c.deleted_at.is_(None),
        )
    ).first()
    return board is not None and can(
        actor,
        Abilities.DISCUSSION_READ,
        {
            "type": "board",
            "id": board.id,
            "visibility": board.visibility,
            "postingPolicy": board.posting_policy,
        },
        conn,
    )


def get_by_id(
    conn: Connection,
    actor: Actor | None,
    attachment_id: AttachmentID,
    storage: Storage,
) -> AttachmentDetail:
    record = repository.get(conn, attachment_id)
    if record is None or not _can_read(actor, record, conn) or not downloadable(conn, actor, record):
        raise not_found("Attachment not found")
    view = to_attachment(record, storage)
    return AttachmentDetail(
        id=view.id,
        object_key=view.object_key,
        original_filename=view.original_filename,
        mime_type=view.mime_type,
        size_bytes=view.size_bytes,
        is_image=view.is_image,
        download_url=view.download_url,
        state=record.state,
        created_at=record.created_at,
    )


def list_for_discussion(
    conn: Connection,
    discussion_id: int,
    storage: Storage,
    viewer: Actor | None = None,
) -> list[AttachmentView]:
    records = repository.list_attached(conn, DiscussionID(discussion_id))
    return [to_attachment(record, storage) for record in records if downloadable(conn, viewer, record)]


def reap_orphans(
    conn: Connection,
    storage: Storage,
    older_than_ms: int = 24 * 3600 * 1000,
    uploaded_older_than_ms: int = 7 * 24 * 3600 * 1000,
) -> int:
    """Remove expired never-attached or deleted-discussion attachments."""
    now = now_ms()
    candidates = repository.orphan_candidates(
        conn,
        pending_cutoff=now - older_than_ms,
        uploaded_cutoff=now - uploaded_older_than_ms,
    )
    removed = 0
    for attachment_id, object_key in candidates:
        try:
            storage.delete_object(object_key)
        except Exception:
            logger.warning(
                "[attachments] reap skipped bad key id=%s key=%r",
                attachment_id,
                object_key,
                exc_info=True,
            )
            continue
        repository.remove(conn, attachment_id)
        removed += 1
    return removed


def delete(
    conn: Connection,
    actor: Actor | None,
    attachment_id: AttachmentID,
    storage: Storage,
) -> None:
    record = repository.get(conn, attachment_id)
    if record is None or not _can_read(actor, record, conn):
        raise not_found("Attachment not found")
    if record.discussion_id is not None and not downloadable(conn, actor, record):
        raise not_found("Attachment not found")
    repository.remove(conn, attachment_id)
    storage.delete_object(record.object_key)
