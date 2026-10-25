"""Connection-owned persistence boundary for files; caller owns commit/rollback."""
from __future__ import annotations

from typing import Any
from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from .models import CategoryRecord, FileFilters, FileRecord, FolderRecord
from ..authz import Actor
from ..core.db import now_ms
from ..core.records import require_int, require_str
from ..core.schema import attachments, file_categories, file_downloads, file_favorites, file_folders, file_ratings, file_resources, users


def _visible(viewer: Actor | None) -> ColumnElement[bool]:
    if viewer is not None and viewer.role == "admin":
        return file_resources.c.id > 0
    choices = [file_resources.c.visibility == "public"]
    if viewer is not None:
        choices.append(file_resources.c.uploader_id == viewer.id)
        if viewer.status == "active":
            choices.append(file_resources.c.visibility == "members")
    return or_(*choices)


def _joined() -> Select[Any]:
    return select(
        file_resources,
        file_categories.c.slug.label("category_slug"),
        file_categories.c.name.label("category_name"),
        file_categories.c.kind.label("category_kind"),
        users.c.username.label("uploader_username"),
        users.c.display_name.label("uploader_display_name"),
        users.c.discriminator.label("uploader_discriminator"),
    ).select_from(file_resources.join(file_categories, file_resources.c.category_id == file_categories.c.id)
                  .join(users, file_resources.c.uploader_id == users.c.id))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FileRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def lock_resource(self, resource_id: int) -> None:
        # SQLite has no SELECT FOR UPDATE. Acquire its write lock BEFORE reading
        # a vote/favorite/dedup record; the lock lasts until the caller commits.
        self._conn.execute(update(file_resources).where(file_resources.c.id == resource_id)
                           .values(rating_sum=file_resources.c.rating_sum))

    def get(self, resource_id: int) -> FileRecord | None:
        row = self._conn.execute(_joined().where(file_resources.c.id == resource_id,
                                                file_resources.c.deleted_at.is_(None))).mappings().first()
        return None if row is None else FileRecord.model_validate(dict(row))

    def get_by_key(self, object_key: str) -> FileRecord | None:
        row = self._conn.execute(select(file_resources).where(file_resources.c.object_key == object_key)).mappings().first()
        return None if row is None else FileRecord.model_validate(dict(row))

    def categories(self) -> list[CategoryRecord]:
        rows = self._conn.execute(select(file_categories).where(file_categories.c.deleted_at.is_(None))
                                 .order_by(file_categories.c.sort_order, file_categories.c.id)).mappings()
        return [CategoryRecord.model_validate(dict(row)) for row in rows]

    def category(self, category_id: int) -> CategoryRecord | None:
        row = self._conn.execute(select(file_categories).where(file_categories.c.id == category_id,
                                                              file_categories.c.deleted_at.is_(None))).mappings().first()
        return None if row is None else CategoryRecord.model_validate(dict(row))

    def category_by_slug(self, slug: str) -> CategoryRecord | None:
        row = self._conn.execute(select(file_categories).where(file_categories.c.slug == slug,
                                                              file_categories.c.deleted_at.is_(None))).mappings().first()
        return None if row is None else CategoryRecord.model_validate(dict(row))

    def insert_category(self, values: dict[str, Any]) -> int:
        value = self._conn.execute(file_categories.insert().values(**values).returning(file_categories.c.id)).scalar_one()
        return require_int(value, "category id")

    def seed_category(self, values: dict[str, Any]) -> int:
        result = self._conn.execute(sqlite_insert(file_categories).values(**values)
                                    .on_conflict_do_nothing(index_elements=[file_categories.c.slug]))
        return max(0, result.rowcount)

    def update_category(self, category_id: int, values: dict[str, Any]) -> None:
        self._conn.execute(update(file_categories).where(file_categories.c.id == category_id).values(**values))

    def category_resource_count(self, category_id: int) -> int:
        return require_int(self._conn.execute(select(func.count()).select_from(file_resources).where(
            file_resources.c.category_id == category_id, file_resources.c.deleted_at.is_(None))).scalar_one(), "count")

    def category_counts(self, viewer: Actor | None) -> dict[int, int]:
        rows = self._conn.execute(select(file_resources.c.category_id, func.count()).where(
            file_resources.c.deleted_at.is_(None), file_resources.c.status == "published", _visible(viewer)
        ).group_by(file_resources.c.category_id)).all()
        return {require_int(row[0], "category id"): require_int(row[1], "count") for row in rows}

    # ================================================================ 文件夹 / folders

    def folders(self) -> list[FolderRecord]:
        rows = self._conn.execute(select(file_folders).where(file_folders.c.deleted_at.is_(None))
                                  .order_by(file_folders.c.sort_order, file_folders.c.id)).mappings()
        return [FolderRecord.model_validate(dict(row)) for row in rows]

    def folder(self, folder_id: int) -> FolderRecord | None:
        row = self._conn.execute(select(file_folders).where(file_folders.c.id == folder_id,
                                                            file_folders.c.deleted_at.is_(None))).mappings().first()
        return None if row is None else FolderRecord.model_validate(dict(row))

    def folder_by_name(self, parent_id: int | None, name: str) -> FolderRecord | None:
        """同一父级下按名字查重（大小写不敏感）。

        重名会让面包屑与移动目标都变得含糊，所以在同一层内禁止重名；不同层可以同名，
        因为它们的路径不同、并不难区分。
        Duplicate detection within one parent, case-insensitively. A duplicate would make both the
        breadcrumb and the move target ambiguous, so names are unique per level; the same name may
        repeat at different levels, where the paths still tell them apart.
        """
        stmt = select(file_folders).where(file_folders.c.deleted_at.is_(None),
                                          func.lower(file_folders.c.name) == name.strip().lower())
        stmt = stmt.where(file_folders.c.parent_id.is_(None) if parent_id is None
                          else file_folders.c.parent_id == parent_id)
        row = self._conn.execute(stmt).mappings().first()
        return None if row is None else FolderRecord.model_validate(dict(row))

    def insert_folder(self, values: dict[str, Any]) -> int:
        value = self._conn.execute(file_folders.insert().values(**values).returning(file_folders.c.id)).scalar_one()
        return require_int(value, "folder id")

    def update_folder(self, folder_id: int, values: dict[str, Any]) -> None:
        self._conn.execute(update(file_folders).where(file_folders.c.id == folder_id).values(**values))

    def folder_resource_count(self, folder_id: int) -> int:
        """该文件夹里的**物理**资料数（不分可见性、含已归档）。

        仅供删除前的"要不要先清空"判断与子文件夹判断使用；下发给客户端的计数走
        folder_visible_counts，那是按查看者过滤过的。
        The physical resource count of a folder, regardless of visibility and including archived
        ones. Used only for the pre-delete "is it empty" check and for child counting; what reaches
        the client is folder_visible_counts, which is filtered per viewer.
        """
        return require_int(self._conn.execute(select(func.count()).select_from(file_resources).where(
            file_resources.c.folder_id == folder_id, file_resources.c.deleted_at.is_(None))).scalar_one(), "count")

    def folder_visible_counts(self, viewer: Actor | None) -> dict[int, int]:
        rows = self._conn.execute(select(file_resources.c.folder_id, func.count()).where(
            file_resources.c.deleted_at.is_(None), file_resources.c.status == "published",
            file_resources.c.folder_id.is_not(None), _visible(viewer)
        ).group_by(file_resources.c.folder_id)).all()
        return {require_int(row[0], "folder id"): require_int(row[1], "count") for row in rows}

    def folder_child_counts(self) -> dict[int, int]:
        rows = self._conn.execute(select(file_folders.c.parent_id, func.count()).where(
            file_folders.c.deleted_at.is_(None), file_folders.c.parent_id.is_not(None)
        ).group_by(file_folders.c.parent_id)).all()
        return {require_int(row[0], "folder id"): require_int(row[1], "count") for row in rows}

    def folder_child_count(self, folder_id: int) -> int:
        return require_int(self._conn.execute(select(func.count()).select_from(file_folders).where(
            file_folders.c.parent_id == folder_id, file_folders.c.deleted_at.is_(None))).scalar_one(), "count")

    def folder_depth(self, folder_id: int) -> int:
        """从顶层数起的层数（顶层文件夹 = 1）。

        用于限制嵌套深度：面包屑每多一层就多一次向上查找，而且深到一定程度后导航本身
        就不再是"分类"而是"迷宫"了。seen 集合兜住理论上不该出现的数据环（自引用父级）。
        Depth counted from the top level, where a top-level folder is 1. It bounds nesting: every
        extra level costs the breadcrumb another upward lookup, and past a certain point the
        navigation stops classifying anything and becomes a maze instead. The seen set absorbs a
        data cycle (possible in principle with a self-referencing parent).
        """
        depth = 0
        seen: set[int] = set()
        current: int | None = folder_id
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            row = self._conn.execute(select(file_folders.c.parent_id).where(file_folders.c.id == current)).first()
            current = None if row is None or row[0] is None else require_int(row[0], "folder id")
        return depth

    def detach_folder_resources(self, folder_id: int) -> int:
        """把该文件夹里的资料全部移到"未分类"，返回被移走的份数。

        删除文件夹时只解除归属，绝不删除资料本身：资料是长期资产，而文件夹只是视图。
        Moves every resource in the folder into "unfiled" and returns how many moved. Deleting a
        folder only detaches; it never deletes the resources themselves, because a resource is a
        long-lived asset while a folder is just a view.
        """
        result = self._conn.execute(update(file_resources).where(
            file_resources.c.folder_id == folder_id, file_resources.c.deleted_at.is_(None)
        ).values(folder_id=None, updated_at=now_ms()))
        return int(result.rowcount or 0)

    def is_descendant_folder(self, candidate_id: int, ancestor_id: int) -> bool:
        """candidate 是否为 ancestor 的后代（用于拒绝"把文件夹移进自己的子树"）。

        Whether candidate is a descendant of ancestor, used to refuse moving a folder into its own
        subtree, which would detach that subtree from the root and make the breadcrumb loop.
        """
        seen: set[int] = set()
        current: int | None = candidate_id
        while current is not None and current not in seen:
            if current == ancestor_id:
                return True
            seen.add(current)
            row = self._conn.execute(select(file_folders.c.parent_id).where(file_folders.c.id == current)).first()
            current = None if row is None else (None if row[0] is None else require_int(row[0], "folder id"))
        return False

    def list_resources(self, viewer: Actor | None, filters: FileFilters, page: int, size: int) -> tuple[list[FileRecord], int]:
        stmt = _joined().where(file_resources.c.deleted_at.is_(None), _visible(viewer),
                               file_resources.c.status == ("archived" if filters.status == "archived" else "published"))
        if filters.category:
            stmt = stmt.where(file_categories.c.slug == filters.category)
        if filters.categoryId:
            stmt = stmt.where(file_resources.c.category_id == filters.categoryId)
        # 文件夹过滤与可见性过滤是 AND 关系，不是替代关系：先按 _visible 收窄受众，
        # 再在其中挑出该文件夹里的那些。顺序反过来的话，文件夹就成了可见性的旁路。
        # Folder filtering ANDs with the visibility filter instead of replacing it: the audience is
        # narrowed by _visible first and the folder then selects from within it. The other order
        # would turn a folder into a bypass around visibility.
        if filters.folderId:
            stmt = stmt.where(file_resources.c.folder_id == filters.folderId)
        elif filters.unfiled:
            stmt = stmt.where(file_resources.c.folder_id.is_(None))
        if filters.kind:
            stmt = stmt.where(file_categories.c.kind == filters.kind)
        if filters.uploaderId:
            stmt = stmt.where(file_resources.c.uploader_id == filters.uploaderId)
        if filters.featured:
            stmt = stmt.where(file_resources.c.is_featured == 1)
        if filters.tag:
            stmt = stmt.where(file_resources.c.tags.like(f'%"{_escape(filters.tag.strip().lower())}"%', escape="\\"))
        if filters.q and filters.q.strip():
            pattern = f"%{_escape(filters.q.strip())}%"
            stmt = stmt.where(or_(*(col.like(pattern, escape="\\") for col in (
                file_resources.c.title, file_resources.c.description_md, file_resources.c.tags, file_resources.c.original_filename))))
        total = require_int(self._conn.execute(select(func.count()).select_from(stmt.subquery())).scalar_one(), "count")
        if filters.sort == "downloads":
            stmt = stmt.order_by(file_resources.c.download_count.desc())
        elif filters.sort == "favorites":
            stmt = stmt.order_by(file_resources.c.favorite_count.desc())
        elif filters.sort == "rating":
            stmt = stmt.order_by(func.coalesce(file_resources.c.rating_sum * 1.0 / func.nullif(file_resources.c.rating_count, 0), 0).desc())
        elif filters.sort == "name":
            stmt = stmt.order_by(func.lower(file_resources.c.title), file_resources.c.id)
        stmt = stmt.order_by(file_resources.c.created_at.desc(), file_resources.c.id.desc()).limit(size).offset((page - 1) * size)
        return [FileRecord.model_validate(dict(row)) for row in self._conn.execute(stmt).mappings()], total

    def personal_resources(self, viewer: Actor, *, favorites: bool) -> list[FileRecord]:
        stmt = _joined().where(file_resources.c.deleted_at.is_(None))
        if favorites:
            stmt = stmt.join(file_favorites, file_favorites.c.resource_id == file_resources.c.id).where(
                file_favorites.c.user_id == viewer.id, or_(_visible(viewer), file_resources.c.is_public == 1)).order_by(file_favorites.c.created_at.desc())
        else:
            stmt = stmt.where(file_resources.c.uploader_id == viewer.id).order_by(file_resources.c.created_at.desc())
        return [FileRecord.model_validate(dict(row)) for row in self._conn.execute(stmt).mappings()]

    def visible_tags(self, viewer: Actor | None) -> list[str]:
        rows = self._conn.execute(select(file_resources.c.tags).where(file_resources.c.deleted_at.is_(None),
                                  file_resources.c.status == "published", _visible(viewer))).all()
        return [require_str(row[0], "tags") for row in rows]

    def viewer_flags(self, viewer: Actor | None, ids: list[int]) -> tuple[set[int], dict[int, int]]:
        if viewer is None or not ids:
            return set(), {}
        favs = self._conn.execute(select(file_favorites.c.resource_id).where(file_favorites.c.user_id == viewer.id,
                                 file_favorites.c.resource_id.in_(ids))).all()
        ratings = self._conn.execute(select(file_ratings.c.resource_id, file_ratings.c.score).where(
            file_ratings.c.user_id == viewer.id, file_ratings.c.resource_id.in_(ids))).all()
        return ({require_int(r[0], "resource id") for r in favs},
                {require_int(r[0], "resource id"): require_int(r[1], "score") for r in ratings})

    def insert_resource(self, values: dict[str, Any]) -> int:
        value = self._conn.execute(file_resources.insert().values(**values).returning(file_resources.c.id)).scalar_one()
        return require_int(value, "resource id")

    def update_resource(self, resource_id: int, values: dict[str, Any]) -> None:
        self._conn.execute(update(file_resources).where(file_resources.c.id == resource_id).values(**values))

    def favorite(self, resource_id: int, user_id: int, on: bool) -> None:
        if on:
            result = self._conn.execute(sqlite_insert(file_favorites).values(resource_id=resource_id, user_id=user_id,
                created_at=now_ms()).on_conflict_do_nothing(index_elements=[file_favorites.c.resource_id, file_favorites.c.user_id]))
            delta = 1
        else:
            result = self._conn.execute(file_favorites.delete().where(file_favorites.c.resource_id == resource_id,
                                                                     file_favorites.c.user_id == user_id))
            delta = -1
        if result.rowcount:
            self._conn.execute(update(file_resources).where(file_resources.c.id == resource_id)
                               .values(favorite_count=file_resources.c.favorite_count + delta))

    def rating(self, resource_id: int, user_id: int, score: int | None) -> None:
        if score is None:
            self._conn.execute(file_ratings.delete().where(file_ratings.c.resource_id == resource_id,
                                                           file_ratings.c.user_id == user_id))
        else:
            timestamp = now_ms()
            self._conn.execute(sqlite_insert(file_ratings).values(resource_id=resource_id, user_id=user_id,
                score=score, created_at=timestamp, updated_at=timestamp).on_conflict_do_update(
                    index_elements=[file_ratings.c.resource_id, file_ratings.c.user_id],
                    set_={"score": score, "updated_at": timestamp}))
        # Recompute from the authoritative rows while holding the write lock. This
        # also repairs existing counter drift when the resource is next rated.
        aggregate = self._conn.execute(select(func.count(), func.coalesce(func.sum(file_ratings.c.score), 0)).where(
            file_ratings.c.resource_id == resource_id)).one()
        self.update_resource(resource_id, {"rating_count": require_int(aggregate[0], "rating count"),
                                           "rating_sum": require_int(aggregate[1], "rating sum")})

    def record_download(self, resource_id: int, user_id: int | None, client_ip: str | None) -> None:
        stmt = select(file_downloads.c.id).where(file_downloads.c.resource_id == resource_id)
        if user_id is not None:
            stmt = stmt.where(file_downloads.c.user_id == user_id)
        elif client_ip:
            stmt = stmt.where(file_downloads.c.client_ip == client_ip,
                              file_downloads.c.created_at >= now_ms() - 24 * 3600 * 1000)
        counted = (user_id is not None or bool(client_ip)) and self._conn.execute(stmt).first() is None
        self._conn.execute(file_downloads.insert().values(resource_id=resource_id, user_id=user_id,
                                                         client_ip=client_ip, created_at=now_ms()))
        if counted:
            self._conn.execute(update(file_resources).where(file_resources.c.id == resource_id)
                               .values(download_count=file_resources.c.download_count + 1))

    def claimed_keys(self) -> set[str]:
        attachment_keys = {require_str(row[0], "object key")
                           for row in self._conn.execute(select(attachments.c.object_key)).all()}
        resource_keys = {require_str(row[0], "object key") for row in self._conn.execute(
            select(file_resources.c.object_key).where(file_resources.c.deleted_at.is_(None))).all()}
        return attachment_keys | resource_keys
