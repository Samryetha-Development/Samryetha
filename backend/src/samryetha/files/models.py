"""Validated commands and persistence records for the resource library."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class FileRecord(BaseModel):
    model_config = ConfigDict(strict=True)
    id: int
    category_id: int
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
