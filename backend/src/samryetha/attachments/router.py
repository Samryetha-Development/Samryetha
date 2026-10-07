"""/api/attachments — 镜像 backend/src/attachments/routes.ts。

presign → signed PUT → signed GET。待审/被封父帖的下载同时核验当前会话权限。
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import FileResponse

from .. import attachments as att
from .service import AttachmentUploadService
from ..attachments.models import (
    AttachmentConfigResponse,
    AttachmentOperationOkResponse,
    AttachmentResponse,
    PresignBody,
    PresignResponse,
)
from ..core.db import Database
from ..core.deps import CurrentUser, DbConn, get_current_user, get_storage, require_active_user, require_user
from ..core.errors import bad_request, forbidden
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
    return PresignResponse.model_validate(att.AttachmentService(conn, storage=storage).presign(user, body.to_command()))


@router.get("/api/attachments/{attachment_id}", response_model=AttachmentResponse)
def get_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> AttachmentResponse:
    result = att.AttachmentService(conn, storage=storage).get_by_id(user, AttachmentID(attachment_id))
    return AttachmentResponse.model_validate(result)


@router.delete("/api/attachments/{attachment_id}", response_model=AttachmentOperationOkResponse)
def delete_attachment(
    attachment_id: AttachmentId,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_user),
) -> AttachmentOperationOkResponse:
    att.AttachmentService(conn, storage=storage).delete(user, AttachmentID(attachment_id))
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
    db: Database = request.app.state.db
    storage: Storage = request.app.state.storage
    await AttachmentUploadService(db, storage).upload(object_key, declared, request.stream())
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
    meta, full = att.AttachmentService(conn, storage).file_for_download(viewer, object_key)
    # Derive MIME from the object key rather than trusting the stored/client value.
    mime = content_type_for_object_key(object_key)
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
