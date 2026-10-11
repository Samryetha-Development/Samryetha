"""Validated commands and persistence records for the resource library."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class FileRecord(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    category_id: int
    # NULL = 未分类。历史行没有这一列时由 ensure_schema_drift 补上并保持 NULL。
    # NULL means unfiled. Rows that predate the column get it added by ensure_schema_drift and stay
    # NULL, which is exactly the "unfiled" bucket.
    folder_id: int | None = None
    uploader_id: int
    title: str
    description_md: str
    tags: str
    object_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str | None = None
    visibility: str
    status: str
    version: int
    is_featured: int
    download_count: int
    favorite_count: int
    rating_sum: int
    rating_count: int
    created_at: int
    updated_at: int
    deleted_at: int | None = None
    category_slug: str = ""
    category_name: str = ""
    category_kind: str = ""
    uploader_username: str = ""
    uploader_display_name: str | None = None
    uploader_discriminator: int | None = None


class CategoryRecord(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    slug: str
    name: str
    description: str
    kind: str
    sort_order: int
    is_system: int


class FolderRecord(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    parent_id: int | None = None
    name: str
    description: str
    sort_order: int
    deleted_at: int | None = None
    created_at: int
    updated_at: int


class Command(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FilePresignBody(Command):
    filename: Annotated[str, Field(min_length=1, max_length=255)]
    mimeType: Annotated[str, Field(min_length=1, max_length=100)] = "application/octet-stream"
    sizeBytes: Annotated[int, Field(gt=0, le=50 * 1024 * 1024)]


class ResourceCreateBody(Command):
    objectKey: str
    expires: str
    sig: str
    sizeBytes: Annotated[int, Field(gt=0, le=50 * 1024 * 1024)]
    categoryId: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(min_length=1, max_length=160)]
    descriptionMarkdown: Annotated[str, Field(max_length=20_000)] = ""
    tags: list[str] | str = []
    originalFilename: str = ""
    mimeType: str = "application/octet-stream"
    visibility: str = "members"
    sha256: str | None = None


class FilePromoteFromAttachmentBody(Command):
    """把论坛已有附件转入文件服务的命令（管理员专属）。

    title 允许缺省：缺省时由服务层从附件原始文件名推导。
    The command that promotes an existing forum attachment into the file service (admins
    only). The title may be omitted, in which case the service derives it from the
    attachment's original filename.
    """

    attachmentId: Annotated[int, Field(ge=1)]
    categoryId: Annotated[int, Field(ge=1)]
    title: Annotated[str, Field(max_length=160)] | None = None
    descriptionMarkdown: Annotated[str, Field(max_length=20_000)] = ""
    tags: list[str] | str = []
    # 缺省由服务层取 "members"（与 create_resource 同缺省），并被源附件受众上限向下夹紧。
    # Defaults to "members" in the service (the same default create_resource uses) and is
    # clamped downwards by the source attachment's audience ceiling.
    visibility: str | None = None


class ResourcePatchBody(Command):
    title: Annotated[str, Field(min_length=1, max_length=160)] | None = None
    descriptionMarkdown: Annotated[str, Field(max_length=20_000)] | None = None
    tags: list[str] | str | None = None
    categoryId: Annotated[int, Field(ge=1)] | None = None
    visibility: str | None = None
    status: str | None = None


class RatingBody(Command):
    score: Annotated[int, Field(ge=1, le=5)]


class CategoryCreateBody(Command):
    slug: Annotated[str, Field(min_length=1, max_length=60)]
    name: Annotated[str, Field(min_length=1, max_length=60)]
    description: Annotated[str, Field(max_length=500)] = ""
    kind: str = "other"
    sortOrder: int = 0


class CategoryPatchBody(Command):
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    kind: str | None = None
    sortOrder: int | None = None


class FolderCreateBody(Command):
    name: Annotated[str, Field(min_length=1, max_length=60)]
    # 缺省 = 顶层文件夹。父级必须已存在，否则 404（不泄漏"某个不存在的 id 是否曾被用过"）。
    # Omitted means a top-level folder. The parent must exist or the request is answered with 404,
    # which reveals nothing about whether a missing id was ever used.
    parentId: Annotated[int, Field(ge=1)] | None = None
    description: Annotated[str, Field(max_length=500)] = ""
    sortOrder: int = 0


class FolderPatchBody(Command):
    name: Annotated[str, Field(min_length=1, max_length=60)] | None = None
    description: Annotated[str, Field(max_length=500)] | None = None
    # 改父级（移动文件夹）：未提供 = 不动；提供整数 = 挂到该文件夹下；显式 null = 移到顶层。
    # "未提供"与"显式 null"靠 model_fields_set 区分，而不是让 null 同时背两个意思——
    # 用 0 表示顶层之类的写法在查询串里太容易与手误混淆。
    # Re-parenting (moving a folder): absent leaves it alone, an integer nests it under that
    # folder, and an explicit null moves it to the top level. Absent and explicit-null are told
    # apart through model_fields_set rather than making null carry both meanings, because spellings
    # such as 0-means-root are far too easy to confuse with a typo.
    parentId: Annotated[int, Field(ge=1)] | None = None
    sortOrder: int | None = None


class ResourceFolderBody(Command):
    """把一份资料放进某个文件夹，或移出到"未分类"。

    Moves one resource into a folder, or out of every folder into "unfiled".

    folderId 缺省（不提供）与显式 null 都表示"移出到未分类"——这个端点的语义就是"设置归属"，
    不是"增量更新"，所以不存在"未提供"这个状态。
    Both an omitted folderId and an explicit null mean "move out into unfiled": this endpoint sets
    the membership rather than patching it, so there is no "not provided" state.
    """

    folderId: Annotated[int, Field(ge=1)] | None = None


class FileFilters(Command):
    category: str | None = None
    categoryId: int | None = None
    kind: str | None = None
    tag: str | None = None
    q: str | None = None
    sort: str | None = None
    status: str | None = None
    featured: bool = False
    uploaderId: int | None = None
    # 文件夹过滤：folderId 命中某一个文件夹，unfiled=true 只取"未分类"。
    # 两者刻意分开而不是用 folderId=0 表示未分类——0 与"没有提供"在查询串里长得太像，
    # 一个手误就会把"未分类"变成"全部"。
    # Folder filtering: folderId selects one folder, unfiled selects only the unfiled bucket. They
    # are kept apart rather than spelling "unfiled" as folderId=0, because 0 and "absent" look far
    # too alike in a query string and a single typo would turn "unfiled" into "everything".
    folderId: int | None = None
    unfiled: bool = False
    page: int | None = None
    pageSize: int | None = None


FileKind = Literal["guide", "outline", "syllabus", "exam", "other"]
FileVisibility = Literal["public", "members", "private"]
FileSort = Literal["latest", "downloads", "favorites", "rating", "name"]


class FileCategory(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    kind: FileKind
    sortOrder: int
    isSystem: bool
    resourceCount: int | None = None


class FileCategoryList(BaseModel):
    items: list[FileCategory]


class FileFolder(BaseModel):
    """文件夹（扁平返回，前端按 parentId 自行拼树与面包屑）。

    A folder, returned flat: the client builds the tree and the breadcrumb from parentId itself.

    resourceCount 是**当前查看者可见的**该文件夹内资料数，不是该文件夹的物理总数——
    否则数字本身就会泄漏"这里有多少份你看不到的资料"。
    resourceCount counts the resources this viewer may see, not every row physically filed here;
    otherwise the number itself would leak how many resources are hidden from them.
    """

    id: int
    parentId: int | None = None
    name: str
    description: str
    sortOrder: int
    resourceCount: int
    childCount: int


class FileFolderList(BaseModel):
    items: list[FileFolder]


class FileCategoryRef(BaseModel):
    id: int
    slug: str
    name: str
    kind: FileKind


class FileAuthor(BaseModel):
    id: int
    username: str
    handle: str
    displayName: str


class FileResourceSummary(BaseModel):
    id: int
    title: str
    preview: str
    folderId: int | None = None
    category: FileCategoryRef
    uploader: FileAuthor
    tags: list[str]
    originalFilename: str
    mimeType: str
    extension: str
    sizeBytes: int
    visibility: FileVisibility
    status: Literal["published", "archived"]
    version: int
    isFeatured: bool
    downloadCount: int
    favoriteCount: int
    ratingAvg: float | None
    ratingCount: int
    isFavorited: bool
    myRating: int | None
    createdAt: int
    updatedAt: int


class FilePermissions(BaseModel):
    update: bool
    delete: bool


class FileResourceDetail(FileResourceSummary):
    descriptionMarkdown: str
    can: FilePermissions
    sha256: str | None = None


class FileResourceItems(BaseModel):
    items: list[FileResourceSummary]
    total: int


class FileResourceList(FileResourceItems):
    page: int
    pageSize: int
    sort: FileSort


class FileTagCount(BaseModel):
    tag: str
    count: int


class FileStats(BaseModel):
    resourceCount: int
    categoryCount: int


class FileConfig(BaseModel):
    categories: list[FileCategory]
    folders: list[FileFolder]
    tagCloud: list[FileTagCount]
    stats: FileStats
    allowedExtensions: list[str]
    maxUploadBytes: int
    kinds: list[FileKind]
    visibilities: list[FileVisibility]
    sorts: list[FileSort]
    maxTags: int


class FilePresign(BaseModel):
    objectKey: str
    uploadUrl: str
    expires: str
    sig: str
    expiresAt: int
    maxUploadBytes: int
    contentType: str


class FileDownloadTicket(BaseModel):
    id: int
    title: str
    originalFilename: str
    sizeBytes: int
    downloadUrl: str
    expiresAt: int
    objectKey: str


class FileFavoriteState(BaseModel):
    resourceId: int
    isFavorited: bool
    favoriteCount: int


class FileRatingState(BaseModel):
    resourceId: int
    myRating: int | None
    ratingAvg: float | None
    ratingCount: int


class FileMutationResult(BaseModel):
    ok: bool


class FileFolderDeletion(FileMutationResult):
    """删除文件夹的结果。

    刻意把"被解绑到未分类的资料份数"回给调用方：删除是一个不可见后果的动作，
    界面必须能说出"有 N 份资料被移到未分类"，而不是只说一句 "ok"。
    The outcome of deleting a folder. The number of resources detached into "unfiled" is reported
    back deliberately: deleting has consequences the caller cannot see, so the interface has to be
    able to say "N resources moved to unfiled" rather than just "ok".
    """

    detachedResourceCount: int
