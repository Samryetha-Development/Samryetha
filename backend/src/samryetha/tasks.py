"""开发任务追踪 service — 独立表 tasks，不依赖 feedback。

公开可读、登录可写；author 记录创建者用于展示与将来 OAuth 归因。
分组字段 category（如 Frontend/Backend/Design/…，空则 General），
优先级 priority（urgent|normal），状态 status（open|done，done_at 记完成时刻）。
"""

from __future__ import annotations

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import not_found
from .schema import task_comments, tasks, users
from .users import make_handle

DEFAULT_CATEGORY = "General"


# ---------------------------------------------------------------- dto helpers

# 作者列别名（join 时避免与 tasks.id 撞名）。
_AUTHOR_ALIASES = {
    "author_id": users.c.id.label("author_user_id"),
    "author_username": users.c.username.label("author_username"),
    "author_discriminator": users.c.discriminator.label("author_discriminator"),
    "author_display_name": users.c.display_name.label("author_display_name"),
}

_TASK_SELECT = select(
    tasks.c.id,
    tasks.c.category,
    tasks.c.title,
    tasks.c.notes,
    tasks.c.priority,
    tasks.c.status,
    tasks.c.done_at,
    tasks.c.created_at,
    tasks.c.updated_at,
    tasks.c.author_id,
    *_AUTHOR_ALIASES.values(),
)


def _author_ref(row: dict) -> dict:
    return {
        "id": row["author_user_id"],
        "username": row["author_username"],
        "handle": make_handle(row["author_username"], row["author_discriminator"]),
        "displayName": row["author_display_name"],
    }


def _task_dto(row: dict) -> dict:
    return {
        "id": row["id"],
        "author": _author_ref(row),
        "category": row["category"],
        "title": row["title"],
        "notes": row["notes"],
        "priority": row["priority"],
        "status": row["status"],
        "doneAt": row["done_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _fetch_task(conn: Connection, task_id: int) -> dict | None:
    row = conn.execute(_TASK_SELECT.join(users, users.c.id == tasks.c.author_id).where(tasks.c.id == task_id)).first()
    return _task_dto(dict(row._mapping)) if row else None


def list_tasks(conn: Connection) -> dict:
    """全部任务（开放看板，量小不分页），按创建倒序；附分组统计。"""
    rows = conn.execute(_TASK_SELECT.join(users, users.c.id == tasks.c.author_id).order_by(tasks.c.created_at.desc(), tasks.c.id.desc())).all()
    items = [_task_dto(dict(r._mapping)) for r in rows]

    # 分组统计：每个 category 的 open/done 计数。
    counts: dict[str, dict] = {}
    for it in items:
        bucket = counts.setdefault(it["category"], {"open": 0, "done": 0})
        bucket[it["status"]] += 1
    categories = [{"category": c, "open": counts[c]["open"], "done": counts[c]["done"]} for c in sorted(counts)]
    return {"items": items, "categories": categories}


def create_task(conn: Connection, author_id: int, payload: dict) -> dict:
    category = (payload.get("category") or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
    now = now_ms()
    res = conn.execute(
        tasks.insert().values(
            author_id=author_id,
            category=category,
            title=payload["title"],
            notes=payload.get("notes") or "",
            priority=payload.get("priority") or "normal",
            status=payload.get("status") or "open",
            done_at=now if payload.get("status") == "done" else None,
            created_at=now,
            updated_at=now,
        )
    )
    task = _fetch_task(conn, res.inserted_primary_key[0])
    assert task is not None
    return task


def update_task(conn: Connection, task_id: int, patch: dict) -> dict:
    if conn.execute(select(tasks.c.id).where(tasks.c.id == task_id)).first() is None:
        raise not_found("Task not found")
    values = {"updated_at": now_ms()}
    if "category" in patch:
        values["category"] = (patch["category"] or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
    if "title" in patch:
        values["title"] = patch["title"]
    if "notes" in patch:
        values["notes"] = patch["notes"] or ""
    if "priority" in patch:
        values["priority"] = patch["priority"]
    conn.execute(update(tasks).where(tasks.c.id == task_id).values(**values))
    task = _fetch_task(conn, task_id)
    assert task is not None
    return task


def set_task_status(conn: Connection, task_id: int, status: str) -> dict:
    now = now_ms()
    res = conn.execute(
        update(tasks)
        .where(tasks.c.id == task_id)
        .values(status=status, done_at=now if status == "done" else None, updated_at=now)
    )
    if res.rowcount == 0:
        raise not_found("Task not found")
    task = _fetch_task(conn, task_id)
    assert task is not None
    return task


def delete_task(conn: Connection, task_id: int) -> None:
    res = conn.execute(delete(tasks).where(tasks.c.id == task_id))
    if res.rowcount == 0:
        raise not_found("Task not found")
    conn.execute(delete(task_comments).where(task_comments.c.task_id == task_id))


# ---------------------------------------------------------------- comments


def _comment_author(conn: Connection, user_id: int) -> dict | None:
    row = conn.execute(select(users).where(users.c.id == user_id)).first()
    return dict(row._mapping) if row else None


def _comment_author_ref(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
    }


def _comment_dto(c: dict, author: dict | None) -> dict:
    return {
        "id": c["id"],
        "taskId": c["task_id"],
        "parentCommentId": c["parent_comment_id"],
        "author": _comment_author_ref(author) if author else None,
        "body": c["body"],
        "isDeleted": c["deleted_at"] is not None,
        "createdAt": c["created_at"],
        "updatedAt": c["updated_at"],
    }


def get_comment_for_authz(conn: Connection, comment_id: int) -> dict | None:
    row = conn.execute(
        select(
            task_comments.c.id,
            task_comments.c.task_id,
            task_comments.c.author_id,
            task_comments.c.deleted_at,
        ).where(task_comments.c.id == comment_id)
    ).first()
    if row is None:
        return None
    return {
        "id": row.id,
        "taskId": row.task_id,
        "authorId": row.author_id,
        "deletedAt": row.deleted_at,
    }


def list_comments(conn: Connection, task_id: int) -> list[dict]:
    if conn.execute(select(tasks.c.id).where(tasks.c.id == task_id)).first() is None:
        raise not_found("Task not found")
    rows = conn.execute(
        select(task_comments)
        .where(and_(task_comments.c.task_id == task_id, task_comments.c.deleted_at.is_(None)))
        .order_by(task_comments.c.created_at)
    ).all()
    author_ids = {r.author_id for r in rows}
    authors: dict[int, dict] = {}
    if author_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(author_ids))).all():
            authors[u.id] = dict(u._mapping)
    return [_comment_dto(dict(r._mapping), authors.get(r.author_id)) for r in rows]


def create_comment(conn: Connection, actor_id: int, task_id: int, body: str, parent_comment_id: int | None) -> dict:
    if conn.execute(select(tasks.c.id).where(tasks.c.id == task_id)).first() is None:
        raise not_found("Task not found")
    # 校验父评论：必须属于同一任务且未软删（嵌套评论）
    if parent_comment_id is not None:
        parent = conn.execute(
            select(task_comments.c.id).where(
                and_(
                    task_comments.c.id == parent_comment_id,
                    task_comments.c.task_id == task_id,
                    task_comments.c.deleted_at.is_(None),
                )
            )
        ).first()
        if parent is None:
            raise not_found("Parent comment not found")
    now = now_ms()
    res = conn.execute(
        task_comments.insert().values(
            task_id=task_id,
            author_id=actor_id,
            parent_comment_id=parent_comment_id,
            body=body,
            created_at=now,
            updated_at=now,
        )
    )
    row = conn.execute(select(task_comments).where(task_comments.c.id == res.inserted_primary_key[0])).first()
    comment = dict(row._mapping)
    return _comment_dto(comment, _comment_author(conn, comment["author_id"]))


def update_comment(conn: Connection, comment_id: int, body: str) -> dict:
    row = conn.execute(select(task_comments).where(task_comments.c.id == comment_id)).first()
    if row is None or row.deleted_at is not None:
        raise not_found("Comment not found")
    conn.execute(update(task_comments).where(task_comments.c.id == comment_id).values(body=body, updated_at=now_ms()))
    updated = conn.execute(select(task_comments).where(task_comments.c.id == comment_id)).first()
    comment = dict(updated._mapping)
    return _comment_dto(comment, _comment_author(conn, comment["author_id"]))


def delete_comment(conn: Connection, comment_id: int) -> None:
    row = conn.execute(select(task_comments).where(task_comments.c.id == comment_id)).first()
    if row is None or row.deleted_at is not None:
        raise not_found("Comment not found")
    conn.execute(
        update(task_comments)
        .where(task_comments.c.id == comment_id)
        .values(deleted_at=now_ms(), updated_at=now_ms())
    )
