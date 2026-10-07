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
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import FileResponse

from . import service
from .service import FileService
from .models import FileConfig, FileCategoryList, FileCategory, FileMutationResult, FileResourceList, FileResourceItems, FileResourceDetail, FileDownloadTicket, FilePresign, FileFavoriteState, FileRatingState
from .models import FilePresignBody, FilePromoteFromAttachmentBody, ResourceCreateBody, ResourcePatchBody, RatingBody, CategoryCreateBody, CategoryPatchBody, FileFilters
from ..core.deps import CurrentUser, CurrentUserDep, DbConn, get_current_user, get_storage, require_active_user, require_admin
from ..core.errors import bad_request, forbidden, not_found
from ..adapters.storage import Storage
from ..adapters.storage import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, OBJECT_KEY_RE, content_type_for_object_key, sanitize_filename

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


@router.get("/api/files/config", response_model=FileConfig, response_model_exclude_unset=True)
def files_config(
    conn: DbConn,
    viewer: CurrentUserDep,
) -> dict[str, Any]:
    """一次性下发前端所需的全部约束与导航数据。

    前端不用手抄扩展名白名单/分类列表，避免与后端漂移；后端仍做权威校验。
    Hands the front end every constraint and navigation datum it needs in one call, so no
    allow-list or category list is hand-copied into the client and drifted from the
    server. The server still performs the authoritative validation.
    """
    return {
        "categories": FileService(conn).list_categories(viewer),
        "tagCloud": FileService(conn).list_tag_cloud(viewer),
        "stats": FileService(conn).category_stats(viewer),
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "kinds": list(service.KINDS),
        "visibilities": list(service.VISIBILITIES),
        "sorts": list(service.SORTS),
        "maxTags": service.MAX_TAGS,
    }


# ================================================================ 分类 / categories


@router.get("/api/files/categories", response_model=FileCategoryList, response_model_exclude_unset=True)
def list_categories(conn: DbConn, viewer: CurrentUserDep) -> dict[str, Any]:
    return {"items": FileService(conn).list_categories(viewer)}


@router.post("/api/files/categories", status_code=201, response_model=FileCategory, response_model_exclude_unset=True)
def create_category(body: CategoryCreateBody, conn: DbConn, _admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    return FileService(conn).create_category(body)


@router.patch("/api/files/categories/{category_id}", response_model=FileCategory, response_model_exclude_unset=True)
def update_category(
    category_id: ResourceId,
    body: CategoryPatchBody,
    conn: DbConn,
    _admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    return FileService(conn).update_category(category_id, body)


@router.delete("/api/files/categories/{category_id}", response_model=FileMutationResult, response_model_exclude_unset=True)
def delete_category(category_id: ResourceId, conn: DbConn, admin: CurrentUser = Depends(require_admin)) -> dict[str, Any]:
    FileService(conn).delete_category(category_id, admin.id)
    return {"ok": True}


# ================================================================ 列表 / listing


@router.get("/api/files/resources", response_model=FileResourceList, response_model_exclude_unset=True)
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
) -> dict[str, Any]:
    return FileService(conn).list_resources(viewer, FileFilters.model_validate({
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
        }))


@router.get("/api/files/favorites", response_model=FileResourceItems, response_model_exclude_unset=True)
def list_favorites(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).list_favorites(user)


@router.get("/api/files/mine", response_model=FileResourceItems, response_model_exclude_unset=True)
def list_mine(conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).list_mine(user)


# ================================================================ 详情 / detail


@router.get("/api/files/resources/{resource_id}", response_model=FileResourceDetail, response_model_exclude_unset=True)
def get_resource(resource_id: ResourceId, conn: DbConn, viewer: CurrentUserDep, storage: Storage = Depends(get_storage)) -> dict[str, Any]:
    return FileService(conn).get_resource(viewer, resource_id)


@router.get("/api/files/resources/{resource_id}/download", response_model=FileDownloadTicket, response_model_exclude_unset=True)
def prepare_download(
    resource_id: ResourceId,
    request: Request,
    conn: DbConn,
    viewer: CurrentUserDep,
    storage: Storage = Depends(get_storage),
    preview: bool = Query(default=False),
) -> dict[str, Any]:
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
    payload = FileService(conn, storage).download_payload(viewer, resource_id)
    if not preview:
        row = FileService(conn).get_resource_row(resource_id)
        if row is not None:
            FileService(conn).record_download(viewer, row, _client_ip(request))
    return payload


# ================================================================ 上传 / upload


@router.post("/api/files/resources/presign", response_model=FilePresign, response_model_exclude_unset=True)
def presign_upload(
    body: FilePresignBody,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    return FileService(conn, storage).presign_upload(user, body)


@router.put("/api/files/upload/{uploader_id}/{object_key:path}", status_code=204)
async def upload_bytes(request: Request, uploader_id: UploaderId, object_key: str, conn: DbConn) -> Response:
    size = request.query_params.get("size")
    expires = request.query_params.get("expires")
    sig = request.query_params.get("sig")
    if not size or not expires or not sig:
        raise bad_request("Invalid upload URL")
    try:
        declared_size = int(size)
    except ValueError:
        raise bad_request("Invalid upload URL")
    user = get_current_user(request, conn)
    await FileService(conn, get_storage(request)).receive_upload(
        user, uploader_id, object_key, declared_size, expires, sig, request.stream(),
    )
    return Response(status_code=204)


@router.post("/api/files/resources", status_code=201, response_model=FileResourceDetail, response_model_exclude_unset=True)
def create_resource(
    body: ResourceCreateBody,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    user: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    return FileService(conn, storage).create_resource(user, body)


@router.post("/api/files/resources/from-attachment", status_code=201, response_model=FileResourceDetail, response_model_exclude_unset=True)
def promote_from_attachment(
    body: FilePromoteFromAttachmentBody,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """把论坛已有附件转入文件服务（管理员专属）。

    两道防线：本依赖先兜一道 require_admin（未登录 401 / 非管理员 403），
    service 层再走 authz 的 FILE_PROMOTE_FROM_ATTACHMENT 能力——授权判定不散落在路由里。
    依赖先于 body 解析执行，所以非管理员无论发什么载荷都拿不到任何与附件存在性有关的响应。
    Two layers: this dependency guards with require_admin (401 when signed out, 403 otherwise),
    and the service then goes through the authz ability FILE_PROMOTE_FROM_ATTACHMENT, so no
    authorisation decision is scattered into the route. Dependencies run before the body is
    parsed, so a non-admin gets no response that depends on whether the attachment exists.
    """
    return FileService(conn, storage).promote_from_attachment(admin, body)


@router.patch("/api/files/resources/{resource_id}", response_model=FileResourceDetail, response_model_exclude_unset=True)
def update_resource(
    resource_id: ResourceId,
    body: ResourcePatchBody,
    conn: DbConn,
    viewer: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    return FileService(conn).update_resource(viewer, resource_id, body)


@router.delete("/api/files/resources/{resource_id}", response_model=FileMutationResult, response_model_exclude_unset=True)
def delete_resource(
    resource_id: ResourceId,
    conn: DbConn,
    storage: Storage = Depends(get_storage),
    viewer: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    FileService(conn, storage).delete_resource(viewer, resource_id)
    return {"ok": True}


# ================================================================ 收藏与评分 / favorite & rating


@router.put("/api/files/resources/{resource_id}/favorite", response_model=FileFavoriteState, response_model_exclude_unset=True)
def add_favorite(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).set_favorite(user, resource_id, True)


@router.delete("/api/files/resources/{resource_id}/favorite", response_model=FileFavoriteState, response_model_exclude_unset=True)
def remove_favorite(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).set_favorite(user, resource_id, False)


@router.put("/api/files/resources/{resource_id}/rating", response_model=FileRatingState, response_model_exclude_unset=True)
def set_rating(resource_id: ResourceId, body: RatingBody, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).set_rating(user, resource_id, body.score)


@router.delete("/api/files/resources/{resource_id}/rating", response_model=FileRatingState, response_model_exclude_unset=True)
def clear_rating(resource_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    return FileService(conn).clear_rating(user, resource_id)


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

    row = FileService(conn).get_resource_by_key(object_key)
    FileService(conn).assert_visible(viewer, row)

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
