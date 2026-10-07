"""Resource library use cases; SQL belongs to FileRepository."""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import AsyncIterable
from typing import Any, cast
from urllib.parse import quote

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from .models import CategoryCreateBody, CategoryPatchBody, CategoryRecord, FileFilters, FileRecord, FilePresignBody, ResourceCreateBody, ResourcePatchBody
from .repository import FileRepository
from ..adapters.storage import MAX_UPLOAD_BYTES, OBJECT_KEY_RE, Storage, content_type_for_object_key
from ..authz import Abilities, Actor, AuthorizationService
from ..core.db import now_ms
from ..core.errors import bad_request, conflict, forbidden, not_found
from ..users.service import make_handle

KINDS = ("guide", "outline", "syllabus", "exam", "other")
VISIBILITIES = ("public", "members", "private")
SORTS = ("latest", "downloads", "favorites", "rating", "name")
STATUSES = ("published", "archived")
MAX_TAGS = 8
MAX_TAG_LENGTH = 24
MAX_TITLE_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 20_000
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50
DEFAULT_ORPHAN_RETENTION_MS = 24 * 3600 * 1000
SEED_CATEGORIES = (
    ("freshman-guide", "新生攻略", "guide", "报到流程、校园生活、必备物品与常见问题。", 10),
    ("exam-outline", "复习提纲", "outline", "各科期末与阶段性复习提纲。", 20),
    ("study-syllabus", "学习纲要", "syllabus", "课程知识框架、重点章节与自学路径。", 30),
    ("past-papers", "历年题", "exam", "历年考试真题与参考答案。", 40),
    ("other-materials", "其他资料", "other", "不属于以上分类的学习资料。", 90),
)


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_tags(raw: list[str] | str | None) -> list[str]:
    entries = [raw] if isinstance(raw, str) else raw or []
    tags: list[str] = []
    for entry in entries:
        for item in re.split(r"[,，、;；]+", entry):
            tag = _flat(item).lower()[:MAX_TAG_LENGTH]
            if tag and tag not in tags:
                tags.append(tag)
    return tags[:MAX_TAGS]


def _tags(raw: str) -> list[str]:
    try:
        parsed: object = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in cast(list[object], parsed) if isinstance(item, (str, int, float))]


def _average(row: FileRecord) -> float | None:
    return round(row.rating_sum / row.rating_count, 2) if row.rating_count else None


def _category(row: CategoryRecord, count: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"id": row.id, "slug": row.slug, "name": row.name, "description": row.description,
                             "kind": row.kind, "sortOrder": row.sort_order, "isSystem": bool(row.is_system)}
    if count is not None:
        result["resourceCount"] = count
    return result


def _resource(row: FileRecord) -> dict[str, object]:
    return {"id": row.id, "type": "file_resource", "uploaderId": row.uploader_id,
            "visibility": row.visibility, "status": row.status}


def _summary(row: FileRecord, favorited: bool, rating: int | None) -> dict[str, Any]:
    preview = _flat(row.description_md)
    return {
        "id": row.id, "title": row.title, "preview": preview if len(preview) <= 160 else preview[:160] + "...",
        "category": {"id": row.category_id, "slug": row.category_slug, "name": row.category_name, "kind": row.category_kind},
        "uploader": {"id": row.uploader_id, "username": row.uploader_username,
                     "handle": make_handle(row.uploader_username, row.uploader_discriminator), "displayName": row.uploader_display_name},
        "tags": _tags(row.tags), "originalFilename": row.original_filename, "mimeType": row.mime_type,
        "extension": os.path.splitext(row.original_filename)[1].lower(), "sizeBytes": row.size_bytes,
        "visibility": row.visibility, "status": row.status, "version": row.version, "isFeatured": bool(row.is_featured),
        "downloadCount": row.download_count, "favoriteCount": row.favorite_count, "ratingAvg": _average(row),
        "ratingCount": row.rating_count, "isFavorited": favorited, "myRating": rating,
        "createdAt": row.created_at, "updatedAt": row.updated_at,
    }


def _upload_pathname(uploader_id: int, object_key: str, size_bytes: int) -> str:
    return f"/api/files/upload/{uploader_id}/{object_key}@size={size_bytes}"


def build_object_url(base_path: str, object_key: str, params: dict[str, object]) -> str:
    query = "&".join(f"{name}={quote(str(value), safe='')}" for name, value in params.items() if value is not None)
    path = f"{base_path}/{quote(object_key, safe='/')}"
    return f"{path}?{query}" if query else path


def verify_upload_signature(storage: Storage, uploader_id: int, object_key: str, size_bytes: int, expires: str, sig: str) -> None:
    if not storage.verify_signature("PUT", _upload_pathname(uploader_id, object_key, size_bytes), expires, sig):
        raise bad_request("Invalid or expired upload ticket")


class FileService:
    def __init__(self, conn: Connection, storage: Storage | None = None) -> None:
        self._repository = FileRepository(conn)
        self._authz = AuthorizationService(conn)
        self._storage = storage

    def _require_storage(self) -> Storage:
        if self._storage is None:
            raise RuntimeError("FileService operation requires storage")
        return self._storage

    def get_resource_row(self, resource_id: int) -> FileRecord | None:
        return self._repository.get(resource_id)

    def get_resource_by_key(self, object_key: str) -> FileRecord:
        row = self._repository.get_by_key(object_key)
        if row is None or row.deleted_at is not None:
            raise not_found("Resource not found")
        return row

    def _require_resource(self, resource_id: int) -> FileRecord:
        row = self._repository.get(resource_id)
        if row is None:
            raise not_found("Resource not found")
        return row

    def assert_visible(self, viewer: Actor | None, row: FileRecord) -> None:
        if not self._authz.can(viewer, Abilities.FILE_READ, _resource(row)):
            raise not_found("Resource not found")

    def _write_resource(self, viewer: Actor | None, resource_id: int) -> FileRecord:
        self._repository.lock_resource(resource_id)
        row = self._require_resource(resource_id)
        self.assert_visible(viewer, row)
        return row

    def list_categories(self, viewer: Actor | None = None, include_counts: bool = True) -> list[dict[str, Any]]:
        counts = self._repository.category_counts(viewer) if include_counts else {}
        return [_category(row, counts.get(row.id, 0) if include_counts else None) for row in self._repository.categories()]

    def ensure_seed_categories(self) -> int:
        created = 0
        for slug, name, kind, description, order in SEED_CATEGORIES:
            created += self._repository.seed_category({"slug": slug, "name": name, "kind": kind, "description": description,
                                                      "sort_order": order, "is_system": 1, "created_at": now_ms(), "updated_at": now_ms()})
        return created

    def create_category(self, command: CategoryCreateBody) -> dict[str, Any]:
        slug = _flat(command.slug).lower()
        if not slug:
            raise bad_request("Category slug is required")
        if self._repository.category_by_slug(slug) is not None:
            raise conflict("Category slug already exists")
        if command.kind not in KINDS:
            raise bad_request("Unsupported category kind")
        try:
            category_id = self._repository.insert_category({"slug": slug, "name": command.name, "description": command.description,
                "kind": command.kind, "sort_order": command.sortOrder, "is_system": 0, "created_at": now_ms(), "updated_at": now_ms()})
        except IntegrityError:
            raise conflict("Category slug already exists")
        return _category(self._require_category(category_id), 0)

    def _require_category(self, category_id: int) -> CategoryRecord:
        category = self._repository.category(category_id)
        if category is None:
            raise not_found("Category not found")
        return category

    def update_category(self, category_id: int, command: CategoryPatchBody) -> dict[str, Any]:
        self._require_category(category_id)
        values: dict[str, Any] = {"updated_at": now_ms()}
        for name, value in command.model_dump(exclude_none=True).items():
            if name == "kind" and value not in KINDS:
                raise bad_request("Unsupported category kind")
            values["sort_order" if name == "sortOrder" else name] = value
        self._repository.update_category(category_id, values)
        return _category(self._require_category(category_id), 0)

    def delete_category(self, category_id: int, actor_id: int) -> None:
        category = self._require_category(category_id)
        if category.is_system:
            raise conflict("Built-in categories cannot be deleted")
        if self._repository.category_resource_count(category_id):
            raise conflict("Category still holds resources")
        self._repository.update_category(category_id, {"deleted_at": now_ms(), "deleted_by": actor_id, "updated_at": now_ms()})

    def _summaries(self, viewer: Actor | None, rows: list[FileRecord]) -> list[dict[str, Any]]:
        favs, ratings = self._repository.viewer_flags(viewer, [row.id for row in rows])
        return [_summary(row, row.id in favs, ratings.get(row.id)) for row in rows]

    def list_resources(self, viewer: Actor | None, filters: FileFilters | None = None) -> dict[str, Any]:
        filters = filters or FileFilters()
        page = max(1, filters.page or 1)
        size = min(MAX_PAGE_SIZE, max(1, filters.pageSize or DEFAULT_PAGE_SIZE))
        rows, total = self._repository.list_resources(viewer, filters, page, size)
        return {"items": self._summaries(viewer, rows), "total": total, "page": page, "pageSize": size,
                "sort": filters.sort if filters.sort in SORTS else "latest"}

    def list_tag_cloud(self, viewer: Actor | None, limit: int = 20) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for raw in self._repository.visible_tags(viewer):
            for tag in _tags(raw):
                counts[tag] = counts.get(tag, 0) + 1
        return [{"tag": tag, "count": count} for tag, count in sorted(counts.items(), key=lambda entry: (-entry[1], entry[0]))[:max(1, limit)]]

    def category_stats(self, viewer: Actor | None) -> dict[str, int]:
        return {"resourceCount": sum(self._repository.category_counts(viewer).values()), "categoryCount": len(self._repository.categories())}

    def get_resource(self, viewer: Actor | None, resource_id: int) -> dict[str, Any]:
        row = self._require_resource(resource_id)
        self.assert_visible(viewer, row)
        dto = self._summaries(viewer, [row])[0]
        dto["descriptionMarkdown"] = row.description_md
        dto["can"] = {"update": self._authz.can(viewer, Abilities.FILE_UPDATE, _resource(row)),
                      "delete": self._authz.can(viewer, Abilities.FILE_DELETE, _resource(row))}
        if row.sha256:
            dto["sha256"] = row.sha256
        return dto

    def list_favorites(self, viewer: Actor | None) -> dict[str, Any]:
        rows = [] if viewer is None else self._repository.personal_resources(viewer, favorites=True)
        return {"items": self._summaries(viewer, rows), "total": len(rows)}

    def list_mine(self, viewer: Actor | None) -> dict[str, Any]:
        rows = [] if viewer is None else self._repository.personal_resources(viewer, favorites=False)
        return {"items": self._summaries(viewer, rows), "total": len(rows)}

    def is_object_claimed(self, object_key: str) -> bool:
        return self._repository.get_by_key(object_key) is not None

    def presign_upload(self, user: Actor, command: FilePresignBody) -> dict[str, Any]:
        self._authz.assert_can(user, Abilities.FILE_CREATE, None)
        storage = self._require_storage()
        filename = _flat(command.filename)
        if not filename:
            raise bad_request("Filename is required")
        object_key = storage.create_upload_session(user.id, filename, command.mimeType, command.sizeBytes)
        signed = storage.sign_path("PUT", _upload_pathname(user.id, object_key, command.sizeBytes), 900)
        return {"objectKey": object_key, "uploadUrl": build_object_url(f"/api/files/upload/{user.id}", object_key,
                {"size": command.sizeBytes, "expires": signed["expires"], "sig": signed["sig"]}),
                "expires": signed["expires"], "sig": signed["sig"], "expiresAt": signed["expiresAt"],
                "maxUploadBytes": MAX_UPLOAD_BYTES, "contentType": content_type_for_object_key(object_key)}

    async def receive_upload(
        self, user: Actor | None, uploader_id: int, object_key: str, declared_size: int,
        expires: str, sig: str, chunks: AsyncIterable[bytes],
    ) -> None:
        storage = self._require_storage()
        if declared_size <= 0 or declared_size > MAX_UPLOAD_BYTES:
            raise bad_request("File too large")
        if not OBJECT_KEY_RE.match(object_key):
            raise bad_request("Invalid object key")
        if self.is_object_claimed(object_key):
            raise conflict("This upload has already been published")
        if user is None or user.id != uploader_id or user.status != "active":
            raise forbidden("Upload session is no longer available")
        verify_upload_signature(storage, uploader_id, object_key, declared_size, expires, sig)
        full = storage.path_for(object_key)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=os.path.dirname(full))
        wrote = 0
        try:
            with os.fdopen(fd, "wb") as fh:
                async for chunk in chunks:
                    wrote += len(chunk)
                    if wrote > MAX_UPLOAD_BYTES or wrote > declared_size:
                        raise bad_request("File too large")
                    fh.write(chunk)
            if wrote != declared_size:
                raise bad_request("Upload size does not match upload session")
            try:
                os.link(temporary, full)
            except FileExistsError:
                raise conflict("This object has already been uploaded")
            # Fail closed on filesystems without atomic hard links. A check/replace
            # fallback would let concurrent ticket replays overwrite published bytes.
        finally:
            os.remove(temporary)

    def create_resource(self, user: Actor, command: ResourceCreateBody) -> dict[str, Any]:
        self._repository.lock_resource(0)
        self._authz.assert_actor_current(user.id, expected_role=user.role)
        self._authz.assert_can(user, Abilities.FILE_CREATE, None)
        storage = self._require_storage()
        verify_upload_signature(storage, user.id, command.objectKey, command.sizeBytes, command.expires, command.sig)
        full = storage.path_for(command.objectKey)
        if not os.path.isfile(full):
            raise bad_request("Uploaded file was not found")
        if os.path.getsize(full) != command.sizeBytes:
            raise bad_request("Uploaded file size does not match")
        category = self._require_category(command.categoryId)
        title = _flat(command.title)
        if not title:
            raise bad_request("Title is required")
        if command.visibility not in VISIBILITIES:
            raise bad_request("Unsupported visibility")
        if self.is_object_claimed(command.objectKey):
            raise conflict("This upload has already been published")
        try:
            resource_id = self._repository.insert_resource({"category_id": category.id, "uploader_id": user.id, "title": title,
                "description_md": command.descriptionMarkdown, "tags": json.dumps(normalize_tags(command.tags), ensure_ascii=False),
                "object_key": command.objectKey, "original_filename": _flat(command.originalFilename) or "file",
                "mime_type": content_type_for_object_key(command.objectKey), "size_bytes": command.sizeBytes, "sha256": command.sha256,
                "visibility": command.visibility, "status": "published", "version": 1, "is_featured": 0,
                "created_at": now_ms(), "updated_at": now_ms()})
        except IntegrityError:
            raise conflict("This upload has already been published")
        return self.get_resource(user, resource_id)

    def update_resource(self, viewer: Actor, resource_id: int, command: ResourcePatchBody) -> dict[str, Any]:
        self._repository.lock_resource(resource_id)
        self._authz.assert_actor_current(viewer.id, expected_role=viewer.role)
        row = self._require_resource(resource_id)
        self._authz.assert_can(viewer, Abilities.FILE_UPDATE, _resource(row))
        values: dict[str, Any] = {"updated_at": now_ms()}
        if command.title is not None:
            values["title"] = _flat(command.title)
            if not values["title"]:
                raise bad_request("Title is required")
        if command.descriptionMarkdown is not None:
            values["description_md"] = command.descriptionMarkdown
        if command.tags is not None:
            values["tags"] = json.dumps(normalize_tags(command.tags), ensure_ascii=False)
        if command.categoryId is not None:
            values["category_id"] = self._require_category(command.categoryId).id
        for name, value, allowed in (("visibility", command.visibility, VISIBILITIES), ("status", command.status, STATUSES)):
            if value is not None:
                if value not in allowed:
                    raise bad_request(f"Unsupported {name}")
                values[name] = value
        self._repository.update_resource(resource_id, values)
        return self.get_resource(viewer, resource_id)

    def delete_resource(self, viewer: Actor, resource_id: int) -> None:
        self._repository.lock_resource(resource_id)
        self._authz.assert_actor_current(viewer.id, expected_role=viewer.role)
        row = self._require_resource(resource_id)
        self._authz.assert_can(viewer, Abilities.FILE_DELETE, _resource(row))
        self._repository.update_resource(resource_id, {"deleted_at": now_ms(), "deleted_by": viewer.id, "updated_at": now_ms()})

    def set_favorite(self, viewer: Actor, resource_id: int, on: bool) -> dict[str, Any]:
        self._write_resource(viewer, resource_id)
        self._authz.assert_actor_current(viewer.id, expected_role=viewer.role)
        self._authz.assert_can(viewer, Abilities.FILE_CREATE, None)
        self._repository.favorite(resource_id, viewer.id, on)
        return {"resourceId": resource_id, "isFavorited": on, "favoriteCount": self._require_resource(resource_id).favorite_count}

    def set_rating(self, viewer: Actor, resource_id: int, score: int) -> dict[str, Any]:
        if not 1 <= score <= 5:
            raise bad_request("Score must be between 1 and 5")
        self._write_resource(viewer, resource_id)
        self._authz.assert_actor_current(viewer.id, expected_role=viewer.role)
        self._authz.assert_can(viewer, Abilities.FILE_CREATE, None)
        self._repository.rating(resource_id, viewer.id, score)
        return self._rating_response(resource_id, score)

    def clear_rating(self, viewer: Actor, resource_id: int) -> dict[str, Any]:
        self._write_resource(viewer, resource_id)
        self._authz.assert_actor_current(viewer.id, expected_role=viewer.role)
        self._repository.rating(resource_id, viewer.id, None)
        return self._rating_response(resource_id, None)

    def _rating_response(self, resource_id: int, score: int | None) -> dict[str, Any]:
        row = self._require_resource(resource_id)
        return {"resourceId": resource_id, "myRating": score, "ratingAvg": _average(row), "ratingCount": row.rating_count}

    def record_download(self, viewer: Actor | None, row: FileRecord, client_ip: str | None) -> None:
        self._write_resource(viewer, row.id)
        self._repository.record_download(row.id, None if viewer is None else viewer.id, client_ip)

    def download_payload(self, viewer: Actor | None, resource_id: int) -> dict[str, Any]:
        row = self._require_resource(resource_id)
        self.assert_visible(viewer, row)
        signed = self._require_storage().sign_path("GET", f"/api/files/serve/{row.object_key}", 3600)
        return {"id": row.id, "title": row.title, "originalFilename": row.original_filename, "sizeBytes": row.size_bytes,
                "downloadUrl": build_object_url("/api/files/serve", row.object_key, {"expires": signed["expires"], "sig": signed["sig"]}),
                "expiresAt": signed["expiresAt"], "objectKey": row.object_key}

    def reap_orphan_objects(self, older_than_ms: int = DEFAULT_ORPHAN_RETENTION_MS) -> int:
        known = self._repository.claimed_keys()
        root = os.path.realpath(self._require_storage().root)
        if not os.path.isdir(root):
            return 0
        threshold = now_ms() - older_than_ms
        removed = 0
        for entry in os.scandir(root):
            if not entry.is_dir(follow_symlinks=False):
                continue
            for child in os.scandir(entry.path):
                key = f"{entry.name}/{child.name}"
                if not child.is_file(follow_symlinks=False) or not OBJECT_KEY_RE.match(key) or key in known:
                    continue
                try:
                    if int(child.stat().st_mtime * 1000) <= threshold:
                        os.remove(child.path)
                        removed += 1
                except OSError:
                    continue
        return removed
