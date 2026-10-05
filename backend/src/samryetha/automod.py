"""审核编排：规则 → 模型 → 队列，并把结果落到内容表与 `moderation_queue`。

调用方只需要 `submit()`：它返回一个判定，调用方据此决定新内容对外可见性。

**发布即审核，不设确认窗口**（用户确认的流程）。两段分流：

  1. 规则层**确定性命中**（score ≥ BLOCK_THRESHOLD）→ **直接封禁，不调用模型**。
     关键词匹配是确定性的、误杀面窄，已确定的结论不值得再花一次模型调用；
     这条路径也不受模型可用性影响（"未成年"这类词命中即封）。
  2. **其余全部交给模型**，模型的结论直接生效：
       - allow（risk < LLM_REVIEW_AT）→ 公开、不入队；
       - review（LLM_REVIEW_AT ≤ risk < LLM_BLOCK_AT）→ 压成 `pending`，进队列等人工；
       - block（risk ≥ LLM_BLOCK_AT）→ **直接封禁**，并进队列标 `blocked_by_machine`。

  为什么不做"规则层零信号就直接放行"：规则层每条关键词权重都是 100，命中即 100、
  未命中即 0，**没有中间态**。若按"零信号直放"实现，`我想要买银，有文成年图片咝`
  这类**变体写法**（规则层零信号）会直接公开、连模型都不过——而变体恰恰最需要语义
  判定。所以只有**已经确定**的结论才短路。

**机器封禁不是终局**：`enqueue()` 把机器封禁记成 `resolution = blocked_by_machine`，
`review_state` 保持 `pending`、`reviewer_id` 留空，管理员在后台随时**维持**或**推翻**
（推翻记 `overturned = 1`）。这是"封禁最终决定权仍在人工"的落点。

**降级策略（fail-open 到规则层）**：
  - 模型未配置 → 只用规则；
  - 模型超时/报错/返回不可解析 → 记日志 + 只用规则，并在 signals 里留一条
    `llm_unavailable`，让人工知道"这条没经过语义审核"；
  - 无论如何都不因为模型挂了而拒绝发布。
  注意：`max_tokens` 必须给够（默认 1000）。实测 300 时 Kimi 这类推理型模型会有
  ~13% 的调用被截断（`finish_reason=length`、content 为空），解析失败后静默降级到
  规则层，等于最该拦的内容走了最弱的通道；`classify()` 对截断会自动重试一次。

**已停用**（发布即审核后不再使用，保留只为兼容旧配置与历史数据）：
  `hold_until` 不再写入，`finalize_pending()` / `_hold_deadline` /
  `RESOLUTION_PUBLISHED_BY_AI` / `automod_worker.ModerationWorker` 均为空转。
"""

from __future__ import annotations

import json
import logging
from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from .automod_providers import AutomodUnavailable, verdict_from_llm
from .automod_rules import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    REVIEW_THRESHOLD,
    Verdict,
    evaluate_rules,
    excerpt_of,
    merge_verdicts,
    signals_json,
)
from .config import Settings
from .db import now_ms

logger = logging.getLogger("samryetha.automod")

# 内容类型常量（与 moderation_queue.content_type 对应）
CONTENT_DISCUSSION = "discussion"
CONTENT_REPLY = "reply"
CONTENT_PROFILE = "profile"
CONTENT_MESSAGE = "message"
CONTENT_ATTACHMENT = "attachment"

# 模型分数的转人工门槛（与规则层的 REVIEW_THRESHOLD 独立：模型的 0-100 分布不同，
# 用同一阈值会要么过松要么过严）。
LLM_REVIEW_AT = 45
# 模型判到该分数以上即直接封禁。85 是"模型很有把握属于六类之一"的档位：
# 提示词里 70 以上才是"疑似属于六类"，取 85 避免把模糊地带直接封掉。
# 误判由队列兜底——AI 封禁一律标 blocked_by_machine，管理员随时推翻。
LLM_BLOCK_AT = 85

# ---- 处置结果（moderation_queue.resolution）----------------------------------
# 三个取值的共同前提：NULL（未设）表示"还在确认窗口内、谁都没处置过"。
# 窗口超时后由 AI 复审落定为下面两者之一，人工可事后推翻。
# 窗口超时，AI 复审放行 → 先行公开（人工可推翻改为封禁）。
RESOLUTION_PUBLISHED_BY_AI = "published_by_ai"
# 人工放行：既包括窗口内直接批准，也包括事后推翻 AI 封禁、重新放行。
RESOLUTION_PUBLISHED_BY_HUMAN = "published_by_human"
# 封禁：既包括 AI 复审仍不放行的先行封禁，也包括人工驳回、人工推翻 AI 放行。
# 审核失败的记录全部留存，仅管理员可访问。
RESOLUTION_BLOCKED = "blocked"
# 机器**直接**封禁（发布即审核，不再经过确认窗口）：规则层确定性命中，或模型判 block。
# 与 RESOLUTION_BLOCKED 的区别只在"怎么发生的"，可见性完全一样（仅管理员）。
# 这种记录 reviewer_id 为空、review_state 仍是 pending，等管理员事后复审／推翻。
RESOLUTION_BLOCKED_BY_MACHINE = "blocked_by_machine"
# 所有「内容不予公开」的处置：可见性一律仅管理员，且只有管理员能推翻。
BLOCKED_RESOLUTIONS = (RESOLUTION_BLOCKED, RESOLUTION_BLOCKED_BY_MACHINE)


def _provider_for(settings: Settings):
    """按配置构造 provider；未配置返回 None（= 只用规则层）。"""
    base_url = getattr(settings, "automod_base_url", None)
    api_key = getattr(settings, "automod_api_key", None)
    model = getattr(settings, "automod_model", None)
    if not base_url or not model:
        return None
    from .automod_providers import OpenAICompatibleProvider

    headers = settings.automod_header_map if hasattr(settings, "automod_header_map") else {}
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key or "not-needed",
        model=model,
        timeout_seconds=float(getattr(settings, "automod_timeout_seconds", 12)),
        temperature=getattr(settings, "automod_temperature", 0.0),
        extra_headers=headers,
    )


def _is_new_account(conn: Connection, author_id: int) -> bool:
    from .schema import discussions

    count = conn.execute(
        select(discussions.c.id).where(discussions.c.author_id == author_id).limit(1)
    ).first()
    return count is None


def review_content(
    conn: Connection,
    settings: Settings,
    *,
    text: str,
    context: str = "post",
    recent_bodies: list[str] | None = None,
    is_new_account: bool = False,
    is_public_board: bool = True,
    recheck: bool = False,
) -> Verdict:
    """规则 → 模型，返回最终判定。不写库。

    **两段分流（确定性优先，其余全部交给模型）**：

    1. 规则层**确定性命中**（达 BLOCK 阈值）→ 直接封禁，**不调用模型**。
       关键词匹配是确定性的、误杀面窄，已确定的结论不值得再花一次模型调用；
       这条路径也不受模型可用性影响（"未成年"这类词命中即封，不看模型脸色）。
    2. **其余全部交给模型**，模型的结论直接生效：
       allow → 公开 / review → pending 等人工 / block → 直接封禁并进队列。

    为什么不做"规则层零信号就直接放行"：规则层目前所有关键词权重都是 100，
    命中即 100 分、未命中即 0 分，**没有中间态**。按"零信号直放"实现的话，
    "我想要买银，有文成年图片咝" 这类**变体写法**（规则层零信号）会直接公开、
    连模型都不过——而变体恰恰最需要语义判定，那会比现在漏得更厉害。
    所以只有"规则已经确定"才跳过模型，其余一律交给模型。

    ``is_public_board``：露骨描写规则只在公开版块成立（隐藏版内允许），
    由调用方传入版块可见性。
    """
    rules = evaluate_rules(
        text,
        recent_bodies=recent_bodies or [],
        is_public_board=is_public_board,
        is_new_account=is_new_account,
    )
    # 分流①：规则层已经确定封禁，直接用它的结论，不花模型的钱。
    if rules.decision == DECISION_BLOCK:
        return rules

    # 分流②：其余全部交给模型。
    provider = _provider_for(settings)
    if provider is None:
        return rules

    model: Verdict | None = None
    try:
        result = provider.classify(text, context=context, recheck=recheck)
        model = verdict_from_llm(result, review_at=LLM_REVIEW_AT, block_at=LLM_BLOCK_AT)
    except AutomodUnavailable as exc:
        # 降级：模型不可用不阻断发布，但必须留下痕迹，让人工知道这条没经过语义审核。
        logger.warning("automod model unavailable, falling back to rules: %s", exc)
        merged = merge_verdicts(rules, None)
        from .automod_rules import Signal

        merged.signals.append(Signal("llm_unavailable", 0, "语义审核不可用，本次仅按规则判定"))
        return merged
    return merge_verdicts(rules, model)


def held_status(settings: Settings, verdict) -> str | None:
    """内容刚提交后应该写入的 `moderation_status`；`None` = 不改动（保持默认可见）。

    新语义（**发布即审核，不再有确认窗口**）：

    - `allow` → 立即可见，不入队；
    - `block` → **直接封禁**（`rejected`）。规则层命中是确定性的；模型命中也会被封禁，
      但下面 `enqueue` 会把它送进队列，由管理员随时复审、推翻（见 `needsAdminReview`）；
    - `review` → 只压成 `pending` 等人工看一眼（模型认为可疑但没到封禁线）。

    `AUTOMOD_HOLD_PENDING=false`（先发后审的过渡部署）时一律只入队、不改可见性。

    三个写入路径（帖子回复 / 私信 / 用户资料）共用，避免"block 在帖子里被处理、
    在私信里却漏掉"这类不一致。
    """
    if verdict.decision == DECISION_ALLOW:
        return None
    if not getattr(settings, "automod_hold_pending", True):
        return None
    if verdict.decision == DECISION_BLOCK:
        return "rejected"
    return "pending"


def needs_admin_review(verdict) -> bool:
    """这条判定是否应当进队列等管理员（事后）复审。

    - 规则层/模型判 block：已直接封禁，但**必须**留一条队列记录，管理员才能察觉并推翻。
      模型判的 block 尤其要——它是"规则拿不准才交给模型"的那批，误判概率高于关键词。
    - 模型判 review：内容被压住，等人放行或确认封禁。
    - allow：不入队（否则队列会被正常内容淹没）。
    """
    return verdict.decision != DECISION_ALLOW


def _hold_deadline(settings: Settings, *, now: int | None = None) -> int | None:
    """人工确认窗口的截止时间；窗口配成 0 或负数 = 不设窗口（一直等人，旧行为）。"""
    seconds = getattr(settings, "automod_confirm_window_seconds", 60)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = 60
    if seconds <= 0:
        return None
    return (now if now is not None else now_ms()) + seconds * 1000


def supersede_content(conn: Connection, *, content_type: str, content_id: int) -> None:
    """Keep historical verdicts and snapshots, but retire their write authority."""
    from .schema import moderation_queue

    conn.execute(
        moderation_queue.update().where(
            moderation_queue.c.content_type == content_type,
            moderation_queue.c.content_id == content_id,
            moderation_queue.c.superseded_at.is_(None),
        ).values(superseded_at=now_ms())
    )


def enqueue(
    conn: Connection,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    excerpt: str,
    verdict: Verdict,
    hold_until: int | None = None,
    submitted_text: str = "",
) -> int | None:
    """把一次判定登记进人工队列；`allow` 不入队（否则队列会被正常内容淹没）。

    `hold_until` 现在只在显式传入时才有值——「发布即审核」不再设确认窗口，
    队列的角色从"限时待办"变成"事后复核清单"。
    """
    if verdict.decision == DECISION_ALLOW:
        return None
    from .schema import moderation_queue

    # Every submitted version gets an immutable record. Reusing an AI-blocked
    # pending row would erase its retained evidence and let stale decisions act
    # on a different version of the content.
    supersede_content(conn, content_type=content_type, content_id=content_id)
    values = {
        "excerpt": excerpt,
        "decision": verdict.decision,
        "score": verdict.score,
        "signals": signals_json(verdict.signals),
        "hold_until": hold_until,
        "submitted_text": submitted_text,
        # 机器直接封禁（发布即审核）：立刻标成 blocked_by_machine，队列据此把它列进
        # "机器已封禁 · 待管理员复审"，管理员能一眼看到并随时推翻。
        # review_state 保持 pending、reviewer_id 留空 = 尚未经人工定案。
        "resolution": RESOLUTION_BLOCKED_BY_MACHINE if verdict.decision == DECISION_BLOCK else None,
        "resolved_at": now_ms() if verdict.decision == DECISION_BLOCK else None,
    }
    result = conn.execute(
        insert(moderation_queue).values(
            content_type=content_type,
            content_id=content_id,
            author_id=author_id,
            review_state="pending",
            created_at=now_ms(),
            **values,
        )
    )
    return result.inserted_primary_key[0]


def apply_review_state(
    conn: Connection,
    *,
    content_type: str,
    content_id: int,
    status: str,
) -> None:
    """人工决定回写到内容表。"""
    from .schema import discussions, direct_messages, replies, users

    if content_type == CONTENT_DISCUSSION:
        conn.execute(
            discussions.update().where(discussions.c.id == content_id).values(moderation_status=status)
        )
    elif content_type == CONTENT_REPLY:
        conn.execute(replies.update().where(replies.c.id == content_id).values(moderation_status=status))
    elif content_type == CONTENT_PROFILE:
        if status == "approved":
            # 通过：把待审资料提升为正式资料。
            from .users import _promote_pending_profile

            _promote_pending_profile(conn, content_id)
        else:
            # 驳回：只改状态。待审原文（pending_display_name/bio）**故意留着**——
            # 主字段 display_name/bio 仍是上一次通过的值，所以公开面读不到它，
            # 但内容没有被删除，管理员可以从留存库调取。
            conn.execute(
                users.update()
                .where(users.c.id == content_id)
                .values(profile_moderation_status=status)
            )
    elif content_type == CONTENT_MESSAGE:
        conn.execute(
            direct_messages.update().where(direct_messages.c.id == content_id).values(moderation_status=status)
        )
    elif content_type == CONTENT_ATTACHMENT:
        # 附件本身没有 moderation_status 列（它靠 state 表达生命周期）。被驳回的附件
        # 直接标记 orphaned，下载端点会拒绝它；批准则不动。
        if status == "rejected":
            from .schema import attachments

            conn.execute(
                attachments.update().where(attachments.c.id == content_id).values(state="orphaned")
            )


def submit(
    conn: Connection,
    settings: Settings,
    *,
    content_type: str,
    content_id: int,
    author_id: int,
    text: str,
    title: str | None = None,
    context: str = "post",
    recent_bodies: list[str] | None = None,
    is_new_account: bool | None = None,
    is_public_board: bool = True,
) -> Verdict:
    """一条内容的完整审核流程：判定 → 入队（如需要）→ 返回判定。

    调用方用 `held_status(settings, verdict)` 决定新内容的可见性。
    """
    if recent_bodies is None:
        recent_bodies = []
    if is_new_account is None:
        is_new_account = _is_new_account(conn, author_id)

    # 初审统一组合一次标题与正文；快照与模型读取同一版本。
    review_input = f"{title}\n{text}" if title else text
    verdict = review_content(
        conn,
        settings,
        text=review_input,
        context=context,
        recent_bodies=recent_bodies,
        is_new_account=is_new_account,
        is_public_board=is_public_board,
    )
    enqueue(
        conn,
        content_type=content_type,
        content_id=content_id,
        author_id=author_id,
        excerpt=excerpt_of(title, text),
        verdict=verdict,
        # 发布即审核：不再设确认窗口（hold_until 留空），队列只作事后复核清单。
        hold_until=None,
        submitted_text=review_input,
    )
    return verdict


# ---------------------------------------------------------------- 逾期复审


def load_content(conn: Connection, *, content_type: str, content_id: int) -> dict:
    """取回待审内容的正文与版块可见性，供逾期复审用。

    队列只存摘要（`excerpt`），复审要看全文，所以按类型回表读。读不到（内容已被删除）
    返回 `exists=False`——此时按"无法确认违规"处理，先行放行。
    """
    from .schema import attachments, boards, direct_messages, discussions, replies, users

    if content_type == CONTENT_DISCUSSION:
        row = conn.execute(
            select(discussions.c.title, discussions.c.body_md, discussions.c.board_id).where(
                discussions.c.id == content_id
            )
        ).first()
        if row is None:
            return {"exists": False, "text": "", "title": None, "is_public_board": True}
        visibility = conn.execute(select(boards.c.visibility).where(boards.c.id == row.board_id)).first()
        return {
            "exists": True,
            "text": row.body_md or "",
            "title": row.title,
            "is_public_board": bool(visibility) and visibility[0] == "public",
        }
    if content_type == CONTENT_REPLY:
        row = conn.execute(
            select(replies.c.body_md, replies.c.discussion_id).where(replies.c.id == content_id)
        ).first()
        if row is None:
            return {"exists": False, "text": "", "title": None, "is_public_board": True}
        board = conn.execute(
            select(boards.c.visibility)
            .join(discussions, discussions.c.board_id == boards.c.id)
            .where(discussions.c.id == row.discussion_id)
        ).first()
        return {
            "exists": True,
            "text": row.body_md or "",
            "title": None,
            "is_public_board": bool(board) and board[0] == "public",
        }
    if content_type == CONTENT_MESSAGE:
        row = conn.execute(
            select(direct_messages.c.body).where(direct_messages.c.id == content_id)
        ).first()
        # 私信不是公开版块：露骨描写规则不适用（与写入路径的 is_public_board=False 一致）。
        return {"exists": row is not None, "text": (row.body if row else "") or "", "title": None, "is_public_board": False}
    if content_type == CONTENT_PROFILE:
        row = conn.execute(
            select(
                users.c.display_name,
                users.c.bio,
                users.c.pending_display_name,
                users.c.pending_bio,
                users.c.profile_moderation_status,
            ).where(users.c.id == content_id)
        ).first()
        if row is None:
            return {"exists": False, "text": "", "title": None, "is_public_board": True}
        # 复审要看**用户提交的那一版**，不是对外展示的旧资料：待审/被驳回时用 pending_*。
        # 这样留存库拿到的是失败原文（也正因为它只在这里，主字段没有被污染）。
        display = row.pending_display_name or row.display_name
        bio = row.pending_bio if row.pending_bio is not None else row.bio
        return {
            "exists": True,
            "text": f"{display}\n{bio or ''}",
            "title": None,
            "is_public_board": True,
        }
    if content_type == CONTENT_ATTACHMENT:
        row = conn.execute(
            select(attachments.c.original_filename).where(attachments.c.id == content_id)
        ).first()
        # 附件没有可复审的正文；文件名只作线索，不据此判违规。
        return {"exists": row is not None, "text": (row.original_filename if row else "") or "", "title": None, "is_public_board": True}
    return {"exists": False, "text": "", "title": None, "is_public_board": True}


def _verdict_snapshot(verdict: Verdict | None, *, note: str = "") -> dict:
    if verdict is None:
        return {"decision": "allow", "score": 0, "source": "system", "signals": [], "note": note}
    return {
        "decision": verdict.decision,
        "score": verdict.score,
        "source": verdict.source,
        "signals": [
            {"rule": s.rule, "weight": s.weight, "detail": s.detail} for s in verdict.signals
        ],
        "note": note,
    }


def _finalize_one(conn: Connection, settings: Settings, row: dict, *, now: int, notify: bool) -> dict | None:
    """对一条超时未定案的内容做复审，并按复审结论落定。返回处置摘要；被人抢先则返回 None。

    规则（用户确认）：**复审放行才放行**，复审仍不放行（review/block）则封禁。
    "必须初审和复审都放行"——进入窗口的内容初审必然未放行，因此实际由复审决定。

    落定只是"先行"：`review_state` 保持 pending，人工可以维持或推翻。

    **落定要抢占**：`UPDATE ... WHERE resolution IS NULL`，只有 rowcount==1 才回写可见性与
    发通知。定时 worker 与管理员手动 `POST /finalize` 可能同时扫到同一条，没有这个条件
    就会重复落定、重复给作者发通知。
    """
    from .schema import moderation_queue

    content = load_content(conn, content_type=row["content_type"], content_id=row["content_id"])
    verdict: Verdict | None = None
    note = ""
    if not content["exists"]:
        # 内容已被删除（或类型未知）：无可复审，也无法封禁。记一条封禁结论把队列行收口，
        # 避免 worker 每轮都重新扫到它。
        note = "内容已不存在，无法复审"
    else:
        context = "reply" if row["content_type"] == CONTENT_REPLY else "post"
        try:
            # 复审同样要带标题：只审正文会让"正文正常、标题违规"的内容在复审阶段被放行
            # （见 PR #70 审查意见 #3）。
            recheck_input = (
                f"{content['title']}\n{content['text']}" if content.get("title") else content["text"]
            )
            verdict = review_content(
                conn,
                settings,
                text=recheck_input,
                context=context,
                is_public_board=content["is_public_board"],
                is_new_account=False,
                recheck=True,
            )
        except Exception:  # noqa: BLE001 — 复审失败不能把内容卡死：按封禁收口，人工可放行
            logger.exception("[automod] recheck failed for queue item %s", row["id"])
            verdict = None
            note = "复审失败（AI 不可用或内部错误），先按封禁处理，请人工确认"

    # 只有复审明确放行才公开；review（不确定）同样不放行——"都必须放行才放行"。
    published = bool(verdict is not None and verdict.decision == DECISION_ALLOW)
    if published:
        note = note or "AI 复审未发现违规，已先行公开"
    else:
        note = note or "AI 复审仍未放行，已先行封禁，请人工复核（可放行）"

    resolution = RESOLUTION_PUBLISHED_BY_AI if published else RESOLUTION_BLOCKED
    recheck = {
        "at": now,
        **_verdict_snapshot(verdict, note=note),
        "published": published,
    }
    claimed = conn.execute(
        moderation_queue.update()
        .where(
            moderation_queue.c.id == row["id"],
            # 抢占条件：仍然是"没落定过"的状态。并发下只有一个事务能拿到 rowcount==1。
            moderation_queue.c.resolution.is_(None),
            moderation_queue.c.superseded_at.is_(None),
        )
        .values(
            resolution=resolution,
            resolved_at=now,
            recheck=json.dumps(recheck, ensure_ascii=False),
            # review_state 保持 pending：这不是人工定案，人工仍可维持或推翻。
        )
    )
    if claimed.rowcount != 1:
        logger.info("[automod] queue item %s was already finalized by someone else", row["id"])
        return None
    apply_review_state(
        conn,
        content_type=row["content_type"],
        content_id=row["content_id"],
        status="approved" if published else "rejected",
    )
    if notify and content["exists"]:
        from .review_queue import notify_author

        notify_author(conn, row, approved=published, note=note, by_ai=True)
    return {
        "id": row["id"],
        "contentType": row["content_type"],
        "contentId": row["content_id"],
        "resolution": resolution,
        "published": published,
        "recheck": recheck,
    }


def finalize_pending(
    conn: Connection,
    settings: Settings,
    *,
    now: int | None = None,
    limit: int | None = None,
    notify: bool = True,
) -> list[dict]:
    """把过了人工确认窗口、仍无人定案的内容复审后落定。

    这是"机器只标记"与"内容不能无限期待审"之间的接口：第一次判定只标记，
    窗口结束后的第二次判定才生效，而且只是**先行**——`review_state` 仍是 pending，
    人工可以维持（追认）或推翻（封禁）。

    幂等：落定用 `UPDATE ... WHERE resolution IS NULL` 抢占，处理过的不会再被处理；
    同一行被 worker 与 `POST /finalize` 同时扫到时只有一个能落定。
    """
    from .schema import moderation_queue

    if not getattr(settings, "automod_enabled", False):
        return []
    if not getattr(settings, "automod_auto_finalize", True):
        return []
    moment = now_ms() if now is None else now
    batch = limit if limit is not None else getattr(settings, "automod_finalize_batch", 50)
    batch = max(1, int(batch))
    rows = conn.execute(
        select(moderation_queue)
        .where(
            moderation_queue.c.review_state == "pending",
            moderation_queue.c.resolution.is_(None),
            moderation_queue.c.superseded_at.is_(None),
            moderation_queue.c.hold_until.is_not(None),
            moderation_queue.c.hold_until <= moment,
        )
        .order_by(moderation_queue.c.hold_until.asc(), moderation_queue.c.id.asc())
        .limit(batch)
    ).all()
    finalized = []
    for raw in rows:
        row = dict(raw._mapping)
        try:
            # savepoint：单条失败要把它自己整个回滚掉，否则会留下"队列已落定、内容却没改"
            # 的半成品——那行再也不会被扫到，内容就永久卡在待审里，比报错更难查。
            # 回滚到 savepoint 后这一行保持 resolution IS NULL，下一轮可以重试；
            # 外层事务不受影响，其余条目照常处理。
            with conn.begin_nested():
                result = _finalize_one(conn, settings, row, now=moment, notify=notify)
        except Exception:  # noqa: BLE001
            # 这里必须吞掉异常。本函数与调用方共用一个事务（worker 一轮一个事务），
            # 让异常上抛会把整批回滚，而队首那条坏数据下一轮还会被扫到——结果是所有
            # 逾期内容永远不落定。跳过它，其余照常。
            logger.exception("[automod] finalize failed for queue item %s, skipping", row.get("id"))
            continue
        if result is not None:
            finalized.append(result)
    return finalized


def queue_counts(conn: Connection) -> dict[str, int]:
    """队列各状态条数。

    除 review_state 的三个计数外，额外给出流程上有意义的桶：
      - awaiting：还在确认窗口内、一次都没处置过（版主最该先看的）；
      - aiPublished：AI 复审放行、已先行公开，等人工追认；
      - aiBlocked：AI 复审未放行、已先行封禁，等人工放行（人工可推翻）；
      - blocked：全部封禁记录（含人工驳回、人工推翻），即管理员留存库的总数。
    """
    from sqlalchemy import func

    from .schema import moderation_queue

    rows = conn.execute(
        select(moderation_queue.c.review_state, func.count())
        .where(moderation_queue.c.superseded_at.is_(None))
        .group_by(moderation_queue.c.review_state)
    ).all()
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for state, total in rows:
        if state in counts:
            counts[state] = total

    def _count(*conds) -> int:
        return int(
            conn.execute(select(func.count()).select_from(moderation_queue).where(moderation_queue.c.superseded_at.is_(None), *conds)).scalar_one()
        )

    counts["awaiting"] = _count(
        moderation_queue.c.review_state == "pending",
        moderation_queue.c.resolution.is_(None),
    )
    counts["aiPublished"] = _count(
        moderation_queue.c.review_state == "pending",
        moderation_queue.c.resolution == RESOLUTION_PUBLISHED_BY_AI,
    )
    counts["aiBlocked"] = _count(
        moderation_queue.c.resolution.in_(BLOCKED_RESOLUTIONS),
        moderation_queue.c.reviewer_id.is_(None),
    )
    counts["blocked"] = int(conn.execute(select(func.count()).select_from(moderation_queue).where(
        moderation_queue.c.resolution.in_(BLOCKED_RESOLUTIONS)
    )).scalar_one())
    return counts
