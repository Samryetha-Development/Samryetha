"""搜索 service — 镜像 backend/src/search/service.ts。

SQLite 无 FTS → LIKE 子串；中文逐字符命中。按 last_reply_at desc, created_at desc, id desc。
"""

from __future__ import annotations

import re

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection

from .discussions import moderation_visible, visible_board_ids, preview
from .schema import boards, discussions, users
from .users import make_handle

_LIKE_ESCAPE = re.compile(r'[\\%_]')


def escape_like(s: str) -> str:
    return _LIKE_ESCAPE.sub(lambda m: "\\" + m.group(0), s)


def search_discussions(conn: Connection, viewer, opts: dict) -> dict:
    q = escape_like((opts["q"] or "").strip()[:100])
    limit = min(opts.get("limit") or 20, 50)
    visible = visible_board_ids(conn, viewer)
    match = or_(
        discussions.c.title.like(f"%{q}%"),
        discussions.c.body_md.like(f"%{q}%"),
    )
    conds = [discussions.c.deleted_at.is_(None), discussions.c.board_id.in_(visible), match]
    # 审核状态的可见性必须和列表/详情一致，否则待审、乃至审核失败的原文会从搜索里漏出去。
    # 尤其是"审核失败仅管理员可访问"——漏掉这一条，搜索就成了绕过封禁的后门。
    moderation_predicate = moderation_visible(
        discussions.c.moderation_status, discussions.c.author_id, viewer
    )
    if moderation_predicate is not None:
        conds.append(moderation_predicate)
    if opts.get("boardSlug"):
        board = conn.execute(select(boards).where(boards.c.slug == opts["boardSlug"])).first()
        if board is not None:
            conds.append(discussions.c.board_id == board.id)

    total = conn.execute(
        select(func.count()).select_from(discussions).where(and_(*conds))
    ).scalar() or 0

    cols = [
        discussions.c.id,
        discussions.c.title,
        discussions.c.body_md,
        discussions.c.reply_count,
        discussions.c.is_pinned,
        discussions.c.is_locked,
        discussions.c.created_at,
        discussions.c.last_reply_at,
        discussions.c.board_id,
        discussions.c.author_id,
    ]
    rows = conn.execute(
        select(*cols)
        .where(and_(*conds))
        .order_by(
            discussions.c.last_reply_at.desc(),
            discussions.c.created_at.desc(),
            discussions.c.id.desc(),
        )
        .limit(limit)
    ).all()
    board_ids = {r.board_id for r in rows}
    author_ids = {r.author_id for r in rows}
    board_map: dict[int, dict] = {}
    author_map: dict[int, dict] = {}
    if board_ids:
        for row in conn.execute(select(boards).where(boards.c.id.in_(board_ids))).all():
            board_map[row.id] = dict(row._mapping)
    if author_ids:
        for row in conn.execute(select(users).where(users.c.id.in_(author_ids))).all():
            author_map[row.id] = dict(row._mapping)
    items = []
    for r in rows:
        board = board_map.get(r.board_id)
        author = author_map.get(r.author_id)
        activity = r.last_reply_at if r.last_reply_at is not None else r.created_at
        items.append(
            {
                "id": r.id,
                "title": r.title,
                "preview": preview(r.body_md),
                "board": {
                    "id": r.board_id,
                    "slug": board["slug"] if board else "",
                    "name": board["name"] if board else "",
                },
                "author": {
                    "id": r.author_id,
                    "username": author["username"] if author else "",
                    "handle": make_handle(author["username"], author["discriminator"]) if author else "",
                    "displayName": author["display_name"] if author else "",
                },
                "replyCount": r.reply_count,
                "isPinned": (r.is_pinned == 1),
                "isLocked": (r.is_locked == 1),
                "createdAt": r.created_at,
                "lastActivityAt": activity,
            }
        )
    return {"items": items, "total": total}
