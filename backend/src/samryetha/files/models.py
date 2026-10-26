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
    # Open to Public 直链公开标记（0/1）。public_set_by / public_set_at 记录最后一次切换
    # 公开状态的管理员与时间（设置与取消都会更新），仅供管理员视图的审计展示。
    # The Open to Public direct-link flag (0/1). public_set_by and public_set_at record the admin and
    # time of the last toggle, in both directions (set and unset), for the admin audit view only.
    is_public: int
    public_set_by: int | None = None
    public_set_at: int | None = None
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
    # 上传时直接指定归属文件夹。缺省或显式 null 表示落在根目录（不属于任何文件夹）。
    # 与 PATCH 的 folderId 不同，这里没有"保持原值"的语义：一份新资料的归属总要有一个值。
    # The destination folder may be given at upload time; omitting it or passing an explicit null
    # files the resource at the root (it belongs to no folder). Unlike the PATCH body's folderId there
    # is no "leave it alone" meaning here: a brand-new resource always has to land somewhere.
    folderId: Annotated[int, Field(ge=1)] | None = None


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


class FilePublicPatchBody(Command):
    """Open to Public 直链公开开关的写命令（管理员专属）。

    单独成一个命令而不是塞进 ResourcePatchBody：visibility 可由 owner 或管理员改，
    而 is_public 是管理员专属的治理开关，权限模型不同，不该共享同一个 PATCH 体。
    A dedicated command for the Open to Public direct-link flag (admins only). It lives apart from
    ResourcePatchBody because visibility is changeable by an owner or an admin, whereas is_public is
    an admin-only governance switch with a different permission model, so they must not share one
    PATCH body.
    """

    isPublic: bool


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
# 发布状态：命名别名与 service.STATUSES 同源，供库表 DTO 与 FileConfig 共用一份定义，
# 免得"资料的状态"与"配置下发的状态白名单"各自写一遍、日后再漂移。
# The publication status, named so the record DTO and FileConfig share one definition instead of
# repeating the literal twice and drifting apart later; it mirrors service.STATUSES.
FileStatus = Literal["published", "archived"]
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
    status: FileStatus
    version: int
    isFeatured: bool
    # Open to Public 直链公开标记：列表/详情据此展示「公开」徽章。
    # The Open to Public direct-link flag: the list and detail pages show the "public" badge from it.
    isPublic: bool
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
    # 审计字段（仅管理员视图返回）：谁在何时最后一次设置/取消公开。
    # Audit fields (admin view only): who last set or unset the public flag, and when.
    publicSetBy: int | None = None
    publicSetAt: int | None = None


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


class FilePreviewProblem(BaseModel):
    code: str
    message: str
    requestId: str
    details: object | None = None


class FilePreviewError(BaseModel):
    error: FilePreviewProblem


class FileConfig(BaseModel):
    categories: list[FileCategory]
    folders: list[FileFolder]
    tagCloud: list[FileTagCount]
    stats: FileStats
    allowedExtensions: list[str]
    maxUploadBytes: int
    kinds: list[FileKind]
    visibilities: list[FileVisibility]
    # 状态白名单同样下发：客户端的编辑表单要列出可选的发布状态，
    # 硬编码两个字面量就等于把后端白名单抄进前端，正是本 DTO 存在的理由要避免的事。
    # The status allow-list ships as well: the client's edit form has to offer the selectable
    # publication states, and hard-coding the two literals would be exactly the hand-copying of a
    # backend allow-list this DTO exists to prevent.
    statuses: list[FileStatus]
    sorts: list[FileSort]
    maxTags: int
    # 文档预览：只有后端真的装了转换器才为真。客户端据此决定 Office 文件显示"转换后
    # 预览"还是"下载后查看"，不必让前端去猜服务器有没有装 LibreOffice。
    # Document preview is true only when the backend actually has a converter, so the client
    # never has to guess whether LibreOffice exists on the host.
    documentPreview: bool
    convertibleExtensions: list[str]


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
    downloadCount: int


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


# ================================================================ 批量操作 / batch operations


class BatchSelectionBody(Command):
    """一次批量操作选中的资料集合。

    上限 50 与列表页单页上限 MAX_PAGE_SIZE 对齐：界面一次最多勾选一页，后端按同一口径设限，
    免得出现"界面能勾、后端拒收"的错位。去重与非法值在服务层再校验一次——pydantic 的 ge
    只挡单个元素，挡不住重复 id 造成的重复计数。
    The set of resources one batch operation acts on. The cap of 50 matches the listing page's
    MAX_PAGE_SIZE: the interface can only tick one page at a time and the backend draws the line in
    the same place, so "the interface lets me tick it, the backend rejects it" cannot happen.
    De-duplication and invalid values are validated again in the service layer, because pydantic's
    ge guards a single element and cannot stop duplicate ids from being counted twice.
    """

    ids: Annotated[list[Annotated[int, Field(ge=1)]], Field(min_length=1, max_length=50)]


class BatchMoveBody(BatchSelectionBody):
    """批量移动到某个文件夹；folderId 缺省或显式 null 都表示"移到未分类"。

    语义与单条 PUT /resources/{id}/folder 完全一致（设置归属而非增量更新），只是作用在多条上。
    Batch move into a folder; an omitted or explicit-null folderId means "move to unfiled". The
    semantics are exactly those of the single-resource PUT /resources/{id}/folder: set the
    membership rather than patch it, only across many rows.
    """

    folderId: Annotated[int, Field(ge=1)] | None = None


class BatchCopyBody(BatchSelectionBody):
    """批量复制；folderId 缺省表示"复制件落在与原资料相同的归属"。"""

    folderId: Annotated[int, Field(ge=1)] | None = None


class BatchRenameBody(BatchSelectionBody):
    """批量重命名：只支持三种可解释、可预测的规则，不做正则替换。

    mode = prefix  -> 标题变成 "<prefix><原标题>"
    mode = suffix  -> 标题变成 "<原标题><suffix>"
    mode = replace -> 把标题里所有的 find 替换成 replace（find 必填且非空）

    刻意不做正则：批量动作一次改 N 条标题，正则写错一次就毁掉 N 条，而"找出写错的那条"要人工
    逐条翻。三种字符串规则错了也只错在字面上，肉眼一看就能发现。
    Batch rename supports three explainable, predictable rules and no regular expressions.
    mode=prefix prepends, mode=suffix appends, mode=replace rewrites every occurrence of find.
    Regular expressions are deliberately excluded: a batch action rewrites N titles at once, one
    bad pattern destroys all N, and finding the rows it mangled means reading them one by one.
    These three string rules can only go wrong in ways the eye catches immediately.
    """

    mode: Literal["prefix", "suffix", "replace"]
    prefix: Annotated[str, Field(max_length=60)] = ""
    suffix: Annotated[str, Field(max_length=60)] = ""
    find: Annotated[str, Field(max_length=160)] = ""
    replace: Annotated[str, Field(max_length=160)] = ""


class BatchEditBody(BatchSelectionBody):
    """批量编辑共享元数据：只改提供的字段，未提供的字段一律不动。

    刻意不包含 title / descriptionMarkdown：标题逐条不同，批量改成同一个标题就是批量毁掉
    区分度；简介同理。这两项走 BatchRenameBody 或单条编辑。
    Batch edit the shared metadata: only the fields actually provided change. Title and
    descriptionMarkdown are deliberately absent: titles differ per resource and rewriting them all
    to one string destroys the very thing that tells them apart. Those two go through the rename
    endpoint or the single-resource editor.
    """

    categoryId: Annotated[int, Field(ge=1)] | None = None
    visibility: str | None = None
    status: str | None = None
    tags: list[str] | str | None = None


class BatchItemResult(BaseModel):
    """单条结果：成功给出 id，失败给出 id 与服务端原因。

    失败必须逐条可见。批量动作最坏的形态是"看起来成功、实际有几条没做"，那会让调用方以为
    数据已经统一，之后再拿这份错误认知去做别的决定。
    One row's outcome: the id on success, the id plus the server's reason on failure. Failures have
    to be visible per row, because the worst shape of a batch action is "looked like it worked while
    a few rows did not", which leaves the caller believing the data is uniform and making further
    decisions on that belief.
    """

    id: int
    ok: bool
    error: str | None = None


class BatchResult(BaseModel):
    """批量动作的总体结果：请求数、成功数、失败数，以及逐条明细。"""

    requested: int
    succeeded: int
    failed: int
    results: list[BatchItemResult]
