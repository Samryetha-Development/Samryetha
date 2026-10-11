"""Typed business operations for private discussion drafts."""

from __future__ import annotations

from samryetha.drafts.repository import DraftRepository

from collections.abc import Sequence
from dataclasses import replace

from sqlalchemy.engine import Connection

from ..authz import Abilities, Actor, AuthorizationService
from ..boards import BoardService
from ..core.errors import not_found, validation_failed
from ..core.ids import AttachmentID, DraftID, UserID
from ..adapters.storage import Storage, content_type_for_object_key
from .models import (
    DraftAttachment,
    DraftDetail,
    DraftPage,
    DraftRecord,
    DraftSummary,
    SaveDraft,
)


class DraftService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, storage: Storage | None = None) -> None:
        self._conn = conn
        self._storage = storage
        self._repository = DraftRepository(self._conn)

    def _require_storage(self) -> Storage:
        if self._storage is None:
            raise RuntimeError("DraftService operation requires storage")
        return self._storage

    def _owned(self, actor: Actor, draft_id: DraftID) -> DraftRecord:
        record = self._repository.owned(UserID(actor.id), draft_id)
        if record is None:
            raise not_found("Draft not found")
        return record

    def require_owned(self, actor: Actor, draft_id: int) -> DraftRecord:
        """Ownership snapshot used by discussion publication concurrency checks."""
        return self._owned(actor, DraftID(draft_id))

    def list_drafts(self, actor: Actor, before_id: int | None = None, limit: int = 20) -> DraftPage:
        records, has_more = self._repository.list_records(
            UserID(actor.id), DraftID(before_id) if before_id is not None else None, min(limit, 50)
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

    def get_draft(self, actor: Actor, draft_id: int) -> DraftDetail:
        storage = self._require_storage()
        record = self._owned(actor, DraftID(draft_id))
        attachment_items: list[DraftAttachment] = []
        for attachment in self._repository.attachment_records(record.id):
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
            poll=record.poll,
            body_format=record.body_format,
            attachments=tuple(attachment_items),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def save_draft(self, actor: Actor, command: SaveDraft, draft_id: int | None = None) -> DraftDetail:
        self._require_storage()
        typed_draft_id = DraftID(draft_id) if draft_id is not None else None
        if typed_draft_id is not None:
            self._owned(actor, typed_draft_id)
        if command.board_slug is not None:
            board = BoardService(self._conn).get_board_for_authz(command.board_slug)
            if board is None:
                raise not_found("Board not found")
            AuthorizationService(self._conn).assert_can(actor, Abilities.DISCUSSION_READ, {"type": "board", **board})
        attachment_ids = tuple(dict.fromkeys(command.attachment_ids))
        owners = self._repository.available_attachment_owners(UserID(actor.id), attachment_ids)
        if len(owners) != len(attachment_ids) or any(
            owner is not None and owner != typed_draft_id for owner in owners.values()
        ):
            raise validation_failed(
                [{"field": "attachmentIds", "message": "One or more attachments are unavailable", "code": "custom"}]
            )
        normalized_command = replace(command, attachment_ids=attachment_ids)
        saved_id = self._repository.save(UserID(actor.id), normalized_command, typed_draft_id)
        return self.get_draft(actor, saved_id)

    def delete_draft(self, actor: Actor, draft_id: int) -> None:
        typed_draft_id = DraftID(draft_id)
        self._owned(actor, typed_draft_id)
        self._repository.delete(typed_draft_id)

    def validate_publish_attachments(self, draft_id: int | None, attachment_ids: Sequence[int]) -> None:
        typed_draft_id = DraftID(draft_id) if draft_id is not None else None
        referenced = self._repository.referenced_draft_ids([AttachmentID(value) for value in attachment_ids])
        if any(value != typed_draft_id for value in referenced):
            raise validation_failed(
                [{"field": "attachmentIds", "message": "Attachment belongs to another draft", "code": "custom"}]
            )
