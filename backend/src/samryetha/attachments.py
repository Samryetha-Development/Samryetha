"""附件 service — 镜像 backend/src/attachments/service.ts。"""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can
from .errors import not_found, internal_error
from .db import now_ms
from .schema import attachments
from .storage import content_type_for_object_key


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


def _dto(r: dict, storage) -> dict:
    mime = content_type_for_object_key(r["object_key"])
    return {
        "id": r["id"], "objectKey": r["object_key"], "originalFilename": r["original_filename"],
        "mimeType": mime, "sizeBytes": r["size_bytes"], "isImage": mime.startswith("image/"),
        "downloadUrl": storage.generate_download_url(r["object_key"]),
    }


def get_by_id(conn: Connection, actor, attachment_id: int, storage) -> dict:
    row = conn.execute(select(attachments).where(attachments.c.id == attachment_id)).first()
    if row is None:
        raise not_found("Attachment not found")
    r = dict(row._mapping)
    assert_can(actor, Abilities.ATTACHMENT_DELETE, {"type": "attachment", "id": attachment_id, "uploaderId": r["uploader_id"]}, conn)
    return {**_dto(r, storage), "state": r["state"], "createdAt": r["created_at"]}


def list_for_discussion(conn: Connection, discussion_id: int, storage) -> list[dict]:
    rows = conn.execute(
        select(attachments).where(
            (attachments.c.discussion_id == discussion_id) & (attachments.c.state == "attached")
        ).order_by(attachments.c.id)
    ).all()
    return [_dto(dict(row._mapping), storage) for row in rows]


def delete(conn: Connection, actor, attachment_id: int, storage) -> None:
    row = conn.execute(select(attachments).where(attachments.c.id == attachment_id)).first()
    if row is None:
        raise not_found("Attachment not found")
    r = dict(row._mapping)
    assert_can(actor, Abilities.ATTACHMENT_DELETE, {"type": "attachment", "id": attachment_id, "uploaderId": r["uploader_id"]}, conn)
    conn.execute(attachments.delete().where(attachments.c.id == attachment_id))
    storage.delete_object(r["object_key"])
