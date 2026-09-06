"""板块 service — 镜像 backend/src/boards/service.ts。"""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import and_, func, select, update
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import conflict, not_found
from .schema import board_members, boards, discussions, users
from .users import make_handle


def _start_of_today_ms() -> int:
    today = datetime.now()
    local_midnight = today.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local_midnight.timestamp() * 1000)


def get_by_slug(conn: Connection, slug: str) -> dict | None:
    row = conn.execute(
        select(boards).where(and_(boards.c.slug == slug, boards.c.deleted_at.is_(None)))
    ).first()
    return dict(row._mapping) if row else None


def get_board_for_authz(conn: Connection, slug: str) -> dict | None:
    b = get_by_slug(conn, slug)
    if b is None:
        return None
    return {
        "id": b["id"],
        "visibility": b["visibility"],
        "postingPolicy": b["posting_policy"],
        "slug": b["slug"],
    }


def _summary(conn: Connection, board: dict, viewer_id: int | None) -> dict:
    board_id = board["id"]
    member_count = conn.execute(
        select(func.count()).select_from(board_members).where(board_members.c.board_id == board_id)
    ).scalar() or 0
    today = _start_of_today_ms()
    activity = conn.execute(
        select(func.count()).select_from(discussions).where(
            (discussions.c.board_id == board_id)
            & (discussions.c.created_at >= today)
            & (discussions.c.deleted_at.is_(None))
        )
    ).scalar() or 0
    role_row = None
    if viewer_id is not None:
        role_row = conn.execute(
            select(board_members.c.role).where(
                (board_members.c.board_id == board_id) & (board_members.c.user_id == viewer_id)
            )
        ).first()
    return {
        "id": board_id,
        "slug": board["slug"],
        "name": board["name"],
        "description": board["description"],
        "visibility": board["visibility"],
        "postingPolicy": board["posting_policy"],
        "memberCount": member_count,
        "todayActivity": activity,
        "currentUserRole": (role_row[0] if role_row else None),
    }


def _is_visible(conn: Connection, viewer, board: dict) -> bool:
    """板块对 viewer 可见？全局 mod/admin 全见；public 全见；其余仅成员可见。
    镜像 boards/service.ts 的 visibleBoardRows。"""
    if viewer is not None and viewer.role == "admin":
        return True
    if board["visibility"] == "public":
        return True
    if viewer is None:
        return False
    row = conn.execute(
        select(board_members.c.board_id).where(
            (board_members.c.board_id == board["id"]) & (board_members.c.user_id == viewer.id)
        )
    ).first()
    return row is not None


def list_boards(conn: Connection, viewer) -> list[dict]:
    rows = conn.execute(
        select(boards).where(boards.c.deleted_at.is_(None)).order_by(boards.c.name)
    ).all()
    viewer_id = viewer.id if viewer is not None else None
    out = []
    for r in rows:
        b = dict(r._mapping)
        if not _is_visible(conn, viewer, b):
            continue
        out.append(_summary(conn, b, viewer_id))
    return out


def get_board(conn: Connection, viewer, slug: str) -> dict:
    b = get_by_slug(conn, slug)
    # 不可见 → 统一 notFound，不泄漏板块存在性（镜像 TS：visible.some → throw notFound）
    if b is None or not _is_visible(conn, viewer, b):
        raise not_found("Board not found")
    return _summary(conn, b, viewer.id if viewer is not None else None)


def create_board(conn: Connection, actor_id: int, data: dict) -> dict:
    slug = _slugify(data["slug"])
    if get_by_slug(conn, slug):
        raise conflict("Board slug already exists")
    _now = now_ms()
    res = conn.execute(
        boards.insert().values(
            slug=slug,
            name=data["name"],
            description=data.get("description") or "",
            visibility=data.get("visibility") or "public",
            posting_policy=data.get("postingPolicy") or "members",
            created_by_user_id=actor_id,
            created_at=_now,
            updated_at=_now,
        )
    )
    board_id = res.inserted_primary_key[0]
    conn.execute(
        board_members.insert().values(
            board_id=board_id, user_id=actor_id, role="moderator", joined_at=now_ms()
        )
    )
    created = get_by_slug(conn, slug)
    return _summary(conn, created, None)


def _slugify(slug: str) -> str:
    import re

    return re.sub(r"\s+", "-", slug.strip().lower())


def update_board(conn: Connection, slug: str, patch: dict) -> dict:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    values: dict = {"updated_at": now_ms()}
    if "name" in patch:
        values["name"] = patch["name"]
    if "description" in patch:
        values["description"] = patch["description"]
    if "visibility" in patch:
        values["visibility"] = patch["visibility"]
    if "postingPolicy" in patch:
        values["posting_policy"] = patch["postingPolicy"]
    conn.execute(update(boards).where(boards.c.id == board["id"]).values(**values))
    updated = get_by_slug(conn, slug)
    return _summary(conn, updated, None)


def delete_board(conn: Connection, actor_id: int, slug: str, reason: str | None) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    _now = now_ms()
    conn.execute(
        update(boards)
        .where(boards.c.id == board["id"])
        .values(deleted_at=_now, deleted_by=actor_id, deletion_reason=reason, updated_at=_now)
    )


def join_board(conn: Connection, user_id: int, slug: str) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    existing = conn.execute(
        select(board_members.c.board_id).where(
            (board_members.c.board_id == board["id"]) & (board_members.c.user_id == user_id)
        )
    ).first()
    if existing:
        raise conflict("Already a member")
    conn.execute(
        board_members.insert().values(
            board_id=board["id"], user_id=user_id, role="member", joined_at=now_ms()
        )
    )


def leave_board(conn: Connection, user_id: int, slug: str) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    conn.execute(
        board_members.delete().where(
            (board_members.c.board_id == board["id"]) & (board_members.c.user_id == user_id)
        )
    )


def list_members(conn: Connection, viewer, slug: str) -> list[dict]:
    board = get_by_slug(conn, slug)
    if board is None or not _is_visible(conn, viewer, board):
        raise not_found("Board not found")
    rows = conn.execute(
        select(users.c.id, users.c.username, users.c.display_name, users.c.discriminator,
               board_members.c.role)
        .select_from(board_members)
        .join(users, board_members.c.user_id == users.c.id)
        .where(board_members.c.board_id == board["id"])
    ).all()
    return [
        {
            "id": r.id,
            "username": r.username,
            "handle": make_handle(r.username, r.discriminator),
            "displayName": r.display_name,
            "role": r.role,
        }
        for r in rows
    ]


def update_member_role(conn: Connection, slug: str, user_id: int, role: str) -> None:
    board = get_by_slug(conn, slug)
    if board is None:
        raise not_found("Board not found")
    existing = conn.execute(
        select(board_members.c.role).where(
            (board_members.c.board_id == board["id"]) & (board_members.c.user_id == user_id)
        )
    ).first()
    if existing is None:
        raise not_found("User is not a member of this board")
    conn.execute(
        update(board_members)
        .where((board_members.c.board_id == board["id"]) & (board_members.c.user_id == user_id))
        .values(role=role)
    )
