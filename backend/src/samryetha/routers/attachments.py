"""/api/attachments — 镜像 backend/src/attachments/routes.ts。

presign → 客户端直接 signed PUT 直传 → serve 带签 GET。upload/serve 不经 cookie，只验签名。
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .. import attachments as att
from ..deps import CurrentUser, DbConn, get_storage, require_active_user, require_user
from ..errors import bad_request, forbidden, not_found
from ..schema import attachments
from ..storage import ALLOWED_MIME_TYPES, MAX_UPLOAD_BYTES, OBJECT_KEY_RE, content_type_for_object_key

router = APIRouter()

AttachmentId = Annotated[int, Path(ge=1)]


class PresignBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    mimeType: Annotated[str, Field(min_length=1, max_length=100)]
    sizeBytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]


@router.post("/api/attachments/presign")
def presign(
    body: PresignBody,
    conn: DbConn,
    storage: object = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    # 只允许白名单内的 Content-Type，防客户端把 text/html 之类的可执行类型带进附件
    if body.mimeType not in ALLOWED_MIME_TYPES:
        raise bad_request("Unsupported content type")
    return att.presign(conn, user, body.model_dump(), storage)


@router.get("/api/attachments/{attachment_id}")
def get_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: object = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> dict:
    return att.get_by_id(conn, attachment_id, storage)


@router.delete("/api/attachments/{attachment_id}")
def delete_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: object = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> dict:
    att.delete(conn, user, attachment_id, storage)
    return {"ok": True}


def _signed_request_ok(request: Request, method: str, prefix: str, object_key: str) -> tuple[str, str]:
    expires = request.query_params.get("expires")
    sig = request.query_params.get("sig")
    if not OBJECT_KEY_RE.match(object_key) or not expires or not sig:
        raise bad_request("Invalid upload URL" if method == "PUT" else "Invalid download URL")
    pathname = f"/api/attachments/{'upload' if method == 'PUT' else 'serve'}/{object_key}"
    storage = request.app.state.storage
    if not storage.verify_signature(method, pathname, expires, sig):
        raise forbidden("Invalid or expired upload signature" if method == "PUT" else "Invalid or expired download signature")
    return expires, sig


@router.put("/api/attachments/upload/{object_key:path}", status_code=204)
async def upload(request: Request, object_key: str) -> Response:
    _signed_request_ok(request, "PUT", "/api/attachments/upload", object_key)
    declared = int(request.headers.get("content-length") or 0)
    if declared > MAX_UPLOAD_BYTES:
        raise bad_request("File too large")
    # 按 presign 时声明的 size_bytes 收紧上限，防客户端绕过声明体积上传超大文件（镜像 attachments/routes.ts）
    conn = request.app.state.db.engine.connect()
    try:
        meta = conn.execute(
            select(attachments.c.size_bytes).where(attachments.c.object_key == object_key)
        ).first()
    finally:
        conn.close()
    if meta is not None and declared > meta.size_bytes:
        raise bad_request("File too large")
    storage = request.app.state.storage
    try:
        full = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    wrote = 0
    try:
        with open(full, "wb") as fh:
            async for chunk in request.stream():
                wrote += len(chunk)
                if wrote > MAX_UPLOAD_BYTES:
                    raise bad_request("File too large")
                fh.write(chunk)
    except Exception as exc:
        raise bad_request("Upload failed")
    return Response(status_code=204)


@router.get("/api/attachments/serve/{object_key:path}")
async def serve(request: Request, object_key: str) -> Response:
    _signed_request_ok(request, "GET", "/api/attachments/serve", object_key)
    storage = request.app.state.storage
    conn = request.app.state.db.engine.connect()
    try:
        meta = conn.execute(
            select(attachments).where(attachments.c.object_key == object_key)
        ).first()
    finally:
        conn.close()
    # 不信任入库/客户端声明的 mime_type：按 objectKey 扩展名推导，杜绝 text/html 内联渲染 → 存储型 XSS
    if meta is None:
        raise not_found("Attachment not found")
    mime = content_type_for_object_key(object_key)
    try:
        full = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    if not os.path.exists(full):
        raise bad_request("File not found")
    filename = os.path.basename(object_key)
    return FileResponse(
        full,
        headers={
            "content-type": mime,
            "x-content-type-options": "nosniff",
            "content-disposition": f'inline; filename="{filename}"',
        },
    )
