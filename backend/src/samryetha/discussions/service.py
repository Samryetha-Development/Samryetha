"""讨论/回复 service — 镜像 backend/src/discussions/service.ts。

时间戳毫秒 int；ThreadSummary/ReplyDTO/DiscussionDetail 均 camelCase。
帖子流按 created_at 倒序排列；last_reply_at 仅用于展示最新活动时间。
"""

from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement

from ..authz import Abilities, Actor, assert_can, can
from ..boards import get_board_for_authz
from ..config import Settings
from ..db import now_ms
from ..events.content_events import publish_content
from .. import drafts
from ..errors import bad_request, conflict, forbidden, internal_error, not_found, validation_failed
from ..automod import (
    CONTENT_DISCUSSION,
    CONTENT_REPLY,
    assert_author_current,
    held_status,
    prepare_submission,
    submit as submit_for_review,
)
from ..adapters.markdown import render_body
from ..events.outbox import emit_event
from ..notifications.models import MentionCreatedPayload
from ..automod.rules import Verdict
from .models import (
    AuthorResponse,
    AuthoredReplyListResponse,
    AuthoredReplyResponse,
    BodyFormat,
    BoardRefResponse,
    CreateDiscussionBody,
    CreateReplyBody,
    DiscussionDetailResponse,
    DiscussionFeed,
    DiscussionFeedQuery,
    DiscussionListResponse,
    DiscussionSort,
    DiscussionPermissionsResponse,
    ModerationStatus,
    LegacyDiscussionFeedOptions,
    LegacyPageOptions,
    PageQuery,
    ReplyListResponse,
    ReplyResponse,
    ThreadSummaryResponse,
    UpdateDiscussionBody,
)
from .repository import (
    BoardRecord,
    DiscussionFeedRecord,
    DiscussionRecord,
    ReplyRecord,
    UserSummaryRecord,
    board_records,
    discussion_titles,
    get_board,
    get_discussion as repository_get_discussion,
    get_reply as repository_get_reply,
    list_discussion_replies,
    list_feed,
    list_reply_feed,
    user_summaries,
)
from ..ids import DiscussionID, ReplyID, UserID
from ..schema import (
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
from ..users import make_handle

MAX_REPLY_DEPTH = 8


def _required_inserted_id(value: object, entity: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{entity} insert did not return an integer primary key")
    return value


def preview(md: str) -> str:
    flat = re.sub(r"\s+", " ", md or "").strip()
    return flat if len(flat) <= 160 else flat[:160] + "…"


def to_author(user: UserSummaryRecord) -> AuthorResponse:
    return AuthorResponse(
        id=user.id,
        username=user.username,
        handle=make_handle(user.username, user.discriminator),
        display_name=user.display_name,
    )


# ---------------------------------------------------------------- visibility


def _append_moderation_cond(
    conds: list[ColumnElement[bool]],
    status_column: ColumnElement[str],
    author_column: ColumnElement[int],
    viewer: Actor | None,
) -> None:
    """把可见性谓词追加进已有的 conds（版主不过滤时是空操作）。"""
    predicate = moderation_visible(status_column, author_column, viewer)
    if predicate is not None:
        conds.append(predicate)


def moderation_visible(
    status_column: ColumnElement[str],
    author_column: ColumnElement[int],
    viewer: Actor | None,
) -> ColumnElement[bool] | None:
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


def visible_board_ids(conn: Connection, viewer: Actor | None) -> list[int]:
    all_rows = conn.execute(select(boards.c.id, boards.c.visibility).where(boards.c.deleted_at.is_(None))).all()
    all_ids = [r.id for r in all_rows]
    if viewer is not None and viewer.role == "admin":
        return all_ids
    member_ids: set[int] = set()
    if viewer is not None:
        rows = conn.execute(select(board_members.c.board_id).where(board_members.c.user_id == viewer.id)).all()
        member_ids = {r[0] for r in rows}
    return [r.id for r in all_rows if r.visibility == "public" or r.id in member_ids]


# ---------------------------------------------------------------- helpers


def get_discussion_row(conn: Connection, discussion_id: int) -> DiscussionRecord | None:
    return repository_get_discussion(conn, DiscussionID(discussion_id))


def _build_thread(
    activity: int,
    board: BoardRecord | None,
    author: UserSummaryRecord | None,
    row: DiscussionFeedRecord,
) -> ThreadSummaryResponse:
    return ThreadSummaryResponse(
        id=row.id,
        title=row.title,
        preview=preview(row.body_md),
        board=BoardRefResponse(
            id=row.board_id,
            slug=board.slug if board else "",
            name=board.name if board else "",
        ),
        author=(
            to_author(author)
            if author is not None
            else AuthorResponse(id=row.author_id, username="", handle="", display_name="")
        ),
        reply_count=row.reply_count,
        is_pinned=row.is_pinned,
        is_locked=row.is_locked,
        # 审核状态：让界面能把"审核中"贴出来。能读到这一行的人本来就已经通过了
        # moderation_visible 过滤（作者看自己的 pending、管理员全都看得到），
        # 所以这里不存在额外泄漏；rejected 对非管理员根本不会出现在结果里。
        moderation_status=row.moderation_status,
        created_at=row.created_at,
        last_activity_at=activity,
    )


def to_threads(conn: Connection, rows: list[DiscussionFeedRecord]) -> list[ThreadSummaryResponse]:
    if not rows:
        return []
    board_ids = {r.board_id for r in rows}
    author_ids = {r.author_id for r in rows}
    board_map = board_records(conn, board_ids)
    author_map = user_summaries(conn, author_ids)
    items: list[ThreadSummaryResponse] = []
    for r in rows:
        activity = r.last_reply_at if r.last_reply_at is not None else r.created_at
        items.append(_build_thread(activity, board_map.get(r.board_id), author_map.get(r.author_id), r))
    return items


def _rows_for(
    conn: Connection,
    conds: list[ColumnElement[bool]],
    limit: int,
    sort: str = "date",
) -> list[DiscussionFeedRecord]:
    return list_feed(conn, conds, limit=limit, sort_by_replies=sort == "replies")


_MENTION_PATTERN = re.compile(r"@([a-z0-9_]{3,30})", re.IGNORECASE)


def emit_mentions(
    conn: Connection, *, body: str, author_id: int, discussion_id: int, reply_id: int | None, title: str
) -> None:
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
            payload=MentionCreatedPayload(
                discussion_id=discussion_id,
                reply_id=reply_id,
                author_id=author_id,
                mentioned_user_id=row.id,
            ),
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


def _next_cursor(last: ThreadSummaryResponse, sort: str = "date") -> str:
    """与 _cursor_cond 三段式配套的游标生成（两处必须同改）。"""
    sort_value = last.reply_count if sort == "replies" else last.created_at
    return f"{1 if last.is_pinned else 0}_{sort_value}_{last.id}"


# ---------------------------------------------------------------- detail


def load_detail(
    conn: Connection,
    viewer: Actor | None,
    discussion: DiscussionRecord,
) -> DiscussionDetailResponse:
    board = get_board(conn, discussion.board_id)
    author = user_summaries(conn, [discussion.author_id]).get(discussion.author_id)
    saved = following = None
    if viewer is not None:
        saved = conn.execute(
            select(discussion_saves.c.discussion_id).where(
                (discussion_saves.c.user_id == viewer.id) & (discussion_saves.c.discussion_id == discussion.id)
            )
        ).first()
        following = conn.execute(
            select(discussion_follows.c.discussion_id).where(
                (discussion_follows.c.user_id == viewer.id) & (discussion_follows.c.discussion_id == discussion.id)
            )
        ).first()
    board_response = BoardRefResponse(
        id=discussion.board_id,
        slug=board.slug if board else "",
        name=board.name if board else "",
    )
    author_response = (
        to_author(author)
        if author is not None
        else AuthorResponse(id=discussion.author_id, username="", handle="", display_name="")
    )
    discussion_res = {
        "type": "discussion",
        "id": discussion.id,
        "authorId": discussion.author_id,
        "boardId": discussion.board_id,
        "isLocked": discussion.is_locked,
        "deletedAt": discussion.deleted_at,
    }
    return DiscussionDetailResponse(
        id=discussion.id,
        title=discussion.title,
        preview=preview(discussion.body_md),
        board=board_response,
        author=author_response,
        reply_count=discussion.reply_count,
        save_count=discussion.save_count,
        is_pinned=discussion.is_pinned,
        is_locked=discussion.is_locked,
        body_markdown=discussion.body_md,
        body_html=discussion.body_html,
        body_format=discussion.body_format,
        is_saved=saved is not None,
        is_following=following is not None,
        moderation_status=discussion.moderation_status,
        created_at=discussion.created_at,
        last_activity_at=discussion.last_reply_at or discussion.created_at,
        can=DiscussionPermissionsResponse(
            update=can(viewer, Abilities.DISCUSSION_UPDATE, discussion_res, conn),
            delete=can(viewer, Abilities.DISCUSSION_DELETE, discussion_res, conn),
        ),
    )


# ---------------------------------------------------------------- main list


def list_discussions(
    conn: Connection,
    viewer: Actor | None,
    query: DiscussionFeedQuery | LegacyDiscussionFeedOptions,
) -> DiscussionListResponse:
    if not isinstance(query, DiscussionFeedQuery):
        query = DiscussionFeedQuery(
            cursor=query.get("cursor"),
            limit=query.get("limit", 20),
            feed=DiscussionFeed(query.get("feed", "latest")),
            sort=DiscussionSort(query.get("sort", "date")),
            board_slug=query.get("boardSlug"),
        )
    limit = min(query.limit, 50)
    sort = query.sort.value
    visible = visible_board_ids(conn, viewer)
    conds: list[ColumnElement[bool]] = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
    ]
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    board_slug = query.board_slug
    if board_slug:
        board = get_board_for_authz(conn, board_slug)
        if board is None:
            raise not_found("Board not found")
        conds.append(discussions.c.board_id == board["id"])
    cur = _cursor_cond(query.cursor, sort)
    if cur is not None:
        conds.append(cur)

    if query.feed is DiscussionFeed.Followed:
        if viewer is None:
            return DiscussionListResponse(items=[], next_cursor=None)
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
            return DiscussionListResponse(items=[], next_cursor=None)
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
    if board_slug == "announcements" and items and not any(item.is_pinned for item in items):
        items[0].is_pinned = True
    return DiscussionListResponse(items=items, next_cursor=next_cursor)


def get_discussion(conn: Connection, viewer: Actor | None, discussion_id: int) -> DiscussionDetailResponse:
    d = get_discussion_row(conn, discussion_id)
    if d is None or d.deleted_at:
        raise not_found("Discussion not found")
    # 待审内容：作者与版主可见（作者要在自己帖子里看到"审核中"），其他人 404。
    # 用 404 而不是 403：403 等于告诉外人"这里有个被审的帖子"。
    status = d.moderation_status
    if status is not ModerationStatus.Approved:
        # 审核失败的原文只有管理员能看（用户要求）；版主只看得到待审的 pending。
        privileged = viewer is not None and (
            viewer.role == "admin"
            or (viewer.role == "moderator" and status is ModerationStatus.Pending)
            or (status is ModerationStatus.Pending and viewer.id == d.author_id)
        )
        if not privileged:
            raise not_found("Discussion not found")
    board = get_board(conn, d.board_id)
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
    row = conn.execute(select(boards.c.visibility).where(boards.c.id == board_id)).first()
    return bool(row) and row[0] == "public"


def _prepare_moderation(
    conn: Connection,
    settings: Settings | None,
    *,
    content_type: str,
    author_id: int,
    text: str,
    title: str | None = None,
    is_public_board: bool = True,
    editing: bool = False,
) -> Verdict | None:
    """Review before any writes; edited content keeps the existing rule context."""
    if settings is None or not getattr(settings, "automod_enabled", False):
        return
    recent: list[str] = []
    if not editing and content_type == CONTENT_DISCUSSION:
        recent = [
            r[0]
            for r in conn.execute(
                select(discussions.c.body_md)
                .where(discussions.c.author_id == author_id)
                .order_by(discussions.c.id.desc())
                .limit(5)
            ).all()
            if r[0]
        ]
    elif not editing and content_type == CONTENT_REPLY:
        recent = [
            r[0]
            for r in conn.execute(
                select(replies.c.body_md).where(replies.c.author_id == author_id).order_by(replies.c.id.desc()).limit(5)
            ).all()
            if r[0]
        ]

    return prepare_submission(
        conn,
        settings,
        author_id=author_id,
        text=text,
        title=title,
        context="post" if content_type == CONTENT_DISCUSSION else "reply",
        recent_bodies=recent,
        is_public_board=is_public_board,
    )


def _apply_moderation(
    conn: Connection,
    settings: Settings | None,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    text: str,
    verdict: Verdict | None,
    title: str | None = None,
) -> None:
    if verdict is None:
        return
    if settings is None:
        raise internal_error()
    submit_for_review(
        conn,
        settings,
        content_type=content_type,
        content_id=content_id,
        author_id=author_id,
        text=text,
        title=title,
        verdict=verdict,
    )
    if verdict.decision == "allow":
        return
    # 机器只标记：review/block 都先压成 pending，等确认窗口 / AI 复审 / 人工定案。
    # AUTOMOD_HOLD_PENDING=false（先发后审）时只入队，不改可见性。
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
    settings: Settings | None,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    title: str | None,
    text: str,
    verdict: Verdict | None,
) -> None:
    """编辑后再过一次审核：**改文不能沿用旧结论**。

    没有这一步时，先发一条正常内容拿到 approved，再 PATCH 成违禁正文，内容会带着
    approved 留在公开面（见 PR #70 审查意见 #2）。

    行为与首次提交一致：判 allow 就维持可见（并把上一版残留的待审痕迹清掉），
    否则压回 pending 重新排队、重新计时。判定为 allow 时不需要入队——队列里若还留着
    上一版的待审记录，要把它收口，否则版主会看到一条已经不该看的待审项。
    """
    if verdict is None:
        return
    if settings is None:
        raise internal_error()
    is_discussion = content_type == CONTENT_DISCUSSION
    submit_for_review(
        conn,
        settings,
        content_type=content_type,
        content_id=content_id,
        author_id=author_id,
        text=text,
        title=title,
        verdict=verdict,
    )
    status = held_status(settings, verdict)
    if status is None:
        # 判定放行：清掉可能残留的待审/封禁状态，并把队列里这一版收口。
        _settle_queue_as_approved(conn, content_type=content_type, content_id=content_id)
    table = discussions if is_discussion else replies
    conn.execute(table.update().where(table.c.id == content_id).values(moderation_status=status or "approved"))


def _settle_queue_as_approved(conn: Connection, *, content_type: str, content_id: int) -> None:
    """An allowed edit retires old queue entries without deleting their evidence."""
    from ..automod import supersede_content

    supersede_content(conn, content_type=content_type, content_id=content_id)


def create_discussion(
    conn: Connection,
    actor: Actor,
    data: CreateDiscussionBody,
    settings: Settings | None = None,
) -> DiscussionDetailResponse:
    draft_id = data.draft_id
    draft = None
    if draft_id is not None:
        draft = drafts.require_owned(conn, actor, draft_id)
    drafts.validate_publish_attachments(conn, draft_id, data.attachment_ids or [])
    title = (data.title or "").strip()
    if not title:
        # 未提供标题：用正文第一句话自动生成
        # No title provided: auto-derive from the body's first sentence
        title = _derive_title(data.body_markdown)
    board = get_board_for_authz(conn, data.board_slug)
    if board is None:
        raise not_found("Board not found")
    board_res = {"type": "board", **board}
    assert_can(actor, Abilities.DISCUSSION_CREATE, board_res, conn)
    body_format = data.body_format
    body_html = render_body(data.body_markdown, body_format.value)
    verdict = _prepare_moderation(
        conn,
        settings,
        content_type=CONTENT_DISCUSSION,
        author_id=actor.id,
        title=title,
        text=data.body_markdown,
        is_public_board=board.get("visibility") == "public",
    )
    _now = now_ms()
    res = conn.execute(
        discussions.insert().values(
            board_id=board["id"],
            author_id=actor.id,
            title=title,
            body_md=data.body_markdown,
            body_html=body_html,
            body_format=body_format.value,
            created_at=_now,
            updated_at=_now,
        )
    )
    primary_key = res.inserted_primary_key
    if primary_key is None:
        raise internal_error()
    disc_id = _required_inserted_id(primary_key[0], "discussion")
    assert_author_current(conn, actor.id, expected_role=actor.role)
    # The INSERT acquires the write lock. Recheck authorization and draft state
    # now, since they could have changed while the provider was running.
    current_board = get_board_for_authz(conn, data.board_slug)
    if current_board is None or current_board["id"] != board["id"]:
        raise conflict("Board changed during review; reload and try again")
    assert_can(actor, Abilities.DISCUSSION_CREATE, {"type": "board", **current_board}, conn)
    if current_board.get("visibility") != board.get("visibility"):
        raise conflict("Board changed during review; reload and try again")
    if draft_id is not None and drafts.require_owned(conn, actor, draft_id) != draft:
        raise conflict("Draft changed during review; reload and try again")
    drafts.validate_publish_attachments(conn, draft_id, data.attachment_ids or [])
    # Persist the prepared verdict and content in the same short transaction.
    _apply_moderation(
        conn,
        settings,
        content_type=CONTENT_DISCUSSION,
        content_id=disc_id,
        author_id=actor.id,
        title=title,
        text=data.body_markdown,
        verdict=verdict,
    )
    att_ids = data.attachment_ids or []
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
            raise validation_failed(
                [{"field": "attachmentIds", "message": "One or more attachments are unavailable", "code": "custom"}]
            )
    publish_content(conn, CONTENT_DISCUSSION, disc_id)
    if draft_id is not None:
        drafts.delete_draft(conn, actor, draft_id)
    # 自己刚发的内容一定要能拿到（否则界面会在"发布成功"后立刻查不到，看着像失败）。
    # 被驳回时 get_discussion 会 404，所以这里对作者放宽：拿 row 直接拼 DTO。
    row = get_discussion_row(conn, disc_id)
    if row is not None and row.moderation_status is not ModerationStatus.Approved:
        return load_detail(conn, actor, row)
    return get_discussion(conn, actor, disc_id)


def update_discussion(
    conn: Connection,
    actor: Actor,
    discussion_id: int,
    patch: UpdateDiscussionBody,
    settings: Settings | None = None,
) -> DiscussionDetailResponse:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d.author_id,
        "boardId": d.board_id,
        "isLocked": d.is_locked,
        "deletedAt": d.deleted_at,
    }
    assert_can(actor, Abilities.DISCUSSION_UPDATE, res, conn)
    values: dict[str, object] = {"updated_at": max(now_ms(), d.updated_at + 1)}
    changed_fields = patch.model_fields_set
    if "title" in changed_fields:
        title = (patch.title or "").strip()
        if not title:
            # 编辑时清空标题：用（本次或已有）正文第一句话自动生成
            # Cleared title on edit: auto-derive from the (patched or existing) body's first sentence
            body_for_title = patch.body_markdown or d.body_md
            title = _derive_title(body_for_title)
        values["title"] = title
    if "body_markdown" in changed_fields and patch.body_markdown is not None:
        body_format = patch.body_format or BodyFormat.Markdown
        values["body_md"] = patch.body_markdown
        values["body_html"] = render_body(patch.body_markdown, body_format.value)
        values["body_format"] = body_format.value
    verdict = None
    if "title" in changed_fields or "body_markdown" in changed_fields:
        verdict = _prepare_moderation(
            conn,
            settings,
            content_type=CONTENT_DISCUSSION,
            author_id=d.author_id,
            title=str(values.get("title", d.title)),
            text=str(values.get("body_md", d.body_md)),
            is_public_board=_board_is_public(conn, d.board_id),
            editing=True,
        )
    changed = conn.execute(
        update(discussions)
        .where(
            discussions.c.id == discussion_id,
            discussions.c.updated_at == d.updated_at,
            discussions.c.moderation_status == d.moderation_status.value,
            discussions.c.title == d.title,
            discussions.c.body_md == d.body_md,
            discussions.c.body_format == d.body_format.value,
            discussions.c.board_id == d.board_id,
            discussions.c.deleted_at.is_(None),
            discussions.c.is_locked == (1 if d.is_locked else 0),
        )
        .values(**values)
    )
    if changed.rowcount != 1:
        raise conflict("Discussion changed during review; reload and try again")
    assert_author_current(conn, actor.id, expected_role=actor.role)
    assert_can(actor, Abilities.DISCUSSION_UPDATE, res, conn)
    # 标题或正文改了就要重新过审。不重审的话，先发正常内容拿到 approved、再改成违禁文本，
    # 内容会带着旧结论留在公开面（见 PR #70 审查意见 #2）。
    if "title" in changed_fields or "body_markdown" in changed_fields:
        _remoderate_edit(
            conn,
            settings,
            content_type=CONTENT_DISCUSSION,
            content_id=discussion_id,
            author_id=d.author_id,
            title=str(values.get("title", d.title)),
            text=str(values.get("body_md", d.body_md)),
            verdict=verdict,
        )
        publish_content(conn, CONTENT_DISCUSSION, discussion_id)
        # 重新送审后，读回的可见性要按新状态判断：作者不该在编辑成功的那一刻
        # 拿到一条已经变回待审的内容的完整 DTO（load_own_after_write 会处理）。
        row = get_discussion_row(conn, discussion_id)
        if row is not None and row.moderation_status is not ModerationStatus.Approved:
            return load_detail(conn, actor, row)
    return get_discussion(conn, actor, discussion_id)


def delete_discussion(conn: Connection, actor: Actor, discussion_id: int, reason: str | None) -> None:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d.author_id,
        "boardId": d.board_id,
        "isLocked": d.is_locked,
        "deletedAt": d.deleted_at,
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
    conn.execute(update(attachments).where(attachments.c.discussion_id == discussion_id).values(state="orphaned"))


def _reply_dto(row: ReplyRecord, author: UserSummaryRecord) -> ReplyResponse:
    deleted = row.deleted_at is not None
    return ReplyResponse(
        id=row.id,
        discussion_id=row.discussion_id,
        parent_reply_id=row.parent_reply_id,
        author=to_author(author),
        body_markdown="" if deleted else row.body_md,
        body_html=None if deleted else row.body_html,
        body_format=row.body_format,
        is_deleted=deleted,
        # 同 _build_thread：能读到这条回复的人已经过了 moderation_visible 过滤。
        moderation_status=row.moderation_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_reply(
    conn: Connection,
    actor: Actor,
    discussion_id: int,
    data: CreateReplyBody,
    settings: Settings | None = None,
) -> ReplyResponse:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d.author_id,
        "boardId": d.board_id,
        "isLocked": d.is_locked,
        "deletedAt": d.deleted_at,
    }
    assert_can(actor, Abilities.REPLY_CREATE, res, conn)
    assert_content_visible(d, actor)
    board = get_board(conn, d.board_id)
    if board is None:
        raise not_found("Board not found")
    assert_can(
        actor,
        Abilities.DISCUSSION_READ,
        {
            "type": "board",
            "id": board.id,
            "visibility": board.visibility,
            "postingPolicy": board.posting_policy,
        },
        conn,
    )
    # 校验父评论：parentReplyId 必须属于同一 discussion 且未被软删，否则产生跨帖孤儿回复，父不存在时外键触发 500
    # Validate parent reply: it must belong to the same discussion and not be soft-deleted, otherwise orphan replies / FK 500
    parent_reply_id = data.parent_reply_id
    if parent_reply_id is not None:
        parent = repository_get_reply(conn, ReplyID(parent_reply_id))
        if parent is None or parent.discussion_id != discussion_id or parent.deleted_at is not None:
            raise not_found("Parent reply not found")
        assert_content_visible(parent, actor)
        depth = 1
        ancestor_id = parent.parent_reply_id
        while ancestor_id is not None:
            depth += 1
            if depth >= MAX_REPLY_DEPTH:
                raise validation_failed(
                    [
                        {
                            "field": "parentReplyId",
                            "message": f"Replies cannot be nested deeper than {MAX_REPLY_DEPTH} levels",
                            "code": "max_depth",
                        }
                    ]
                )
            ancestor = repository_get_reply(conn, ancestor_id)
            if ancestor is None or ancestor.discussion_id != discussion_id:
                raise not_found("Parent reply not found")
            ancestor_id = ancestor.parent_reply_id
    body_format = data.body_format
    body_html = render_body(data.body_markdown, body_format.value)
    verdict = _prepare_moderation(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        author_id=actor.id,
        text=data.body_markdown,
        is_public_board=_board_is_public(conn, d.board_id),
    )
    _now = now_ms()
    ins = conn.execute(
        replies.insert().values(
            discussion_id=discussion_id,
            author_id=actor.id,
            parent_reply_id=data.parent_reply_id,
            body_md=data.body_markdown,
            body_html=body_html,
            body_format=body_format.value,
            created_at=_now,
            updated_at=_now,
        )
    )
    primary_key = ins.inserted_primary_key
    if primary_key is None:
        raise internal_error()
    reply_id = _required_inserted_id(primary_key[0], "reply")
    assert_author_current(conn, actor.id, expected_role=actor.role)
    current_discussion = get_discussion_row(conn, discussion_id)
    if current_discussion is None:
        raise not_found("Discussion not found")
    assert_can(
        actor,
        Abilities.REPLY_CREATE,
        {
            "type": "discussion",
            "id": discussion_id,
            "authorId": current_discussion.author_id,
            "boardId": current_discussion.board_id,
            "isLocked": current_discussion.is_locked,
            "deletedAt": current_discussion.deleted_at,
        },
        conn,
    )
    if current_discussion.board_id != d.board_id:
        raise conflict("Discussion changed during review; reload and try again")
    d = current_discussion
    _apply_moderation(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        content_id=reply_id,
        author_id=actor.id,
        text=data.body_markdown,
        verdict=verdict,
    )
    # Recheck after moderation too: a parent can be hidden during a slow review.
    current_parent = get_discussion_row(conn, discussion_id)
    if current_parent is None:
        raise not_found("Discussion not found")
    assert_content_visible(current_parent, actor)
    conn.execute(
        update(discussions)
        .where(discussions.c.id == discussion_id)
        .values(reply_count=discussions.c.reply_count + 1, last_reply_at=_now, updated_at=_now)
    )
    publish_content(conn, CONTENT_REPLY, reply_id)
    row = repository_get_reply(conn, ReplyID(reply_id))
    author = user_summaries(conn, [UserID(actor.id)]).get(UserID(actor.id))
    if row is None or author is None:
        raise internal_error()
    return _reply_dto(row, author)


class _ModeratedContent(Protocol):
    @property
    def author_id(self) -> UserID: ...

    @property
    def moderation_status(self) -> ModerationStatus: ...


def assert_content_visible(
    content: _ModeratedContent,
    viewer: Actor | None,
) -> None:
    """父帖不可见时，按 404 处理它的回复/衍生数据。

    规则与 `get_discussion` 完全一致（404 而不是 403，避免告诉外人"这里有个被审的东西"）：
      - 管理员：全可见；
      - 版主：可见 approved 与 pending，看不到 rejected；
      - 作者：可见自己的 pending；
      - 其他人：只看得到 approved。
    """
    status = content.moderation_status
    if status is ModerationStatus.Approved:
        return
    if viewer is not None and viewer.role == "admin":
        return
    if viewer is not None and viewer.role == "moderator" and status is ModerationStatus.Pending:
        return
    if viewer is not None and status is ModerationStatus.Pending and viewer.id == content.author_id:
        return
    raise not_found("Discussion not found")


def list_replies(conn: Connection, viewer: Actor | None, discussion_id: int) -> ReplyListResponse:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    board = get_board(conn, d.board_id)
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
    assert_content_visible(d, viewer)
    reply_conds: list[ColumnElement[bool]] = [replies.c.discussion_id == discussion_id]
    _append_moderation_cond(reply_conds, replies.c.moderation_status, replies.c.author_id, viewer)
    rows = list_discussion_replies(conn, reply_conds)
    author_map = user_summaries(conn, {row.author_id for row in rows})
    items: list[ReplyResponse] = []
    for row in rows:
        author = author_map.get(row.author_id) or UserSummaryRecord(
            id=row.author_id, username="", display_name="", discriminator=None
        )
        items.append(_reply_dto(row, author))
    return ReplyListResponse(items=items)


def update_reply(
    conn: Connection,
    actor: Actor,
    reply_id: int,
    body_markdown: str,
    body_format: str = "markdown",
    settings: Settings | None = None,
) -> ReplyResponse:
    row = repository_get_reply(conn, ReplyID(reply_id))
    if row is None:
        raise not_found("Reply not found")
    res = {
        "type": "reply",
        "id": reply_id,
        "authorId": row.author_id,
        "discussionId": row.discussion_id,
    }
    assert_can(actor, Abilities.REPLY_UPDATE, res, conn)
    verdict = _prepare_moderation(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        author_id=row.author_id,
        text=body_markdown,
        editing=True,
    )
    _now = max(now_ms(), row.updated_at + 1)
    changed = conn.execute(
        update(replies)
        .where(
            replies.c.id == reply_id,
            replies.c.updated_at == row.updated_at,
            replies.c.moderation_status == row.moderation_status.value,
            replies.c.deleted_at.is_(None),
            replies.c.body_md == row.body_md,
            replies.c.body_format == row.body_format.value,
        )
        .values(
            body_md=body_markdown,
            body_html=render_body(body_markdown, body_format),
            body_format=body_format,
            updated_at=_now,
        )
    )
    if changed.rowcount != 1:
        raise conflict("Reply changed during review; reload and try again")
    assert_author_current(conn, actor.id, expected_role=actor.role)
    assert_can(actor, Abilities.REPLY_UPDATE, res, conn)
    # 编辑要重新过审，理由同 update_discussion。
    _remoderate_edit(
        conn,
        settings,
        content_type=CONTENT_REPLY,
        content_id=reply_id,
        author_id=row.author_id,
        title=None,
        text=body_markdown,
        verdict=verdict,
    )
    publish_content(conn, CONTENT_REPLY, reply_id)
    updated = repository_get_reply(conn, ReplyID(reply_id))
    if updated is None:
        raise internal_error()
    author = user_summaries(conn, [updated.author_id]).get(updated.author_id)
    if author is None:
        raise internal_error()
    return _reply_dto(updated, author)


def delete_reply(conn: Connection, actor: Actor, reply_id: int, reason: str | None) -> None:
    row = repository_get_reply(conn, ReplyID(reply_id))
    if row is None:
        raise not_found("Reply not found")
    res = {
        "type": "reply",
        "id": reply_id,
        "authorId": row.author_id,
        "discussionId": row.discussion_id,
    }
    assert_can(actor, Abilities.REPLY_DELETE, res, conn)
    if row.deleted_at is not None:
        return  # 已软删，幂等：不重复递减 reply_count
    _now = now_ms()
    conn.execute(
        update(replies)
        .where(replies.c.id == reply_id)
        .values(deleted_at=_now, deleted_by=actor.id if actor else None, deletion_reason=reason, updated_at=_now)
    )
    conn.execute(
        update(discussions)
        .where(discussions.c.id == row.discussion_id)
        .values(reply_count=discussions.c.reply_count - 1)
    )


# ---------------------------------------------------------------- save/follow/pin/lock


def save(conn: Connection, actor: Actor, discussion_id: int) -> None:
    existing = conn.execute(
        select(discussion_saves.c.discussion_id).where(
            (discussion_saves.c.user_id == actor.id) & (discussion_saves.c.discussion_id == discussion_id)
        )
    ).first()
    if existing:
        return
    conn.execute(discussion_saves.insert().values(user_id=actor.id, discussion_id=discussion_id, created_at=now_ms()))
    conn.execute(
        update(discussions).where(discussions.c.id == discussion_id).values(save_count=discussions.c.save_count + 1)
    )
    emit_event(
        conn,
        "discussion.saved",
        aggregate_type="discussion",
        aggregate_id=str(discussion_id),
        payload={"discussionId": discussion_id, "userId": actor.id},
    )


def unsave(conn: Connection, actor: Actor, discussion_id: int) -> None:
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


def follow(conn: Connection, actor: Actor, discussion_id: int) -> None:
    existing = conn.execute(
        select(discussion_follows.c.discussion_id).where(
            (discussion_follows.c.user_id == actor.id) & (discussion_follows.c.discussion_id == discussion_id)
        )
    ).first()
    if existing:
        return
    conn.execute(discussion_follows.insert().values(user_id=actor.id, discussion_id=discussion_id, created_at=now_ms()))
    emit_event(
        conn,
        "discussion.followed",
        aggregate_type="discussion",
        aggregate_id=str(discussion_id),
        payload={"discussionId": discussion_id, "userId": actor.id},
    )


def unfollow(conn: Connection, actor: Actor, discussion_id: int) -> None:
    conn.execute(
        discussion_follows.delete().where(
            (discussion_follows.c.user_id == actor.id) & (discussion_follows.c.discussion_id == discussion_id)
        )
    )


def _toggle(conn: Connection, actor: Actor, discussion_id: int, field: str) -> None:
    d = get_discussion_row(conn, discussion_id)
    if d is None:
        raise not_found("Discussion not found")
    res = {
        "type": "discussion",
        "id": discussion_id,
        "authorId": d.author_id,
        "boardId": d.board_id,
        "isLocked": d.is_locked,
        "deletedAt": d.deleted_at,
    }
    ability = Abilities.DISCUSSION_PIN if field == "is_pinned" else Abilities.DISCUSSION_LOCK
    assert_can(actor, ability, res, conn)
    current_value = d.is_pinned if field == "is_pinned" else d.is_locked
    new_val = 0 if current_value else 1
    if field == "is_pinned" and new_val == 1:
        # 每分区置顶上限 5 个
        # Per-board pin limit of 5
        pinned_count = (
            conn.execute(
                select(func.count())
                .select_from(discussions)
                .where(
                    (discussions.c.board_id == d.board_id)
                    & (discussions.c.is_pinned == 1)
                    & (discussions.c.deleted_at.is_(None))
                )
            ).scalar()
            or 0
        )
        if pinned_count >= 5:
            raise conflict("This board already has 5 pinned discussions")
    conn.execute(update(discussions).where(discussions.c.id == discussion_id).values(**{field: new_val}))


def pin(conn: Connection, actor: Actor, discussion_id: int) -> None:
    _toggle(conn, actor, discussion_id, "is_pinned")


def lock(conn: Connection, actor: Actor, discussion_id: int) -> None:
    _toggle(conn, actor, discussion_id, "is_locked")


# ---------------------------------------------------------------- user feeds


def list_by_author(
    conn: Connection,
    viewer: Actor | None,
    author_id: int,
    query: PageQuery | LegacyPageOptions,
) -> DiscussionListResponse:
    if not isinstance(query, PageQuery):
        query = PageQuery(cursor=query.get("cursor"), limit=query.get("limit", 20))
    limit, cursor = min(query.limit, 50), query.cursor
    visible = visible_board_ids(conn, viewer)
    conds: list[ColumnElement[bool]] = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
        discussions.c.author_id == author_id,
    ]
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    cur = _cursor_cond(cursor)
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
    return DiscussionListResponse(items=items, next_cursor=next_cursor)


def list_saved(
    conn: Connection,
    viewer: Actor | None,
    owner_id: int,
    query: PageQuery | LegacyPageOptions,
) -> DiscussionListResponse:
    if not isinstance(query, PageQuery):
        query = PageQuery(cursor=query.get("cursor"), limit=query.get("limit", 20))
    if viewer is None or viewer.id != owner_id:
        raise forbidden("Saved discussions are private")
    limit, cursor = min(query.limit, 50), query.cursor
    save_ids = [
        r[0]
        for r in conn.execute(
            select(discussion_saves.c.discussion_id).where(discussion_saves.c.user_id == owner_id)
        ).all()
    ]
    if not save_ids:
        return DiscussionListResponse(items=[], next_cursor=None)
    visible = visible_board_ids(conn, viewer)
    conds: list[ColumnElement[bool]] = [
        discussions.c.deleted_at.is_(None),
        discussions.c.id.in_(save_ids),
        discussions.c.board_id.in_(visible),
    ]
    # 收藏是可见性的一个出口：漏了这条，被封禁的帖子还能从"我的收藏"里读到标题和摘要。
    _append_moderation_cond(conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    cur = _cursor_cond(cursor)
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
    return DiscussionListResponse(items=items, next_cursor=next_cursor)


def list_replies_by_author(
    conn: Connection,
    viewer: Actor | None,
    author_id: int,
    query: PageQuery | LegacyPageOptions,
) -> AuthoredReplyListResponse:
    if not isinstance(query, PageQuery):
        query = PageQuery(cursor=query.get("cursor"), limit=query.get("limit", 20))
    limit, cursor = min(query.limit, 50), query.cursor
    # 游标先验（畸形 400）：放在空集提前返回之前，语义与 discussion 侧统一。
    cursor_id: int | None = None
    if cursor:
        try:
            cursor_id = int(cursor)
        except (TypeError, ValueError):
            raise bad_request("Invalid cursor")
        if cursor_id < 1:
            raise bad_request("Invalid cursor")
    visible = visible_board_ids(conn, viewer)
    disc_conds: list[ColumnElement[bool]] = [
        discussions.c.deleted_at.is_(None),
        discussions.c.board_id.in_(visible),
    ]
    # 父帖不可见的，它的回复也不该出现在"某人的回复"列表里——否则会连父帖标题一起泄漏
    # （见 PR #70 审查意见 #7）。
    _append_moderation_cond(disc_conds, discussions.c.moderation_status, discussions.c.author_id, viewer)
    disc_ids = [r[0] for r in conn.execute(select(discussions.c.id).where(and_(*disc_conds))).all()]
    if not disc_ids:
        return AuthoredReplyListResponse(items=[], next_cursor=None)
    conds: list[ColumnElement[bool]] = [
        replies.c.author_id == author_id,
        replies.c.deleted_at.is_(None),
        replies.c.discussion_id.in_(disc_ids),
    ]
    _append_moderation_cond(conds, replies.c.moderation_status, replies.c.author_id, viewer)
    if cursor_id is not None:
        conds.append(replies.c.id < cursor_id)
    rows = list_reply_feed(conn, conds, limit=limit)
    has_more = len(rows) > limit
    page = rows[:limit] if has_more else rows
    d_ids = {r.discussion_id for r in page}
    a_ids = {r.author_id for r in page}
    d_map = discussion_titles(conn, d_ids)
    a_map = user_summaries(conn, a_ids)
    items: list[AuthoredReplyResponse] = []
    for r in page:
        author = a_map.get(r.author_id) or UserSummaryRecord(
            id=r.author_id,
            username="",
            display_name="",
            discriminator=None,
        )
        reply = _reply_dto(r, author)
        items.append(
            AuthoredReplyResponse(
                **reply.model_dump(),
                discussion_title=d_map.get(r.discussion_id, ""),
            )
        )
    next_cursor = None
    if has_more and items:
        next_cursor = str(items[-1].id)
    return AuthoredReplyListResponse(items=items, next_cursor=next_cursor)
