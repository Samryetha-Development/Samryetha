"""/api/attachments — 镜像 backend/src/attachments/routes.ts。

presign → signed PUT → signed GET。待审/被封父帖的下载同时核验当前会话权限。
"""

from __future__ import annotations

import os
import tempfile
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import FileResponse

from .. import attachments as att
from ..attachments import repository
from ..attachments.models import (
    AttachmentConfigResponse,
    AttachmentOperationOkResponse,
    AttachmentResponse,
    PresignBody,
    PresignResponse,
)
from ..core.db import Database
from ..core.deps import CurrentUser, DbConn, get_current_user, get_storage, require_active_user, require_user
from ..core.errors import APIError, bad_request, forbidden, not_found
from ..core.ids import AttachmentID
from ..adapters.storage import (
    ALLOWED_EXTENSIONS,
    MAX_UPLOAD_BYTES,
    OBJECT_KEY_RE,
    Storage,
    content_type_for_object_key,
    sanitize_filename,
)

router = APIRouter()

AttachmentId = Annotated[int, Path(ge=1)]


@router.get("/api/attachments/config", response_model=AttachmentConfigResponse)
def attachments_config() -> AttachmentConfigResponse:
    """上传约束下发：前端以此为准做即时校验，免前后端白名单手抄漂移（后端仍权威校验）。"""
    return AttachmentConfigResponse(
        allowed_extensions=sorted(ALLOWED_EXTENSIONS),
        max_upload_bytes=MAX_UPLOAD_BYTES,
    )


@router.post("/api/attachments/presign", response_model=PresignResponse)
def presign(
    body: PresignBody,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> PresignResponse:
    # The extension is the source of truth. Browsers commonly report empty or
    # non-standard MIME types for otherwise permitted files.
    return PresignResponse.model_validate(att.presign(conn, user, body.to_command(), storage))


@router.get("/api/attachments/{attachment_id}", response_model=AttachmentResponse)
def get_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> AttachmentResponse:
    result = att.get_by_id(conn, user, AttachmentID(attachment_id), storage)
    return AttachmentResponse.model_validate(result)


@router.delete("/api/attachments/{attachment_id}", response_model=AttachmentOperationOkResponse)
def delete_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> AttachmentOperationOkResponse:
    att.delete(conn, user, AttachmentID(attachment_id), storage)
    return AttachmentOperationOkResponse(ok=True)


def _signed_request_ok(request: Request, method: str, object_key: str) -> tuple[str, str]:
    expires = request.query_params.get("expires")
    sig = request.query_params.get("sig")
    if not OBJECT_KEY_RE.match(object_key) or not expires or not sig:
        raise bad_request("Invalid upload URL" if method == "PUT" else "Invalid download URL")
    pathname = f"/api/attachments/{'upload' if method == 'PUT' else 'serve'}/{object_key}"
    storage: Storage = request.app.state.storage
    if not storage.verify_signature(method, pathname, expires, sig):
        raise forbidden(
            "Invalid or expired upload signature" if method == "PUT" else "Invalid or expired download signature"
        )
    return expires, sig


@router.put("/api/attachments/upload/{object_key:path}", status_code=204)
async def upload(request: Request, object_key: str) -> Response:
    _signed_request_ok(request, "PUT", object_key)
    try:
        declared = int(request.headers.get("content-length") or 0)
    except (TypeError, ValueError):
        # 非法 Content-Length（如 "abc"）转 400，而非 500。
        raise bad_request("Invalid Content-Length")
    if declared > MAX_UPLOAD_BYTES:
        raise bad_request("File too large")
    # 按 presign 时声明的 size_bytes 收紧上限，防客户端绕过声明体积上传超大文件（镜像 attachments/routes.ts）
    db: Database = request.app.state.db
    conn = db.engine.connect()
    try:
        meta = repository.get_by_object_key(conn, object_key)
        uploader_status = None if meta is None else repository.user_status(conn, meta.uploader_id)
    finally:
        conn.close()
    if meta is None or meta.state.value != "pending":
        raise forbidden("Upload session is no longer available")
    # 上传签名只证明"URL 未过期"，不证明"上传者仍可用"：封禁/注销用户持有效签名直传必须拦。
    if uploader_status != "active":
        raise forbidden("Upload session is no longer available")
    if declared > meta.size_bytes:
        raise bad_request("File too large")
    storage: Storage = request.app.state.storage
    try:
        full = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    wrote = 0
    fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=os.path.dirname(full))
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                wrote += len(chunk)
                if wrote > MAX_UPLOAD_BYTES or wrote > meta.size_bytes:
                    raise bad_request("File too large")
                fh.write(chunk)
        if wrote != meta.size_bytes:
            raise bad_request("Upload size does not match upload session")
        conn = db.engine.connect()
        try:
            with conn.begin():
                if not repository.mark_uploaded(conn, object_key):
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
    return Response(status_code=204)


@router.get("/api/attachments/serve/{object_key:path}")
async def serve(
    request: Request,
    object_key: str,
    conn: DbConn,
    viewer: CurrentUser | None = Depends(get_current_user),
) -> Response:
    _signed_request_ok(request, "GET", object_key)
    storage: Storage = request.app.state.storage
    meta = repository.get_by_object_key(conn, object_key)
    # 不信任入库/客户端声明的 mime_type：按 objectKey 扩展名推导，杜绝 text/html 内联渲染 → 存储型 XSS
    if meta is None:
        raise not_found("Attachment not found")
    if not att.downloadable(conn, viewer, meta):
        raise not_found("Attachment not found")
    mime = content_type_for_object_key(object_key)
    try:
        full = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    if not os.path.exists(full):
        # 磁盘有行无文件：按"不存在"处理（404），不向客户端暴露存储层细节。
        raise not_found("Attachment not found")
    disposition = "inline" if mime.startswith("image/") else "attachment"
    filename = sanitize_filename(meta.original_filename)
    content_disposition = f"{disposition}; filename=\"download\"; filename*=UTF-8''{quote(filename)}"
    return FileResponse(
        full,
        headers={
            "content-type": mime,
            "x-content-type-options": "nosniff",
            "content-disposition": content_disposition,
            "cache-control": "private, no-store",
        },
    )
