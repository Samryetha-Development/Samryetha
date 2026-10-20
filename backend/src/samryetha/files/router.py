"""/api/files — 文件服务（面向新生的资料库）路由。

File service routes (a resource library for newcomers).

约定 / Conventions:
- 读接口按可见性在 SQL 层过滤；不可见一律 404，不泄漏存在性。
  Read endpoints filter by visibility in SQL; anything invisible is a plain 404 and never
  leaks existence.
- 上传字节、新建资料与分类管理要求全局管理员；评分/收藏等普通互动要求 active 账户。
  路由层只做"是否登录/账户是否可用"这一层认证，能不能做由 service 层经 authz 判定，
  避免同一条规则在路由与业务层各写一份。
  Uploading bytes, creating resources and category management require the global admin; ordinary
  interactions such as rating and favouriting require an active account. The routing layer only
  authenticates (signed in, account usable); what an actor may do is decided in the service layer
  through authz, so one rule never lives in two places.
- 文件字节走 storage.py 的 HMAC presign 三段式：presign -> signed PUT -> signed GET。
  File bytes use storage.py's three-step HMAC presign flow: presign -> signed PUT ->
  signed GET.
"""

from __future__ import annotations

import os
from pathlib import Path as FsPath
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import FileResponse

from . import service
from .service import FileService
from .models import FileConfig, FileCategoryList, FileCategory, FileFolder, FileFolderList, FileFolderDeletion, FileMutationResult, FileResourceList, FileResourceItems, FileResourceDetail, FileDownloadTicket, FilePresign, FileFavoriteState, FileRatingState
from .models import FilePresignBody, FilePromoteFromAttachmentBody, FilePublicPatchBody, ResourceCreateBody, ResourcePatchBody, ResourceFolderBody, RatingBody, CategoryCreateBody, CategoryPatchBody, FolderCreateBody, FolderPatchBody, FileFilters
from ..core.deps import CurrentUser, CurrentUserDep, DbConn, get_current_user, get_storage, require_active_user, require_admin
from ..core.errors import bad_request, forbidden, not_found
from ..adapters.storage import Storage
from ..adapters.storage import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, OBJECT_KEY_RE, content_type_for_object_key, sanitize_filename
from ..adapters.docpreview import (
    CONVERTIBLE_EXTENSIONS,
    ConversionFailed,
    ConversionUnavailable,
    cache_path,
    convert_once,
    is_convertible,
)

router = APIRouter()

ResourceId = Annotated[int, Path(ge=1)]
UploaderId = Annotated[int, Path(ge=1)]

# 允许内联预览的扩展名：只放"浏览器渲染不会执行脚本"的类型。
# 之所以不用 mimeType 判断，是因为 mimeType 来自客户端声明，可被伪造成 text/html。
# Extensions allowed to render inline: only types a browser cannot execute script from.
# The check deliberately avoids mimeType, which is client-declared and could be forged as
# text/html.
INLINE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif",
    ".pdf", ".txt", ".md", ".csv",
    # 音视频：<video>/<audio> 要能直接取字节播放（配合 Range 拖动进度条）。
    # 这里只放浏览器能原生解码的容器，避免"能内联但放不出来"的假象。
    # A/V: <video>/<audio> pull the bytes directly (Range makes seeking work). Only
    # containers browsers decode natively are listed, so an inline URL never turns into
    # a silent playback failure.
    ".mp4", ".m4v", ".webm", ".ogv", ".mov",
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac",
}


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
    request: Request,
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
        "folders": FileService(conn).list_folders(viewer),
        "tagCloud": FileService(conn).list_tag_cloud(viewer),
        "stats": FileService(conn).category_stats(viewer),
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "maxUploadBytes": MAX_UPLOAD_BYTES,
        "kinds": list(service.KINDS),
        "visibilities": list(service.VISIBILITIES),
        # 状态白名单随之下发：客户端的资料编辑表单要列出可选的发布状态，
        # 硬编码两个字面量就等于把后端白名单抄进前端——正是本接口存在的理由要避免的事。
        # The status allow-list ships alongside them: the client's edit form has to offer the
        # selectable publication states, and hard-coding the two literals would be exactly the
        # hand-copying of a backend allow-list this endpoint exists to prevent.
        "statuses": list(service.STATUSES),
        "sorts": list(service.SORTS),
        "maxTags": service.MAX_TAGS,
        # 文档预览能力随配置下发：没装转换器时前端就不该给 Office 文件挂一个会失败的
        # iframe，而应直接提示"下载后查看"。
        # Document-preview capability ships with the config: without a converter the client
        # must not mount an iframe that is doomed to fail.
        "documentPreview": getattr(request.app.state, "doc_preview", None) is not None,
        "convertibleExtensions": sorted(CONVERTIBLE_EXTENSIONS),
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


# ================================================================ 文件夹 / folders


@router.get("/api/files/folders", response_model=FileFolderList, response_model_exclude_unset=True)
def list_folders(conn: DbConn, viewer: CurrentUserDep) -> dict[str, Any]:
    """列出全部文件夹（含按查看者可见性算出的资料数）。

    空文件夹也返回：它们是导航骨架，隐藏起来只会让人以为资料不见了。
    Every folder is returned, empty ones included: they are the navigation skeleton, and hiding one
    would only make people think its resources had vanished.
    """
    return {"items": FileService(conn).list_folders(viewer)}


@router.post("/api/files/folders", status_code=201, response_model=FileFolder, response_model_exclude_unset=True)
def create_folder(
    body: FolderCreateBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    """新建文件夹（仅全局管理员；判定在 service 层，路由只做认证）。

    Creates a folder. Global admins only; the decision lives in the service layer and the route
    only authenticates, so one rule never ends up written twice.
    """
    return FileService(conn).create_folder(user, body)


@router.patch("/api/files/folders/{folder_id}", response_model=FileFolder, response_model_exclude_unset=True)
def update_folder(
    folder_id: ResourceId,
    body: FolderPatchBody,
    conn: DbConn,
    user: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    return FileService(conn).update_folder(user, folder_id, body)


@router.delete("/api/files/folders/{folder_id}", response_model=FileFolderDeletion, response_model_exclude_unset=True)
def delete_folder(folder_id: ResourceId, conn: DbConn, user: CurrentUser = Depends(require_active_user)) -> dict[str, Any]:
    """删除文件夹。资料一律移到"未分类"，绝不删除；仍有子文件夹时拒绝（409）。

    Deletes a folder. Its resources always move to "unfiled" and are never deleted; a folder that
    still has subfolders is refused with 409.
    """
    return FileService(conn).delete_folder(user, folder_id)


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
    # 文件夹过滤：folderId 命中某一个文件夹，unfiled=true 只取"未分类"。
    # 不提供任何一个 = 全部（含已归类与未归类），与改动前完全一致。
    # Folder filtering: folderId selects one folder and unfiled selects only the unfiled bucket.
    # Providing neither means everything, filed and unfiled alike, exactly as before this change.
    folderId: int | None = Query(default=None, ge=1),
    unfiled: bool = Query(default=False),
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
            "folderId": folderId,
            "unfiled": unfiled,
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


@router.get("/api/files/resources/{resource_id}/document-preview")
def document_preview(
    resource_id: ResourceId,
    request: Request,
    conn: DbConn,
    viewer: CurrentUserDep,
) -> Response:
    """把 Office 文档转成 PDF 后就地返回，交给浏览器内建的 PDF 阅读器。

    为什么不是签名 URL：这个端点自己用会话 Cookie 鉴权，`<iframe src>` 直接指过来即可，
    不必把一次性 token 拼进 iframe 地址（那会出现在浏览器历史与 Referer 里）。
    Why not a signed URL: this endpoint authenticates by session cookie, so an `<iframe src>`
    can point straight at it instead of embedding a one-shot token in the URL, where it would
    end up in browser history and Referer headers.

    与 /download 一致的语义：看一眼不算一次下载，所以不记下载量。
    转换结果按 object_key 缓存，同一文档只转换一次。
    """
    converter = getattr(request.app.state, "doc_preview", None)
    if converter is None:
        # 没装转换器：明确 404，前端据此展示"下载后查看"，而不是渲染一个空 iframe。
        raise not_found("Document preview is not available")

    row = FileService(conn).get_resource_row(resource_id)
    if row is None or getattr(row, "deleted_at", None) is not None:
        raise not_found("Resource not found")
    FileService(conn).assert_visible(viewer, row)

    object_key = row.object_key
    if not is_convertible(os.path.splitext(object_key)[1]):
        raise bad_request("This file type cannot be converted for preview")

    storage = request.app.state.storage
    try:
        source = storage.path_for(object_key)
    except Exception:
        raise forbidden("Invalid object key")
    if not os.path.exists(source):
        raise not_found("Resource not found")

    settings = request.app.state.settings
    destination = cache_path(settings.preview_cache_dir, object_key)
    try:
        pdf = convert_once(converter, FsPath(source), destination)
    except ConversionUnavailable:
        # 配置成强制启用但运行时找不到二进制：按"不可用"处理，别把 500 抛给读者。
        raise not_found("Document preview is not available")
    except ConversionFailed:
        # 损坏/加密/超时：预览是增强功能，降级为下载，不报 500。
        raise not_found("This document could not be converted for preview")

    return FileResponse(
        pdf,
        media_type="application/pdf",
        headers={
            "x-content-type-options": "nosniff",
            "content-disposition": "inline",
            # 私有缓存短一些：转换结果不会变（object_key 不可变），但没必要让共享缓存留存。
            "cache-control": "private, max-age=300",
        },
    )


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


@router.put("/api/files/resources/{resource_id}/folder", response_model=FileResourceDetail, response_model_exclude_unset=True)
def set_resource_folder(
    resource_id: ResourceId,
    body: ResourceFolderBody,
    conn: DbConn,
    viewer: CurrentUser = Depends(require_active_user),
) -> dict[str, Any]:
    """把一份资料放进某个文件夹，或移出到"未分类"。

    刻意做成一个语义化的独立端点，而不是往 PATCH 里塞一个 folderId 字段：PATCH 用
    "字段为 None 即不改"的约定，而这里 null 必须表示"移出到未分类"这个真实动作——
    两种语义挤在同一个 None 上，早晚会把某个人把资料静默移出文件夹。
    Deliberately a small endpoint of its own rather than a folderId field on PATCH: PATCH treats a
    None field as "leave it alone", while here null has to mean the real action "move to unfiled".
    Squeezing both meanings onto one None would eventually move somebody's resources out of a folder
    without them asking.
    """
    return FileService(conn).set_resource_folder(viewer, resource_id, body)


@router.patch("/api/files/resources/{resource_id}/public", response_model=FileResourceDetail, response_model_exclude_unset=True)
def set_resource_public(
    resource_id: ResourceId,
    body: FilePublicPatchBody,
    conn: DbConn,
    admin: CurrentUser = Depends(require_admin),
) -> dict[str, Any]:
    """切换 Open to Public 直链公开开关（管理员专属）。

    两道防线：本依赖先兜 require_admin（未登录 401 / 非管理员 403），service 层再走
    authz 的 FILE_SET_PUBLIC 能力。依赖先于 body 解析执行，所以非管理员无论发什么载荷、
    无论目标资源是否存在，都拿不到任何与资源存在性有关的响应——正是"幽灵 ID + 合法载荷"
    应回 403 而非 404 的语义（鉴权先于资源查找）。
    Toggles the Open to Public direct-link flag (admins only). Two layers: this dependency guards
    with require_admin (401 when signed out, 403 otherwise), and the service then goes through the
    authz ability FILE_SET_PUBLIC. Dependencies run before the body is parsed, so a non-admin gets
    no response that depends on whether the resource exists - which is exactly why a ghost id with a
    valid payload must answer 403 rather than 404 (authorisation precedes the resource lookup).
    """
    return FileService(conn).set_public(admin, resource_id, body)


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
