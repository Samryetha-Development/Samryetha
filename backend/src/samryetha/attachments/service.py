"""Typed attachment service — upload sessions, visibility, and cleanup."""

from __future__ import annotations

from samryetha.attachments.repository import AttachmentPromotionSource, AttachmentRepository

import logging
import os
import tempfile
from collections.abc import AsyncIterable

from sqlalchemy.engine import Connection

from .models import (
    AttachmentDetail,
    AttachmentRecord,
    AttachmentState,
    AttachmentView,
    CreateAttachment,
    PresignResult,
)
from ..authz import Abilities, Actor, AuthorizationService
from ..core.db import Database, now_ms
from ..core.errors import APIError, bad_request, conflict, forbidden, internal_error, not_found
from ..core.ids import AttachmentID, DiscussionID, UserID
from ..adapters.storage import MAX_UPLOAD_BYTES, Storage, content_type_for_object_key

logger = logging.getLogger("samryetha.attachments")


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


class AttachmentService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, storage: Storage | None = None) -> None:
        self._conn = conn
        self._storage = storage
        self._repository = AttachmentRepository(self._conn)

    def _require_storage(self) -> Storage:
        if self._storage is None:
            raise RuntimeError("AttachmentService operation requires storage")
        return self._storage

    def presign(self, actor: Actor | None, command: CreateAttachment) -> PresignResult:
        storage = self._require_storage()
        if actor is None:
            raise internal_error()
        AuthorizationService(self._conn).assert_can(actor, Abilities.ATTACHMENT_CREATE, None)
        object_key = storage.create_upload_session(
            uploader_id=actor.id,
            original_filename=command.filename,
            mime_type=command.mime_type,
            size_bytes=command.size_bytes,
        )
        safe_mime_type = content_type_for_object_key(object_key)
        attachment_id = self._repository.create_pending(UserID(actor.id), command, object_key, safe_mime_type)
        upload = storage.generate_upload_url(object_key, content_type=safe_mime_type)
        return PresignResult(
            attachment_id=attachment_id,
            object_key=object_key,
            upload_url=upload["url"],
            upload_method=upload["method"],
            upload_headers=upload["headers"],
        )

    def _can_read(self, actor: Actor | None, record: AttachmentRecord) -> bool:
        if actor is not None and record.uploader_id == actor.id:
            return True
        return AuthorizationService(self._conn).can(actor, Abilities.ATTACHMENT_MODERATE, None)

    def downloadable(self, actor: Actor | None, record: AttachmentRecord) -> bool:
        """A signature never overrides the current parent access permissions."""
        if record.state is AttachmentState.Orphaned:
            return False
        if record.discussion_id is None:
            return True

        from ..discussions import DiscussionService

        parent = DiscussionService(self._conn).get_discussion_row(record.discussion_id)
        if parent is None or parent.deleted_at is not None:
            return False
        board = self._repository.active_board(parent.board_id)
        return board is not None and AuthorizationService(self._conn).can(
            actor,
            Abilities.DISCUSSION_READ,
            {"type": "board", "id": board.id, "visibility": board.visibility, "postingPolicy": board.posting_policy},
        )

    def file_for_download(self, actor: Actor | None, object_key: str) -> tuple[AttachmentRecord, str]:
        storage = self._require_storage()
        record = self._repository.get_by_object_key(object_key)
        if record is None or not self.downloadable(actor, record):
            raise not_found("Attachment not found")
        try:
            full = storage.path_for(object_key)
        except Exception:
            raise forbidden("Invalid object key")
        if not os.path.exists(full):
            raise not_found("Attachment not found")
        return record, full

    def get_by_id(self, actor: Actor | None, attachment_id: AttachmentID) -> AttachmentDetail:
        storage = self._require_storage()
        record = self._repository.get(attachment_id)
        if record is None or not self._can_read(actor, record) or not self.downloadable(actor, record):
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

    def promotion_source(self, attachment_id: AttachmentID) -> AttachmentPromotionSource | None:
        """读取「可转入文件服务」的附件快照；不可转换时返回 None。

        只做读取与资格判定，不含任何授权判断——授权由调用方（文件服务）走 authz 完成。
        Reads the promotion snapshot of an attachment, or None when it is not eligible. It
        performs a read plus an eligibility check only: authorisation stays with the caller
        (the file service), which goes through authz.
        """
        return self._repository.promotion_source(attachment_id)

    def list_for_discussion(self, discussion_id: int, viewer: Actor | None = None) -> list[AttachmentView]:
        storage = self._require_storage()
        records = self._repository.list_attached(DiscussionID(discussion_id))
        return [to_attachment(record, storage) for record in records if self.downloadable(viewer, record)]

    def reap_orphans(
        self, older_than_ms: int = 24 * 3600 * 1000, uploaded_older_than_ms: int = 7 * 24 * 3600 * 1000
    ) -> int:
        """Remove expired never-attached or deleted-discussion attachments."""
        storage = self._require_storage()
        now = now_ms()
        candidates = self._repository.orphan_candidates(
            pending_cutoff=now - older_than_ms, uploaded_cutoff=now - uploaded_older_than_ms
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
            self._repository.remove(attachment_id)
            removed += 1
        return removed

    def delete(self, actor: Actor | None, attachment_id: AttachmentID) -> None:
        storage = self._require_storage()
        record = self._repository.get(attachment_id)
        if record is None or not self._can_read(actor, record):
            raise not_found("Attachment not found")
        if record.discussion_id is not None and not self.downloadable(actor, record):
            raise not_found("Attachment not found")
        # 该对象可能已被转入文件服务：删掉它会让资料的回源链路断掉（磁盘无字节）。
        # 因此先拒绝，让调用方先去处理对应资料，而不是留下一条指向空对象的资料。
        # The object may already have been promoted into the file service; deleting it would
        # break the resource's byte path (a row whose file is gone). Refuse first so the caller
        # deals with the resource, rather than leaving a resource pointing at nothing.
        if self._repository.claimed_by_resource(record.object_key):
            raise conflict("This attachment is published in the file library")
        self._repository.remove(attachment_id)
        storage.delete_object(record.object_key)


class AttachmentUploadService:
    """Stream privately, then atomically publish the winner in a short transaction."""

    def __init__(self, db: Database, storage: Storage) -> None:
        self._db = db
        self._storage = storage

    async def upload(self, object_key: str, declared: int, chunks: AsyncIterable[bytes]) -> None:
        if declared > MAX_UPLOAD_BYTES:
            raise bad_request("File too large")
        # 按 presign 时声明的 size_bytes 收紧上限，防客户端绕过声明体积上传超大文件（镜像 attachments/routes.ts）
        db = self._db
        conn = db.engine.connect()
        try:
            meta = AttachmentRepository(conn).get_by_object_key(object_key)
            uploader_status = None if meta is None else AttachmentRepository(conn).user_status(meta.uploader_id)
        finally:
            conn.close()
        if meta is None or meta.state.value != "pending":
            raise forbidden("Upload session is no longer available")
        # 上传签名只证明"URL 未过期"，不证明"上传者仍可用"：封禁/注销用户持有效签名直传必须拦。
        if uploader_status != "active":
            raise forbidden("Upload session is no longer available")
        if declared > meta.size_bytes:
            raise bad_request("File too large")
        storage = self._storage
        try:
            full = storage.path_for(object_key)
        except Exception:
            raise forbidden("Invalid object key")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        wrote = 0
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=os.path.dirname(full))
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in chunks:
                    wrote += len(chunk)
                    if wrote > MAX_UPLOAD_BYTES or wrote > meta.size_bytes:
                        raise bad_request("File too large")
                    fh.write(chunk)
            if wrote != meta.size_bytes:
                raise bad_request("Upload size does not match upload session")
            conn = db.engine.connect()
            try:
                with conn.begin():
                    if not AttachmentRepository(conn).mark_uploaded(object_key):
                        raise forbidden("Upload session is no longer available")
                    # Each request writes privately; only the winner publishes the file.
                    os.replace(temporary, full)
            finally:
                conn.close()
        except APIError:
            raise
        except Exception:
            raise bad_request("Upload failed")
        finally:
            try:
                os.remove(temporary)
            except FileNotFoundError:
                pass
