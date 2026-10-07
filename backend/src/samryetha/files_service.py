"""文件服务 service — 面向新生的资料库（新生攻略 / 复习提纲 / 学习纲要 / 历年题）。

File service: a resource library for newcomers (freshman guides, course outlines,
study syllabi, past exam papers).

设计要点 / Design notes:
- 授权一律走 authz.can()/assert_can()，本模块只负责"取到资源并交给授权矩阵判定"。
  Authorisation always goes through authz.can()/assert_can(); this module only loads the
  resource and hands it to the authorisation matrix.
- 不可见的内容统一抛 not_found（而非 forbidden），不向调用方泄漏资源是否存在。
  Invisible content always raises not_found (never forbidden) so the caller cannot tell
  whether the resource exists.
- 冗余计数列（download/favorite/rating）与本模块的明细表在同一事务内更新。
  The denormalised counters (download/favorite/rating) are updated in the same
  transaction as their detail tables, and only from this module.
- 文件字节走 storage.py 的 HMAC presign；本模块不直接读写磁盘。
  File bytes go through storage.py's HMAC presign; this module never touches the disk.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection

from . import storage as storage_module
from .authz import Abilities, assert_can, can
from .db import now_ms
from .errors import bad_request, conflict, not_found
from .schema import (
    attachments,
    file_categories,
    file_downloads,
    file_favorites,
    file_ratings,
    file_resources,
    users,
)
from .users import make_handle

# ---------------------------------------------------------------- 常量 / constants

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

PREVIEW_LENGTH = 160

# 内建分类：首次启动时幂等写入，构成"新生专区"的导航骨架。
# Built-in categories, inserted idempotently on first start; they form the navigation
# skeleton of the newcomer area.
SEED_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "slug": "freshman-guide",
        "name": "新生攻略",
        "kind": "guide",
        "description": "报到流程、校园生活、必备物品与常见问题。",
        "sort_order": 10,
    },
    {
        "slug": "exam-outline",
        "name": "复习提纲",
        "kind": "outline",
        "description": "各科期末与阶段性复习提纲。",
        "sort_order": 20,
    },
    {
        "slug": "study-syllabus",
        "name": "学习纲要",
        "kind": "syllabus",
        "description": "课程知识框架、重点章节与自学路径。",
        "sort_order": 30,
    },
    {
        "slug": "past-papers",
        "name": "历年题",
        "kind": "exam",
        "description": "历年考试真题与参考答案。",
        "sort_order": 40,
    },
    {
        "slug": "other-materials",
        "name": "其他资料",
        "kind": "other",
        "description": "不属于以上分类的学习资料。",
        "sort_order": 90,
    },
)


# ---------------------------------------------------------------- 小工具 / helpers


def _flat(text: str) -> str:
    """折叠空白，供列表预览与搜索展示使用。
    Collapse whitespace, for list previews and search snippets.
    """
    return re.sub(r"\s+", " ", text or "").strip()


def preview_of(markdown: str) -> str:
    flat = _flat(markdown)
    return flat if len(flat) <= PREVIEW_LENGTH else flat[:PREVIEW_LENGTH] + "..."

def _like_escape(term: str) -> str:
    """转义 LIKE 通配符，避免用户输入的 % 与 _ 变成通配（配合 escape="\\\\" 使用）。
    Escape LIKE wildcards so a literal % or _ from user input cannot act as a wildcard
    (used together with escape="\\\\").
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalize_tags(raw: Any) -> list[str]:
    """归一化标签：接受列表或逗号/顿号分隔字符串，转小写、去空、去重、限长限量。

    归一化是必需的：不做的话"高数/高等数学"会分裂成两个互不相干的标签，
    而标签是本模块仅次于分类的第二导航维度。
    Normalise tags: accept a list or a comma/ideographic-comma separated string, then
    lower-case, drop empties, de-duplicate, and clamp both length and count.
    Normalisation matters: without it "maths" and "mathematics" split into two unrelated
    tags, and tags are the second navigation axis of this module after categories.
    """
    if raw is None:
        return []
    items: list[str] = []
    if isinstance(raw, str):
        items = re.split(r"[,，、;；]+", raw)
    elif isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, str):
                items.extend(re.split(r"[,，、;；]+", entry))
    else:
        raise bad_request("Tags must be a list or a string")
    out: list[str] = []
    for item in items:
        tag = _flat(item).lower()
        if not tag:
            continue
        tag = tag[:MAX_TAG_LENGTH]
        if tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


def tags_to_json(tags: list[str]) -> str:
    return json.dumps(tags, ensure_ascii=False)


def tags_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        # 手工改库或历史脏数据不该让整个列表 500：解析不了就当没有标签。
        # Hand-edited rows or legacy junk must not turn the whole list into a 500:
        # unparsable tags simply count as no tags.
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, (str, int, float))]


def _clamp_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    p = page if isinstance(page, int) and page > 0 else 1
    size = page_size if isinstance(page_size, int) and page_size > 0 else DEFAULT_PAGE_SIZE
    return p, min(size, MAX_PAGE_SIZE)


def _row_to_dict(row) -> dict:
    return dict(row._mapping)


# ---------------------------------------------------------------- 授权辅助 / authz glue


def resource_for_authz(row: dict) -> dict:
    """把资料行转成 authz.can() 需要的鸭子对象（camelCase，与既有 service 一致）。
    Turn a resource row into the duck-typed object authz.can() expects (camelCase keys,
    matching the existing services).
    """
    return {
        "id": row["id"],
        "type": "file_resource",
        "uploaderId": row["uploader_id"],
        "visibility": row["visibility"],
        "moderationStatus": row["moderation_status"],
        "status": row["status"],
    }


def _viewer_id(viewer) -> int | None:
    return viewer.id if viewer is not None else None


def _is_admin(viewer) -> bool:
    return viewer is not None and viewer.role == "admin"


def _is_active_actor(viewer) -> bool:
    return viewer is not None and viewer.status == "active"


def _visible_predicate(viewer):
    """SQL 谓词：哪些资料行可以进入列表/计数。

    与 authz.can(FILE_READ) 必须保持同一套语义；之所以在 SQL 里再表达一次，
    是因为列表与总数必须在数据库层过滤，否则分页数会泄漏不可见资源的存在。
    SQL predicate for which resource rows may enter a list or a count. It must stay
    semantically identical to authz.can(FILE_READ); it is expressed in SQL as well
    because the list and its total have to be filtered in the database, otherwise the
    page count would leak the existence of invisible resources.
    """
    if _is_admin(viewer):
        return None
    conditions = [
        and_(
            file_resources.c.moderation_status == "approved",
            file_resources.c.visibility == "public",
        )
    ]
    if _is_active_actor(viewer):
        conditions.append(
            and_(
                file_resources.c.moderation_status == "approved",
                file_resources.c.visibility == "members",
            )
        )
    if viewer is not None:
        # 自己的资料无论审核状态与可见性都看得到，否则上传者会在待审期"弄丢"自己的文件。
        # Your own resource is visible regardless of moderation state and visibility,
        # otherwise an uploader would appear to lose their own file while it is pending.
        conditions.append(file_resources.c.uploader_id == viewer.id)
    return or_(*conditions)


def _not_deleted():
    return file_resources.c.deleted_at.is_(None)


def get_resource_row(conn: Connection, resource_id: int) -> dict | None:
    row = conn.execute(
        select(file_resources).where(
            and_(file_resources.c.id == resource_id, _not_deleted())
        )
    ).first()
    return _row_to_dict(row) if row else None


def get_resource_row_any_state(conn: Connection, resource_id: int) -> dict | None:
    """不过滤软删除：仅供已授权后的更新/删除路径使用。
    Skips the soft-delete filter; only for already-authorised update/delete paths.
    """
    row = conn.execute(
        select(file_resources).where(file_resources.c.id == resource_id)
    ).first()
    return _row_to_dict(row) if row else None


def assert_visible(conn: Connection, viewer, row: dict) -> None:
    """不可见或不存在统一 404，不泄漏资源存在性。
    Invisible or missing alike raise 404, never leaking whether the resource exists.
    """
    if not can(viewer, Abilities.FILE_READ, resource_for_authz(row), conn):
        raise not_found("Resource not found")


# ---------------------------------------------------------------- 分类 / categories


def _category_counts(conn: Connection, viewer) -> dict[int, int]:
    stmt: Select = select(
        file_resources.c.category_id, func.count().label("total")
    ).where(_not_deleted(), file_resources.c.status == "published")
    predicate = _visible_predicate(viewer)
    if predicate is not None:
        stmt = stmt.where(predicate)
    stmt = stmt.group_by(file_resources.c.category_id)
    return {row.category_id: row.total for row in conn.execute(stmt).all()}


def _category_dto(row: dict, count: int | None = None) -> dict:
    dto = {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "kind": row["kind"],
        "sortOrder": row["sort_order"],
        "isSystem": bool(row["is_system"]),
    }
    if count is not None:
        dto["resourceCount"] = count
    return dto


def list_categories(conn: Connection, viewer=None, include_counts: bool = True) -> list[dict]:
    rows = conn.execute(
        select(file_categories)
        .where(file_categories.c.deleted_at.is_(None))
        .order_by(file_categories.c.sort_order, file_categories.c.id)
    ).all()
    counts = _category_counts(conn, viewer) if include_counts else {}
    return [
        _category_dto(_row_to_dict(row), counts.get(row.id, 0) if include_counts else None)
        for row in rows
    ]


def get_category_by_slug(conn: Connection, slug: str) -> dict | None:
    row = conn.execute(
        select(file_categories).where(
            and_(file_categories.c.slug == slug, file_categories.c.deleted_at.is_(None))
        )
    ).first()
    return _row_to_dict(row) if row else None


def get_category(conn: Connection, category_id: int) -> dict | None:
    row = conn.execute(
        select(file_categories).where(
            and_(file_categories.c.id == category_id, file_categories.c.deleted_at.is_(None))
        )
    ).first()
    return _row_to_dict(row) if row else None


def ensure_seed_categories(conn: Connection) -> int:
    """幂等写入内建分类，返回本次新建数量（已存在则 0）。
    Idempotently insert the built-in categories and return how many were created
    (zero when they all already exist).
    """
    created = 0
    _now = now_ms()
    for seed in SEED_CATEGORIES:
        if get_category_by_slug(conn, seed["slug"]) is not None:
            continue
        conn.execute(
            file_categories.insert().values(
                slug=seed["slug"],
                name=seed["name"],
                description=seed["description"],
                kind=seed["kind"],
                sort_order=seed["sort_order"],
                is_system=1,
                created_at=_now,
                updated_at=_now,
            )
        )
        created += 1
    return created


def create_category(conn: Connection, data: dict) -> dict:
    slug = _flat(data["slug"]).lower()
    if not slug:
        raise bad_request("Category slug is required")
    if get_category_by_slug(conn, slug) is not None:
        raise conflict("Category slug already exists")
    kind = data.get("kind") or "other"
    if kind not in KINDS:
        raise bad_request("Unsupported category kind")
    _now = now_ms()
    res = conn.execute(
        file_categories.insert().values(
            slug=slug,
            name=data["name"],
            description=data.get("description") or "",
            kind=kind,
            sort_order=int(data.get("sortOrder") or 0),
            is_system=0,
            created_at=_now,
            updated_at=_now,
        )
    )
    created = get_category(conn, res.inserted_primary_key[0])
    return _category_dto(created, 0)


def update_category(conn: Connection, category_id: int, patch: dict) -> dict:
    existing = get_category(conn, category_id)
    if existing is None:
        raise not_found("Category not found")
    values: dict[str, Any] = {"updated_at": now_ms()}
    if "name" in patch and patch["name"] is not None:
        values["name"] = patch["name"]
    if "description" in patch and patch["description"] is not None:
        values["description"] = patch["description"]
    if "kind" in patch and patch["kind"] is not None:
        if patch["kind"] not in KINDS:
            raise bad_request("Unsupported category kind")
        values["kind"] = patch["kind"]
    if "sortOrder" in patch and patch["sortOrder"] is not None:
        values["sortOrder"] = int(patch["sortOrder"])
    if "sortOrder" in values:
        values["sort_order"] = values.pop("sortOrder")
    conn.execute(
        update(file_categories).where(file_categories.c.id == category_id).values(**values)
    )
    return _category_dto(get_category(conn, category_id), 0)


def delete_category(conn: Connection, category_id: int, actor_id: int) -> None:
    existing = get_category(conn, category_id)
    if existing is None:
        raise not_found("Category not found")
    if existing["is_system"]:
        raise conflict("Built-in categories cannot be deleted")
    remaining = conn.execute(
        select(func.count())
        .select_from(file_resources)
        .where(
            and_(
                file_resources.c.category_id == category_id,
                _not_deleted(),
            )
        )
    ).scalar() or 0
    # 非空分类拒绝删除：直接删会让其中的资料变成悬空行（列表按分类过滤时再也找不到）。
    # Refuse to delete a non-empty category: doing so would orphan its resources, which
    # could then no longer be found by any category filter.
    if remaining > 0:
        raise conflict("Category still holds resources")
    _now = now_ms()
    conn.execute(
        update(file_categories)
        .where(file_categories.c.id == category_id)
        .values(deleted_at=_now, deleted_by=actor_id, updated_at=_now)
    )


# ---------------------------------------------------------------- 序列化 / serialisation


def _author_of(row) -> dict:
    return {
        "id": row.uploader_id,
        "username": row.uploader_username,
        "handle": make_handle(row.uploader_username, row.uploader_discriminator),
        "displayName": row.uploader_display_name,
    }


def _base_columns():
    return (
        select(
            file_resources,
            file_categories.c.slug.label("category_slug"),
            file_categories.c.name.label("category_name"),
            file_categories.c.kind.label("category_kind"),
            users.c.username.label("uploader_username"),
            users.c.display_name.label("uploader_display_name"),
            users.c.discriminator.label("uploader_discriminator"),
        )
        .select_from(
            file_resources.join(file_categories, file_resources.c.category_id == file_categories.c.id)
            .join(users, file_resources.c.uploader_id == users.c.id)
        )
    )


def _rating_avg(rating_sum: int, rating_count: int) -> float | None:
    if not rating_count:
        return None
    return round(rating_sum / rating_count, 2)


def _extension_of(filename: str) -> str:
    match = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    return match.group(1).lower() if match else ""


def _summary(row, viewer, favorited: bool = False, my_rating: int | None = None) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "preview": preview_of(row.description_md),
        "category": {
            "id": row.category_id,
            "slug": row.category_slug,
            "name": row.category_name,
            "kind": row.category_kind,
        },
        "uploader": _author_of(row),
        "tags": tags_from_json(row.tags),
        "originalFilename": row.original_filename,
        "mimeType": row.mime_type,
        "extension": _extension_of(row.original_filename),
        "sizeBytes": row.size_bytes,
        "visibility": row.visibility,
        "moderationStatus": row.moderation_status,
        "status": row.status,
        "version": row.version,
        "isFeatured": bool(row.is_featured),
        "downloadCount": row.download_count,
        "favoriteCount": row.favorite_count,
        "ratingAvg": _rating_avg(row.rating_sum, row.rating_count),
        "ratingCount": row.rating_count,
        "isFavorited": favorited,
        "myRating": my_rating,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


def _viewer_flags(conn: Connection, viewer, resource_ids: list[int]) -> tuple[set[int], dict[int, int]]:
    """批量取"我收藏了哪些 / 我给过几分"，避免列表页逐行查询（N+1）。
    Batch-load "which ones I favourited" and "what score I gave", avoiding an N+1 query
    per row on the list page.
    """
    uid = _viewer_id(viewer)
    if uid is None or not resource_ids:
        return set(), {}
    fav_rows = conn.execute(
        select(file_favorites.c.resource_id).where(
            and_(
                file_favorites.c.user_id == uid,
                file_favorites.c.resource_id.in_(resource_ids),
            )
        )
    ).all()
    rating_rows = conn.execute(
        select(file_ratings.c.resource_id, file_ratings.c.score).where(
            and_(
                file_ratings.c.user_id == uid,
                file_ratings.c.resource_id.in_(resource_ids),
            )
        )
    ).all()
    return {r.resource_id for r in fav_rows}, {r.resource_id: r.score for r in rating_rows}


# ---------------------------------------------------------------- 列表 / listing


def _apply_filters(stmt: Select, filters: dict) -> Select:
    if filters.get("category"):
        stmt = stmt.where(file_categories.c.slug == filters["category"])
    if filters.get("categoryId"):
        stmt = stmt.where(file_resources.c.category_id == int(filters["categoryId"]))
    if filters.get("kind"):
        stmt = stmt.where(file_categories.c.kind == filters["kind"])
    if filters.get("uploaderId"):
        stmt = stmt.where(file_resources.c.uploader_id == int(filters["uploaderId"]))
    if filters.get("tag"):
        # 标签存 JSON 数组文本，命中判定为"包含 \"tag\" 子串"（标签已归一化为小写）。
        # Tags live in a JSON array text column, so a hit means the text contains the
        # substring "tag" (tags are already normalised to lower case).
        tag = _flat(str(filters["tag"])).lower()
        if tag:
            stmt = stmt.where(
                file_resources.c.tags.like(f'%"{_like_escape(tag)}"%', escape="\\")
            )
    query = _flat(str(filters.get("q") or ""))
    if query:
        pattern = f"%{_like_escape(query)}%"
        stmt = stmt.where(
            or_(
                file_resources.c.title.like(pattern, escape="\\"),
                file_resources.c.description_md.like(pattern, escape="\\"),
                file_resources.c.tags.like(pattern, escape="\\"),
                file_resources.c.original_filename.like(pattern, escape="\\"),
            )
        )
    return stmt


def _apply_sort(stmt: Select, sort: str | None) -> Select:
    key = sort if sort in SORTS else "latest"
    if key == "downloads":
        return stmt.order_by(
            file_resources.c.download_count.desc(), file_resources.c.created_at.desc()
        )
    if key == "favorites":
        # 收藏数排序：界面调研证据（Discourse order:likes 与 GitHub 界面化下载量）表明
        # "别人都在用/都在存"比主观评分更能反映资料价值，故与下载量并列为一等排序维度。
        # Sorting by saves: the UI research (Discourse's order:likes, GitHub surfacing
        # download counts in the UI) shows "others are using or keeping this" reflects a
        # resource's value better than a subjective score, so this ranks alongside
        # downloads as a first-class sort dimension.
        return stmt.order_by(
            file_resources.c.favorite_count.desc(), file_resources.c.created_at.desc()
        )
    if key == "rating":
        # 未评分的用 0 参与排序（SQLite 里 NULL 在 DESC 下会排到最后，但显式 coalesce 更清楚）。
        # Unrated resources sort with a score of 0 (SQLite would push NULL to the end
        # under DESC, but an explicit coalesce states the intent).
        score = func.coalesce(
            file_resources.c.rating_sum * 1.0 / func.nullif(file_resources.c.rating_count, 0),
            0.0,
        )
        return stmt.order_by(score.desc(), file_resources.c.created_at.desc())
    if key == "name":
        return stmt.order_by(func.lower(file_resources.c.title), file_resources.c.id)
    return stmt.order_by(file_resources.c.created_at.desc(), file_resources.c.id.desc())


def list_resources(conn: Connection, viewer, filters: dict | None = None) -> dict:
    filters = dict(filters or {})
    page, page_size = _clamp_page(filters.get("page"), filters.get("pageSize"))
    status = filters.get("status") or "published"
    if status not in STATUSES:
        status = "published"

    conditions = [_not_deleted()]
    predicate = _visible_predicate(viewer)
    if predicate is not None:
        conditions.append(predicate)
    if status == "archived":
        conditions.append(file_resources.c.status == "archived")
    else:
        # 默认列表排除归档：归档是"留档备查"，不应占据默认浏览流。
        # The default list excludes archived items: archiving is for keeping records,
        # not for occupying the default browsing flow.
        conditions.append(file_resources.c.status == "published")
    if filters.get("featured"):
        conditions.append(file_resources.c.is_featured == 1)

    base = _base_columns().where(and_(*conditions))
    base = _apply_filters(base, filters)

    total = conn.execute(
        select(func.count())
        .select_from(base.subquery())
    ).scalar() or 0

    rows = conn.execute(
        _apply_sort(base, filters.get("sort")).limit(page_size).offset((page - 1) * page_size)
    ).all()

    ids = [row.id for row in rows]
    favorited, my_ratings = _viewer_flags(conn, viewer, ids)
    items = [
        _summary(row, viewer, row.id in favorited, my_ratings.get(row.id)) for row in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "sort": filters.get("sort") if filters.get("sort") in SORTS else "latest",
    }


def list_tag_cloud(conn: Connection, viewer, limit: int = 20) -> list[dict]:
    """标签云：统计当前可见资料的标签频次，取前 N 个。
    Tag cloud: count tag frequencies over currently visible resources and return the
    top N entries.
    """
    stmt = select(file_resources.c.tags).where(
        _not_deleted(), file_resources.c.status == "published"
    )
    predicate = _visible_predicate(viewer)
    if predicate is not None:
        stmt = stmt.where(predicate)
    counts: dict[str, int] = {}
    for row in conn.execute(stmt).all():
        for tag in tags_from_json(row.tags):
            counts[tag] = counts.get(tag, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"tag": tag, "count": count} for tag, count in ordered[: max(1, limit)]]


# ---------------------------------------------------------------- 详情 / detail


def get_resource(conn: Connection, viewer, resource_id: int) -> dict:
    row = conn.execute(
        _base_columns().where(and_(file_resources.c.id == resource_id, _not_deleted()))
    ).first()
    # 不存在与不可见都走 404，调用方无法区分。
    # Missing and invisible both raise 404 so the caller cannot tell them apart.
    if row is None:
        raise not_found("Resource not found")
    raw = _row_to_dict(row)
    assert_visible(conn, viewer, raw)

    favorited, my_ratings = _viewer_flags(conn, viewer, [row.id])
    dto = _summary(row, viewer, row.id in favorited, my_ratings.get(row.id))
    dto["descriptionMarkdown"] = row.description_md
    resource = resource_for_authz(raw)
    dto["can"] = {
        "update": can(viewer, Abilities.FILE_UPDATE, resource, conn),
        "delete": can(viewer, Abilities.FILE_DELETE, resource, conn),
    }
    # 没有 sha256（历史上未计算或上传中断）时不展示空字段，前端无需判空。
    # Omit the hash when absent (never computed, or an interrupted upload) so the client
    # does not have to render an empty field.
    if row.sha256:
        dto["sha256"] = row.sha256
    return dto


# ---------------------------------------------------------------- 上传 / upload


def _upload_pathname(uploader_id: int, object_key: str, size_bytes: int) -> str:
    """上传签名的被签名字符串：把上传者、对象键与声明体积三者绑在一起。

    绑定上传者：否则任何人拿到别人的 objectKey 就能把该对象挂到自己名下；
    绑定体积：否则上传接口无法校正"声明 1KB 实传 50MB"。

    注意这是**签名串**而非真实 URL：真实 URL 的查询串由 sign_path 追加 expires/sig，
    若把 size 也写成查询串会拼出两个 "?"。这里用 "@size=" 后缀，
    两端都由同一组入参确定性重建，因此不存在解析歧义。
    The string that gets signed for an upload binds uploader, object key and declared
    size together. Binding the uploader stops anyone who learns another user's object key
    from claiming that object as their own; binding the size lets the upload endpoint
    reject a 50 MB body that was declared as 1 KB.
    Note this is the signed string, not the real URL: sign_path appends the expires/sig
    query string to the real URL, so expressing the size as another query parameter would
    produce two "?" characters. The "@size=" suffix is rebuilt deterministically from the
    same inputs on both sides, so there is no parsing ambiguity.
    """
    return f"/api/files/upload/{uploader_id}/{object_key}@size={size_bytes}"


def _upload_route(uploader_id: int, object_key: str) -> str:
    """真实的上传路由路径（不带查询串）。
    The real upload route path, without a query string.
    """
    return f"/api/files/upload/{uploader_id}/{object_key}"


def encode_object_key(object_key: str) -> str:
    """把对象键编码成可安全放进 URL 路径段的形式（保留 "/" 作为目录分隔）。

    objectKey 里带着原始文件名，而文件名可以含 URL 保留字符：
    - "#" 会让它后面的所有内容（包括 expires/sig 查询串）变成 URL fragment，服务端根本收不到；
    - 字面 "%" 或形如 "%20" 的内容会在解析时被当作百分号转义解码，使服务端重建出的
      签名输入与原签名输入不一致。
    两者都会让一个完全合法的文件名无法上传。
    objectKey embeds the original filename, and filenames may contain URL-reserved characters:
    "#" turns everything after it (the expires/sig query string included) into a URL fragment the
    server never receives, while a literal "%" or something shaped like "%20" is decoded as a
    percent-escape so the server rebuilds a signing input different from the one that was signed.
    Either way an entirely legitimate filename becomes impossible to upload.

    签名始终针对**未编码**的原始对象键计算，只有真实 URL 走这里编码；服务端收到请求后
    Starlette 会解码回原始形式，两边因此一致（生成与校验都基于同一规范形式）。
    Signatures are always computed over the raw, unencoded object key and only the real URL is
    encoded here; Starlette decodes the incoming request back to the raw form, so both sides agree
    because generation and verification share one canonical form.
    """
    return quote(object_key, safe="/")


def build_object_url(base_path: str, object_key: str, params: dict[str, Any]) -> str:
    """拼出带查询串的对象 URL，路径段按上面的规则编码。
    Assemble an object URL with its query string, encoding the path segment as described above.
    """
    query = "&".join(f"{name}={quote(str(value), safe='')}" for name, value in params.items() if value is not None)
    encoded = encode_object_key(object_key)
    return f"{base_path}/{encoded}?{query}" if query else f"{base_path}/{encoded}"


def is_object_claimed(conn: Connection, object_key: str) -> bool:
    """对象是否已被某条资料认领（软删除的行也算认领，其对象键仍占位）。
    Whether an object has been claimed by a resource row (soft-deleted rows still count, since
    their object key remains taken).
    """
    row = conn.execute(
        select(file_resources.c.id).where(file_resources.c.object_key == object_key)
    ).first()
    return row is not None


def presign_upload(conn: Connection, user, storage, data: dict) -> dict:
    """申请上传会话：校验扩展名与体积，返回签名上传地址。

    这里刻意**不**建数据库行：资料元数据（标题/分类）此时还没收集，
    先落一行再回填会让"半成品行"出现在列表里。
    Deliberately creates no database row here: the resource metadata (title, category) has
    not been collected yet, and inserting a row first would surface half-finished rows in
    the listing.
    """
    filename = _flat(str(data.get("filename") or ""))
    if not filename:
        raise bad_request("Filename is required")
    size_bytes = int(data.get("sizeBytes") or 0)
    if size_bytes <= 0:
        raise bad_request("File size must be positive")
    if size_bytes > storage_module.MAX_UPLOAD_BYTES:
        raise bad_request("File too large")
    mime_type = _flat(str(data.get("mimeType") or "application/octet-stream"))
    # create_upload_session 内部按扩展名白名单校验并生成 objectKey。
    # create_upload_session validates the extension against the allow-list and builds the
    # object key.
    object_key = storage.create_upload_session(user.id, filename, mime_type, size_bytes)
    signed = storage.sign_path("PUT", _upload_pathname(user.id, object_key, size_bytes), 900)
    # 真实 URL 走编码后的路径段，签名仍针对未编码的规范形式（见 build_object_url 的说明）。
    # The real URL uses an encoded path segment while the signature stays over the raw canonical
    # form (see the note on build_object_url).
    upload_url = build_object_url(
        f"/api/files/upload/{user.id}",
        object_key,
        {"size": size_bytes, "expires": signed["expires"], "sig": signed["sig"]},
    )
    return {
        "objectKey": object_key,
        "uploadUrl": upload_url,
        "expires": signed["expires"],
        "sig": signed["sig"],
        "expiresAt": signed["expiresAt"],
        "maxUploadBytes": storage_module.MAX_UPLOAD_BYTES,
        "contentType": storage_module.content_type_for_object_key(object_key),
    }


def verify_upload_signature(storage, uploader_id: int, object_key: str, size_bytes: int, expires: str, sig: str) -> None:
    """校验上传签名；失败一律 bad_request（不区分原因，避免给探测者反馈）。
    Verify an upload signature; any failure is a plain bad_request, so a prober gets no
    feedback about which part was wrong.
    """
    pathname = _upload_pathname(uploader_id, object_key, int(size_bytes))
    if not storage.verify_signature("PUT", pathname, str(expires), sig):
        raise bad_request("Invalid or expired upload ticket")


def create_resource(conn: Connection, user, storage, data: dict) -> dict:
    assert_can(user, Abilities.FILE_CREATE, None, conn)

    object_key = _flat(str(data.get("objectKey") or ""))
    size_bytes = int(data.get("sizeBytes") or 0)
    if not object_key or size_bytes <= 0:
        raise bad_request("objectKey and sizeBytes are required")
    # 签名证明 objectKey 确由本用户 presign 得到，且体积与当时声明一致。
    # The signature proves the object key really came from this user's presign call and
    # that the size matches what was declared back then.
    verify_upload_signature(
        storage,
        user.id,
        object_key,
        size_bytes,
        str(data.get("expires") or ""),
        str(data.get("sig") or ""),
    )

    # 还必须确认对象真的落盘、体积也对得上。
    # 只验签名是不够的：用户可以只申请 presign、根本不 PUT 字节就直接创建条目，
    # 于是列表里会出现一条"永远下载不到"的资料（要等 serve 阶段才发现文件不存在），
    # 既误导浏览者，也让冗余计数与真实内容对不上。
    # The object must also really exist on disk with a matching size. Verifying only the
    # signature is not enough: a user can request a presign, skip the PUT entirely and still
    # create a row, which leaves a resource in the listing that can never be downloaded (the
    # missing file is only discovered at serve time). That misleads browsers and desynchronises
    # the counters from the real content.
    try:
        full = storage.path_for(object_key)
    except Exception:
        raise bad_request("Invalid object key")
    if not os.path.exists(full):
        raise bad_request("Uploaded file was not found")
    if os.path.getsize(full) != size_bytes:
        raise bad_request("Uploaded file size does not match")

    category = get_category(conn, int(data.get("categoryId") or 0))
    if category is None:
        raise not_found("Category not found")

    title = _flat(str(data.get("title") or ""))
    if not title:
        raise bad_request("Title is required")
    if len(title) > MAX_TITLE_LENGTH:
        raise bad_request("Title is too long")
    description = str(data.get("descriptionMarkdown") or "")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise bad_request("Description is too long")

    visibility = data.get("visibility") or "members"
    if visibility not in VISIBILITIES:
        raise bad_request("Unsupported visibility")

    # object_key 唯一约束是最后一道防线：重复提交同一个对象键会在此冲突。
    # The unique constraint on object_key is the last line of defence: resubmitting the
    # same object key collides here.
    existing = conn.execute(
        select(file_resources.c.id).where(file_resources.c.object_key == object_key)
    ).first()
    if existing is not None:
        raise conflict("This upload has already been published")

    _now = now_ms()
    res = conn.execute(
        file_resources.insert().values(
            category_id=category["id"],
            uploader_id=user.id,
            title=title,
            description_md=description,
            tags=tags_to_json(normalize_tags(data.get("tags"))),
            object_key=object_key,
            original_filename=_flat(str(data.get("originalFilename") or "")) or "file",
            mime_type=_flat(str(data.get("mimeType") or "application/octet-stream")),
            size_bytes=size_bytes,
            sha256=data.get("sha256") or None,
            visibility=visibility,
            moderation_status="approved",
            status="published",
            version=1,
            is_featured=0,
            created_at=_now,
            updated_at=_now,
        )
    )
    return get_resource(conn, user, res.inserted_primary_key[0])


def update_resource(conn: Connection, viewer, resource_id: int, patch: dict) -> dict:
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    assert_can(viewer, Abilities.FILE_UPDATE, resource_for_authz(row), conn)

    values: dict[str, Any] = {"updated_at": now_ms()}
    if patch.get("title") is not None:
        title = _flat(str(patch["title"]))
        if not title:
            raise bad_request("Title is required")
        if len(title) > MAX_TITLE_LENGTH:
            raise bad_request("Title is too long")
        values["title"] = title
    if patch.get("descriptionMarkdown") is not None:
        description = str(patch["descriptionMarkdown"])
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise bad_request("Description is too long")
        values["description_md"] = description
    if patch.get("tags") is not None:
        values["tags"] = tags_to_json(normalize_tags(patch["tags"]))
    if patch.get("categoryId") is not None:
        category = get_category(conn, int(patch["categoryId"]))
        if category is None:
            raise not_found("Category not found")
        values["category_id"] = category["id"]
    if patch.get("visibility") is not None:
        if patch["visibility"] not in VISIBILITIES:
            raise bad_request("Unsupported visibility")
        values["visibility"] = patch["visibility"]
    if patch.get("status") is not None:
        if patch["status"] not in STATUSES:
            raise bad_request("Unsupported status")
        values["status"] = patch["status"]

    conn.execute(
        update(file_resources).where(file_resources.c.id == resource_id).values(**values)
    )
    return get_resource(conn, viewer, resource_id)


def delete_resource(conn: Connection, viewer, resource_id: int, storage) -> None:
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    assert_can(viewer, Abilities.FILE_DELETE, resource_for_authz(row), conn)
    _now = now_ms()
    conn.execute(
        update(file_resources)
        .where(file_resources.c.id == resource_id)
        .values(deleted_at=_now, deleted_by=viewer.id, updated_at=_now)
    )
    # 磁盘对象延迟删除：软删除行仍保留 objectKey 供管理员追溯，实际文件由运维清理脚本处理。
    # The on-disk object is left for a later sweep: the soft-deleted row keeps its object
    # key for admin traceability, and an operations script reclaims the bytes.
    _ = storage


# ---------------------------------------------------------------- 收藏 / favorites


def set_favorite(conn: Connection, viewer, resource_id: int, on: bool) -> dict:
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    assert_visible(conn, viewer, row)
    if viewer is None or viewer.status != "active":
        raise not_found("Resource not found")

    existing = conn.execute(
        select(file_favorites.c.resource_id).where(
            and_(
                file_favorites.c.resource_id == resource_id,
                file_favorites.c.user_id == viewer.id,
            )
        )
    ).first()

    if on and existing is None:
        # 用 ON CONFLICT DO NOTHING 而不是先查后插：两个并发请求可能都读到"不存在"，
        # 于是都去插入，第二个撞复合主键抛 IntegrityError 变成 500。
        # 这里让数据库来定胜负，并只在真的插进去时才动计数。
        # Use ON CONFLICT DO NOTHING instead of check-then-insert: two concurrent requests can
        # both observe "not present" and both attempt the insert, so the second collides with
        # the composite primary key and surfaces as a 500. Let the database arbitrate and only
        # move the counter when a row was genuinely inserted.
        result = conn.execute(
            sqlite_insert(file_favorites)
            .values(resource_id=resource_id, user_id=viewer.id, created_at=now_ms())
            .on_conflict_do_nothing(
                index_elements=[file_favorites.c.resource_id, file_favorites.c.user_id]
            )
        )
        if result.rowcount:
            _bump_counter(conn, resource_id, file_resources.c.favorite_count, 1)
    elif not on and existing is not None:
        conn.execute(
            file_favorites.delete().where(
                and_(
                    file_favorites.c.resource_id == resource_id,
                    file_favorites.c.user_id == viewer.id,
                )
            )
        )
        _bump_counter(conn, resource_id, file_resources.c.favorite_count, -1)

    fresh = get_resource_row(conn, resource_id)
    return {
        "resourceId": resource_id,
        "isFavorited": bool(on),
        "favoriteCount": max(0, int(fresh["favorite_count"])) if fresh else 0,
    }


def list_favorites(conn: Connection, viewer) -> dict:
    if viewer is None:
        return {"items": [], "total": 0}
    conditions = [file_favorites.c.user_id == viewer.id, _not_deleted()]
    # 必须同样套用可见性谓词：收藏发生在"当时可见"的时刻，但上传者之后可以把资料改成
    # private 或让它进入待审，授权是**每次请求重新判定**的，收藏列表不能成为绕过它的后门。
    # The visibility predicate must be applied here too: a favourite is created when the
    # resource happens to be visible, but the uploader may later switch it to private or send
    # it back to moderation. Authorisation is re-evaluated on every request, and the favourites
    # list must not become a back door around that.
    predicate = _visible_predicate(viewer)
    if predicate is not None:
        conditions.append(predicate)
    rows = conn.execute(
        _base_columns()
        .join(file_favorites, file_favorites.c.resource_id == file_resources.c.id)
        .where(and_(*conditions))
        .order_by(file_favorites.c.created_at.desc())
    ).all()
    ids = [row.id for row in rows]
    _, my_ratings = _viewer_flags(conn, viewer, ids)
    items = [_summary(row, viewer, True, my_ratings.get(row.id)) for row in rows]
    return {"items": items, "total": len(items)}


def list_mine(conn: Connection, viewer) -> dict:
    """我上传的资料：包含待审与归档，上传者需要看到自己文件的真实状态。
    Resources I uploaded: pending and archived included, because an uploader needs to see
    the real state of their own files.
    """
    if viewer is None:
        return {"items": [], "total": 0}
    rows = conn.execute(
        _base_columns()
        .where(and_(file_resources.c.uploader_id == viewer.id, _not_deleted()))
        .order_by(file_resources.c.created_at.desc())
    ).all()
    ids = [row.id for row in rows]
    favorited, my_ratings = _viewer_flags(conn, viewer, ids)
    items = [
        _summary(row, viewer, row.id in favorited, my_ratings.get(row.id)) for row in rows
    ]
    return {"items": items, "total": len(items)}


# ---------------------------------------------------------------- 评分 / ratings


def set_rating(conn: Connection, viewer, resource_id: int, score: int) -> dict:
    if viewer is None or viewer.status != "active":
        raise not_found("Resource not found")
    if not 1 <= int(score) <= 5:
        raise bad_request("Score must be between 1 and 5")
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    assert_visible(conn, viewer, row)

    existing = conn.execute(
        select(file_ratings.c.score).where(
            and_(
                file_ratings.c.resource_id == resource_id,
                file_ratings.c.user_id == viewer.id,
            )
        )
    ).first()
    _now = now_ms()
    if existing is None:
        result = conn.execute(
            sqlite_insert(file_ratings)
            .values(
                resource_id=resource_id,
                user_id=viewer.id,
                score=int(score),
                created_at=_now,
                updated_at=_now,
            )
            .on_conflict_do_nothing(
                index_elements=[file_ratings.c.resource_id, file_ratings.c.user_id]
            )
        )
        # 只有真的插入成功才计票：并发下第二个请求会走空，此时计数绝不能动。
        # Only a genuinely inserted row counts as a vote; under concurrency the second request
        # inserts nothing and must not touch the counters.
        if result.rowcount:
            _bump_counter(conn, resource_id, file_resources.c.rating_count, 1)
            _bump_counter(conn, resource_id, file_resources.c.rating_sum, int(score))
    elif existing.score != int(score):
        delta = int(score) - int(existing.score)
        conn.execute(
            update(file_ratings)
            .where(
                and_(
                    file_ratings.c.resource_id == resource_id,
                    file_ratings.c.user_id == viewer.id,
                )
            )
            .values(score=int(score), updated_at=_now)
        )
        _bump_counter(conn, resource_id, file_resources.c.rating_sum, delta)

    fresh = get_resource_row(conn, resource_id)
    return {
        "resourceId": resource_id,
        "myRating": int(score),
        "ratingAvg": _rating_avg(fresh["rating_sum"], fresh["rating_count"]) if fresh else None,
        "ratingCount": int(fresh["rating_count"]) if fresh else 0,
    }


def clear_rating(conn: Connection, viewer, resource_id: int) -> dict:
    if viewer is None:
        raise not_found("Resource not found")
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    # 撤销评分同样要过可见性校验。缺了它，一个从未获得阅读权限的账户只要 DELETE 一下
    # 就能拿到私有资料的 ratingAvg / ratingCount——不可见资源"统一 404"的约定被这条路径破坏，
    # 等于凭空多出一个探测资料存在性与口碑的接口。
    # Clearing a rating must pass the visibility check as well. Without it, an account that never
    # had read access could simply issue a DELETE and read back a private resource's ratingAvg and
    # ratingCount, breaking the "invisible means 404" contract and turning this route into a probe
    # for a resource's existence and reputation.
    assert_visible(conn, viewer, row)
    existing = conn.execute(
        select(file_ratings.c.score).where(
            and_(
                file_ratings.c.resource_id == resource_id,
                file_ratings.c.user_id == viewer.id,
            )
        )
    ).first()
    if existing is not None:
        conn.execute(
            file_ratings.delete().where(
                and_(
                    file_ratings.c.resource_id == resource_id,
                    file_ratings.c.user_id == viewer.id,
                )
            )
        )
        _bump_counter(conn, resource_id, file_resources.c.rating_count, -1)
        _bump_counter(conn, resource_id, file_resources.c.rating_sum, -int(existing.score))
    fresh = get_resource_row(conn, resource_id)
    return {
        "resourceId": resource_id,
        "myRating": None,
        "ratingAvg": _rating_avg(fresh["rating_sum"], fresh["rating_count"]) if fresh else None,
        "ratingCount": int(fresh["rating_count"]) if fresh else 0,
    }


def _bump_counter(conn: Connection, resource_id: int, column, delta: int) -> None:
    """原子增减冗余计数列，并夹紧到 0（防并发下出现负数）。
    Atomically adjust a denormalised counter column and clamp it at zero, so concurrency
    can never drive it negative.
    """
    conn.execute(
        update(file_resources)
        .where(file_resources.c.id == resource_id)
        .values({column.key: func.max(0, column + delta)})
    )


# ---------------------------------------------------------------- 下载 / downloads


DOWNLOAD_DEDUP_WINDOW_MS = 24 * 3600 * 1000


def record_download(conn: Connection, viewer, row: dict, client_ip: str | None) -> None:
    """记录一次下载，并按去重规则决定是否让 download_count 自增。

    登录用户按 (resource, user) 终身去重；匿名用户按 (resource, ip, 24 小时) 去重。
    两条规则都不影响明细表——明细始终全量落库。
    Record one download and decide, by the de-duplication rule, whether download_count
    should increase. A signed-in user is de-duplicated per (resource, user) for good; an
    anonymous one per (resource, ip, 24 hours). Neither rule affects the log table, which
    always records every hit.
    """
    resource_id = row["id"]
    uid = _viewer_id(viewer)
    counted = False
    if uid is not None:
        seen = conn.execute(
            select(file_downloads.c.id).where(
                and_(
                    file_downloads.c.resource_id == resource_id,
                    file_downloads.c.user_id == uid,
                )
            )
        ).first()
        counted = seen is None
    elif client_ip:
        threshold = now_ms() - DOWNLOAD_DEDUP_WINDOW_MS
        seen = conn.execute(
            select(file_downloads.c.id).where(
                and_(
                    file_downloads.c.resource_id == resource_id,
                    file_downloads.c.client_ip == client_ip,
                    file_downloads.c.created_at >= threshold,
                )
            )
        ).first()
        counted = seen is None
    else:
        # 既无用户也无 IP（例如内部调用）时保守计数：明细仍记录，但不动计数。
        # With neither a user nor an IP (an internal caller, say) stay conservative:
        # log the hit but leave the counter alone.
        counted = False

    conn.execute(
        file_downloads.insert().values(
            resource_id=resource_id,
            user_id=uid,
            client_ip=client_ip,
            created_at=now_ms(),
        )
    )
    if counted:
        _bump_counter(conn, resource_id, file_resources.c.download_count, 1)


def download_payload(conn: Connection, viewer, resource_id: int, storage) -> dict:
    """校验可见性并返回下载视图（供路由 302 到签名服务地址）。
    Validate visibility and return the download view the route redirects to, using a
    freshly signed serve URL.
    """
    row = get_resource_row(conn, resource_id)
    if row is None:
        raise not_found("Resource not found")
    assert_visible(conn, viewer, row)
    # 回源 URL 同样要编码路径段：文件名里的 "#" 与 "%" 会让地址无法使用。
    # The serve URL encodes its path segment too: a "#" or "%" in the filename would otherwise
    # make the address unusable.
    signed = storage.sign_path("GET", f"/api/files/serve/{row['object_key']}", 3600)
    return {
        "id": row["id"],
        "title": row["title"],
        "originalFilename": row["original_filename"],
        "sizeBytes": row["size_bytes"],
        "downloadUrl": build_object_url(
            "/api/files/serve",
            row["object_key"],
            {"expires": signed["expires"], "sig": signed["sig"]},
        ),
        "expiresAt": signed["expiresAt"],
        "objectKey": row["object_key"],
    }


# ---------------------------------------------------------------- 统计 / stats


def category_stats(conn: Connection, viewer) -> dict:
    """页头用的轻量统计：资料总数与分类数。
    Lightweight header stats: total resources and category count.
    """
    stmt = select(func.count()).select_from(file_resources).where(
        _not_deleted(), file_resources.c.status == "published"
    )
    predicate = _visible_predicate(viewer)
    if predicate is not None:
        stmt = stmt.where(predicate)
    total = conn.execute(stmt).scalar() or 0
    category_total = conn.execute(
        select(func.count()).select_from(file_categories).where(
            file_categories.c.deleted_at.is_(None)
        )
    ).scalar() or 0
    return {"resourceCount": total, "categoryCount": category_total}


# ---------------------------------------------------------------- 孤儿对象回收 / orphan sweep

DEFAULT_ORPHAN_RETENTION_MS = 24 * 3600 * 1000


def reap_orphan_objects(conn: Connection, storage, older_than_ms: int = DEFAULT_ORPHAN_RETENTION_MS) -> int:
    """删除磁盘上"谁都不认领"的对象文件，返回删除数量。

    为什么必须有这一步：presign 阶段刻意不建数据库行（元数据还没收集），所以
    "申请了上传地址、传了字节、但从未创建资料"的请求会在磁盘上留下**没有任何行引用的文件**。
    现有的 attachments 回收器只看 attachments 表，扫不到这类文件，于是磁盘可以被
    无限上传撑满——这是一条真实的滥用路径，不是理论风险。
    Why this step is mandatory: presign deliberately creates no database row (the metadata has
    not been collected yet), so a request that obtains an upload URL, uploads the bytes and
    never creates a resource leaves a file on disk that no row references. The existing
    attachments reaper only inspects the attachments table and cannot see those files, so
    unbounded uploads could fill the disk; that is a real abuse path, not a theoretical one.

    安全边界（三条都必须满足才删）：
    1. 对象键既不在 attachments 表、也不在 file_resources 表里；
    2. 文件的最后修改时间早于保留窗口（默认 24 小时，避免误删正在上传的字节）；
    3. 对象键符合命名规范（uuid/文件名），不碰任何形状异常的条目。
    Three conditions must all hold before anything is removed: the object key appears in
    neither the attachments table nor the resources table; the file's mtime is older than the
    retention window (24 hours by default, so bytes still being uploaded are never touched);
    and the object key matches the naming scheme, so nothing oddly shaped is ever considered.
    """
    from .storage import OBJECT_KEY_RE

    known: set[str] = set()
    for table in (attachments, file_resources):
        for row in conn.execute(select(table.c.object_key)).all():
            if row[0]:
                known.add(row[0])

    root = os.path.realpath(getattr(storage, "root", ""))
    if not root or not os.path.isdir(root):
        return 0

    threshold = now_ms() - older_than_ms
    removed = 0
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        for child in os.scandir(entry.path):
            if not child.is_file():
                continue
            object_key = f"{entry.name}/{child.name}"
            if not OBJECT_KEY_RE.match(object_key):
                continue
            if object_key in known:
                continue
            try:
                if int(child.stat().st_mtime * 1000) > threshold:
                    continue
                os.remove(child.path)
                removed += 1
            except OSError:
                # 单个文件删不掉不该让整轮回收失败；留到下一轮再试。
                # One undeletable file must not abort the whole sweep; the next round retries.
                continue
    return removed
