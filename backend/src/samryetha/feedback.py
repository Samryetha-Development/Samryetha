"""反馈 service — 镜像 backend/src/feedback/service.ts。

项目 / 条目 / 成员。feedback_api_keys 的 project_ids 存 JSON 文本。
"""

from __future__ import annotations

import json

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can
from .db import now_ms
from .errors import not_found
from .schema import (
    feedback_api_keys,
    feedback_comments,
    feedback_items,
    feedback_project_members,
    feedback_projects,
    users,
)
from .users import make_handle


# ---------------------------------------------------------------- dto helpers

def _author_ref(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "handle": make_handle(row["username"], row["discriminator"]),
        "displayName": row["display_name"],
    }


def _item_dto(it: dict, author: dict | None) -> dict:
    return {
        "id": it["id"],
        "seq": it["seq"],
        "projectId": it["project_id"],
        "author": _author_ref(author) if author else None,
        "title": it["title"],
        "detail": it["detail"],
        "type": it["type"],
        "urgency": it["urgency"],
        "status": it["status"],
        "closedAt": it["closed_at"],
        "editedAt": it["edited_at"],
        "createdAt": it["created_at"],
        "updatedAt": it["updated_at"],
    }


def _member_dto(r) -> dict:
    return {
        "userId": r["user_id"],
        "username": r["username"],
        "handle": make_handle(r["username"], r["discriminator"]),
        "displayName": r["display_name"],
        "isProgrammer": bool(r["is_programmer"]),
        "joinedAt": r["joined_at"],
    }


def _project_admin(conn: Connection, p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "description": p["description"],
        "members": members_of(conn, p["id"]),
        "createdAt": p["created_at"],
    }


# ---------------------------------------------------------------- project queries

def _project_row(conn: Connection, project_id: int) -> dict | None:
    row = conn.execute(
        select(feedback_projects).where(
            and_(feedback_projects.c.id == project_id, feedback_projects.c.deleted_at.is_(None))
        )
    ).first()
    return dict(row._mapping) if row else None


def get_project_for_authz(conn: Connection, project_id: int) -> dict | None:
    p = _project_row(conn, project_id)
    return {"id": p["id"], "projectId": p["id"]} if p else None


def members_of(conn: Connection, project_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            feedback_project_members.c.user_id,
            users.c.username,
            users.c.discriminator,
            users.c.display_name,
            feedback_project_members.c.is_programmer,
            feedback_project_members.c.joined_at,
        )
        .select_from(feedback_project_members)
        .join(users, users.c.id == feedback_project_members.c.user_id)
        .where(feedback_project_members.c.project_id == project_id)
    ).all()
    return [_member_dto(dict(r._mapping)) for r in rows]


def list_my_projects(conn: Connection, viewer_id: int, is_admin: bool) -> list[dict]:
    projects = conn.execute(
        select(feedback_projects)
        .where(feedback_projects.c.deleted_at.is_(None))
        .order_by(feedback_projects.c.name)
    ).all()
    out = []
    for p in projects:
        p = dict(p._mapping)
        member_rows = members_of(conn, p["id"])
        mine = next((m for m in member_rows if m["userId"] == viewer_id), None)
        if not mine and not is_admin:
            continue
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "description": p["description"],
                "memberCount": len(member_rows),
                "isProgrammer": is_admin or bool(mine and mine["isProgrammer"]),
                "createdAt": p["created_at"],
            }
        )
    return out


def list_projects_for_admin(conn: Connection) -> list[dict]:
    projects = conn.execute(
        select(feedback_projects)
        .where(feedback_projects.c.deleted_at.is_(None))
        .order_by(feedback_projects.c.created_at)
    ).all()
    return [_project_admin(conn, dict(p._mapping)) for p in projects]


def create_project(conn: Connection, actor_id: int, name: str, description: str | None) -> dict:
    _now = now_ms()
    res = conn.execute(
        feedback_projects.insert().values(
            name=name, description=description or "", created_by_user_id=actor_id, created_at=_now, updated_at=_now
        )
    )
    return _project_admin(conn, _project_row(conn, res.inserted_primary_key[0]))


def update_project(conn: Connection, project_id: int, patch: dict) -> None:
    p = _project_row(conn, project_id)
    if p is None:
        raise not_found("Project not found")
    values: dict = {"updated_at": now_ms()}
    if "name" in patch:
        values["name"] = patch["name"]
    if "description" in patch:
        values["description"] = patch["description"]
    conn.execute(update(feedback_projects).where(feedback_projects.c.id == project_id).values(**values))


def delete_project(conn: Connection, actor_id: int, project_id: int) -> None:
    p = _project_row(conn, project_id)
    if p is None:
        raise not_found("Project not found")
    _now = now_ms()
    conn.execute(
        update(feedback_projects)
        .where(feedback_projects.c.id == project_id)
        .values(deleted_at=_now, deleted_by=actor_id, updated_at=_now)
    )
    conn.execute(
        update(feedback_items)
        .where(feedback_items.c.project_id == project_id)
        .values(deleted_at=_now, deleted_by=actor_id, updated_at=_now)
    )


def set_project_members(conn: Connection, project_id: int, members: list[dict]) -> None:
    p = _project_row(conn, project_id)
    if p is None:
        raise not_found("Project not found")
    conn.execute(delete(feedback_project_members).where(feedback_project_members.c.project_id == project_id))
    if members:
        _now = now_ms()
        conn.execute(
            feedback_project_members.insert().values(
                [
                    {
                        "project_id": project_id,
                        "user_id": m["userId"],
                        "is_programmer": 1 if m.get("isProgrammer") else 0,
                        "joined_at": _now,
                    }
                    for m in members
                ]
            )
        )


# ---------------------------------------------------------------- items

def _item_row(conn: Connection, item_id: int) -> dict | None:
    row = conn.execute(
        select(feedback_items).where(
            and_(feedback_items.c.id == item_id, feedback_items.c.deleted_at.is_(None))
        )
    ).first()
    return dict(row._mapping) if row else None


def _with_author(conn: Connection, it: dict) -> dict:
    author = conn.execute(select(users).where(users.c.id == it["author_id"])).first()
    return _item_dto(it, dict(author._mapping) if author else None)


def item_by_id(conn: Connection, item_id: int) -> dict | None:
    it = _item_row(conn, item_id)
    if it is None:
        return None
    return _with_author(conn, it)


def _list_items(conn: Connection, project_id: int | None = None) -> list[dict]:
    stmt = select(feedback_items).where(feedback_items.c.deleted_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(feedback_items.c.project_id == project_id)
    rows = conn.execute(stmt.order_by(feedback_items.c.created_at)).all()
    author_ids = list({r.author_id for r in rows})
    authors = {}
    if author_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(author_ids))).all():
            authors[u.id] = dict(u._mapping)
    return [_item_dto(dict(r._mapping), authors.get(r.author_id)) for r in rows]


def _next_seq(conn: Connection, project_id: int) -> int:
    m = conn.execute(select(func.max(feedback_items.c.seq)).where(feedback_items.c.project_id == project_id)).scalar()
    return (m or 0) + 1


def get_item_for_authz(conn: Connection, item_id: int) -> dict | None:
    row = conn.execute(
        select(
            feedback_items.c.id,
            feedback_items.c.project_id,
            feedback_items.c.author_id,
            feedback_items.c.deleted_at,
        ).where(feedback_items.c.id == item_id)
    ).first()
    if row is None:
        return None
    return {
        "id": row.id,
        "projectId": row.project_id,
        "authorId": row.author_id,
        "deletedAt": row.deleted_at,
    }


def get_comment_for_authz(conn: Connection, comment_id: int) -> dict | None:
    row = conn.execute(
        select(
            feedback_comments.c.id,
            feedback_comments.c.item_id,
            feedback_comments.c.author_id,
            feedback_comments.c.deleted_at,
        ).where(feedback_comments.c.id == comment_id)
    ).first()
    if row is None:
        return None
    it = conn.execute(select(feedback_items.c.project_id).where(feedback_items.c.id == row.item_id)).first()
    return {
        "id": row.id,
        "itemId": row.item_id,
        "projectId": it[0] if it else None,
        "authorId": row.author_id,
        "deletedAt": row.deleted_at,
    }


def list_feedback(conn: Connection, viewer_id: int, is_admin: bool, project_id: int) -> dict:
    items = _list_items(conn, project_id)
    member = conn.execute(
        select(feedback_project_members.c.is_programmer).where(
            and_(
                feedback_project_members.c.project_id == project_id,
                feedback_project_members.c.user_id == viewer_id,
            )
        )
    ).first()
    return {"items": items, "canManage": is_admin or bool(member and member[0] == 1)}


def create_feedback(conn: Connection, actor_id: int, body: dict) -> dict:
    _now = now_ms()
    seq = _next_seq(conn, body["projectId"])
    res = conn.execute(
        feedback_items.insert().values(
            project_id=body["projectId"],
            author_id=actor_id,
            seq=seq,
            title=body["title"],
            detail=body.get("detail") or "",
            type=body["type"],
            urgency=body.get("urgency") or "normal",
            created_at=_now,
            updated_at=_now,
        )
    )
    created = item_by_id(conn, res.inserted_primary_key[0])
    if created is None:
        raise not_found("Feedback item not found")
    return created


def update_feedback(conn: Connection, item_id: int, patch: dict) -> dict:
    row = item_by_id(conn, item_id)
    if row is None:
        raise not_found("Feedback item not found")
    values: dict = {"updated_at": now_ms()}
    if "title" in patch:
        values["title"] = patch["title"]
    if "detail" in patch:
        values["detail"] = patch["detail"]
    if "type" in patch:
        values["type"] = patch["type"]
    if "urgency" in patch:
        values["urgency"] = patch["urgency"]
    _now = now_ms()
    if "title" in patch or "detail" in patch or "type" in patch or "urgency" in patch:
        values["edited_at"] = _now
    conn.execute(update(feedback_items).where(feedback_items.c.id == item_id).values(**values))
    return item_by_id(conn, item_id)


def delete_feedback(conn: Connection, actor_id: int, item_id: int) -> None:
    row = item_by_id(conn, item_id)
    if row is None:
        raise not_found("Feedback item not found")
    conn.execute(
        update(feedback_items)
        .where(feedback_items.c.id == item_id)
        .values(deleted_at=now_ms(), deleted_by=actor_id, updated_at=now_ms())
    )


def set_feedback_status(conn: Connection, item_id: int, status: str) -> dict:
    row = item_by_id(conn, item_id)
    if row is None:
        raise not_found("Feedback item not found")
    _now = now_ms()
    conn.execute(
        update(feedback_items)
        .where(feedback_items.c.id == item_id)
        .values(status=status, closed_at=None if status == "open" else _now, updated_at=_now)
    )
    return item_by_id(conn, item_id)


def list_feedback_for_agent(conn: Connection, project_id: int | None = None) -> list[dict]:
    return _list_items(conn, project_id)


# ---------------------------------------------------------------- comments

def _comment_dto(c: dict, author: dict | None) -> dict:
    return {
        "id": c["id"],
        "itemId": c["item_id"],
        "parentCommentId": c["parent_comment_id"],
        "author": _author_ref(author) if author else None,
        "body": c["body"],
        "isDeleted": c["deleted_at"] is not None,
        "createdAt": c["created_at"],
        "updatedAt": c["updated_at"],
    }


def _comment_with_author(conn: Connection, comment_id: int) -> dict:
    row = conn.execute(select(feedback_comments).where(feedback_comments.c.id == comment_id)).first()
    if row is None:
        raise not_found("Comment not found")
    c = dict(row._mapping)
    author = conn.execute(select(users).where(users.c.id == c["author_id"])).first()
    return _comment_dto(c, dict(author._mapping) if author else None)


def list_comments(conn: Connection, item_id: int) -> list[dict]:
    if _item_row(conn, item_id) is None:
        raise not_found("Feedback item not found")
    rows = conn.execute(
        select(feedback_comments)
        .where(and_(feedback_comments.c.item_id == item_id, feedback_comments.c.deleted_at.is_(None)))
        .order_by(feedback_comments.c.created_at)
    ).all()
    author_ids = {r.author_id for r in rows}
    authors: dict[int, dict] = {}
    if author_ids:
        for u in conn.execute(select(users).where(users.c.id.in_(author_ids))).all():
            authors[u.id] = dict(u._mapping)
    return [_comment_dto(dict(r._mapping), authors.get(r.author_id)) for r in rows]


def create_comment(conn: Connection, actor_id: int, item_id: int, body: str, parent_comment_id: int | None) -> dict:
    it = _item_row(conn, item_id)
    if it is None:
        raise not_found("Feedback item not found")
    # 校验父评论：必须属于同一 item 且未软删（嵌套评论）
    # Validate parent comment: must belong to the same item and not be soft-deleted (nested comments)
    if parent_comment_id is not None:
        parent = conn.execute(
            select(feedback_comments.c.id).where(
                and_(
                    feedback_comments.c.id == parent_comment_id,
                    feedback_comments.c.item_id == item_id,
                    feedback_comments.c.deleted_at.is_(None),
                )
            )
        ).first()
        if parent is None:
            raise not_found("Parent comment not found")
    _now = now_ms()
    res = conn.execute(
        feedback_comments.insert().values(
            item_id=item_id,
            author_id=actor_id,
            parent_comment_id=parent_comment_id,
            body=body,
            created_at=_now,
            updated_at=_now,
        )
    )
    return _comment_with_author(conn, res.inserted_primary_key[0])


def update_comment(conn: Connection, comment_id: int, body: str) -> dict:
    row = conn.execute(select(feedback_comments).where(feedback_comments.c.id == comment_id)).first()
    if row is None or row.deleted_at is not None:
        raise not_found("Comment not found")
    conn.execute(
        update(feedback_comments).where(feedback_comments.c.id == comment_id).values(body=body, updated_at=now_ms())
    )
    return _comment_with_author(conn, comment_id)


def delete_comment(conn: Connection, comment_id: int) -> None:
    row = conn.execute(select(feedback_comments).where(feedback_comments.c.id == comment_id)).first()
    if row is None or row.deleted_at is not None:
        raise not_found("Comment not found")
    conn.execute(
        update(feedback_comments)
        .where(feedback_comments.c.id == comment_id)
        .values(deleted_at=now_ms(), updated_at=now_ms())
    )


# ---------------------------------------------------------------- agent keys

AGENT_KEY_PREFIX = "fb-agent:"
_KEY_PREFIX_SHOW = "fb_"


def _hash_agent_key(key: str) -> str:
    import hashlib

    return hashlib.sha256((AGENT_KEY_PREFIX + key).encode()).hexdigest()


def generate_agent_key() -> str:
    import secrets

    return _KEY_PREFIX_SHOW + secrets.token_hex(24)


def _key_dto(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["key_prefix"],
        "role": row["role"],
        "projectIds": _parse_project_ids(row["project_ids"]),
        "enabled": bool(row["enabled"]),
        "lastUsedAt": row["last_used_at"],
        "createdAt": row["created_at"],
    }


def _parse_project_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [int(x) for x in data] if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def list_keys(conn: Connection) -> list[dict]:
    rows = conn.execute(select(feedback_api_keys).order_by(feedback_api_keys.c.created_at)).all()
    return [_key_dto(dict(r._mapping)) for r in rows]


def create_key(conn: Connection, name: str, role: str, project_ids: list[int]) -> tuple[str, dict]:
    key = generate_agent_key()
    _now = now_ms()
    res = conn.execute(
        feedback_api_keys.insert().values(
            name=name,
            key_hash=_hash_agent_key(key),
            key_prefix=key[:8],
            role=role,
            project_ids=json.dumps(project_ids),
            enabled=1,
            created_at=_now,
        )
    )
    row = conn.execute(select(feedback_api_keys).where(feedback_api_keys.c.id == res.inserted_primary_key[0])).first()
    return key, _key_dto(dict(row._mapping))


def set_key_enabled(conn: Connection, key_id: int, enabled: bool) -> None:
    row = conn.execute(select(feedback_api_keys.c.id).where(feedback_api_keys.c.id == key_id)).first()
    if row is None:
        raise not_found("API key not found")
    conn.execute(
        update(feedback_api_keys).where(feedback_api_keys.c.id == key_id).values(enabled=1 if enabled else 0)
    )


def delete_key(conn: Connection, key_id: int) -> None:
    row = conn.execute(select(feedback_api_keys.c.id).where(feedback_api_keys.c.id == key_id)).first()
    if row is None:
        raise not_found("API key not found")
    conn.execute(delete(feedback_api_keys).where(feedback_api_keys.c.id == key_id))


def verify_key(conn: Connection, raw_key: str | None) -> dict | None:
    if not raw_key:
        return None
    row = conn.execute(
        select(feedback_api_keys).where(
            and_(
                feedback_api_keys.c.key_hash == _hash_agent_key(raw_key),
                feedback_api_keys.c.enabled == 1,
            )
        )
    ).first()
    if row is None:
        return None
    _now = now_ms()
    conn.execute(update(feedback_api_keys).where(feedback_api_keys.c.id == row.id).values(last_used_at=_now))
    return _key_dto(dict(row._mapping))


def agent_can_access_project(agent: dict, project_id: int) -> bool:
    return len(agent["projectIds"]) == 0 or project_id in agent["projectIds"]
