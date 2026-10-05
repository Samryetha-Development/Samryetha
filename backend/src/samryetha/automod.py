"""审核编排：规则 → 模型 → 队列，并把结果落到内容表与 `moderation_queue`。

调用方只需要 `submit()`：它返回一个判定，调用方据此决定新内容对外可见性。

**降级策略（用户明确要求的 fail-open 到规则层）**：
  - 模型未配置 → 只用规则；
  - 模型超时/报错/返回不可解析 → 记日志 + 只用规则，并在 signals 里留一条
    `llm_unavailable`，让人工知道"这条没经过语义审核"；
  - 无论如何都不因为模型挂了而拒绝发布。

**判定与可见性（"机器只标记，人工追认"，用户确认的流程）**：
  - allow  → 内容 `moderation_status = approved`，立即可见，**不入队**（避免队列被
    正常内容淹没，这是先审后发能长期跑下去的前提）；
  - review → 内容先压住（`pending`，仅作者与版主可见），入队等人确认；
  - block  → **同样只压住**（`pending`）。机器命中不等于封禁成立，见下。

**1 分钟确认窗口 + 逾期 AI 复审**（`finalize_pending`）：
  机器判定不通过时，内容先压住并设 `hold_until = now + AUTOMOD_CONFIRM_WINDOW_SECONDS`。
    - 窗口内版主定案 → 按人工结论落定；
    - 窗口超时且无人定案 → AI **独立复审一次**，由复审结论落定：
        · 复审放行 → 先行公开（`resolution = published_by_ai`）；
        · 复审仍不放行（review/block）→ 封禁（`resolution = blocked`）。
      用户确认的规则是"必须初审和复审都放行才放行，否则封禁"；由于进入窗口的内容
      初审必然未放行，所以实际由**复审单独决定**：复审判通过才公开。
    - 无论哪种落定，`review_state` 都保持 `pending`，人工可以**维持**（追认）或
      **推翻**：推翻 AI 放行 → 改为封禁；推翻 AI 封禁 → 重新放行。两者都记
      `overturned = 1`。
  审核失败（`resolution = blocked`）的内容全部留存，**只有管理员可访问**。
"""

from __future__ import annotations

import json
import logging
from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from .automod_providers import AutomodUnavailable, verdict_from_llm
from .automod_rules import (
    DECISION_ALLOW,
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
    """跑一遍规则 + 模型，返回最终判定。不写库。

    ``is_public_board``：露骨描写规则只在公开版块成立（隐藏版内允许），
    由调用方传入版块可见性。

    ``recheck=True``：这是"窗口超时后的第二次判定"。提示词里会明确要求模型独立重判，
    不要被"此前已被标记"影响（见 automod_providers._RECHECK_PROMPT）。
    """
    rules = evaluate_rules(
        text,
        recent_bodies=recent_bodies or [],
        is_public_board=is_public_board,
        is_new_account=is_new_account,
    )
    provider = _provider_for(settings)
    if provider is None:
        return rules

    model: Verdict | None = None
    try:
        result = provider.classify(text, context=context, recheck=recheck)
        model = verdict_from_llm(result, review_at=LLM_REVIEW_AT)
    except AutomodUnavailable as exc:
        # 降级：模型不可用不阻断发布，但必须留下痕迹，让人工知道这条没经过语义审核。
        logger.warning("automod model unavailable, falling back to rules: %s", exc)
        merged = merge_verdicts(rules, None)
        from .automod_rules import Signal

        merged.signals.append(Signal("llm_unavailable", 0, "语义审核不可用，本次仅按规则判定"))
        return merged
    return merge_verdicts(rules, model)


def held_status(settings: Settings, verdict) -> str | None:
    """内容刚提交后应该写入的 moderation_status；`None` = 不改动（保持默认可见）。

    - 判定 allow → 立即可见，不入队；
    - `AUTOMOD_HOLD_PENDING=false`（先发后审）→ 只入队，内容照样可见；
    - 其余（review/block）→ 一律压成 pending 等确认窗口 / AI 复审 / 人工定案。

    **机器只标记**：review 与 block 都只压住，不成立封禁——要么人工在确认窗口内定案，
    要么窗口超时后由复审落定，再由人工维持或推翻。

    三个写入路径（帖子回复 / 私信 / 用户资料）共用，避免"block 在帖子里被压住、
    在私信里却漏掉"这类不一致。
    """
    if verdict.decision == DECISION_ALLOW:
        return None
    if not getattr(settings, "automod_hold_pending", True):
        return None
    return "pending"


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
    """把一次判定登记进人工队列。allow 不入队（否则队列会被正常内容淹没）。"""
    if verdict.decision == DECISION_ALLOW:
        return None
    from .schema import moderation_queue

    # 同一内容重复入队（编辑后再次提交）时复用未处理的记录，避免队列里出现同一内容多条。
    # hold_until 一并刷新：编辑后重新计时，否则用户能在窗口已经过去时"卡"出一条立即放行的内容。
    existing = conn.execute(
        select(moderation_queue.c.id).where(
            moderation_queue.c.content_type == content_type,
            moderation_queue.c.content_id == content_id,
            moderation_queue.c.review_state == "pending",
        )
    ).first()
    values = {
        "excerpt": excerpt,
        "decision": verdict.decision,
        "score": verdict.score,
        "signals": signals_json(verdict.signals),
        "hold_until": hold_until,
        # 送审文本快照：留存库读它而不是回表读当前值（见 schema.py 该列注释）。
        "submitted_text": submitted_text,
        # **必须把上一版的落定结果清空**。AI 落定后 review_state 仍是 pending，所以这里会
        # 复用到同一行；如果留着旧的 resolution/resolved_at/recheck，新一版内容就被上一版的
        # 结论顶住了：worker 只扫 `resolution IS NULL`，于是永远不再处理这一版，用户会看到
        # 内容卡在待审、而队列里显示的却是上一版的封禁结论。
        # 旧结论也不该被继承：它判的是上一版正文。留痕靠 moderation_actions。
        "resolution": None,
        "resolved_at": None,
        "recheck": "",
    }
    if existing is not None:
        conn.execute(
            moderation_queue.update().where(moderation_queue.c.id == existing.id).values(**values)
        )
        return existing.id
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

    # 判定输入 = 标题 + 正文。`title` 不参与判定是个真实的绕过口子：违规词只放标题、
    # 正文写正常内容就会整体放行（见 PR #70 审查意见 #3）。调用方传进来的 text 若已经
    # 拼过标题（discussions.moderation_text），这里再拼一次不会有副作用——
    # 规则命中和模型的判定都是"命中即可"，重复文本不会改变结论。
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
        hold_until=_hold_deadline(settings),
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
        select(moderation_queue.c.review_state, func.count()).group_by(moderation_queue.c.review_state)
    ).all()
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for state, total in rows:
        if state in counts:
            counts[state] = total

    def _count(*conds) -> int:
        return int(
            conn.execute(select(func.count()).select_from(moderation_queue).where(*conds)).scalar_one()
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
        moderation_queue.c.resolution == RESOLUTION_BLOCKED,
        moderation_queue.c.reviewer_id.is_(None),
    )
    counts["blocked"] = _count(moderation_queue.c.resolution == RESOLUTION_BLOCKED)
    return counts
