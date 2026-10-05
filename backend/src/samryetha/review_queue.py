"""人工审核队列：列表 / 追认 / 推翻 / 留存库。

流程（与 `automod.finalize_pending` 配套）：

```
机器判定 review/block ──► 内容压住(pending) + hold_until
     │
     ├─ 窗口内版主定案 ───────────► published_by_human / blocked（定案）
     └─ 窗口超时无人定案 ──► AI 复审 + 先行放行(published_by_ai)
                                  │
                                  └─ 人工事后追认（维持）或推翻（改为 blocked）
```

两条不变量：

1. **队列状态与内容可见性必须一起变**（`moderation_status`），不是只改队列状态——
   否则队列显示"已封禁"而内容仍然可见，是最难排查的一类 bug。
2. **每次人的决定都写 audit + moderation_actions**：公测期一定有人问"我的帖子为什么
   被删"，能追到是谁、什么时候、依据什么判的，才谈得上申诉。

**管理员留存库**：审核失败（`resolution = blocked`）的内容正文全部留存，只有管理员
可访问（`routers/review_queue.py` 用 `require_admin` 而非 `require_moderator`）。
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.engine import Connection

from .automod import (
    RESOLUTION_BLOCKED,
    RESOLUTION_BLOCKED_BY_MACHINE,
    BLOCKED_RESOLUTIONS,
    RESOLUTION_PUBLISHED_BY_AI,
    RESOLUTION_PUBLISHED_BY_HUMAN,
    apply_review_state,
    load_content,
    queue_counts,
)
from .db import now_ms
from .errors import bad_request, forbidden, not_found
from .schema import moderation_actions, moderation_queue, replies, users

VALID_STATES = ("pending", "approved", "rejected")
VALID_RESOLUTIONS = (
    RESOLUTION_PUBLISHED_BY_AI,
    RESOLUTION_PUBLISHED_BY_HUMAN,
    RESOLUTION_BLOCKED,
    RESOLUTION_BLOCKED_BY_MACHINE,
)
# 「内容不予公开」的处置集合从 automod 引入，保持单一真源。
CONTENT_TYPES = ("discussion", "reply", "profile", "message", "attachment")


def _author_map(conn: Connection, author_ids: Iterable[int]) -> dict[int, dict]:
    ids = {author_id for author_id in author_ids if author_id is not None}
    if not ids:
        return {}
    rows = conn.execute(
        select(users.c.id, users.c.username, users.c.display_name).where(users.c.id.in_(ids))
    ).all()
    return {row.id: {"id": row.id, "username": row.username, "displayName": row.display_name} for row in rows}


def _content_href(conn: Connection, content_type: str, content_id: int) -> str | None:
    """让版主能跳到原文看上下文；附件/资料没有合适的页面锚点，返回 None。

    **reply 的 content_id 是回复 id，不是帖子 id**，两张表的主键互相独立。直接拼
    `/d/{content_id}` 会打开无关帖子或 404（见 PR #70 审查意见 #16），所以要先查出
    它所属的 discussion_id，并带上回复锚点让前端滚到那一条。
    """
    if content_type == "discussion":
        return f"/d/{content_id}"
    if content_type == "reply":
        parent = conn.execute(
            select(replies.c.discussion_id).where(replies.c.id == content_id)
        ).first()
        if parent is None:
            return None
        return f"/d/{parent[0]}#reply-{content_id}"
    return None


def _signals(row: dict) -> list:
    try:
        signals = json.loads(row.get("signals") or "[]")
    except ValueError:
        signals = []
    return signals if isinstance(signals, list) else []


def _recheck(row: dict) -> dict | None:
    raw = row.get("recheck") or ""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _item(conn: Connection, row: dict, author: dict | None, reviewer: dict | None, *, viewer=None) -> dict[str, Any]:
    content_id = row["content_id"]
    resolution = row.get("resolution")
    recheck = _recheck(row)
    # AI 先行处置（还没被人工碰过）时 reviewer_id 为空——队列靠它区分"机器处置"与"人定案"。
    resolved_by_ai = resolution in (RESOLUTION_PUBLISHED_BY_AI, *BLOCKED_RESOLUTIONS) and not row.get("reviewer_id")
    # 审核失败的原文只有管理员能看。队列摘要本身就是正文的一部分，所以对非管理员一并隐去，
    # 只留一个标记让界面提示"仅管理员可见"——否则"仅管理员可访问"会被摘要绕过去。
    excerpt_restricted = resolution in BLOCKED_RESOLUTIONS and not (
        viewer is not None and getattr(viewer, "role", None) == "admin"
    )
    return {
        "id": row["id"],
        "contentType": row["content_type"],
        "contentId": content_id,
        "author": author,
        "excerpt": "" if excerpt_restricted else row["excerpt"],
        "excerptRestricted": excerpt_restricted,
        "decision": row["decision"],
        "score": row["score"],
        "signals": _signals(row),
        "createdAt": row["created_at"],
        "reviewState": row["review_state"],
        "reviewer": reviewer,
        "reviewNote": row["review_note"],
        "reviewedAt": row["reviewed_at"],
        "href": _content_href(conn, row["content_type"], content_id),
        # ---- 确认窗口 + AI 复审落定 ----
        "holdUntil": row.get("hold_until"),
        "resolution": resolution,
        "resolvedAt": row.get("resolved_at"),
        "resolvedByAi": resolved_by_ai,
        "overturned": bool(row.get("overturned")),
        "recheck": recheck,
        # 还在窗口内、一次都没处置过：版主最该先看的。
        "awaitingHuman": row["review_state"] == "pending" and resolution is None,
        # AI 复审放行、已先行公开，等人工追认（维持即认为是终局放行）。
        "needsUphold": resolved_by_ai and resolution == RESOLUTION_PUBLISHED_BY_AI,
        # AI 复审未放行、已先行封禁，等人工放行（推翻）。
        "needsRelease": resolved_by_ai and resolution in BLOCKED_RESOLUTIONS,
    }


def list_queue(
    conn: Connection,
    *,
    viewer=None,
    status: str = "pending",
    content_type: str | None = None,
    resolution: str | None = None,
    cursor: str | int | None = None,
    limit: int = 20,
) -> dict:
    """按可疑度优先（score 高在前），同分按时间倒序。

    ``resolution`` 用来筛"等人工追认的 AI 放行"（`published_by_ai`）或"已封禁"
    （`blocked`）；`awaiting` 是它的别名，表示还在窗口内、谁都没处置的。

    ``viewer`` 只用于一件事：封禁条目的摘要是否对他可见（只有管理员可见）。
    """
    limit = max(1, min(limit, 50))
    conds = [moderation_queue.c.superseded_at.is_(None)]
    if status != "all":
        if status not in VALID_STATES:
            raise bad_request("Invalid review status")
        conds.append(moderation_queue.c.review_state == status)
    if content_type:
        if content_type not in CONTENT_TYPES:
            raise bad_request("Invalid content type")
        conds.append(moderation_queue.c.content_type == content_type)
    if resolution:
        if resolution == "awaiting":
            conds.append(moderation_queue.c.resolution.is_(None))
        elif resolution == "blocked":
            # "已封禁"应当包含机器直封（blocked_by_machine）与人工封禁（blocked），
            # 否则 ?resolution=blocked 会漏掉发布即审核下最大的一类封禁。
            conds.append(moderation_queue.c.resolution.in_(BLOCKED_RESOLUTIONS))
        elif resolution in VALID_RESOLUTIONS:
            conds.append(moderation_queue.c.resolution == resolution)
        else:
            raise bad_request("Invalid resolution")
    if cursor is not None:
        try:
            parts = str(cursor).split(":")
            if len(parts) == 2:
                score, item_id = map(int, parts)
            elif len(parts) == 1:
                # Accept older clients' numeric cursors during rollout.
                item_id = int(parts[0])
                score = conn.execute(select(moderation_queue.c.score).where(moderation_queue.c.id == item_id)).scalar_one_or_none()
            else:
                raise ValueError
            if not 0 <= score <= 100 or item_id < 1:
                raise ValueError
        except (ValueError, TypeError, LookupError):
            raise bad_request("Invalid queue cursor") from None
        conds.append(or_(
            moderation_queue.c.score < score,
            and_(moderation_queue.c.score == score, moderation_queue.c.id < item_id),
        ))

    stmt = select(moderation_queue)
    if conds:
        stmt = stmt.where(and_(*conds))
    rows = conn.execute(
        stmt.order_by(moderation_queue.c.score.desc(), moderation_queue.c.id.desc()).limit(limit + 1)
    ).all()
    page = [dict(row._mapping) for row in rows[:limit]]
    has_more = len(rows) > limit

    authors = _author_map(conn, [row["author_id"] for row in page])
    reviewers = _author_map(conn, [row["reviewer_id"] for row in page if row.get("reviewer_id")])
    items = [
        _item(
            conn,
            row,
            authors.get(row["author_id"]),
            reviewers.get(row["reviewer_id"]) if row.get("reviewer_id") else None,
            viewer=viewer,
        )
        for row in page
    ]
    return {
        "items": items,
        "nextCursor": f"{page[-1]['score']}:{page[-1]['id']}" if has_more and page else None,
        "counts": queue_counts(conn),
    }


def _load_pending_row(conn: Connection, queue_id: int) -> dict:
    row = conn.execute(select(moderation_queue).where(moderation_queue.c.id == queue_id)).first()
    if row is None:
        raise not_found("Queue item not found")
    return dict(row._mapping)


def decide(
    conn: Connection,
    actor,
    queue_id: int,
    *,
    approve: bool,
    note: str | None = None,
) -> dict:
    """人对一条内容定案：放行（含追认/推翻 AI）或封禁（含推翻 AI）。

    `review_state` 只有在人处置后才离开 pending，所以"AI 复审已落定"的内容仍然出现在
    待办队列里——这正是要人工确认的部分。人工改变 AI 先行结论的方向时记 `overturned = 1`：
      - AI 先行封禁 → 人工放行（推翻，重新放行）；
      - AI 先行公开 → 人工封禁（推翻，改为封禁）。
    人工维持 AI 结论（方向一致）不计推翻。留存库据此区分"AI 判错被人纠正"与"人工直接处置"。
    """
    row = _load_pending_row(conn, queue_id)
    if row.get("superseded_at") is not None:
        raise bad_request("This version has been replaced by a newer submission")
    if row["review_state"] != "pending":
        raise bad_request("This item has already been reviewed")
    # 审核失败的内容只有管理员能访问，自然也只有在管理员能处置它——
    # 否则版主会对着一条自己看不到原文的记录做放行/封禁决定。
    if row.get("resolution") in BLOCKED_RESOLUTIONS and getattr(actor, "role", None) != "admin":
        raise forbidden("Only administrators can decide on blocked content")

    prior = row.get("resolution")
    # 只有 AI 落定（reviewer_id 为空）的结论才谈得上被人工推翻。
    prior_by_ai = row.get("reviewer_id") is None
    flipped = prior_by_ai and (
        (approve and prior in BLOCKED_RESOLUTIONS)
        or (not approve and prior == RESOLUTION_PUBLISHED_BY_AI)
    )
    overturned = 1 if flipped else int(row.get("overturned") or 0)
    state = "approved" if approve else "rejected"
    status = "approved" if approve else "rejected"
    resolution = RESOLUTION_PUBLISHED_BY_HUMAN if approve else RESOLUTION_BLOCKED
    _now = now_ms()

    claimed = conn.execute(
        moderation_queue.update()
        .where(
            moderation_queue.c.id == queue_id,
            moderation_queue.c.superseded_at.is_(None),
            moderation_queue.c.review_state == "pending",
        )
        .values(
            review_state=state,
            reviewer_id=actor.id,
            review_note=note,
            reviewed_at=_now,
            resolution=resolution,
            resolved_at=_now,
            overturned=overturned,
        )
    )
    if claimed.rowcount != 1:
        raise bad_request("This version has already been decided or replaced")
    # 回写内容可见性——队列状态与内容状态必须同时变，否则两者会不一致。
    apply_review_state(conn, content_type=row["content_type"], content_id=row["content_id"], status=status)

    if flipped and approve:
        action = "automod.overturn"
        reason = note or "推翻 AI 封禁，重新放行"
    elif flipped:
        action = "automod.overturn"
        reason = note or "推翻 AI 先行放行，改为封禁"
    else:
        action = f"automod.{state}"
        reason = note or ("审核通过" if approve else "审核驳回")
    # 每次决定都留痕：公测期一定有人问"我的帖子为什么被删"，要能追到人、时间和依据。
    conn.execute(
        moderation_actions.insert().values(
            actor_user_id=actor.id,
            action=action,
            target_type=row["content_type"],
            target_id=row["content_id"],
            reason=reason,
            created_at=_now,
        )
    )
    decided = {
        **row,
        "review_state": state,
        "reviewer_id": actor.id,
        "review_note": note,
        "reviewed_at": _now,
        "resolution": resolution,
        "resolved_at": _now,
        "overturned": overturned,
    }
    # 作者是否该收到通知：
    #   - 推翻（方向变了）→ 必须通知，作者要知道结论改了；
    #   - 首次人工定案（prior is None）→ 必须通知；
    #   - **机器直接封禁后由管理员确认**（prior == blocked_by_machine，overturned=False）
    #     → 也必须通知。发布即审核下机器封禁发生在 enqueue 阶段且不发通知，
    #     管理员这道确认就成了作者唯一能收到的处置结果；漏掉他只会看到内容消失。
    should_notify = (
        overturned == 1
        or prior is None
        or (not approve and prior == RESOLUTION_BLOCKED_BY_MACHINE)
    )
    return {
        "ok": True,
        "reviewState": state,
        "resolution": resolution,
        "overturned": bool(overturned),
        "_row": decided,
        "_notify": should_notify,
    }


# ---------------------------------------------------------------- 管理员留存库


def _retained_item(conn: Connection, row: dict) -> dict:
    content = load_content(conn, content_type=row["content_type"], content_id=row["content_id"])
    return {
        "id": row["id"],
        "contentType": row["content_type"],
        "contentId": row["content_id"],
        "authorId": row["author_id"],
        "excerpt": row["excerpt"],
        "title": content.get("title"),
        # 优先用提交时的**快照**：内容表表达的是"现状"，作者/用户之后还能改，改过就再也
        # 拿不到当时送审的那一版了（个人资料是原地更新，最严重——见 PR #70 审查意见 #9）。
        # 本列上线前入队的老记录快照为空，退回读当前内容，至少不是空手。
        "body": row.get("submitted_text") or (content.get("text") or ""),
        "fromSnapshot": bool(row.get("submitted_text")),
        "contentExists": bool(content.get("exists")),
        "decision": row["decision"],
        "score": row["score"],
        "signals": _signals(row),
        "resolution": row.get("resolution"),
        "resolvedAt": row.get("resolved_at"),
        "reviewerId": row.get("reviewer_id"),
        "reviewNote": row.get("review_note"),
        "overturned": bool(row.get("overturned")),
        "recheck": _recheck(row),
        "createdAt": row["created_at"],
        "href": _content_href(conn, row["content_type"], row["content_id"]),
    }


def list_retained(
    conn: Connection,
    *,
    content_type: str | None = None,
    cursor: int | None = None,
    limit: int = 20,
) -> dict:
    """管理员留存库：审核失败内容的完整记录与全文。

    只列 `resolution = blocked`（人工封禁 / 人工推翻 AI）。这些内容在内容表里原样留存，
    从未删除；对外（含版主与作者）不可见，见 `discussions.moderation_visible`。
    """
    limit = max(1, min(limit, 50))
    # 留存库 = 所有「内容不予公开」的处置。漏掉 blocked_by_machine 会让机器直接
    # 封禁的原文不出现在留存库里，"全部留存"就不成立了。
    conds = [moderation_queue.c.resolution.in_(BLOCKED_RESOLUTIONS)]
    if content_type:
        if content_type not in CONTENT_TYPES:
            raise bad_request("Invalid content type")
        conds.append(moderation_queue.c.content_type == content_type)
    if cursor is not None:
        conds.append(moderation_queue.c.id < cursor)

    rows = conn.execute(
        select(moderation_queue)
        .where(and_(*conds))
        .order_by(moderation_queue.c.id.desc())
        .limit(limit + 1)
    ).all()
    page = [dict(row._mapping) for row in rows[:limit]]
    has_more = len(rows) > limit
    authors = _author_map(conn, [row["author_id"] for row in page])
    items = []
    for row in page:
        item = _retained_item(conn, row)
        item["author"] = authors.get(row["author_id"])
        items.append(item)
    total = conn.execute(
        select(func.count())
        .select_from(moderation_queue)
        .where(moderation_queue.c.resolution.in_(BLOCKED_RESOLUTIONS))
    ).scalar_one()
    return {
        "items": items,
        "nextCursor": page[-1]["id"] if has_more and page else None,
        "total": int(total),
    }


def notify_author(conn: Connection, row: dict, *, approved: bool, note: str | None, by_ai: bool = False) -> None:
    """把审核结果告诉作者。

    驳回不通知是最招骂的做法（用户只看到帖子消失）。这里写一条站内通知，
    正文里带上理由，作者至少知道发生了什么、能不能改。

    通知落在原帖/回复上（discussion_id / reply_id），点进去就是自己的内容。

    ``by_ai=True``：结果来自超时后的 AI 复审（先行公开或先行封禁）。文案要说明这是
    **先行**结论，作者知道可以申诉、管理员也可能再看。
    """
    from .notifications import create

    if by_ai and approved:
        body = f"您的内容经 AI 复审后已先行公开。{('说明：' + note) if note else ''}"
    elif by_ai:
        body = f"您的内容经 AI 复审后未通过审核，现已不予公开。{('说明：' + note) if note else ''}"
    elif approved:
        body = "您的内容已通过审核，现已公开。"
    else:
        body = f"您的内容未通过审核。{('原因：' + note) if note else ''}"
    discussion_id = row["content_id"] if row["content_type"] == "discussion" else None
    reply_id = row["content_id"] if row["content_type"] == "reply" else None
    if row["content_type"] == "reply":
        # 回复要挂到它所属的讨论上，前端才能跳到正确页面。
        parent = conn.execute(
            select(replies.c.discussion_id).where(replies.c.id == row["content_id"])
        ).first()
        discussion_id = parent[0] if parent else None
    create(
        conn,
        user_id=row["author_id"],
        type_="moderation",
        discussion_id=discussion_id,
        reply_id=reply_id,
        body=body,
    )
