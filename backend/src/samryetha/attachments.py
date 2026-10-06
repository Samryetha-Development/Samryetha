"""附件 service — 镜像 backend/src/attachments/service.ts。"""

from __future__ import annotations

import logging

from sqlalchemy import insert, or_, select
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can, can
from .errors import not_found, internal_error
from .db import now_ms
from .schema import attachments, boards, discussions, draft_attachments
from .storage import content_type_for_object_key

logger = logging.getLogger("samryetha.attachments")


def presign(conn: Connection, actor, input_: dict, storage) -> dict:
    if actor is None:
        raise internal_error()
    assert_can(actor, Abilities.ATTACHMENT_CREATE, None, conn)
    object_key = storage.create_upload_session(
        uploader_id=actor.id,
        original_filename=input_["filename"],
        mime_type=input_["mimeType"],
        size_bytes=input_["sizeBytes"],
    )
    res = conn.execute(
        insert(attachments).values(
            uploader_id=actor.id,
            object_key=object_key,
            original_filename=input_["filename"],
            mime_type=content_type_for_object_key(object_key),
            size_bytes=input_["sizeBytes"],
            created_at=now_ms(),
        )
    )
    attachment_id = res.inserted_primary_key[0]
    upload = storage.generate_upload_url(object_key, content_type=content_type_for_object_key(object_key))
    return {
        "attachmentId": attachment_id,
        "objectKey": object_key,
        "uploadUrl": upload["url"],
        "uploadMethod": upload["method"],
        "uploadHeaders": upload["headers"],
    }


def to_attachment(r: dict, storage) -> dict:
    mime = content_type_for_object_key(r["object_key"])
    return {
        "id": r["id"], "objectKey": r["object_key"], "originalFilename": r["original_filename"],
        "mimeType": mime, "sizeBytes": r["size_bytes"], "isImage": mime.startswith("image/"),
        "downloadUrl": storage.generate_download_url(r["object_key"]),
    }


def _can_read(actor, r: dict, conn: Connection) -> bool:
    """owner 或全局管理员（ATTACHMENT_MODERATE）可读写；其余一律视为不存在（统一 404）。"""
    if actor is not None and r["uploader_id"] == actor.id:
        return True
    return can(actor, Abilities.ATTACHMENT_MODERATE, None, conn)


def downloadable(conn: Connection, actor, r: dict) -> bool:
    """A signature never overrides the current parent moderation state.

    Approved and unattached uploads retain bearer-URL behavior. Held content
    requires a session that can currently read the parent; rejected content is
    accessible only to administrators. Files remain attached for retention.
    """
    if r["state"] == "orphaned":
        return False
    if r["discussion_id"] is None:
        return True
    parent = conn.execute(select(discussions).where(
        discussions.c.id == r["discussion_id"], discussions.c.deleted_at.is_(None),
    )).first()
    if parent is None:
        return False
    status = parent.moderation_status or "approved"
    if status == "approved":
        return True
    from .discussions import _assert_parent_discussion_visible
    from .errors import ApiError

    try:
        _assert_parent_discussion_visible(dict(parent._mapping), actor)
    except ApiError:
        return False
    board = conn.execute(select(boards).where(boards.c.id == parent.board_id, boards.c.deleted_at.is_(None))).first()
    return board is not None and can(actor, Abilities.DISCUSSION_READ, {
        "type": "board", "id": board.id, "visibility": board.visibility,
        "postingPolicy": board.posting_policy,
    }, conn)


def get_by_id(conn: Connection, actor, attachment_id: int, storage) -> dict:
    row = conn.execute(select(attachments).where(attachments.c.id == attachment_id)).first()
    if row is None:
        raise not_found("Attachment not found")
    r = dict(row._mapping)
    if not _can_read(actor, r, conn) or not downloadable(conn, actor, r):
        # 不存在与非 owner 统一 404：不向无关用户泄露附件存在性。
        raise not_found("Attachment not found")
    return {**to_attachment(r, storage), "state": r["state"], "createdAt": r["created_at"]}


def list_for_discussion(conn: Connection, discussion_id: int, storage, viewer=None) -> list[dict]:
    rows = conn.execute(
        select(attachments).where(
            (attachments.c.discussion_id == discussion_id) & (attachments.c.state == "attached")
        ).order_by(attachments.c.id)
    ).all()
    return [to_attachment(dict(row._mapping), storage) for row in rows
            if downloadable(conn, viewer, dict(row._mapping))]


def reap_orphans(
    conn: Connection,
    storage,
    older_than_ms: int = 24 * 3600 * 1000,
    uploaded_older_than_ms: int = 7 * 24 * 3600 * 1000,
) -> int:
    """Remove expired never-attached or deleted-discussion attachment rows and objects.

    uploaded（已传完但从未挂到讨论）用更长的宽限（默认 7 天）：用户可能 presign 后
    隔几天才发帖；pending/orphaned 仍按 older_than_ms（默认 1 天）回收。
    """
    now = now_ms()
    cutoff = now - older_than_ms
    rows = conn.execute(
        select(attachments.c.id, attachments.c.object_key).where(
            (attachments.c.created_at < cutoff)
            & or_(attachments.c.state == "pending", attachments.c.state == "orphaned")
        )
    ).all()
    uploaded_cutoff = now - uploaded_older_than_ms
    uploaded_rows = conn.execute(
        select(attachments.c.id, attachments.c.object_key).where(
            (attachments.c.state == "uploaded")
            & (attachments.c.discussion_id.is_(None))
            & (attachments.c.created_at < uploaded_cutoff)
            & ~select(draft_attachments.c.attachment_id).where(
                draft_attachments.c.attachment_id == attachments.c.id
            ).exists()
        )
    ).all()
    # 两个查询分别命中 pending/orphaned 与 uploaded，状态互斥，无需去重。
    rows = list(rows) + list(uploaded_rows)
    removed = 0
    for row in rows:
        try:
            storage.delete_object(row.object_key)
        except Exception:
            # 坏 key（如路径逃逸）记日志跳过、保留该行，保持整体推进（其余行继续回收）。
            logger.warning("[attachments] reap skipped bad key id=%s key=%r", row.id, row.object_key, exc_info=True)
            continue
        conn.execute(attachments.delete().where(attachments.c.id == row.id))
        removed += 1
    return removed


def delete(conn: Connection, actor, attachment_id: int, storage) -> None:
    row = conn.execute(select(attachments).where(attachments.c.id == attachment_id)).first()
    if row is None:
        raise not_found("Attachment not found")
    r = dict(row._mapping)
    if not _can_read(actor, r, conn) or (r["discussion_id"] is not None and not downloadable(conn, actor, r)):
        # 不存在与非 owner 统一 404（见 get_by_id）。
        raise not_found("Attachment not found")
    conn.execute(attachments.delete().where(attachments.c.id == attachment_id))
    storage.delete_object(r["object_key"])
