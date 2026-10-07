"""
因为这里的注释冲突了并且我懒得管任何一个所以我全删了。
"""

from __future__ import annotations

from samryetha.automod.repository import AutomodRepository

import json
import logging
from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy.engine import Connection

from .providers import AutomodUnavailable, OpenAICompatibleProvider, verdict_from_llm
from .rules import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    Verdict,
    evaluate_rules,
    excerpt_of,
    merge_verdicts,
    signals_json,
)
from ..core.config import Settings
from ..core.db import now_ms
from ..core.ids import ModerationQueueID
from .repository import ContentSnapshot, QueueFinalizationRecord

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


@dataclass(frozen=True, slots=True)
class PreparedFinalization:
    content: ContentSnapshot
    verdict: Verdict | None
    note: str


class FinalizationResult(TypedDict):
    id: ModerationQueueID
    contentType: str
    contentId: int
    resolution: str
    published: bool
    recheck: dict[str, object]


def _provider_for(settings: Settings) -> OpenAICompatibleProvider | None:
    """按配置构造 provider；未配置返回 None（= 只用规则层）。"""
    base_url = settings.automod_base_url
    api_key = settings.automod_api_key
    model = settings.automod_model
    if not base_url or not model:
        return None
    from .providers import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key or "not-needed",
        model=model,
        timeout_seconds=float(settings.automod_timeout_seconds),
        temperature=settings.automod_temperature,
        extra_headers=settings.automod_header_map,
    )


def held_status(settings: Settings, verdict: Verdict) -> str | None:
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
    if not settings.automod_hold_pending:
        return None
    if verdict.decision == DECISION_BLOCK:
        return "rejected"
    return "pending"


def needs_admin_review(verdict: Verdict) -> bool:
    """这条判定是否应当进队列等管理员（事后）复审。

    - 规则层/模型判 block：已直接封禁，但**必须**留一条队列记录，管理员才能察觉并推翻。
      模型判的 block 尤其要——它是"规则拿不准才交给模型"的那批，误判概率高于关键词。
    - 模型判 review：内容被压住，等人放行或确认封禁。
    - allow：不入队（否则队列会被正常内容淹没）。
    """
    return verdict.decision != DECISION_ALLOW


# ---------------------------------------------------------------- 逾期复审


def _verdict_snapshot(verdict: Verdict | None, *, note: str = "") -> dict[str, object]:
    if verdict is None:
        return {"decision": "allow", "score": 0, "source": "system", "signals": [], "note": note}
    return {
        "decision": verdict.decision,
        "score": verdict.score,
        "source": verdict.source,
        "signals": [{"rule": s.rule, "weight": s.weight, "detail": s.detail} for s in verdict.signals],
        "note": note,
    }


class AutomodService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._repository = AutomodRepository(self._conn)

    def _require_settings(self) -> Settings:
        if self._settings is None:
            raise RuntimeError("AutomodService operation requires settings")
        return self._settings

    def assert_author_current(self, user_id: int, *, expected_role: str | None = None) -> None:
        """Revalidate the request actor after acquiring the write lock."""
        from ..core.errors import conflict

        current = self._repository.actor(user_id)
        if (
            current is None
            or current.status != "active"
            or current.deleted
            or (expected_role is not None and current.role != expected_role)
        ):
            raise conflict("Account permissions changed during review; reload and try again")

    def _is_new_account(self, author_id: int) -> bool:
        return not self._repository.has_discussion(author_id)

    def review(
        self,
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
        settings = self._require_settings()
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
            from .rules import Signal

            merged.signals.append(Signal("llm_unavailable", 0, "语义审核不可用，本次仅按规则判定"))
            return merged
        return merge_verdicts(rules, model)

    def supersede_content(self, *, content_type: str, content_id: int) -> None:
        """Keep historical verdicts and snapshots, but retire their write authority."""
        self._repository.supersede_content(content_type=content_type, content_id=content_id, now=now_ms())

    def enqueue(
        self,
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
        # Every submitted version gets an immutable record. Reusing an AI-blocked
        # pending row would erase its retained evidence and let stale decisions act
        # on a different version of the content.
        self.supersede_content(content_type=content_type, content_id=content_id)
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
        return self._repository.insert_queue_item(
            {
                "content_type": content_type,
                "content_id": content_id,
                "author_id": author_id,
                "review_state": "pending",
                "created_at": now_ms(),
                **values,
            }
        )

    def apply_review_state(self, *, content_type: str, content_id: int, status: str) -> None:
        """人工决定回写到内容表。"""
        if content_type in (CONTENT_DISCUSSION, CONTENT_REPLY, CONTENT_MESSAGE):
            self._repository.update_content_status(content_type=content_type, content_id=content_id, status=status)
        elif content_type == CONTENT_PROFILE:
            if status == "approved":
                # 通过：把待审资料提升为正式资料。
                from ..users import UserService

                UserService(self._conn).promote_pending_profile(content_id)
            else:
                # 驳回：只改状态。待审原文（pending_display_name/bio）**故意留着**——
                # 主字段 display_name/bio 仍是上一次通过的值，所以公开面读不到它，
                # 但内容没有被删除，管理员可以从留存库调取。
                self._repository.update_profile_status(content_id, status)
        elif content_type == CONTENT_ATTACHMENT:
            # 附件本身没有 moderation_status 列（它靠 state 表达生命周期）。被驳回的附件
            # 直接标记 orphaned，下载端点会拒绝它；批准则不动。
            if status == "rejected":
                self._repository.orphan_attachment(content_id)
        if status == "approved" and content_type in (CONTENT_DISCUSSION, CONTENT_REPLY):
            from ..events.content_events import ContentEventService

            ContentEventService(self._conn).publish_content(content_type, content_id)

    def prepare_submission(
        self,
        *,
        author_id: int,
        text: str,
        title: str | None = None,
        context: str = "post",
        recent_bodies: list[str] | None = None,
        is_new_account: bool | None = None,
        is_public_board: bool = True,
    ) -> Verdict:
        """Read-only review phase. Call before the request's first database write."""
        self._require_settings()
        if recent_bodies is None:
            recent_bodies = []
        if is_new_account is None:
            is_new_account = self._is_new_account(author_id)

        # 初审统一组合一次标题与正文；快照与模型读取同一版本。
        review_input = f"{title}\n{text}" if title else text
        return self.review(
            text=review_input,
            context=context,
            recent_bodies=recent_bodies,
            is_new_account=is_new_account,
            is_public_board=is_public_board,
        )

    def submit(
        self,
        *,
        content_type: str,
        content_id: int,
        author_id: int,
        text: str,
        verdict: Verdict,
        title: str | None = None,
    ) -> Verdict:
        """Persist a prepared verdict with the content; never call a provider here."""
        self._require_settings()
        review_input = f"{title}\n{text}" if title else text
        self.enqueue(
            content_type=content_type,
            content_id=content_id,
            author_id=author_id,
            excerpt=excerpt_of(title, text),
            verdict=verdict,
            hold_until=None,
            submitted_text=review_input,
        )
        return verdict

    def load_content(self, *, content_type: str, content_id: int) -> ContentSnapshot:
        """取回待审内容的正文与版块可见性，供逾期复审用。

        队列只存摘要（`excerpt`），复审要看全文，所以按类型回表读。读不到（内容已被删除）
        返回 `exists=False`——此时按"无法确认违规"处理，先行放行。
        """
        return self._repository.load_content(content_type=content_type, content_id=content_id)

    def _prepare_finalization(self, row: QueueFinalizationRecord) -> PreparedFinalization:
        """对一条超时未定案的内容做复审，并按复审结论落定。返回处置摘要；被人抢先则返回 None。

        规则（用户确认）：**复审放行才放行**，复审仍不放行（review/block）则封禁。
        "必须初审和复审都放行"——进入窗口的内容初审必然未放行，因此实际由复审决定。

        落定只是"先行"：`review_state` 保持 pending，人工可以维持或推翻。

        **落定要抢占**：`UPDATE ... WHERE resolution IS NULL`，只有 rowcount==1 才回写可见性与
        发通知。定时 worker 与管理员手动 `POST /finalize` 可能同时扫到同一条，没有这个条件
        就会重复落定、重复给作者发通知。
        """
        self._require_settings()
        content = self.load_content(content_type=row.content_type, content_id=row.content_id)
        verdict: Verdict | None = None
        note = ""
        if not content.exists:
            # 内容已被删除（或类型未知）：无可复审，也无法封禁。记一条封禁结论把队列行收口，
            # 避免 worker 每轮都重新扫到它。
            note = "内容已不存在，无法复审"
        else:
            context = "reply" if row.content_type == CONTENT_REPLY else "post"
            try:
                # 复审同样要带标题：只审正文会让"正文正常、标题违规"的内容在复审阶段被放行
                # （见 PR #70 审查意见 #3）。
                recheck_input = f"{content.title}\n{content.text}" if content.title else content.text
                verdict = self.review(
                    text=recheck_input,
                    context=context,
                    is_public_board=content.is_public_board,
                    is_new_account=False,
                    recheck=True,
                )
            except Exception:  # noqa: BLE001 — 复审失败不能把内容卡死：按封禁收口，人工可放行
                logger.exception("[automod] recheck failed for queue item %s", row.id)
                verdict = None
                note = "复审失败（AI 不可用或内部错误），先按封禁处理，请人工确认"

        return PreparedFinalization(content=content, verdict=verdict, note=note)

    def _finalize_one(
        self, row: QueueFinalizationRecord, prepared: PreparedFinalization, *, now: int, notify: bool
    ) -> FinalizationResult | None:
        """Apply a prepared recheck without holding a write lock across model calls."""
        content = prepared.content
        verdict = prepared.verdict
        note = prepared.note
        # 只有复审明确放行才公开；review（不确定）同样不放行——"都必须放行才放行"。
        published = bool(verdict is not None and verdict.decision == DECISION_ALLOW)
        if published:
            note = note or "AI 复审未发现违规，已先行公开"
        else:
            note = note or "AI 复审仍未放行，已先行封禁，请人工复核（可放行）"

        resolution = RESOLUTION_PUBLISHED_BY_AI if published else RESOLUTION_BLOCKED
        recheck: dict[str, object] = {
            "at": now,
            **_verdict_snapshot(verdict, note=note),
            "published": published,
        }
        claimed = self._repository.claim_finalization(
            row.id, resolution=resolution, resolved_at=now, recheck=json.dumps(recheck, ensure_ascii=False)
        )
        if not claimed:
            logger.info("[automod] queue item %s was already finalized by someone else", row.id)
            return None
        self.apply_review_state(
            content_type=row.content_type, content_id=row.content_id, status="approved" if published else "rejected"
        )
        if notify and content.exists:
            from ..review_queue import ReviewQueueService

            ReviewQueueService(self._conn).notify_author_by_id(row.id, approved=published, note=note, by_ai=True)
        return {
            "id": row.id,
            "contentType": row.content_type,
            "contentId": row.content_id,
            "resolution": resolution,
            "published": published,
            "recheck": recheck,
        }

    def finalize_pending(
        self, *, now: int | None = None, limit: int | None = None, notify: bool = True
    ) -> list[FinalizationResult]:
        """把过了人工确认窗口、仍无人定案的内容复审后落定。

        这是"机器只标记"与"内容不能无限期待审"之间的接口：第一次判定只标记，
        窗口结束后的第二次判定才生效，而且只是**先行**——`review_state` 仍是 pending，
        人工可以维持（追认）或推翻（封禁）。

        幂等：落定用 `UPDATE ... WHERE resolution IS NULL` 抢占，处理过的不会再被处理；
        同一行被 worker 与 `POST /finalize` 同时扫到时只有一个能落定。
        """
        settings = self._require_settings()
        if not settings.automod_enabled:
            return []
        if not settings.automod_auto_finalize:
            return []
        moment = now_ms() if now is None else now
        batch = max(1, limit if limit is not None else settings.automod_finalize_batch)
        rows = self._repository.pending_finalizations(moment=moment, limit=batch)
        # Review the entire batch before the first write/savepoint. Otherwise the
        # second provider call would retain the first item's SQLite write lock.
        prepared_rows: list[tuple[QueueFinalizationRecord, PreparedFinalization]] = []
        for row in rows:
            try:
                prepared_rows.append((row, self._prepare_finalization(row)))
            except Exception:  # noqa: BLE001
                logger.exception("[automod] prepare failed for queue item %s, skipping", row.id)
        finalized: list[FinalizationResult] = []
        for row, prepared in prepared_rows:
            try:
                # savepoint：单条失败要把它自己整个回滚掉，否则会留下"队列已落定、内容却没改"
                # 的半成品——那行再也不会被扫到，内容就永久卡在待审里，比报错更难查。
                # 回滚到 savepoint 后这一行保持 resolution IS NULL，下一轮可以重试；
                # 外层事务不受影响，其余条目照常处理。
                with self._conn.begin_nested():
                    result = self._finalize_one(row, prepared, now=moment, notify=notify)
            except Exception:  # noqa: BLE001
                # 这里必须吞掉异常。本函数与调用方共用一个事务（worker 一轮一个事务），
                # 让异常上抛会把整批回滚，而队首那条坏数据下一轮还会被扫到——结果是所有
                # 逾期内容永远不落定。跳过它，其余照常。
                logger.exception("[automod] finalize failed for queue item %s, skipping", row.id)
                continue
            if result is not None:
                finalized.append(result)
        return finalized

    def queue_counts(self) -> dict[str, int]:
        """队列各状态条数。

        除 review_state 的三个计数外，额外给出流程上有意义的桶：
          - awaiting：还在确认窗口内、一次都没处置过（版主最该先看的）；
          - aiPublished：AI 复审放行、已先行公开，等人工追认；
          - aiBlocked：AI 复审未放行、已先行封禁，等人工放行（人工可推翻）；
          - blocked：全部封禁记录（含人工驳回、人工推翻），即管理员留存库的总数。
        """
        return self._repository.queue_counts(
            blocked_resolutions=BLOCKED_RESOLUTIONS, ai_published=RESOLUTION_PUBLISHED_BY_AI
        )
