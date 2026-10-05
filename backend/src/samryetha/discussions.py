"""讨论/回复 service — 镜像 backend/src/discussions/service.ts。

时间戳毫秒 int；ThreadSummary/ReplyDTO/DiscussionDetail 均 camelCase。
帖子流按 created_at 倒序排列；last_reply_at 仅用于展示最新活动时间。
"""

from __future__ import annotations

import re

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection

from .authz import Abilities, assert_can, can
from .boards import get_board_for_authz
from .db import now_ms
from .errors import bad_request, conflict, forbidden, internal_error, not_found, validation_failed
from .automod import CONTENT_DISCUSSION, CONTENT_REPLY, held_status, submit as submit_for_review
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

MAX_REPLY_DEPTH = 8


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


def _append_moderation_cond(conds: list, status_column, author_column, viewer) -> None:
    """把可见性谓词追加进已有的 conds（版主不过滤时是空操作）。"""
    predicate = moderation_visible(status_column, author_column, viewer)
    if predicate is not None:
        conds.append(predicate)


def moderation_visible(status_column, author_column, viewer):
    """SQL 谓词：待审/被驳回的内容对谁可见。返回 None = 不过滤（管理员）。

    可见性（用户要求："所有审核失败的版全部留存，但只有管理员可以访问"）：

    - **管理员**：全可见 —— 留存库与申诉要调取完整记录；
    - **版主**：可见 approved 与 pending（要在原页面看到待审内容才能处置），
      但**看不到 rejected** —— 封禁后的原文不下放给版主；
    - **作者本人**：可见自己的 pending —— 否则用户以为帖子凭空消失，只会反复重发；
      但**不**可见自己的 rejected（封禁成立后正文只有管理员留存，作者收到通知）；
    - **游客/其他成员**：只看得到 approved。
    """
    if viewer is not None and viewer.role == "admin":
        return None
    if viewer is not None and viewer.role == "moderator":
        return status_column != "rejected"
    if viewer is not None:
        return or_(status_column == "approved", and_(status_column == "pending", author_column == viewer.id))
    return status_column == "approved"


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
        # 审核状态：让界面能把"审核中"贴出来。能读到这一行的人本来就已经通过了
        # moderation_visible 过滤（作者看自己的 pending、管理员全都看得到），
        # 所以这里不存在额外泄漏；rejected 对非管理员根本不会出现在结果里。
        "moderationStatus": r.moderation_status or "approved",
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


def _rows_for(conn: Connection, conds, limit: int, sort: str = "date") -> list:
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
        # 列表 DTO 要带审核状态（"审核中"标记），所以这一列必须选出来。
        discussions.c.moderation_status,
    ]
    primary_sort = discussions.c.reply_count if sort == "replies" else discussions.c.created_at
    stmt = (
        select(*cols)
        .where(and_(*conds))
        .order_by(discussions.c.is_pinned.desc(), primary_sort.desc(), discussions.c.id.desc())
        # SQL 层 limit+1 取 has_more（原全表查出再切片，大分区下浪费内存/IO）。
        .limit(limit + 1)
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


def _cursor_cond(cursor: str | None, sort: str = "date"):
    """Discussion 游标：`{isPinned}_{sortValue}_{id}`，与生成处配套。

    排序是 (is_pinned DESC, sortValue DESC, id DESC) 三段式，游标必须带上分区键
    is_pinned，否则跨"置顶/非置顶"边界翻页会丢行或重行。旧式 `{sortValue}_{id}`
    兼容为未置顶分区；空游标不过滤；其余畸形一律 400（与 reply 侧统一）。
    """
    if not cursor:
        return None
    parts = cursor.split("_")
    try:
        if len(parts) == 3:
            pinned = int(parts[0])
            at = int(parts[1])
            cid = int(parts[2])
        elif len(parts) == 2:
            pinned = 0
            at = int(parts[0])
            cid = int(parts[1])
        else:
            raise ValueError("bad cursor segments")
    except (TypeError, ValueError):
        raise bad_request("Invalid cursor")
    if pinned not in (0, 1) or at < 0 or cid < 1:
        raise bad_request("Invalid cursor")
    primary_sort = discussions.c.reply_count if sort == "replies" else discussions.c.created_at
    if len(parts) == 3:
        return or_(
            (discussions.c.is_pinned < pinned),
            (discussions.c.is_pinned == pinned) & (primary_sort < at),
            (discussions.c.is_pinned == pinned) & (primary_sort == at) & (discussions.c.id < cid),
        )
    return or_((primary_sort < at), (primary_sort == at) & (discussions.c.id < cid))


def _next_cursor(last: dict, sort: str = "date") -> str:
    """与 _cursor_cond 三段式配套的游标生成（两处必须同改）。"""
    sort_value = last["replyCount"] if sort == "replies" else last["createdAt"]
    return f"{1 if last['isPinned'] else 0}_{sort_value}_{last['id']}"


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
        "moderationStatus": d.get("moderation_status") or "approved",
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
    sort = opts.get("sort") or "date"
    visible = visible_board_ids(conn, viewer)
    conds = [discussions.c.deleted_at.is_(None), discussions.c.board_id.in_(visible)]
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    if opts.get("boardSlug"):
        board = get_board_for_authz(conn, opts["boardSlug"])
        if board is None:
            raise not_found("Board not found")
        conds.append(discussions.c.board_id == board["id"])
    cur = _cursor_cond(opts.get("cursor"), sort)
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

    rows = _rows_for(conn, conds, limit, sort)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    next_cursor = _next_cursor(items[-1], sort) if has_more and items else None
    # announcement 分区：没有手动置顶时自动置顶最新公告
    # Announcement board: auto-pin the latest announcement when nothing is manually pinned
    if opts.get("boardSlug") == "announcements" and items and not any(it["isPinned"] for it in items):
        items[0]["isPinned"] = True
    return {"items": items, "nextCursor": next_cursor}


def get_discussion(conn: Connection, viewer, discussion_id: int) -> dict:
    d = get_discussion_row(conn, discussion_id)
    if d is None or d["deleted_at"]:
        raise not_found("Discussion not found")
    # 待审内容：作者与版主可见（作者要在自己帖子里看到"审核中"），其他人 404。
    # 用 404 而不是 403：403 等于告诉外人"这里有个被审的帖子"。
    status = d.get("moderation_status") or "approved"
    if status != "approved":
        # 审核失败的原文只有管理员能看（用户要求）；版主只看得到待审的 pending。
        privileged = viewer is not None and (
            viewer.role == "admin"
            or (viewer.role == "moderator" and status == "pending")
            or (status == "pending" and viewer.id == d["author_id"])
        )
        if not privileged:
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


def _derive_title(body: str) -> str:
    # 无标题时用正文首行/首句，再将句内空白压缩并截断到 100 字符。
    # No title: use the body's first line/sentence, then collapse its whitespace and truncate to 100 chars.
    text = (body or "").strip()
    if not text:
        return "Untitled"
    m = re.search(r"[。！？!?.\r\n]", text)
    first = text[: m.start()] if m else text
    first = re.sub(r"\s+", " ", first)
    first = first.strip().strip("。！？!?.;；,，、")
    fallback = re.sub(r"\s+", " ", text).strip().strip("。！？!?.;；,，、")
    return (first or fallback)[:100] or "Untitled"


def _board_is_public(conn: Connection, board_id: int) -> bool:
    """露骨描写规则的适用前提：内容是否落在公开版块。"""
    row = conn.execute(
        select(boards.c.visibility).where(boards.c.id == board_id)
    ).first()
    return bool(row) and row[0] == "public"


def _apply_moderation(
    conn: Connection,
    settings,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    text: str,
    title: str | None = None,
    is_public_board: bool = True,
) -> None:
    """自动审核一条刚落库的内容，并回写它的可见性。

    先 INSERT 再判定是刻意的：队列用 (content_type, content_id) 指回内容，
    所以必须已有 id。判定结果只改 moderation_status（默认 approved），
    因此关闭审核时这里等于一次空操作。

    查重需要该作者近期内容：这里取最近的帖子/回复正文，命中即加权。
    """
    if settings is None or not getattr(settings, "automod_enabled", False):
        return
    recent: list[str] = []
    if content_type == CONTENT_DISCUSSION:
        recent = [r[0] for r in conn.execute(
            select(discussions.c.body_md)
            .where(discussions.c.author_id == author_id, discussions.c.id != content_id)
            .order_by(discussions.c.id.desc()).limit(5)
        ).all() if r[0]]
    elif content_type == CONTENT_REPLY:
        recent = [r[0] for r in conn.execute(
            select(replies.c.body_md)
            .where(replies.c.author_id == author_id, replies.c.id != content_id)
            .order_by(replies.c.id.desc()).limit(5)
        ).all() if r[0]]

    verdict = submit_for_review(
        conn,
        settings,
        content_type=content_type,
        content_id=content_id,
        author_id=author_id,
        text=text,
        title=title,
        context="post" if content_type == CONTENT_DISCUSSION else "reply",
        recent_bodies=recent,
        is_public_board=is_public_board,
    )
    if verdict.decision == "allow":
        return
    # 机器只标记：review/block 都先压成 pending，等确认窗口 / AI 复审 / 人工定案。
    # AUTOMOD_HOLD_PENDING=false（先发后审）时只入队，不改可见性。
    from .automod import held_status

    status = held_status(settings, verdict)
    if status is None:
        return
    table = discussions if content_type == CONTENT_DISCUSSION else replies
    conn.execute(table.update().where(table.c.id == content_id).values(moderation_status=status))


def moderation_text(title: str | None, body: str) -> str:
    """拼出送审文本：**标题一定要参与判定**。

    只审正文是个真实的绕过口子：标题单独放违规词、正文写正常内容，就会整体放行
    （见 PR #70 审查意见 #3）。
    """
    parts = [part for part in (title, body) if part]
    return "\n".join(parts)


def _remoderate_edit(
    conn: Connection,
    settings,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    title: str | None,
    text: str,
) -> None:
    """编辑后再过一次审核：**改文不能沿用旧结论**。

    没有这一步时，先发一条正常内容拿到 approved，再 PATCH 成违禁正文，内容会带着
    approved 留在公开面（见 PR #70 审查意见 #2）。

    行为与首次提交一致：判 allow 就维持可见（并把上一版残留的待审痕迹清掉），
    否则压回 pending 重新排队、重新计时。判定为 allow 时不需要入队——队列里若还留着
    上一版的待审记录，要把它收口，否则版主会看到一条已经不该看的待审项。
    """
    if settings is None or not getattr(settings, "automod_enabled", False):
        return
    from .automod import CONTENT_DISCUSSION, CONTENT_PROFILE, held_status, submit as submit_for_review

    is_discussion = content_type == CONTENT_DISCUSSION
    is_public_board = True
    if is_discussion:
        board_row = conn.execute(
            select(boards.c.visibility)
            .join(discussions, discussions.c.board_id == boards.c.id)
            .where(discussions.c.id == content_id)
        ).first()
        is_public_board = bool(board_row) and board_row[0] == "public"

    verdict = submit_for_review(
        conn,
        settings,
        content_type=content_type,
        content_id=content_id,
        author_id=author_id,
        text=text,
        title=title,
        context="post" if is_discussion else "reply",
        is_public_board=is_public_board,
    )
    status = held_status(settings, verdict)
    if status is None:
        # 判定放行：清掉可能残留的待审/封禁状态，并把队列里这一版收口。
        _settle_queue_as_approved(conn, content_type=content_type, content_id=content_id)
    table = discussions if is_discussion else replies
    conn.execute(
        table.update()
        .where(table.c.id == content_id)
        .values(moderation_status=status or "approved")
    )


def _settle_queue_as_approved(conn: Connection, *, content_type: str, content_id: int) -> None:
    """An allowed edit retires old queue entries without deleting their evidence."""
    from .automod import supersede_content

    supersede_content(conn, content_type=content_type, content_id=content_id)


def create_discussion(conn: Connection, actor, data: dict, settings=None) -> dict:
    if actor is None:
        raise internal_error()
    title = (data.get("title") or "").strip()
    if not title:
        # 未提供标题：用正文第一句话自动生成
        # No title provided: auto-derive from the body's first sentence
        title = _derive_title(data["bodyMarkdown"])
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
    # 自动审核：先落库拿到 id，再判定并回写可见性（入队需要内容 id）。
    _apply_moderation(
        conn,
        settings,
        content_type=CONTENT_DISCUSSION,
        content_id=disc_id,
        author_id=actor.id,
        title=title,
        text=data["bodyMarkdown"],
        # 露骨描写只在公开版块算违规；隐藏版（members/private）内允许。
        is_public_board=board.get("visibility") == "public",
    )
    att_ids = data.get("attachmentIds") or []
    if att_ids:
        unique_att_ids = set(att_ids)
        result = conn.execute(
            update(attachments)
            .where(
                attachments.c.id.in_(unique_att_ids)
                & (attachments.c.uploader_id == actor.id)
                & attachments.c.discussion_id.is_(None)
                & (attachments.c.state == "uploaded")
            )
            .values(discussion_id=disc_id, state="attached")
        )
        if result.rowcount != len(unique_att_ids):
            raise validation_failed([{"field": "attachmentIds", "message": "One or more attachments are unavailable", "code": "custom"}])
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
    # 自己刚发的内容一定要能拿到（否则界面会在"发布成功"后立刻查不到，看着像失败）。
    # 被驳回时 get_discussion 会 404，所以这里对作者放宽：拿 row 直接拼 DTO。
    row = get_discussion_row(conn, disc_id)
    if row is not None and (row.get("moderation_status") or "approved") != "approved":
        return load_detail(conn, actor, row)
    return get_discussion(conn, actor, disc_id)


def update_discussion(conn: Connection, actor, discussion_id: int, patch: dict, settings=None) -> dict:
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
        title = (patch.get("title") or "").strip()
        if not title:
            # 编辑时清空标题：用（本次或已有）正文第一句话自动生成
            # Cleared title on edit: auto-derive from the (patched or existing) body's first sentence
            body_for_title = patch.get("bodyMarkdown") or d["body_md"]
            title = _derive_title(body_for_title)
        values["title"] = title
    if "bodyMarkdown" in patch:
        body_format = patch.get("bodyFormat") or "markdown"
        values["body_md"] = patch["bodyMarkdown"]
        values["body_html"] = render_body(patch["bodyMarkdown"], body_format)
        values["body_format"] = body_format
    conn.execute(update(discussions).where(discussions.c.id == discussion_id).values(**values))
    # 标题或正文改了就要重新过审。不重审的话，先发正常内容拿到 approved、再改成违禁文本，
    # 内容会带着旧结论留在公开面（见 PR #70 审查意见 #2）。
    if "title" in patch or "bodyMarkdown" in patch:
        _remoderate_edit(
            conn,
            settings,
            content_type=CONTENT_DISCUSSION,
            content_id=discussion_id,
            author_id=d["author_id"],
            title=values.get("title", d["title"]),
            text=values.get("body_md", d["body_md"]) or "",
        )
        # 重新送审后，读回的可见性要按新状态判断：作者不该在编辑成功的那一刻
        # 拿到一条已经变回待审的内容的完整 DTO（load_own_after_write 会处理）。
        row = get_discussion_row(conn, discussion_id)
        if row is not None and (row.get("moderation_status") or "approved") != "approved":
            return load_detail(conn, actor, row)
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
    conn.execute(
        update(attachments).where(attachments.c.discussion_id == discussion_id).values(state="orphaned")
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
        # 同 _build_thread：能读到这条回复的人已经过了 moderation_visible 过滤。
        "moderationStatus": row.get("moderation_status") or "approved",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_reply(conn: Connection, actor, discussion_id: int, data: dict, settings=None) -> dict:
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
            select(replies.c.id, replies.c.parent_reply_id).where(
                (replies.c.id == parent_reply_id)
                & (replies.c.discussion_id == discussion_id)
                & (replies.c.deleted_at.is_(None))
            )
        ).first()
        if parent is None:
            raise not_found("Parent reply not found")
        depth = 1
        ancestor_id = parent.parent_reply_id
        while ancestor_id is not None:
            depth += 1
            if depth >= MAX_REPLY_DEPTH:
                raise validation_failed([{
                    "field": "parentReplyId",
                    "message": f"Replies cannot be nested deeper than {MAX_REPLY_DEPTH} levels",
                    "code": "max_depth",
                }])
            ancestor = conn.execute(
                select(replies.c.parent_reply_id).where(
                    (replies.c.id == ancestor_id) & (replies.c.discussion_id == discussion_id)
                )
            ).first()
            if ancestor is None:
                raise not_found("Parent reply not found")
            ancestor_id = ancestor.parent_reply_id
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
    _apply_moderation(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        content_id=reply_id,
        author_id=actor.id,
        text=data["bodyMarkdown"],
        is_public_board=_board_is_public(conn, d["board_id"]),
    )
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


def _assert_parent_discussion_visible(discussion_row: dict, viewer) -> None:
    """父帖不可见时，按 404 处理它的回复/衍生数据。

    规则与 `get_discussion` 完全一致（404 而不是 403，避免告诉外人"这里有个被审的东西"）：
      - 管理员：全可见；
      - 版主：可见 approved 与 pending，看不到 rejected；
      - 作者：可见自己的 pending；
      - 其他人：只看得到 approved。
    """
    status = discussion_row.get("moderation_status") or "approved"
    if status == "approved":
        return
    if viewer is not None and viewer.role == "admin":
        return
    if viewer is not None and viewer.role == "moderator" and status == "pending":
        return
    if viewer is not None and status == "pending" and viewer.id == discussion_row.get("author_id"):
        return
    raise not_found("Discussion not found")


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
    # 父帖不可见时，它的回复也不可见。缺了这一步：父帖被封禁后详情返回 404，
    # 但回复接口照旧把内容吐出来（见 PR #70 审查意见 #7）。
    _assert_parent_discussion_visible(d, viewer)
    reply_conds = [replies.c.discussion_id == discussion_id]
    _append_moderation_cond(reply_conds, replies.c.moderation_status, replies.c.author_id, viewer)
    rows = conn.execute(select(replies).where(and_(*reply_conds)).order_by(replies.c.created_at)).all()
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


def update_reply(
    conn: Connection, actor, reply_id: int, body_markdown: str, body_format: str = "markdown", settings=None
) -> dict:
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
    # 编辑要重新过审，理由同 update_discussion。
    _remoderate_edit(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        content_id=reply_id,
        author_id=rowd["author_id"],
        title=None,
        text=body_markdown,
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
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    cur = _cursor_cond(opts.get("cursor"))
    if cur is not None:
        conds.append(cur)
    rows = _rows_for(conn, conds, limit)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _next_cursor(last)
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
    # 收藏是可见性的一个出口：漏了这条，被封禁的帖子还能从"我的收藏"里读到标题和摘要。
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    cur = _cursor_cond(opts.get("cursor"))
    if cur is not None:
        conds.append(cur)
    rows = _rows_for(conn, conds, limit)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    items = to_threads(conn, page)
    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = _next_cursor(last)
    return {"items": items, "nextCursor": next_cursor}


def list_replies_by_author(conn: Connection, viewer, author_id: int, opts: dict) -> dict:
    limit = min(opts.get("limit") or 20, 50)
    # 游标先验（畸形 400）：放在空集提前返回之前，语义与 discussion 侧统一。
    cursor = opts.get("cursor")
    cursor_id: int | None = None
    if cursor:
        try:
            cursor_id = int(cursor)
        except (TypeError, ValueError):
            raise bad_request("Invalid cursor")
        if cursor_id < 1:
            raise bad_request("Invalid cursor")
    visible = visible_board_ids(conn, viewer)
    disc_conds = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
    ]
    # 父帖不可见的，它的回复也不该出现在"某人的回复"列表里——否则会连父帖标题一起泄漏
    # （见 PR #70 审查意见 #7）。
    _append_moderation_cond(disc_conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    disc_ids = [r[0] for r in conn.execute(select(discussions.c.id).where(and_(*disc_conds))).all()]
    if not disc_ids:
        return {"items": [], "nextCursor": None}
    conds = [
        replies.c.author_id == author_id,
        replies.c.deleted_at.is_(None),
        replies.c.discussion_id.in_(disc_ids),
    ]
    _append_moderation_cond(conds, replies.c.moderation_status, replies.c.author_id, viewer)
    if cursor_id is not None:
        conds.append(replies.c.id < cursor_id)
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
