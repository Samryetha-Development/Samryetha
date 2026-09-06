"""讨论/回复 service — 镜像 backend/src/discussions/service.ts。

时间戳毫秒 int；ThreadSummary/ReplyDTO/DiscussionDetail 均 camelCase。
activityExpr = coalesce(last_reply_at, created_at)。
"""

from __future__ import annotations

import re

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can, can
from .boards import get_board_for_authz
from .db import now_ms
from .errors import conflict, forbidden, internal_error, not_found, validation_failed
from .markdown import render_body
from .outbox import emit_event
from .schema import (
    attachments,
    board_members,
    boards,
    discussion_follows,
    discussion_saves,
    discussions,
    replies,
    user_follows,
    users,
)
from .users import make_handle


def _activity() -> "any":
    return func.coalesce(discussions.c.last_reply_at, discussions.c.created_at)


def preview(md: str) -> str:
    flat = re.sub(r"\s+", " ", md or "").strip()
    return flat if len(flat) <= 160 else flat[:160] + "…"


def to_author(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "handle": make_handle(user["username"], user["discriminator"]),
        "displayName": user["display_name"],
    }


# ---------------------------------------------------------------- visibility


def visible_board_ids(conn: Connection, viewer) -> list[int]:
    all_rows = conn.execute(
        select(boards.c.id, boards.c.visibility).where(boards.c.deleted_at.is_(None))
    ).all()
    all_ids = [r.id for r in all_rows]
    if viewer is not None and viewer.role == "admin":
        return all_ids
    member_ids: set[int] = set()
    if viewer is not None:
        rows = conn.execute(
            select(board_members.c.board_id).where(board_members.c.user_id == viewer.id)
        ).all()
        member_ids = {r[0] for r in rows}
    return [r.id for r in all_rows if r.visibility == "public" or r.id in member_ids]


# ---------------------------------------------------------------- helpers


def get_discussion_row(conn: Connection, discussion_id: int) -> dict | None:
    row = conn.execute(
        select(discussions).where(discussions.c.id == discussion_id)
    ).first()
    return dict(row._mapping) if row else None


def _build_thread(activity: int, board: dict | None, author: dict | None, r) -> dict:
    return {
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


def to_threads(conn: Connection, rows: list) -> list[dict]:
    if not rows:
        return []
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
        activity = r.last_reply_at if r.last_reply_at is not None else r.created_at
        items.append(
            _build_thread(activity, board_map.get(r.board_id), author_map.get(r.author_id), r)
        )
    return items


def _rows_for(conn: Connection, conds) -> list:
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
    stmt = (
        select(*cols)
        .where(and_(*conds))
        .order_by(discussions.c.is_pinned.desc(), _activity().desc(), discussions.c.id.desc())
    )
    return conn.execute(stmt).all()


_MENTION_PATTERN = re.compile(r"@([a-z0-9_]{3,30})", re.IGNORECASE)


def _emit_mentions(conn: Connection, *, body: str, author_id: int, discussion_id: int, reply_id: int | None, title: str) -> None:
    names = list(dict.fromkeys(match.group(1).lower() for match in _MENTION_PATTERN.finditer(body or "")))
    if not names:
        return
    rows = conn.execute(
        select(users.c.id, users.c.username).where(
            users.c.username.in_(names),
            users.c.deleted_at.is_(None),
        )
    ).all()
    for row in rows:
        if row.id == author_id:
            continue
        emit_event(
            conn,
            "mention.created",
            aggregate_type="discussion",
            aggregate_id=str(discussion_id),
            payload={
                "discussionId": discussion_id,
                "replyId": reply_id,
                "authorId": author_id,
                "mentionedUserId": row.id,
                "mentionedUsername": row.username,
                "title": title,
            },
        )


def _cursor_cond(cursor: str | None):
    if not cursor:
        return None
    parts = cursor.split("_")
    if len(parts) != 2:
        return None
    try:
        at = int(parts[0])
        cid = int(parts[1])
    except ValueError:
        return None
    act = _activity()
    return or_((act < at), (act == at) & (discussions.c.id < cid))


# ---------------------------------------------------------------- detail


def load_detail(conn: Connection, viewer, d: dict) -> dict:
    board = conn.execute(select(boards).where(boards.c.id == d["board_id"])).first()
    author = conn.execute(select(users).where(users.c.id == d["author_id"])).first()
    board_res = {
        "id": d["board_id"],
        "slug": board.slug if board else "",
        "name": board.name if board else "",
    }
    saved = following = None
    if viewer is not None:
        saved = conn.execute(
            select(discussion_saves.c.discussion_id).where(
                (discussion_saves.c.user_id == viewer.id)
                & (discussion_saves.c.discussion_id == d["id"])
            )
        ).first()
        following = conn.execute(
            select(discussion_follows.c.discussion_id).where(
                (discussion_follows.c.user_id == viewer.id)
                & (discussion_follows.c.discussion_id == d["id"])
            )
        ).first()
    res = {
        "id": d["id"],
        "title": d["title"],
        "preview": preview(d["body_md"]),
        "board": board_res,
        "author": to_author(dict(author._mapping)) if author else {"id": d["author_id"], "username": "", "handle": "", "displayName": ""},
        "replyCount": d["reply_count"],
        "saveCount": d["save_count"],
        "isPinned": d["is_pinned"] == 1,
        "isLocked": d["is_locked"] == 1,
        "bodyMarkdown": d["body_md"],
        "bodyHtml": d["body_html"],
        "bodyFormat": d.get("body_format") or "markdown",
        "isSaved": saved is not None,
        "isFollowing": following is not None,
        "createdAt": d["created_at"],
        "lastActivityAt": d["last_reply_at"] if d["last_reply_at"] is not None else d["created_at"],
    }
    discussion_res = {
        "type": "discussion",
        "id": d["id"],
        "authorId": d["author_id"],
        "boardId": d["board_id"],
        "isLocked": d["is_locked"],
        "deletedAt": d["deleted_at"],
    }
    res["can"] = {
        "update": can(viewer, Abilities.DISCUSSION_UPDATE, discussion_res, conn),
        "delete": can(viewer, Abilities.DISCUSSION_DELETE, discussion_res, conn),
    }
    return res


# ---------------------------------------------------------------- main list


def list_discussions(conn: Connection, viewer, opts: dict) -> dict:
    limit = min(opts.get("limit") or 20, 50)
    visible = visible_board_ids(conn, viewer)
    conds = [discussions.c.deleted_at.is_(None), discussions.c.board_id.in_(visible)]
    if opts.get("boardSlug"):
        board = get_board_for_authz(conn, opts["boardSlug"])
        if board is None:
            raise not_found("Board not found")
        conds.append(discussions.c.board_id == board["id"])
    cur = _cursor_cond(opts.get("cursor"))
    if cur is not None:
        conds.append(cur)

    feed = opts.get("feed") or "latest"
    if feed == "followed":
        if viewer is None:
            return {"items": [], "nextCursor": None}
        following_ids = [
            r[0]
            for r in conn.execute(
                select(user_follows.c.followee_id).where(user_follows.c.follower_id == viewer.id)
            ).all()
        ]
        followed_disc_ids = [
            r[0]
            for r in conn.execute(
                select(discussion_follows.c.discussion_id).where(discussion_follows.c.user_id == viewer.id)
            ).all()
        ]
        if not following_ids and not followed_disc_ids:
            return {"items": [], "nextCursor": None}
        conds.append(
            or_(
                discussions.c.author_id.in_(following_ids),
                discussions.c.id.in_(followed_disc_ids),
            )
        )

    rows = _rows_for(conn, conds)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    # announcement 分区：没有手动置顶时自动置顶最新公告
    # Announcement board: auto-pin the latest announcement when nothing is manually pinned
    if opts.get("boardSlug") == "announcements" and items and not any(it["isPinned"] for it in items):
        items[0]["isPinned"] = True
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last['lastActivityAt']}_{last['id']}"
    return {"items": items, "nextCursor": next_cursor}


def get_discussion(conn: Connection, viewer, discussion_id: int) -> dict:
    d = get_discussion_row(conn, discussion_id)
    if d is None or d["deleted_at"]:
        raise not_found("Discussion not found")
    board = conn.execute(select(boards).where(boards.c.id == d["board_id"])).first()
    if board is None:
        raise not_found("Board not found")
    board_res = {
        "type": "board",
        "id": board.id,
        "visibility": board.visibility,
        "postingPolicy": board.posting_policy,
    }
    assert_can(viewer, Abilities.DISCUSSION_READ, board_res, conn)
    return load_detail(conn, viewer, d)


# ---------------------------------------------------------------- write ops


def create_discussion(conn: Connection, actor, data: dict) -> dict:
    if actor is None:
        raise internal_error()
    title = data["title"].strip()
    if len(title) < 3:
        raise validation_failed([{"field": "title", "message": "Title must be at least 3 characters", "code": "too_small"}])
    board = get_board_for_authz(conn, data["boardSlug"])
    if board is None:
        raise not_found("Board not found")
    board_res = {"type": "board", **board}
    assert_can(actor, Abilities.DISCUSSION_CREATE, board_res, conn)
    body_format = data.get("bodyFormat") or "markdown"
    body_html = render_body(data["bodyMarkdown"], body_format)
    _now = now_ms()
    res = conn.execute(
        discussions.insert().values(
            board_id=board["id"],
            author_id=actor.id,
            title=title,
            body_md=data["bodyMarkdown"],
            body_html=body_html,
            body_format=body_format,
            created_at=_now,
            updated_at=_now,
        )
    )
    disc_id = res.inserted_primary_key[0]
    att_ids = data.get("attachmentIds") or []
    if att_ids:
        conn.execute(
            update(attachments)
            .where(attachments.c.id.in_(att_ids) & (attachments.c.uploader_id == actor.id))
            .values(discussion_id=disc_id, state="attached")
        )
    _emit_mentions(conn, body=data["bodyMarkdown"], author_id=actor.id, discussion_id=disc_id, reply_id=None, title=title)
    emit_event(
        conn,
        "discussion.created",
        aggregate_type="discussion",
        aggregate_id=str(disc_id),
        payload={
            "discussionId": disc_id,
            "boardId": board["id"],
            "authorId": actor.id,
            "title": title,
        },
    )
    return get_discussion(conn, actor, disc_id)


def update_discussion(conn: Connection, actor, discussion_id: int, patch: dict) -> dict:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d["author_id"],
        "boardId": d["board_id"],
        "isLocked": d["is_locked"],
        "deletedAt": d["deleted_at"],
    }
    assert_can(actor, Abilities.DISCUSSION_UPDATE, res, conn)
    values: dict = {"updated_at": now_ms()}
    if "title" in patch:
        title = patch["title"].strip()
        if len(title) < 3:
            raise validation_failed([{"field": "title", "message": "Title must be at least 3 characters", "code": "too_small"}])
        values["title"] = title
    if "bodyMarkdown" in patch:
        body_format = patch.get("bodyFormat") or "markdown"
        values["body_md"] = patch["bodyMarkdown"]
        values["body_html"] = render_body(patch["bodyMarkdown"], body_format)
        values["body_format"] = body_format
    conn.execute(update(discussions).where(discussions.c.id == discussion_id).values(**values))
    return get_discussion(conn, actor, discussion_id)


def delete_discussion(conn: Connection, actor, discussion_id: int, reason: str | None) -> None:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d["author_id"],
        "boardId": d["board_id"],
        "isLocked": d["is_locked"],
        "deletedAt": d["deleted_at"],
    }
    assert_can(actor, Abilities.DISCUSSION_DELETE, res, conn)
    _now = now_ms()
    conn.execute(
        update(discussions)
        .where(discussions.c.id == discussion_id)
        .values(
            deleted_at=_now,
            deleted_by=actor.id if actor else None,
            deletion_reason=reason,
            updated_at=_now,
        )
    )


def _reply_dto(row: dict, author: dict, discussion_id: int | None = None, deleted: bool = False) -> dict:
    return {
        "id": row["id"],
        "discussionId": discussion_id if discussion_id is not None else row["discussion_id"],
        "parentReplyId": row["parent_reply_id"],
        "author": to_author(author),
        "bodyMarkdown": "" if deleted else row["body_md"],
        "bodyHtml": None if deleted else row["body_html"],
        "bodyFormat": row.get("body_format") or "markdown",
        "isDeleted": deleted or row["deleted_at"] is not None,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_reply(conn: Connection, actor, discussion_id: int, data: dict) -> dict:
    if actor is None:
        raise internal_error()
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d["author_id"],
        "boardId": d["board_id"],
        "isLocked": d["is_locked"],
        "deletedAt": d["deleted_at"],
    }
    assert_can(actor, Abilities.REPLY_CREATE, res, conn)
    # 校验父评论：parentReplyId 必须属于同一 discussion 且未被软删，否则产生跨帖孤儿回复，父不存在时外键触发 500
    # Validate parent reply: it must belong to the same discussion and not be soft-deleted, otherwise orphan replies / FK 500
    parent_reply_id = data.get("parentReplyId")
    if parent_reply_id is not None:
        parent = conn.execute(
            select(replies.c.id).where(
                (replies.c.id == parent_reply_id)
                & (replies.c.discussion_id == discussion_id)
                & (replies.c.deleted_at.is_(None))
            )
        ).first()
        if parent is None:
            raise not_found("Parent reply not found")
    body_format = data.get("bodyFormat") or "markdown"
    body_html = render_body(data["bodyMarkdown"], body_format)
    _now = now_ms()
    ins = conn.execute(
        replies.insert().values(
            discussion_id=discussion_id,
            author_id=actor.id,
            parent_reply_id=data.get("parentReplyId"),
            body_md=data["bodyMarkdown"],
            body_html=body_html,
            body_format=body_format,
            created_at=_now,
            updated_at=_now,
        )
    )
    reply_id = ins.inserted_primary_key[0]
    conn.execute(
        update(discussions)
        .where(discussions.c.id == discussion_id)
        .values(reply_count=discussions.c.reply_count + 1, last_reply_at=_now, updated_at=_now)
    )
    _emit_mentions(conn, body=data["bodyMarkdown"], author_id=actor.id, discussion_id=discussion_id, reply_id=reply_id, title=d["title"])
    emit_event(
        conn,
        "reply.created",
        aggregate_type="discussion",
        aggregate_id=str(discussion_id),
        payload={
            "discussionId": discussion_id,
            "replyId": reply_id,
            "authorId": actor.id,
            "parentReplyId": data.get("parentReplyId"),
            "title": d["title"],
        },
    )
    row = dict(
        conn.execute(select(replies).where(replies.c.id == reply_id)).first()._mapping
    )
    author = dict(
        conn.execute(select(users).where(users.c.id == actor.id)).first()._mapping
    )
    return _reply_dto(row, author)


def list_replies(conn: Connection, viewer, discussion_id: int) -> dict:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    board = conn.execute(select(boards).where(boards.c.id == d["board_id"])).first()
    if board is None:
        raise not_found("Board not found")
    board_res = {
        "type": "board",
        "id": board.id,
        "visibility": board.visibility,
        "postingPolicy": board.posting_policy,
    }
    assert_can(viewer, Abilities.DISCUSSION_READ, board_res, conn)
    rows = conn.execute(
        select(replies)
        .where(replies.c.discussion_id == discussion_id)
        .order_by(replies.c.created_at)
    ).all()
    author_ids = {r.author_id for r in rows}
    author_map: dict[int, dict] = {}
    if author_ids:
        for a in conn.execute(select(users).where(users.c.id.in_(author_ids))).all():
            author_map[a.id] = dict(a._mapping)
    items = []
    for r in rows:
        row = dict(r._mapping)
        deleted = row["deleted_at"] is not None
        author = author_map.get(row["author_id"])
        author_dto = (
            author
            if author
            else {"id": row["author_id"], "username": "", "display_name": "", "discriminator": None}
        )
        items.append(_reply_dto(row, author_dto, deleted=deleted))
    return {"items": items}


def update_reply(conn: Connection, actor, reply_id: int, body_markdown: str, body_format: str = "markdown") -> dict:
    row = conn.execute(select(replies).where(replies.c.id == reply_id)).first()
    if row is None:
        raise not_found("Reply not found")
    rowd = dict(row._mapping)
    res = {
        "type": "reply",
        "id": reply_id,
        "authorId": rowd["author_id"],
        "discussionId": rowd["discussion_id"],
    }
    assert_can(actor, Abilities.REPLY_UPDATE, res, conn)
    _now = now_ms()
    conn.execute(
        update(replies)
        .where(replies.c.id == reply_id)
        .values(body_md=body_markdown, body_html=render_body(body_markdown, body_format), body_format=body_format, updated_at=_now)
    )
    updated = dict(conn.execute(select(replies).where(replies.c.id == reply_id)).first()._mapping)
    author = dict(conn.execute(select(users).where(users.c.id == updated["author_id"])).first()._mapping)
    return _reply_dto(updated, author)


def delete_reply(conn: Connection, actor, reply_id: int, reason: str | None) -> None:
    row = conn.execute(select(replies).where(replies.c.id == reply_id)).first()
    if row is None:
        raise not_found("Reply not found")
    rowd = dict(row._mapping)
    res = {
        "type": "reply",
        "id": reply_id,
        "authorId": rowd["author_id"],
        "discussionId": rowd["discussion_id"],
    }
    assert_can(actor, Abilities.REPLY_DELETE, res, conn)
    if rowd["deleted_at"] is not None:
        return  # 已软删，幂等：不重复递减 reply_count
    _now = now_ms()
    conn.execute(
        update(replies)
        .where(replies.c.id == reply_id)
        .values(deleted_at=_now, deleted_by=actor.id if actor else None, deletion_reason=reason, updated_at=_now)
    )
    conn.execute(
        update(discussions)
        .where(discussions.c.id == rowd["discussion_id"])
        .values(reply_count=discussions.c.reply_count - 1)
    )


# ---------------------------------------------------------------- save/follow/pin/lock


def save(conn: Connection, actor, discussion_id: int) -> None:
    if actor is None:
        raise internal_error()
    existing = conn.execute(
        select(discussion_saves.c.discussion_id).where(
            (discussion_saves.c.user_id == actor.id) & (discussion_saves.c.discussion_id == discussion_id)
        )
    ).first()
    if existing:
        return
    conn.execute(
        discussion_saves.insert().values(user_id=actor.id, discussion_id=discussion_id, created_at=now_ms())
    )
    conn.execute(
        update(discussions)
        .where(discussions.c.id == discussion_id)
        .values(save_count=discussions.c.save_count + 1)
    )
    emit_event(
        conn,
        "discussion.saved",
        aggregate_type="discussion",
        aggregate_id=str(discussion_id),
        payload={"discussionId": discussion_id, "userId": actor.id},
    )


def unsave(conn: Connection, actor, discussion_id: int) -> None:
    if actor is None:
        raise internal_error()
    existing = conn.execute(
        select(discussion_saves.c.discussion_id).where(
            (discussion_saves.c.user_id == actor.id) & (discussion_saves.c.discussion_id == discussion_id)
        )
    ).first()
    if not existing:
        return
    conn.execute(
        discussion_saves.delete().where(
            (discussion_saves.c.user_id == actor.id) & (discussion_saves.c.discussion_id == discussion_id)
        )
    )
    conn.execute(
        update(discussions)
        .where(discussions.c.id == discussion_id)
        .values(save_count=func.max(discussions.c.save_count - 1, 0))
    )


def follow(conn: Connection, actor, discussion_id: int) -> None:
    if actor is None:
        raise internal_error()
    existing = conn.execute(
        select(discussion_follows.c.discussion_id).where(
            (discussion_follows.c.user_id == actor.id) & (discussion_follows.c.discussion_id == discussion_id)
        )
    ).first()
    if existing:
        return
    conn.execute(
        discussion_follows.insert().values(
            user_id=actor.id, discussion_id=discussion_id, created_at=now_ms()
        )
    )
    emit_event(
        conn,
        "discussion.followed",
        aggregate_type="discussion",
        aggregate_id=str(discussion_id),
        payload={"discussionId": discussion_id, "userId": actor.id},
    )


def unfollow(conn: Connection, actor, discussion_id: int) -> None:
    if actor is None:
        raise internal_error()
    conn.execute(
        discussion_follows.delete().where(
            (discussion_follows.c.user_id == actor.id) & (discussion_follows.c.discussion_id == discussion_id)
        )
    )


def _toggle(conn: Connection, actor, discussion_id: int, field: str) -> None:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d["author_id"],
        "boardId": d["board_id"],
        "isLocked": d["is_locked"],
        "deletedAt": d["deleted_at"],
    }
    ability = Abilities.DISCUSSION_PIN if field == "is_pinned" else Abilities.DISCUSSION_LOCK
    assert_can(actor, ability, res, conn)
    new_val = 0 if d[field] == 1 else 1
    if field == "is_pinned" and new_val == 1:
        # 每分区置顶上限 5 个
        # Per-board pin limit of 5
        pinned_count = (
            conn.execute(
                select(func.count())
                .select_from(discussions)
                .where(
                    (discussions.c.board_id == d["board_id"])
                    & (discussions.c.is_pinned == 1)
                    & (discussions.c.deleted_at.is_(None))
                )
            ).scalar()
            or 0
        )
        if pinned_count >= 5:
            raise conflict("This board already has 5 pinned discussions")
    conn.execute(update(discussions).where(discussions.c.id == discussion_id).values(**{field: new_val}))


def pin(conn: Connection, actor, discussion_id: int) -> None:
    _toggle(conn, actor, discussion_id, "is_pinned")


def lock(conn: Connection, actor, discussion_id: int) -> None:
    _toggle(conn, actor, discussion_id, "is_locked")


# ---------------------------------------------------------------- user feeds


def list_by_author(conn: Connection, viewer, author_id: int, opts: dict) -> dict:
    limit = min(opts.get("limit") or 20, 50)
    visible = visible_board_ids(conn, viewer)
    conds = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
        discussions.c.author_id == author_id,
    ]
    cur = _cursor_cond(opts.get("cursor"))
    if cur is not None:
        conds.append(cur)
    rows = _rows_for(conn, conds)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last['lastActivityAt']}_{last['id']}"
    return {"items": items, "nextCursor": next_cursor}


def list_saved(conn: Connection, viewer, owner_id: int, opts: dict) -> dict:
    if viewer is None or viewer.id != owner_id:
        raise forbidden("Saved discussions are private")
    limit = min(opts.get("limit") or 20, 50)
    save_ids = [
        r[0]
        for r in conn.execute(
            select(discussion_saves.c.discussion_id).where(discussion_saves.c.user_id == owner_id)
        ).all()
    ]
    if not save_ids:
        return {"items": [], "nextCursor": None}
    visible = visible_board_ids(conn, viewer)
    conds = [
        discussions.c.deleted_at.is_(None),
        discussions.c.id.in_(save_ids),
        discussions.c.board_id.in_(visible),
    ]
    cur = _cursor_cond(opts.get("cursor"))
    if cur is not None:
        conds.append(cur)
    rows = _rows_for(conn, conds)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = f"{last['lastActivityAt']}_{last['id']}"
    return {"items": items, "nextCursor": next_cursor}


def list_replies_by_author(conn: Connection, viewer, author_id: int, opts: dict) -> dict:
    limit = min(opts.get("limit") or 20, 50)
    visible = visible_board_ids(conn, viewer)
    disc_ids = [
        r[0]
        for r in conn.execute(
            select(discussions.c.id).where(
                discussions.c.deleted_at.is_(None) & discussions.c.board_id.in_(visible)
            )
        ).all()
    ]
    if not disc_ids:
        return {"items": [], "nextCursor": None}
    conds = [
        replies.c.author_id == author_id,
        replies.c.deleted_at.is_(None),
        replies.c.discussion_id.in_(disc_ids),
    ]
    cursor = opts.get("cursor")
    if cursor:
        try:
            cid = int(cursor)
        except ValueError:
            cid = None
        if cid is not None:
            conds.append(replies.c.id < cid)
    cols = [
        replies.c.id,
        replies.c.discussion_id,
        replies.c.parent_reply_id,
        replies.c.body_md,
        replies.c.body_html,
        replies.c.body_format,
        replies.c.created_at,
        replies.c.updated_at,
        replies.c.author_id,
    ]
    rows = conn.execute(
        select(*cols).where(and_(*conds)).order_by(replies.c.id.desc()).limit(limit + 1)
    ).all()
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    d_ids = {r.discussion_id for r in page}
    a_ids = {r.author_id for r in page}
    d_map: dict[int, dict] = {}
    a_map: dict[int, dict] = {}
    if d_ids:
        for row in conn.execute(select(discussions.c.id, discussions.c.title).where(discussions.c.id.in_(d_ids))).all():
            d_map[row.id] = dict(row._mapping)
    if a_ids:
        for row in conn.execute(select(users).where(users.c.id.in_(a_ids))).all():
            a_map[row.id] = dict(row._mapping)
    items = []
    for r in page:
        rowd = {
            "id": r.id,
            "discussion_id": r.discussion_id,
            "parent_reply_id": r.parent_reply_id,
            "body_md": r.body_md,
            "body_html": r.body_html,
            "body_format": r.body_format,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
            "author_id": r.author_id,
            "deleted_at": None,
        }
        author = a_map.get(r.author_id)
        author_dto = (
            author
            if author
            else {"id": r.author_id, "username": "", "display_name": "", "discriminator": None}
        )
        item = _reply_dto(rowd, author_dto)
        item["discussionTitle"] = (d_map.get(r.discussion_id) or {}).get("title") or ""
        items.append(item)
    next_cursor = None
    if has_more and items:
        next_cursor = str(items[-1]["id"])
    return {"items": items, "nextCursor": next_cursor}
