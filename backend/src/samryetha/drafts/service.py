"""Typed business operations for private discussion drafts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, assert_can
from ..boards import get_board_for_authz
from ..errors import not_found, validation_failed
from ..ids import AttachmentID, DraftID, UserID
from ..adapters.storage import Storage, content_type_for_object_key
from . import repository
from .models import (
    DraftAttachment,
    DraftDetail,
    DraftPage,
    DraftRecord,
    DraftSummary,
    SaveDraft,
)


def _owned(conn: Connection, actor: Actor, draft_id: DraftID) -> DraftRecord:
    record = repository.owned(conn, UserID(actor.id), draft_id)
    if record is None:
        raise not_found("Draft not found")
    return record


def require_owned(conn: Connection, actor: Actor, draft_id: int) -> DraftRecord:
    """Compatibility entrypoint used by discussion publication concurrency checks."""
    return _owned(conn, actor, DraftID(draft_id))


def list_drafts(
    conn: Connection,
    actor: Actor,
    before_id: int | None = None,
    limit: int = 20,
) -> DraftPage:
    records, has_more = repository.list_records(
        conn,
        UserID(actor.id),
        DraftID(before_id) if before_id is not None else None,
        min(limit, 50),
    )
    items = tuple(
        DraftSummary(
            id=record.id,
            board_slug=record.board_slug,
            title=record.title,
            preview=" ".join(record.body_markdown.split())[:160],
            body_format=record.body_format,
            attachment_count=attachment_count,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record, attachment_count in records
    )
    next_cursor = str(items[-1].id) if has_more and items else None
    return DraftPage(items=items, next_cursor=next_cursor)


def get_draft(conn: Connection, actor: Actor, draft_id: int, storage: Storage) -> DraftDetail:
    record = _owned(conn, actor, DraftID(draft_id))
    attachment_items: list[DraftAttachment] = []
    for attachment in repository.attachment_records(conn, record.id):
        mime_type = content_type_for_object_key(attachment.object_key)
        attachment_items.append(
            DraftAttachment(
                id=attachment.id,
                object_key=attachment.object_key,
                original_filename=attachment.original_filename,
                mime_type=mime_type,
                size_bytes=attachment.size_bytes,
                is_image=mime_type.startswith("image/"),
                download_url=storage.generate_download_url(attachment.object_key),
            )
        )
    return DraftDetail(
        id=record.id,
        board_slug=record.board_slug,
        title=record.title,
        body_markdown=record.body_markdown,
        body_format=record.body_format,
        attachments=tuple(attachment_items),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def save_draft(
    conn: Connection,
    actor: Actor,
    command: SaveDraft,
    storage: Storage,
    draft_id: int | None = None,
) -> DraftDetail:
    typed_draft_id = DraftID(draft_id) if draft_id is not None else None
    if typed_draft_id is not None:
        _owned(conn, actor, typed_draft_id)
    if command.board_slug is not None:
        board = get_board_for_authz(conn, command.board_slug)
        if board is None:
            raise not_found("Board not found")
        assert_can(actor, Abilities.DISCUSSION_READ, {"type": "board", **board}, conn)
    attachment_ids = tuple(dict.fromkeys(command.attachment_ids))
    owners = repository.available_attachment_owners(conn, UserID(actor.id), attachment_ids)
    if len(owners) != len(attachment_ids) or any(
        owner is not None and owner != typed_draft_id for owner in owners.values()
    ):
        raise validation_failed(
            [{"field": "attachmentIds", "message": "One or more attachments are unavailable", "code": "custom"}]
        )
    normalized_command = replace(command, attachment_ids=attachment_ids)
    saved_id = repository.save(conn, UserID(actor.id), normalized_command, typed_draft_id)
    return get_draft(conn, actor, saved_id, storage)


def delete_draft(conn: Connection, actor: Actor, draft_id: int) -> None:
    typed_draft_id = DraftID(draft_id)
    _owned(conn, actor, typed_draft_id)
    repository.delete(conn, typed_draft_id)


def validate_publish_attachments(conn: Connection, draft_id: int | None, attachment_ids: Sequence[int]) -> None:
    typed_draft_id = DraftID(draft_id) if draft_id is not None else None
    referenced = repository.referenced_draft_ids(conn, [AttachmentID(value) for value in attachment_ids])
    if any(value != typed_draft_id for value in referenced):
        raise validation_failed(
            [{"field": "attachmentIds", "message": "Attachment belongs to another draft", "code": "custom"}]
        )
