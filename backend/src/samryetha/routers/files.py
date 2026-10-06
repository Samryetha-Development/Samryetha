"""/api/files — 文件服务（面向新生的资料库）路由。

File service routes (a resource library for newcomers).

约定 / Conventions:
- 读接口按可见性在 SQL 层过滤；不可见一律 404，不泄漏存在性。
  Read endpoints filter by visibility in SQL; anything invisible is a plain 404 and never
  leaks existence.
- 写接口要求 active 用户；分类管理要求 admin。
  Write endpoints require an active user; category management requires an admin.
- 文件字节走 storage.py 的 HMAC presign 三段式：presign -> signed PUT -> signed GET。
  File bytes use storage.py's three-step HMAC presign flow: presign -> signed PUT ->
  signed GET.
"""

from __future__ import annotations

import os
import tempfile
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from .. import files_service as service
from ..deps import CurrentUser, CurrentUserDep, DbConn, get_current_user, get_storage, require_active_user, require_admin
from ..errors import bad_request, forbidden, not_found
from ..schema import file_resources
from ..storage import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, OBJECT_KEY_RE, content_type_for_object_key, sanitize_filename

router = APIRouter()

ResourceId = Annotated[int, Path(ge=1)]
UploaderId = Annotated[int, Path(ge=1)]

# 允许内联预览的扩展名：只放"浏览器渲染不会执行脚本"的类型。
# 之所以不用 mimeType 判断，是因为 mimeType 来自客户端声明，可被伪造成 text/html。
# Extensions allowed to render inline: only types a browser cannot execute script from.
# The check deliberately avoids mimeType, which is client-declared and could be forged as
# text/html.
INLINE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".pdf", ".txt", ".md", ".csv"}


def _client_ip(request: Request) -> str | None:
    settings = request.app.state.settings
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None


# ================================================================ 配置 / config


class PresignBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    mimeType: Annotated[str, Field(min_length=1, max_length=100)] = "application/octet-stream"
    sizeBytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]


class ResourceCreateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    objectKey: Annotated[str, Field(min_length=1, max_length=512)]
    expires: Annotated[str, Field(min_length=1, max_length=32)]
    sig: Annotated[str, Field(min_length=1, max_length=128)]
    sizeBytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]
    categoryId: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=service.MAX_TITLE_LENGTH)]
    descriptionMarkdown: Annotated[str, Field(max_length=service.MAX_DESCRIPTION_LENGTH)] = ""
    tags: list[str] | str | None = None
    visibility: str = "members"
    originalFilename: Annotated[str, Field(max_length=255)] | None = None
    mimeType: Annotated[str, Field(max_length=100)] | None = None
    sha256: Annotated[str, Field(max_length=64)] | None = None


class ResourcePatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: Annotated[str, Field(min_length=1, max_length=service.MAX_TITLE_LENGTH)] | None = None
    descriptionMarkdown: Annotated[str, Field(max_length=service.MAX_DESCRIPTION_LENGTH)] | None = None
    tags: list[str] | str | None = None
    categoryId: Annotated[int, Field(ge=1)] | None = None
    visibility: str | None = None
    status: str | None = None


class RatingBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: Annotated[int, Field(ge=1, le=5)]


class CategoryCreateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    slug: Annotated[str, Field(min_length=1, max_length=60)]
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(max_length=500)] = ""
    kind: str = "other"
    sortOrder: int = 0


class CategoryPatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    kind: str | None = None
    sortOrder: int | None = None


@router.get("/api/files/config")
def files_config(
    conn: DbConn,
    viewer: CurrentUserDep,
) -> dict:
    """一次性下发前端所需的全部约束与导航数据。

    前端不用手抄扩展名白名单/分类列表，避免与后端漂移；后端仍做权威校验。
    Hands the front end every constraint and navigation datum it needs in one call, so no
    allow-list or category list is hand-copied into the client and drifted from the
    server. The server still performs the authoritative validation.
    """
    return {
        "categories": service.list_categories(conn, viewer),
        "tagCloud": service.list_tag_cloud(conn, viewer),
        "stats": service.category_stats(conn, viewer),
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "kinds": list(service.KINDS),
        "visibilities": list(service.VISIBILITIES),
        "sorts": list(service.SORTS),
        "maxTags": service.MAX_TAGS,
    }


# ================================================================ 分类 / categories


@router.get("/api/files/categories")
def list_categories(conn: DbConn, viewer: CurrentUserDep) -> dict:
    return {"items": service.list_categories(conn, viewer)}


@router.post("/api/files/categories", status_code=201)
def create_category(body: CategoryCreateBody, conn: DbConn, _admin: CurrentUser = Depends(require_admin)) -> dict:
    return service.create_category(conn, body.model_dump())


@router.patch("/api/files/categories/{category_id}")
def update_category(
    category_id: ResourceId,
    body: CategoryPatchBody,
    conn: DbConn,
    _admin: CurrentUser = Depends(require_admin),
) -> dict:
    return service.update_category(conn, category_id, body.model_dump(exclude_none=True))


@router.delete("/api/files/categories/{category_id}")
def delete_category(category_id: ResourceId, conn: DbConn, admin: CurrentUser = Depends(require_admin)) -> dict:
    service.delete_category(conn, category_id, admin.id)
    return {"ok": True}


# ================================================================ 列表 / listing


@router.get("/api/files/resources")
def list_resources(
    conn: DbConn,
    viewer: CurrentUserDep,
    category: str | None = Query(default=None, max_length=60),
    kind: str | None = Query(default=None, max_length=20),
    tag: str | None = Query(default=None, max_length=service.MAX_TAG_LENGTH),
    q: str | None = Query(default=None, max_length=200),
    sort: str | None = Query(default=None, max_length=20),
    status: str | None = Query(default=None, max_length=20),
    featured: bool = Query(default=False),
    uploaderId: int | None = Query(default=None, ge=1),
    page: int | None = Query(default=None, ge=1),
    # 刻意不写 le=：超上限的 pageSize 由 service 夹紧到 MAX_PAGE_SIZE，
    # 而不是回 422——分页参数越界不该让整个列表页报错。
    # Deliberately no le=: an over-large pageSize is clamped to MAX_PAGE_SIZE by the
    # service instead of returning 422, because an out-of-range paging parameter should
    # not break the whole list page.
    pageSize: int | None = Query(default=None, ge=1),
) -> dict:
    return service.list_resources(
        conn,
        viewer,
        {
            "category": category,
            "kind": kind,
            "tag": tag,
            "q": q,
            "sort": sort,
            "status": status,
            "featured": featured,
            "uploaderId": uploaderId,
            "page": page,
            "pageSize": pageSize,
        },
    )


@router.get("/api/files/favorites")
def list_favorites(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.list_favorites(conn, user)


@router.get("/api/files/mine")
def list_mine(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.list_mine(conn, user)


# ================================================================ 详情 / detail


@router.get("/api/files/resources/{resource_id}")
def get_resource(resource_id: ResourceId, conn: DbConn, viewer: CurrentUserDep, storage: object = Depends(get_storage)) -> dict:
    return service.get_resource(conn, viewer, resource_id)


@router.get("/api/files/resources/{resource_id}/download")
def prepare_download(
    resource_id: ResourceId,
    request: Request,
    conn: DbConn,
    viewer: CurrentUserDep,
    storage: object = Depends(get_storage),
    preview: bool = Query(default=False),
) -> dict:
    """返回带签名的下载地址；非预览请求同时记录一次下载。

    返回 JSON 而不是 302：让前端能区分"未登录（401）"与"无权限（404）"，
    并在未登录时就地弹出登录引导，而不是把用户甩到一个空白错误页。
    preview=true 时不计数：在线预览只是"看一眼"，把它算成下载会污染下载量这个
    核心质量信号——而列表页正是靠下载量排序的。
    Returns a signed download URL and, for a non-preview request, records a download.
    Returned as JSON rather than a 302 so the client can tell a signed-out visitor (401)
    apart from a forbidden one (404) and offer sign-in in place instead of dumping the
    user onto a blank error page. With preview=true nothing is counted: an inline preview
    is only a glance, and counting it would pollute the download figure, which is the core
    quality signal the list page sorts by.
    """
    payload = service.download_payload(conn, viewer, resource_id, storage)
    if not preview:
        row = service.get_resource_row(conn, resource_id)
        if row is not None:
            service.record_download(conn, viewer, row, _client_ip(request))
    return payload


# ================================================================ 上传 / upload


@router.post("/api/files/resources/presign")
def presign_upload(
    body: PresignBody,
    conn: DbConn,
    storage: object = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.presign_upload(conn, user, storage, body.model_dump())


@router.put("/api/files/upload/{uploader_id}/{object_key:path}", status_code=204)
async def upload_bytes(request: Request, uploader_id: UploaderId, object_key: str) -> Response:
    """接收文件字节。签名即凭证：签名里绑定了上传者、对象键与声明体积。

    与附件上传的差别：附件在 presign 时就落了一行 attachments（因而能复核上传者状态与
    声明体积），文件服务刻意不在 presign 时建行（元数据还没收集），所以这里改为
    "从会话解析上传者并复核其仍为 active"，其余安全约束（体积、对象键形状、原子落盘）保持一致。
    Receives the file bytes; the signature is the credential, binding uploader, object key
    and declared size. Difference from attachment upload: attachments insert a row at
    presign time (which lets them re-check the uploader's state and declared size), while
    the file service deliberately inserts nothing at presign time because the metadata has
    not been collected yet. Here the uploader is therefore resolved from the session and
    re-checked to be active, while every other constraint (size, object key shape, atomic
    write) stays the same.
    """
    storage = request.app.state.storage
    size_param = request.query_params.get("size")
    expires = request.query_params.get("expires")
    sig = request.query_params.get("sig")
    if not size_param or not expires or not sig:
        raise bad_request("Invalid upload URL")
    try:
        declared_size = int(size_param)
    except (TypeError, ValueError):
        raise bad_request("Invalid upload URL")
    if declared_size <= 0 or declared_size > MAX_UPLOAD_BYTES:
        raise bad_request("File too large")
    if not OBJECT_KEY_RE.match(object_key):
        raise bad_request("Invalid object key")

    # 会话解析与签名对象必须一致：否则 A 可以用自己的会话把 B 的签名对象传上去。
    # The session identity must match the identity inside the signature, otherwise A could
    # use their own session to upload an object signed for B.
    user = await _session_user(request)
    if user is None or user.id != uploader_id or user.status != "active":
        raise forbidden("Upload session is no longer available")

    service.verify_upload_signature(storage, uploader_id, object_key, declared_size, expires, sig)

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
                if wrote > MAX_UPLOAD_BYTES or wrote > declared_size:
                    raise bad_request("File too large")
                fh.write(chunk)
        if wrote != declared_size:
            raise bad_request("Upload size does not match upload session")
        # 先写私有临时文件再原子改名：中断的请求不会在半途留下"半个文件"被下载到。
        # Write to a private temp file first and rename atomically, so an interrupted
        # request can never leave half a file behind to be downloaded.
        os.replace(temporary, full)
    except Exception:
        raise
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
    return Response(status_code=204)


async def _session_user(request: Request):
    """在非 Depends 上下文（流式路由此处手写校验）里解析当前会话用户。
    Resolve the session user outside a Depends context, which the streaming route needs
    because it validates by hand.
    """
    from ..security import SESSION_COOKIE, get_session_user

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    conn = request.app.state.db.engine.connect()
    try:
        row = get_session_user(conn, token)
    finally:
        conn.close()
    if row is None:
        return None
    from ..deps import to_session_user

    return to_session_user(row)


@router.post("/api/files/resources", status_code=201)
def create_resource(
    body: ResourceCreateBody,
    conn: DbConn,
    storage: object = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.create_resource(conn, user, storage, body.model_dump())


@router.patch("/api/files/resources/{resource_id}")
def update_resource(
    resource_id: ResourceId,
    body: ResourcePatchBody,
    conn: DbConn,
    viewer: CurrentUser = Depends(require_active_user),
) -> dict:
    return service.update_resource(conn, viewer, resource_id, body.model_dump(exclude_none=True))


@router.delete("/api/files/resources/{resource_id}")
def delete_resource(
    resource_id: ResourceId,
    conn: DbConn,
    storage: object = Depends(get_storage),
    viewer: CurrentUser = Depends(require_active_user),
) -> dict:
    service.delete_resource(conn, viewer, resource_id, storage)
    return {"ok": True}


# ================================================================ 收藏与评分 / favorite & rating


@router.put("/api/files/resources/{resource_id}/favorite")
def add_favorite(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.set_favorite(conn, user, resource_id, True)


@router.delete("/api/files/resources/{resource_id}/favorite")
def remove_favorite(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.set_favorite(conn, user, resource_id, False)


@router.put("/api/files/resources/{resource_id}/rating")
def set_rating(resource_id: ResourceId, body: RatingBody, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.set_rating(conn, user, resource_id, body.score)


@router.delete("/api/files/resources/{resource_id}/rating")
def clear_rating(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict:
    return service.clear_rating(conn, user, resource_id)


# ================================================================ 字节回源 / serve


@router.get("/api/files/serve/{object_key:path}")
def serve(object_key: str, request: Request, conn: DbConn, viewer: CurrentUserDep) -> Response:
    """回源文件字节。

    两道独立校验缺一不可：签名证明"URL 未过期"，可见性复核证明"这个人此刻仍有权下载"
    （资料可能在签名生成后被改成 private 或软删除，光看签名会放行）。
    Two independent checks, neither optional: the signature proves the URL has not expired,
    while the visibility re-check proves this viewer may still download right now. A
    resource can be switched to private or soft-deleted after a URL was signed, and the
    signature alone would let that through.
    """
    storage = request.app.state.storage
    expires = request.query_params.get("expires")
    sig = request.query_params.get("sig")
    pathname = f"/api/files/serve/{object_key}"
    if not OBJECT_KEY_RE.match(object_key) or not expires or not sig:
        raise bad_request("Invalid download URL")
    if not storage.verify_signature("GET", pathname, expires, sig):
        raise forbidden("Invalid or expired download signature")

    row = conn.execute(
        select(file_resources).where(file_resources.c.object_key == object_key)
    ).first()
    if row is None or row.deleted_at is not None:
        raise not_found("Resource not found")
    raw = dict(row._mapping)
    service.assert_visible(conn, viewer, raw)

    try:
        full = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    if not os.path.exists(full):
        # 库里有行、磁盘没文件：按"不存在"处理，不向客户端暴露存储层细节。
        # A row without a file on disk is treated as missing, never exposing storage
        # internals to the client.
        raise not_found("Resource not found")

    mime = content_type_for_object_key(object_key)
    extension = os.path.splitext(object_key)[1].lower()
    inline = extension in INLINE_EXTENSIONS
    disposition = "inline" if inline else "attachment"
    filename = sanitize_filename(row.original_filename)
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
