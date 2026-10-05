"""自动审核端到端：规则层判定 → 入队 → 可见性 → 人工维持/推翻。

当前语义是**发布即审核**（确认窗口与逾期 AI 复审已取消），这些用例覆盖它最关键的不变量：
  - 规则层**零信号** → 立即公开、**不调用模型**、且不进队列（否则队列会被正常内容淹没）；
  - 规则层**确定性命中** → **直接封禁**（`moderation_status = "rejected"`），同时进队列并标
    `resolution = "blocked_by_machine"`，管理员可事后维持或推翻；
  - 规则层**拿不准**（有信号但未达封禁线）→ 才把内容交给模型，模型结论记进队列
    （模型 review → `pending`，等人工看）；
  - `enqueue` 不再设确认窗口（`hold_until` 恒为 `None`），因此
    `finalize_moderation(...)` 对任何新的内容都返回 `[]`；
  - 机器直接封禁的记录：`review_state == "pending"`、`reviewer_id is None`、
    `resolution == "blocked_by_machine"`；人工 approve → `published_by_human`，
    reject → `blocked`；
  - **封禁成立的原文只有管理员能访问**（版主、作者都不行），但从未被删除；
  - 模型不可用时降级到规则层，**不能**因为模型挂了就拒绝发布。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from conftest import Api  # noqa: E402  （tests/ 不是包，靠 rootdir 直接 import）
from samryetha.automod_providers import AutomodUnavailable, LLMVerdict
from samryetha.config import Settings
from samryetha.schema import discussions, moderation_queue, notifications, replies, users


@pytest.fixture
def automod_app(tmp_path):
    """打开自动审核、但不接模型（= 只跑规则层）的 app。"""
    from fastapi.testclient import TestClient

    from samryetha.main import create_app

    app = create_app(
        Settings(
            _env_file=None,
            database_url=str(tmp_path / "automod.db"),
            upload_dir=str(tmp_path / "uploads"),
            automod_enabled=True,
            automod_hold_pending=True,
        )
    )
    app.state.db.create_schema()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def am(automod_app):
    return Api(automod_app)


def _post(api, board_slug: str, body: str, title: str = "T") -> dict:
    response = api.c.post(
        "/api/discussions",
        json={"boardSlug": board_slug, "title": title, "bodyMarkdown": body},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _board(api) -> str:
    """测试库是空的，先建一个公开板块当发帖目标。

    建板块要用管理员身份，而这里不能污染调用方的登录态（登录用的是同一个
    TestClient 的 cookie jar），所以直接写库，不走 API。
    """
    existing = api.c.get("/api/boards").json()["items"]
    if existing:
        return existing[0]["slug"]
    api.seed_builtin()
    from samryetha.db import now_ms
    from samryetha.schema import boards, users

    with api.app.state.db.request_conn() as conn:
        admin = conn.execute(select(users.c.id).where(users.c.username == "dev")).first()
        assert admin is not None
        conn.execute(
            boards.insert().values(
                slug="general",
                name="General",
                description="",
                visibility="public",
                posting_policy="everyone",
                created_by_user_id=admin.id,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
    return "general"


def _queue_rows(app) -> list[dict]:
    with app.state.db.request_conn() as conn:
        rows = conn.execute(select(moderation_queue)).all()
        return [dict(row._mapping) for row in rows]


def _user_id(app, username: str) -> int:
    with app.state.db.request_conn() as conn:
        return conn.execute(select(users.c.id).where(users.c.username == username)).first()[0]


# ---------------------------------------------------------------- 放行路径


def test_normal_content_is_published_and_not_queued(am):
    am.mkuser("alice")
    am.login("alice")
    created = _post(am, _board(am), "大家好，我是新来的，喜欢打篮球和写代码。")
    # 正常内容立即可见
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200
    # 且不进队列——这是队列能长期跑下去的前提
    assert _queue_rows(am.app) == []


# ---------------------------------------------------------------- 送审路径


def test_banned_content_is_blocked_outright_and_queued(am):
    """规则层确定性命中 → **直接封禁**（发布即审核），并进队列供管理员事后复审。"""
    am.mkuser("bob")
    am.login("bob")
    created = _post(am, _board(am), "出售毒品，需要的私聊，量大从优")
    with am.app.state.db.request_conn() as conn:
        row = conn.execute(select(discussions.c.moderation_status).where(discussions.c.id == created["id"])).first()
    # 规则层命中的是确定性关键词，直接成立封禁，不再等确认窗口。
    assert row[0] == "rejected"

    queued = _queue_rows(am.app)
    assert len(queued) == 1
    assert queued[0]["content_type"] == "discussion"
    assert queued[0]["content_id"] == created["id"]
    assert queued[0]["decision"] == "block"
    assert queued[0]["score"] >= 90
    assert "drug_guns" in queued[0]["signals"]
    # 发布即审核：不再有确认窗口（hold_until 为空），队列退化为事后复核清单。
    assert queued[0]["hold_until"] is None
    # 但要标成"机器已封禁 · 待管理员复审"，管理员才能察觉并推翻。
    assert queued[0]["resolution"] == "blocked_by_machine"


def test_blocked_post_is_echoed_back_but_not_visible_to_author(am):
    """发布接口必须回显刚创建的内容，即使它被拦——否则前端在"发布成功"后查不到，
    看起来就是发布失败。

    新语义下封禁是**立即成立**的：作者自己也不再到它（正文只留给管理员）。
    """
    am.mkuser("carol")
    am.login("carol")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    assert created["id"]
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_other_users_cannot_see_pending_content(am):
    am.mkuser("dave")
    am.login("dave")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    am.c.post("/api/auth/logout")

    am.mkuser("erin")
    am.login("erin")
    # 详情用 404 而不是 403：403 等于告诉外人"这里有个被审的帖子"
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    listed = am.c.get("/api/discussions").json()["items"]
    assert all(item["id"] != created["id"] for item in listed)


def test_clear_violation_is_rejected_immediately_with_machine_resolution(am):
    """规则层确定性命中：发布即封禁（rejected），队列记录标成 blocked_by_machine 等人工复审。"""
    am.mkuser("frank")
    am.login("frank")
    created = _post(am, _board(am), "求萝莉资源，未成年裸照")
    row = _queue_rows(am.app)[0]
    assert row["decision"] == "block"
    assert row["score"] >= 90
    # 机器先行处置：已经落定（resolved_at 有值），但 review_state 仍是 pending、
    # reviewer_id 为空——人工仍可在队列里维持或推翻它。
    assert row["resolution"] == "blocked_by_machine"
    assert row["resolved_at"] is not None
    assert row["reviewer_id"] is None
    assert row["review_state"] == "pending"
    # 确认窗口取消：hold_until 恒为空，逾期复审机制没有任何对象可以处理。
    assert row["hold_until"] is None
    assert am.app.state.finalize_moderation() == []
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "rejected"
    # 封禁后连作者也看不到原文（只有管理员可以访问）。
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_reply_is_moderated_too(am):
    am.mkuser("grace")
    am.login("grace")
    created = _post(am, _board(am), "一个完全正常的帖子内容，用来当回复的宿主。")
    reply = am.c.post(
        f"/api/discussions/{created['id']}/replies",
        json={"bodyMarkdown": "出售毒品，需要的私聊"},
    )
    assert reply.status_code == 201
    kinds = {row["content_type"] for row in _queue_rows(am.app)}
    assert "reply" in kinds


# ---------------------------------------------------------------- 人工处置


def test_admin_approve_makes_content_visible(am):
    """人工放行机器封禁 → 内容恢复可见（resolution=published_by_human，记 overturned）。"""
    am.mkuser("henry")
    am.login("henry")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    queued = _queue_rows(am.app)
    queue_id = queued[0]["id"]

    # 普通用户看不到（机器已直接封禁）
    am.c.post("/api/auth/logout")
    am.mkuser("ivy")
    am.login("ivy")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    # 管理员可以处置；队列里能看到"机器已封禁"
    am.mkuser("root5", role="admin")
    am.login("root5")
    listing = am.c.get("/api/admin/moderation/queue").json()
    assert listing["items"] and listing["counts"]["pending"] == 1
    assert listing["items"][0]["resolution"] == "blocked_by_machine"
    assert listing["items"][0]["signals"]

    approved = am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={"note": "看起来没问题"})
    assert approved.status_code == 200
    assert approved.json()["reviewState"] == "approved"
    assert approved.json()["resolution"] == "published_by_human"
    # 推翻机器封禁 → 记 overturned。
    assert approved.json()["overturned"] is True

    # 批准后普通用户可见
    am.c.post("/api/auth/logout")
    am.login("ivy")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_reject_keeps_content_hidden_from_everyone_but_admins(am):
    """封禁成立后原文只有管理员能访问：作者、版主都看不到（处置它的管理员本身除外）。"""
    am.mkuser("jack")
    am.login("jack")
    created = _post(am, _board(am), "有没有血腥视频，发我看看")
    queue_id = _queue_rows(am.app)[0]["id"]

    # 审核失败的内容只有管理员能处置。
    am.c.post("/api/auth/logout")
    am.mkuser("root6", role="admin")
    am.login("root6")
    rejected = am.c.post(f"/api/admin/moderation/queue/{queue_id}/reject", json={"note": "血腥暴力"})
    assert rejected.status_code == 200
    assert rejected.json()["reviewState"] == "rejected"
    assert rejected.json()["resolution"] == "blocked"
    # 确认机器封禁不算推翻。
    assert rejected.json()["overturned"] is False

    # 版主也看不到被封禁的原文——它不在"版主可见"的范围内。
    am.c.post("/api/auth/logout")
    am.mkuser("mod2", role="moderator")
    am.login("mod2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 作者自己也看不到了（封禁成立，正文只留给管理员）。
    am.c.post("/api/auth/logout")
    am.login("jack")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 管理员能拿到原文。
    am.c.post("/api/auth/logout")
    am.login("root6")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_queue_requires_moderator(am):
    am.mkuser("kim")
    am.login("kim")
    assert am.c.get("/api/admin/moderation/queue").status_code == 403


def test_double_review_is_refused(am):
    am.mkuser("liam")
    am.login("liam")
    _post(am, _board(am), "出售毒品，需要的私聊")
    queue_id = _queue_rows(am.app)[0]["id"]
    am.c.post("/api/auth/logout")
    am.mkuser("root7", role="admin")
    am.login("root7")
    assert am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={}).status_code == 200
    again = am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={})
    assert again.status_code == 400


# ------------------------------------------------- 两段分流：什么时候（不）调用模型


class _StepProvider:
    """按调用顺序依次吐出 risk 的假 provider。

    发布即审核的分流是**两段**：规则层确定性命中 → 直封且**不调模型**；
    **其余全部交给模型**，模型结论直接生效。
    规则清单里每条命中都是 100 分，所以凡是没命中关键词的内容都会走到模型
    （包括"零信号"的正常内容——变体写法规则层也认不出，必须让模型看）。
    """

    def __init__(self, risks: list[int]) -> None:
        self.risks = list(risks)
        self.calls = 0

    def classify(self, text, *, context="post", recheck=False):
        self.calls += 1
        risk = self.risks.pop(0) if self.risks else 0
        return LLMVerdict(risk=risk, category="harassment" if risk >= 45 else "none", reason=f"risk={risk}")


def _install_provider(monkeypatch, risks: list[int]) -> _StepProvider:
    from samryetha import automod

    provider = _StepProvider(risks)
    monkeypatch.setattr(automod, "_provider_for", lambda settings: provider)
    return provider


class _ForbiddenProvider:
    """被调用就失败的假 provider：验证"这条内容根本不该走模型"。"""

    def __init__(self) -> None:
        self.calls = 0

    def classify(self, text, *, context="post", recheck=False):
        self.calls += 1
        raise AssertionError("规则层已能定案，模型不该被调用")


def _install_forbidden_provider(monkeypatch) -> _ForbiddenProvider:
    from samryetha import automod

    provider = _ForbiddenProvider()
    monkeypatch.setattr(automod, "_provider_for", lambda settings: provider)
    return provider


def test_rule_block_does_not_call_the_model(am, monkeypatch):
    """规则层确定性命中 → 直接封禁，一次模型调用都不该发生。"""
    provider = _install_forbidden_provider(monkeypatch)
    am.mkuser("nmodel1")
    am.login("nmodel1")
    created = _post(am, _board(am), "求萝莉资源，未成年裸照")
    assert provider.calls == 0
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "rejected"
    assert _queue_rows(am.app)[0]["resolution"] == "blocked_by_machine"


def test_zero_signal_content_still_goes_to_the_model(am, monkeypatch):
    """规则层零信号**也要**交给模型（方案 A）。

    规则层所有关键词权重都是 100，命中即 100、未命中即 0，没有中间态。
    若"零信号直接放行"，变体写法（"我想要买银，有文成年图片咝"）就会连模型都不过——
    而变体恰恰最需要语义判定。所以只有"规则已确定"才跳过模型。
    """
    provider = _install_provider(monkeypatch, [5])
    am.mkuser("nmodel2")
    am.login("nmodel2")
    created = _post(am, _board(am), "今天食堂的红烧肉有点咸，别的都挺好")
    assert provider.calls == 1, "零信号内容也必须过模型"
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200
    assert _queue_rows(am.app) == []


def test_non_matching_content_calls_the_model_once(am, monkeypatch):
    """规则没命中 → 交给模型，且只调用一次；模型判 review 则压成 pending 等人工。"""
    provider = _install_provider(monkeypatch, [60])
    am.mkuser("nmodel3")
    am.login("nmodel3")
    _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    assert provider.calls == 1
    assert _queue_rows(am.app)[0]["decision"] == "review"


def test_model_review_verdict_holds_content_pending(am, monkeypatch):
    """模型判到封禁线以上 → 直接封禁 + 进队列（blocked_by_machine），管理员可推翻。"""
    provider = _install_provider(monkeypatch, [90])
    am.mkuser("owen")
    am.login("owen")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]
    assert provider.calls == 1
    # 90 >= LLM_BLOCK_AT(85) → 模型判 block，直接封禁并标记待管理员复审
    assert queued["decision"] == "block"
    assert queued["resolution"] == "blocked_by_machine"
    assert queued["hold_until"] is None
    assert queued["score"] == 90
    assert "llm:harassment" in queued["signals"]
    # 已被机器直接封禁：**连作者也看不到**（封禁原文仅管理员可见）
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    am.c.post("/api/auth/logout")
    am.mkuser("owen2")
    am.login("owen2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_model_block_verdict_is_rejected_and_queued_as_machine_block(am):
    """模型判 block 的落库语义：直接封禁（rejected）+ 进队列，标 blocked_by_machine。

    模型判到 `LLM_BLOCK_AT`（默认 85）以上时，`verdict_from_llm` 直接产出 block，
    `merge_verdicts` 也不再把它降级成 review——AI 封禁直接生效，靠队列 + 管理员推翻兜底。
    这里直接构造判定，验证 `held_status` / `enqueue` 的处理与"机器直接封禁"一致。
    """
    from samryetha.automod import RESOLUTION_BLOCKED_BY_MACHINE, enqueue, held_status
    from samryetha.automod_rules import Signal, Verdict

    am.mkuser("pam")
    am.login("pam")
    created = _post(am, _board(am), "一条完全正常的内容，用来承载模型封禁的判定。")
    verdict = Verdict(
        decision="block", score=95, signals=[Signal("llm:csam", 95, "变体写法")], source="llm"
    )
    assert held_status(am.app.state.settings, verdict) == "rejected"
    with am.app.state.db.request_conn() as conn:
        enqueue(
            conn,
            content_type="discussion",
            content_id=created["id"],
            author_id=_user_id(am.app, "pam"),
            excerpt="T",
            verdict=verdict,
        )
    row = [r for r in _queue_rows(am.app) if r["content_id"] == created["id"]][0]
    assert row["decision"] == "block"
    assert row["resolution"] == RESOLUTION_BLOCKED_BY_MACHINE
    assert row["resolved_at"] is not None
    assert row["hold_until"] is None
    assert row["review_state"] == "pending"
    assert row["reviewer_id"] is None


def test_finalize_reports_no_overdue_items(am):
    """确认窗口取消 → 队列里不再有 hold_until，逾期复审恒返回空。"""
    am.mkuser("quinn")
    am.login("quinn")
    _post(am, _board(am), "出售毒品，需要的私聊")
    row = _queue_rows(am.app)[0]
    assert row["hold_until"] is None
    # 无论把时钟推到什么时候，都没有"到期项"可落定。
    assert am.app.state.finalize_moderation() == []
    assert am.app.state.finalize_moderation(now=row["resolved_at"] + 10**9) == []
    after = _queue_rows(am.app)[0]
    assert after["resolution"] == "blocked_by_machine"
    assert after["recheck"] == ""  # 复审机制取消后不再写复审快照


def test_auto_finalize_switch_no_longer_changes_outcomes(tmp_path):
    """AUTOMOD_AUTO_FINALIZE 已经没有对象：窗口取消了，没有逾期项可落定。

    保留这条测试是为了防止"开关被接成影响新内容可见性"的回归：命中即封禁与开关无关，
    finalize 也永远返回空。
    """
    from fastapi.testclient import TestClient

    from samryetha.main import create_app

    app = create_app(
        Settings(
            _env_file=None,
            database_url=str(tmp_path / "noauto.db"),
            upload_dir=str(tmp_path / "uploads"),
            automod_enabled=True,
            automod_auto_finalize=False,
        )
    )
    app.state.db.create_schema()
    with TestClient(app) as client:
        api = Api(client)
        board = _board(api)
        api.mkuser("zoe")
        api.login("zoe")
        created = api.c.post(
            "/api/discussions",
            json={"boardSlug": board, "title": "T", "bodyMarkdown": "出售毒品，需要的私聊"},
        ).json()

        with app.state.db.request_conn() as conn:
            queued = dict(conn.execute(select(moderation_queue)).first()._mapping)
            status = conn.execute(
                select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
            ).first()[0]
        # 命中即封禁，与 AUTO_FINALIZE 无关；队列也不再设窗口。
        assert status == "rejected"
        assert queued["hold_until"] is None
        assert queued["resolution"] == "blocked_by_machine"
        assert app.state.finalize_moderation(now=queued["resolved_at"] + 10**9) == []
        with app.state.db.request_conn() as conn:
            row = dict(conn.execute(select(moderation_queue)).first()._mapping)
        assert row["resolution"] == "blocked_by_machine"


def test_moderator_can_confirm_machine_block(am):
    """人工确认机器封禁 → resolution 变 blocked，内容保持不可见并进入留存库。

    确认机器结论不算"推翻"（overturned=False）。
    """
    am.mkuser("rita")
    am.login("rita")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    queued = _queue_rows(am.app)[0]

    am.c.post("/api/auth/logout")
    am.mkuser("root4", role="admin")
    am.login("root4")
    # 队列里要能认出"机器已封禁、等管理员复审"
    listing = am.c.get("/api/admin/moderation/queue").json()
    item = [i for i in listing["items"] if i["id"] == queued["id"]][0]
    assert item["resolution"] == "blocked_by_machine"
    assert item["needsRelease"] is True
    assert item["reviewState"] == "pending"

    confirmed = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/reject", json={"note": "确认封禁"})
    assert confirmed.status_code == 200
    assert confirmed.json()["reviewState"] == "rejected"
    assert confirmed.json()["resolution"] == "blocked"
    assert confirmed.json()["overturned"] is False

    row = [r for r in _queue_rows(am.app) if r["id"] == queued["id"]][0]
    assert row["review_state"] == "rejected"
    assert row["reviewer_id"] is not None
    # 确认后内容仍不可见
    am.c.post("/api/auth/logout")
    am.login("rita")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_admin_can_release_machine_block(am):
    """管理员推翻机器封禁（approve）→ 重新放行（published_by_human / overturned=1）。"""
    am.mkuser("sam")
    am.login("sam")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    queued = _queue_rows(am.app)[0]

    am.c.post("/api/auth/logout")
    am.mkuser("root5b", role="admin")
    am.login("root5b")
    released = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={"note": "误封"})
    assert released.status_code == 200
    assert released.json()["resolution"] == "published_by_human"
    # 推翻机器封禁 → 记 overturned。
    assert released.json()["overturned"] is True
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_blocked_discussion_disappears_from_saved(am):
    """收藏也是可见性的一个出口：内容一旦被封禁，不能还从"我的收藏"读到标题和摘要。"""
    am.mkuser("xavier")
    am.login("xavier")
    created = _post(am, _board(am), "一条完全正常的帖子，用来验证收藏的可见性。")
    assert am.c.post(f"/api/discussions/{created['id']}/save").status_code == 200

    def saved_ids() -> list[int]:
        data = am.c.get("/api/users/xavier/saved").json()
        return [item["id"] for item in data["items"]]

    assert created["id"] in saved_ids()

    # 作者把正文改成违禁内容 → 重新过审 → 直接封禁 → 收藏里也要消失
    patched = am.c.patch(
        f"/api/discussions/{created['id']}", json={"bodyMarkdown": "出售毒品，需要的私聊"}
    )
    assert patched.status_code == 200, patched.text
    assert created["id"] not in saved_ids()
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


# `test_one_poison_row_does_not_block_the_batch` 已删除：它验证的是逾期复审批处理里用
# savepoint 隔离坏数据。确认窗口取消后不再有到期项，`finalize_pending` 恒返回空，
# 这条批处理路径连同它要防的问题一起消失了。


def test_admin_can_release_machine_block_but_moderator_cannot(am):
    """机器直接封禁只有管理员能处置：封禁原文仅管理员可访问，版主不该对看不到的记录做决定。"""
    am.mkuser("tara")
    am.login("tara")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    queued = _queue_rows(am.app)[0]
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 版主：看不到封禁摘要，也不能处置（否则是在对自己看不到原文的记录做决定）
    am.c.post("/api/auth/logout")
    am.mkuser("mod6", role="moderator")
    am.login("mod6")
    listing = am.c.get("/api/admin/moderation/queue").json()
    item = [i for i in listing["items"] if i["id"] == queued["id"]][0]
    assert item["needsRelease"] is True
    assert item["excerptRestricted"] is True
    assert item["excerpt"] == ""
    assert am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={}).status_code == 403
    assert am.c.post(f"/api/admin/moderation/queue/{queued['id']}/reject", json={}).status_code == 403

    # 管理员：摘要可见，可以推翻机器封禁
    am.c.post("/api/auth/logout")
    am.mkuser("root3", role="admin")
    am.login("root3")
    admin_listing = am.c.get("/api/admin/moderation/queue").json()
    admin_item = [i for i in admin_listing["items"] if i["id"] == queued["id"]][0]
    assert admin_item["excerptRestricted"] is False
    assert admin_item["excerpt"]

    released = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={"note": "误封，放行"})
    assert released.status_code == 200
    assert released.json()["resolution"] == "published_by_human"
    assert released.json()["overturned"] is True
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


# ---------------------------------------------------------------- 管理员留存库


def test_retained_archive_is_admin_only_and_keeps_the_body(am):
    """审核失败的原文全部留存，但只有管理员能访问。"""
    am.mkuser("uma")
    am.login("uma")
    body = "出售毒品，需要的私聊，量大从优"
    created = _post(am, _board(am), body)
    queued = _queue_rows(am.app)[0]
    # 机器直接封禁的记录也进留存库——不然"全部留存"对机器封禁就不成立。
    assert queued["resolution"] == "blocked_by_machine"

    # 版主：拿不到留存库
    am.c.post("/api/auth/logout")
    am.mkuser("mod7", role="moderator")
    am.login("mod7")
    assert am.c.get("/api/admin/moderation/retained").status_code == 403

    # 管理员：拿得到，而且正文还在（内容从未被删除）
    am.c.post("/api/auth/logout")
    am.mkuser("root", role="admin")
    am.login("root")
    archive = am.c.get("/api/admin/moderation/retained").json()
    assert archive["total"] == 1
    item = archive["items"][0]
    assert item["contentId"] == created["id"]
    # 留存库返回**送审快照**（标题 + 正文，因为标题也参与判定），不是回表读当前值。
    assert item["body"].endswith(body)
    assert item["fromSnapshot"] is True
    assert item["contentExists"] is True
    assert item["resolution"] == "blocked_by_machine"
    assert item["author"] and item["author"]["username"] == "uma"
    # 逾期复审机制已取消：不再有 AI 复审快照，但机器判定本身要留痕。
    assert item["recheck"] is None
    assert item["decision"] == "block"
    assert item["signals"] and "drug_guns" in str(item["signals"][0])


def test_finalize_endpoint_is_admin_only(am):
    """终结点权限不变；但窗口取消后它不再有任何到期项可处理（count 恒为 0）。"""
    am.mkuser("vito")
    am.login("vito")
    _post(am, _board(am), "出售毒品，需要的私聊")

    am.c.post("/api/auth/logout")
    am.mkuser("mod8", role="moderator")
    am.login("mod8")
    assert am.c.post("/api/admin/moderation/finalize").status_code == 403

    am.c.post("/api/auth/logout")
    am.mkuser("root2", role="admin")
    am.login("root2")
    forced = am.c.post("/api/admin/moderation/finalize")
    assert forced.status_code == 200
    body = forced.json()
    # 没有 hold_until，就没有任何到期项；机器封禁早已在发布时落定。
    assert body["count"] == 0
    assert body["blocked"] == 0 and body["published"] == 0
    assert body["items"] == []


def test_blocked_content_never_leaks_through_search(am):
    """搜索也必须挡住被封禁的内容——漏了这一条，搜索就是绕过封禁的后门。"""
    am.mkuser("wade")
    am.login("wade")
    created = _post(am, _board(am), "有没有血腥视频，发我看看")
    # 发布即封禁：作者自己也打不开
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 其他人搜不到
    am.c.post("/api/auth/logout")
    am.mkuser("xena")
    am.login("xena")
    hits = am.c.get("/api/search", params={"q": "血腥"}).json()
    assert all(item["id"] != created["id"] for item in hits["items"])

    # 作者本人也搜不到自己的封禁内容（正文只留给管理员）
    am.c.post("/api/auth/logout")
    am.login("wade")
    own_hits = am.c.get("/api/search", params={"q": "血腥"}).json()
    assert all(item["id"] != created["id"] for item in own_hits["items"])

    # 管理员仍搜得到（留存与申诉需要）
    am.c.post("/api/auth/logout")
    am.mkuser("rootw", role="admin")
    am.login("rootw")
    admin_hits = am.c.get("/api/search", params={"q": "血腥"}).json()
    assert any(item["id"] == created["id"] for item in admin_hits["items"])


# ---------------------------------------------------------------- 个人资料


def _patch_profile(api, body: dict) -> dict:
    response = api.c.patch("/api/me/profile", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_edit_is_held_and_keeps_the_old_public_text(am):
    """资料被机器封禁时对外仍展示旧资料（规范 §31），新资料进队列、公开面读不到。"""
    am.mkuser("pat")
    am.login("pat")
    # 第一次改成正常资料：规则层零信号 → 直接放行并提升
    _patch_profile(am, {"displayName": "好人", "bio": "正常简介"})
    public = am.c.get("/api/users/pat").json()
    assert public["displayName"] == "好人" and public["bio"] == "正常简介"
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is False

    # 再改成命中规则层的资料 → 发布即封禁，新资料进队列且不覆盖主字段
    _patch_profile(am, {"displayName": "新名字", "bio": "求萝莉资源，未成年裸照"})

    # 对外（含自己）看到的仍是旧资料
    public = am.c.get("/api/users/pat").json()
    assert public["displayName"] == "好人"
    assert public["bio"] == "正常简介"
    # 但界面要能知道有新版压着待审
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is True
    # 被封禁的这一版进了队列，并标成"机器已封禁"
    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"][0]
    assert queued["decision"] == "block"
    assert queued["resolution"] == "blocked_by_machine"
    assert queued["hold_until"] is None


def test_releasing_a_blocked_profile_promotes_the_new_text(am):
    """管理员放行被机器封禁的资料 → 新资料生效（提升），状态回到 approved。"""
    am.mkuser("pip")
    am.login("pip")
    _patch_profile(am, {"displayName": "旧名字", "bio": "旧简介"})
    _patch_profile(am, {"displayName": "新名字", "bio": "求萝莉资源，未成年裸照"})
    assert am.c.get("/api/users/pip").json()["displayName"] == "旧名字"

    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"][0]
    assert queued["resolution"] == "blocked_by_machine"

    am.c.post("/api/auth/logout")
    am.mkuser("modpip", role="admin")
    am.login("modpip")
    released = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={"note": "误封"})
    assert released.status_code == 200
    assert released.json()["resolution"] == "published_by_human"

    am.c.post("/api/auth/logout")
    am.login("pip")
    public = am.c.get("/api/users/pip").json()
    assert public["displayName"] == "新名字" and public["bio"] == "求萝莉资源，未成年裸照"
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is False


def test_blocked_profile_text_is_retained_for_admins_only(am):
    """审核失败的资料原文：公开面读不到、没有删除，只有管理员能从留存库调取。"""
    am.mkuser("quill")
    am.login("quill")
    _patch_profile(am, {"displayName": "正经名字", "bio": "正经简介"})

    # 命中规则层 → 机器直接封禁（资料不经窗口，直接进留存库）
    _patch_profile(am, {"displayName": "新名字", "bio": "求萝莉资源，未成年裸照"})
    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"][0]
    assert queued["resolution"] == "blocked_by_machine"

    # 公开面（含本人）看不到失败原文
    public = am.c.get("/api/users/quill").json()
    assert "新名字" not in public["displayName"] and "求萝莉资源" not in (public["bio"] or "")
    assert public["displayName"] == "正经名字"

    # 管理员留存库拿得到原文，且内容确实还在库里（只是不在公开字段上）
    am.c.post("/api/auth/logout")
    am.mkuser("root4", role="admin")
    am.login("root4")
    archive = am.c.get("/api/admin/moderation/retained").json()
    profile_items = [item for item in archive["items"] if item["contentType"] == "profile"]
    assert profile_items, archive["items"]
    assert "新名字" in profile_items[0]["body"] and "求萝莉资源" in profile_items[0]["body"]
    assert profile_items[0]["contentExists"] is True

    with am.app.state.db.request_conn() as conn:
        row = conn.execute(select(users).where(users.c.username == "quill")).first()
    assert row.pending_display_name == "新名字"
    assert row.pending_bio == "求萝莉资源，未成年裸照"


def test_profile_edits_go_straight_through_when_automod_is_off(tmp_path):
    """关闭审核时资料照旧直接写主字段（与改动前完全一致）。"""
    from fastapi.testclient import TestClient

    from samryetha.main import create_app

    app = create_app(
        Settings(_env_file=None, database_url=str(tmp_path / "prof.db"), upload_dir=str(tmp_path / "up"))
    )
    app.state.db.create_schema()
    with TestClient(app) as client:
        api = Api(client)
        api.mkuser("rae")
        api.login("rae")
        body = {"displayName": "随便改", "bio": "随便写"}
        assert api.c.patch("/api/me/profile", json=body).status_code == 200
        public = api.c.get("/api/users/rae").json()
        assert public["displayName"] == "随便改" and public["bio"] == "随便写"
        with app.state.db.request_conn() as conn:
            assert conn.execute(select(moderation_queue)).all() == []


# ---------------------------------------------------------------- 降级


def test_model_failure_falls_back_to_rules_and_still_publishes(am, monkeypatch):
    """模型挂了不能变成"全站发不了帖"。"""
    from samryetha import automod

    class BrokenProvider:
        def classify(self, text, *, context="post", recheck=False):
            raise AutomodUnavailable("boom")

    monkeypatch.setattr(automod, "_provider_for", lambda settings: BrokenProvider())
    am.mkuser("mia")
    am.login("mia")
    created = _post(am, _board(am), "完全正常的一条内容，模型挂了也应该能发出来。")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_rules_still_hold_when_model_is_down(am, monkeypatch):
    from samryetha import automod

    class BrokenProvider:
        def classify(self, text, *, context="post", recheck=False):
            raise AutomodUnavailable("boom")

    monkeypatch.setattr(automod, "_provider_for", lambda settings: BrokenProvider())
    am.mkuser("nina")
    am.login("nina")
    created = _post(am, _board(am), "求萝莉资源，未成年裸照")
    # 模型挂了也一样：规则层命中即封禁，根本不需要模型参与。
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "rejected"
    assert _queue_rows(am.app)[0]["decision"] == "block"


def test_automod_disabled_publishes_everything(tmp_path):
    """总开关关闭时（默认）行为与改动前完全一致。"""
    from fastapi.testclient import TestClient

    from samryetha.main import create_app

    app = create_app(
        Settings(_env_file=None, database_url=str(tmp_path / "off.db"), upload_dir=str(tmp_path / "up"))
    )
    app.state.db.create_schema()
    with TestClient(app) as client:
        api = Api(client)
        board = _board(api)
        api.mkuser("olive")
        api.login("olive")
        created = api.c.post(
            "/api/discussions",
            json={"boardSlug": board, "title": "T", "bodyMarkdown": "出售毒品，需要的私聊"},
        ).json()
        assert api.c.get(f"/api/discussions/{created['id']}").status_code == 200
        with app.state.db.request_conn() as conn:
            assert conn.execute(select(moderation_queue)).all() == []


# ---------------------------------------------------------------- provider 细节


def test_provider_sends_identity_headers_and_parses_json():
    """请求头要带上身份与会话 ID（OpenCode Go 等网关据此判定是否为滥用流量）。"""
    import httpx

    from samryetha.automod_providers import OpenAICompatibleProvider

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"risk": 10, "category": "none", "reason": "正常"}'}}]},
            request=httpx.Request("POST", url),
        )

    monkey = httpx.post
    httpx.post = fake_post
    try:
        provider = OpenAICompatibleProvider(
            base_url="https://opencode.ai/zen/go/v1",
            api_key="sk-test",
            model="mimo-v2.6-flash",
            extra_headers={"user-agent": "samryetha-automod/1.0", "x-opencode-session": "samryetha"},
        )
        verdict = provider.classify("大家好")
    finally:
        httpx.post = monkey

    assert captured["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert captured["headers"]["x-opencode-session"] == "samryetha"
    assert captured["headers"]["user-agent"] == "samryetha-automod/1.0"
    assert captured["payload"]["model"] == "mimo-v2.6-flash"
    assert verdict.risk == 10


def test_provider_retries_without_response_format_when_unsupported():
    """端点不支持 response_format 时要去掉它重试，而不是直接判"模型不可用"。"""
    import httpx

    from samryetha.automod_providers import OpenAICompatibleProvider

    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if "response_format" in json:
            return httpx.Response(
                400,
                json={"error": "unsupported parameter: response_format"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"risk": 5, "category": "none", "reason": "ok"}'}}]},
            request=httpx.Request("POST", url),
        )

    monkey = httpx.post
    httpx.post = fake_post
    try:
        provider = OpenAICompatibleProvider(base_url="http://x/v1", api_key="k", model="m")
        verdict = provider.classify("hi")
    finally:
        httpx.post = monkey
    assert len(calls) == 2 and "response_format" not in calls[1]
    assert verdict.risk == 5


def test_model_risk_above_threshold_goes_to_review_not_block():
    """模型判高分也只能送人工——自动拒绝是不可逆的用户伤害。"""
    from samryetha.automod_providers import LLMVerdict, verdict_from_llm

    verdict = verdict_from_llm(LLMVerdict(risk=95, category="harassment", reason="攻击"), review_at=45)
    assert verdict.decision == "review"


def test_provider_appends_independent_recheck_instruction():
    """复审必须显式要求"独立重判"。

    不写这句，模型会锚定"它已被标记"这件事，倾向于确认前一次判定——
    复审就退化成复读，整条"超时复审"流程失去意义。
    """
    import httpx

    from samryetha.automod_providers import OpenAICompatibleProvider

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"risk": 5, "category": "none", "reason": "正常"}'}}]},
            request=httpx.Request("POST", url),
        )

    monkey = httpx.post
    httpx.post = fake_post
    try:
        provider = OpenAICompatibleProvider(base_url="http://x/v1", api_key="k", model="m")
        provider.classify("大家好", recheck=True)
    finally:
        httpx.post = monkey

    messages = captured["payload"]["messages"]
    # 初审只有 system + user 两条；复审要多一条明确要求独立重判的 user 消息。
    assert len(messages) == 3
    recheck_text = messages[-1]["content"]
    assert "复审" in recheck_text
    assert "独立" in recheck_text and "忽略" in recheck_text


# ---------------------------------------------------------------- 新规则清单


def test_banned_categories_all_block_outright():
    """用户给定的封禁清单：命中即封，不经过"转人工"。"""
    from samryetha.automod_rules import evaluate_rules

    cases = {
        "violent_gore": "血腥视频流出，点击查看",
        "trafficking": "有没有渠道做人口贩卖的",
        "drug_guns": "出售毒品，另有大麻交易",
        "csam": "求萝莉资源，未成年裸照",
        "political_abuse": "这个脑残政府又出政策了",
        "personal_attack": "你就是个废物，滚出这个学校",
        "repeat_spam": "顶顶顶顶顶顶顶顶顶顶",
    }
    for rule, text in cases.items():
        verdict = evaluate_rules(text)
        assert verdict.decision == "block", f"{rule} 未封禁：{verdict.decision}"
        assert rule in {s.rule for s in verdict.signals}, f"{rule} 未命中"


def test_explicit_content_blocked_on_public_board_only():
    """露骨描写只在公开版块算违规；隐藏版内允许。"""
    from samryetha.automod_rules import evaluate_rules

    text = "这段性行为描写很详细"
    assert evaluate_rules(text, is_public_board=True).decision == "block"
    hidden = evaluate_rules(text, is_public_board=False)
    assert hidden.decision == "allow"
    assert hidden.score == 0


def test_removed_categories_no_longer_flag():
    """赌博/诈骗/学术不端/站外引流/隐私 已从封禁清单移除。"""
    from samryetha.automod_rules import evaluate_rules

    for text in (
        "推荐一个博彩网站，赔率很高",
        "无门槛兼职，日赚千元",
        "代写论文包过",
        "有兴趣的加微信 abcdef123456",
        "我知道他家住哪，要不要曝光他",
    ):
        verdict = evaluate_rules(text)
        assert verdict.decision == "allow", f"仍被拦截：{text} → {verdict.decision}"


def test_criticism_and_negative_emotion_are_never_flagged():
    """批评学校、负面情绪、争议话题都不能被规则层拦下——这是公测的底线。"""
    from samryetha.automod_rules import evaluate_rules

    for text in (
        "我觉得学校这个规定不太合理，想听听大家意见",
        "这次月考数学炸了，求安慰",
        "老师讲得太快了完全跟不上",
        "食堂的饭也太难吃了吧",
        "我压力好大，有点撑不住",
    ):
        assert evaluate_rules(text).decision == "allow", text


def test_noise_detection_catches_repetition_but_not_normal_text():
    from samryetha.automod_rules import evaluate_rules

    assert evaluate_rules("aaaaaaaaaaaaaaaa").decision == "block"
    assert evaluate_rules("哈哈哈哈哈哈哈哈哈哈").decision == "block"
    # 正常长句、含重复词但语义完整的句子都不该命中
    for text in ("今天的天气真的很不错，适合出去玩", "我我我觉得这个方案可以再讨论一下"):
        assert evaluate_rules(text).decision == "allow", text


# ---------------------------------------------------------------- PR #70 审查意见回归


def test_editing_a_post_reenters_moderation(am, monkeypatch):
    """审查 #2：改文不能沿用旧结论。

    先发正常内容拿到 approved，再 PATCH 成违禁正文——不重审的话内容会带着 approved
    留在公开面。
    """
    _install_provider(monkeypatch, [5])
    am.mkuser("ed1")
    am.login("ed1")
    created = _post(am, _board(am), "完全正常的一条内容")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200

    patched = am.c.patch(
        f"/api/discussions/{created['id']}", json={"bodyMarkdown": "求萝莉资源，未成年裸照"}
    )
    assert patched.status_code == 200, patched.text

    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "rejected", "编辑后没有重新过审"
    queued = [r for r in _queue_rows(am.app) if r["content_id"] == created["id"]]
    assert queued, "编辑后的内容没有进队列"
    # 非作者看不到
    am.c.post("/api/auth/logout")
    am.mkuser("ed2")
    am.login("ed2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_editing_a_reply_reenters_moderation(am):
    """审查 #2：回复同样要重审；命中规则层 → 发布即封禁。"""
    am.mkuser("er1")
    am.login("er1")
    created = _post(am, _board(am), "正常帖子用来当回复的宿主")
    reply = am.c.post(
        f"/api/discussions/{created['id']}/replies", json={"bodyMarkdown": "一条正常回复"}
    ).json()
    patched = am.c.patch(f"/api/replies/{reply['id']}", json={"bodyMarkdown": "求萝莉资源，未成年裸照"})
    assert patched.status_code == 200, patched.text
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(select(replies.c.moderation_status).where(replies.c.id == reply["id"])).first()[0]
    assert status == "rejected"
    queued = [
        r for r in _queue_rows(am.app) if r["content_type"] == "reply" and r["content_id"] == reply["id"]
    ]
    assert queued and queued[0]["resolution"] == "blocked_by_machine"


def test_title_participates_in_review(am):
    """审查 #3：违规词只放标题、正文正常，也要被拦。"""
    am.mkuser("ti1")
    am.login("ti1")
    created = _post(am, _board(am), "这是一个完全正常的正文内容", title="求萝莉资源，未成年裸照")
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    # block 现在直接封禁（发布即审核），不再是 pending
    assert status == "rejected", "标题没有参与判定"
    assert any(r["content_id"] == created["id"] for r in _queue_rows(am.app))


def test_conversation_preview_hides_blocked_message(am):
    """审查 #4：会话预览与未读数不能泄漏被封禁私信的完整正文。"""
    am.mkuser("cv1")
    am.mkuser("cv2")
    am.login("cv1")
    am.c.post("/api/messages", json={"username": "cv2", "body": "先发一条正常的"})
    # 第二条命中规则层 → 发布即封禁，收件人不该看到它
    am.c.post("/api/messages", json={"username": "cv2", "body": "出售毒品，需要的私聊"})

    am.c.post("/api/auth/logout")
    am.login("cv2")
    convs = am.c.get("/api/messages/conversations").json()
    item = convs["items"][0]
    assert item["lastMessage"]["body"] == "先发一条正常的", "会话预览泄漏了被封禁私信正文"
    # 收件人侧：被封禁的那条不算未读——会话里的未读数只该是那条已放行、尚未读的正常私信。
    # 全局未读数必须与之一致（此前它不过滤审核状态，会显示 2 条但只找得到 1 条）。
    assert item["unreadCount"] == 1
    assert am.c.get("/api/messages/unread-count").json()["unreadCount"] == 1


def test_blocked_parent_discussion_hides_its_replies(am, monkeypatch):
    """审查 #7：父帖不可见时，回复接口与用户回复列表也要挡住（含标题）。"""
    _install_provider(monkeypatch, [5])
    am.mkuser("pr1")
    am.login("pr1")
    created = _post(am, _board(am), "正常帖子正文")
    am.c.post(f"/api/discussions/{created['id']}/replies", json={"bodyMarkdown": "一条正常回复"})

    # 直接把父帖压成 rejected（等于人工封禁后的状态）
    with am.app.state.db.request_conn() as conn:
        conn.execute(
            discussions.update().where(discussions.c.id == created["id"]).values(moderation_status="rejected")
        )

    am.c.post("/api/auth/logout")
    am.mkuser("pr2")
    am.login("pr2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    assert am.c.get(f"/api/discussions/{created['id']}/replies").status_code == 404
    listed = am.c.get("/api/users/pr1/replies").json()
    assert listed["items"] == [], "被封禁帖子的回复仍从用户回复列表泄漏"


def test_queue_href_for_reply_points_at_parent_discussion(am):
    """审查 #16：reply 的 href 要用父帖 id，不是回复 id。"""
    am.mkuser("hr1")
    am.login("hr1")
    board = _board(am)
    _post(am, board, "第一帖")
    second = _post(am, board, "第二帖")

    # 命中规则层 → 回复直接进队列（机器已封禁，仍要留痕给人工复审）
    am.c.post(f"/api/discussions/{second['id']}/replies", json={"bodyMarkdown": "出售毒品，需要的私聊"})

    am.c.post("/api/auth/logout")
    am.mkuser("modhr", role="moderator")
    am.login("modhr")
    listing = am.c.get("/api/admin/moderation/queue?status=pending").json()
    replies_items = [i for i in listing["items"] if i["contentType"] == "reply"]
    assert replies_items
    item = replies_items[0]
    assert item["href"].startswith(f"/d/{second['id']}#reply-"), item["href"]


def test_resubmitted_profile_gets_its_own_queue_record(am):
    """审查 #8：每次重提都要新起一条队列记录，旧结论不能顶住新版本。"""
    am.mkuser("rs1")
    am.login("rs1")
    _patch_profile(am, {"displayName": "正常名字", "bio": "正常简介"})

    # 第二版命中规则层 → 机器直接封禁
    _patch_profile(am, {"displayName": "被拒名字", "bio": "求萝莉资源，未成年裸照"})
    row = [r for r in _queue_rows(am.app) if r["content_type"] == "profile"][0]
    assert row["resolution"] == "blocked_by_machine"

    # 再提交一版正常的：旧记录被标 superseded，新版本照常放行/提升，
    # 不会被上一版的封禁结论顶住。
    _patch_profile(am, {"displayName": "又一名", "bio": "又一分"})
    rows = sorted(
        (r for r in _queue_rows(am.app) if r["content_type"] == "profile"), key=lambda r: r["id"]
    )
    assert rows[0]["superseded_at"] is not None, "旧版本仍在写回当前状态"
    assert [r for r in rows if r["superseded_at"] is None] == []
    assert am.c.get("/api/users/rs1").json()["displayName"] == "又一名"
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is False


def test_retained_body_survives_later_profile_edits(am):
    """审查 #9：被拒原文必须留存，后续修改不能改变历史证据。"""
    am.mkuser("ev1")
    am.login("ev1")
    _patch_profile(am, {"displayName": "旧名", "bio": "旧简介"})

    # 命中规则层 → 机器直封，原文进留存库
    _patch_profile(am, {"displayName": "被拒名字", "bio": "求萝莉资源，未成年裸照"})
    row = [r for r in _queue_rows(am.app) if r["content_type"] == "profile"][0]
    assert row["resolution"] == "blocked_by_machine"

    # 用户随后正常改资料
    _patch_profile(am, {"displayName": "全新正常名", "bio": "全新正常简介"})

    am.c.post("/api/auth/logout")
    am.mkuser("root9b", role="admin")
    am.login("root9b")
    archive = am.c.get("/api/admin/moderation/retained").json()
    item = [i for i in archive["items"] if i["contentType"] == "profile"][0]
    assert "被拒名字" in item["body"], "留存库里的被拒原文被后来的修改覆盖了"
    assert "求萝莉资源" in item["body"]


@pytest.mark.parametrize("bio", ["", "   "])
def test_empty_bio_can_be_saved_and_cleared(am, bio):
    am.mkuser("blankbio")
    am.login("blankbio")
    assert am.c.patch("/api/me/profile", json={"bio": "Previously filled"}).status_code == 200
    response = am.c.patch("/api/me/profile", json={"displayName": "Blank Bio", "bio": bio})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["bio"] == ""


def test_queue_cursor_follows_score_and_id_order(am):
    am.mkuser("queueadmin", role="admin")
    am.login("queueadmin")
    with am.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "queueadmin")).scalar_one()
        for score in (100, 50, 50, 10):
            conn.execute(moderation_queue.insert().values(content_type="profile", content_id=uid, author_id=uid, score=score))
    seen = []
    cursor = None
    while True:
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = am.c.get("/api/admin/moderation/queue", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert seen == [1, 3, 2, 4]
    # Previously-issued numeric cursors continue from the same risk position.
    assert am.c.get("/api/admin/moderation/queue?limit=1&cursor=1").json()["items"][0]["id"] == 3
    assert am.c.get("/api/admin/moderation/queue?cursor=garbage").status_code == 400
    assert am.c.get("/api/admin/moderation/queue?cursor=9999").status_code == 400


def test_blocked_profile_versions_remain_retained_and_cannot_decide_new_version(am):
    am.mkuser("versioned")
    am.login("versioned")
    # 第一版：命中规则层 → 机器直封，原文进留存库
    _patch_profile(am, {"displayName": "First version", "bio": "求萝莉资源，未成年裸照"})
    old = _queue_rows(am.app)[0]
    assert old["resolution"] == "blocked_by_machine"
    # 第二版：再提一版命中规则层的资料 → 新起一行，旧行被取代
    _patch_profile(am, {"displayName": "Second version", "bio": "出售毒品，需要的私聊"})
    rows = _queue_rows(am.app)
    assert len(rows) == 2
    assert rows[0]["superseded_at"] is not None
    assert "求萝莉资源" in rows[0]["submitted_text"]

    am.c.post("/api/auth/logout")
    am.mkuser("versionadmin", role="admin")
    am.login("versionadmin")
    retained = am.c.get("/api/admin/moderation/retained").json()
    # 留存库按 id desc 返回，所以两版都在：第一版（已被取代）的原文仍要留存，
    # 不能因为出现了新版本就丢掉历史证据。
    bodies = [item["body"] for item in retained["items"]]
    assert any("求萝莉资源" in body for body in bodies), bodies
    assert any("出售毒品" in body for body in bodies), bodies
    # 已被取代的旧版本不能再被处置
    assert am.c.post(f"/api/admin/moderation/queue/{old['id']}/approve", json={}).status_code == 400
    queue = am.c.get("/api/admin/moderation/queue").json()
    assert [item["id"] for item in queue["items"]] == [rows[1]["id"]]
    assert queue["counts"]["pending"] == 1
    # 第三版正常资料：直接放行并提升，状态回到 approved
    am.c.post("/api/auth/logout")
    am.login("versioned")
    _patch_profile(am, {"displayName": "Allowed version", "bio": "Allowed bio"})
    assert am.app.state.finalize_moderation() == []
    with am.app.state.db.request_conn() as conn:
        assert conn.execute(select(users.c.profile_moderation_status).where(users.c.username == "versioned")).scalar_one() == "approved"


# ---------------------------------------------------------------- 发布即审核的收尾修复


def test_resolution_filter_accepts_machine_block(am):
    """路由 Literal 必须包含 blocked_by_machine，否则 HTTP 传它会 422。"""
    am.mkuser("filt1", role="admin")
    am.login("filt1")
    for value in ("blocked_by_machine", "blocked", "awaiting"):
        r = am.c.get(f"/api/admin/moderation/queue?resolution={value}")
        assert r.status_code == 200, f"{value} -> {r.status_code} {r.text[:120]}"


def test_machine_blocked_profile_status_is_rejected(am, monkeypatch):
    """资料被机器直接封禁时，状态应当是 rejected（与帖子/回复/私信一致），不是 pending。"""
    am.mkuser("pm1")
    am.login("pm1")
    _patch_profile(am, {"displayName": "正常名字", "bio": "正常简介"})
    # 规则层命中 → 直接封禁
    _patch_profile(am, {"displayName": "求萝莉资源", "bio": "未成年裸照"})
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(users.c.profile_moderation_status).where(users.c.username == "pm1")
        ).first()[0]
        row = conn.execute(select(moderation_queue)).first()._mapping
    assert row["resolution"] == "blocked_by_machine"
    assert status == "rejected", f"资料封禁后状态是 {status}，与其它内容类型不一致"
    # 对外仍展示旧资料
    public = am.c.get("/api/users/pm1").json()
    assert public["displayName"] == "正常名字"


def test_admin_confirming_machine_block_notifies_author(am):
    """管理员确认机器封禁（方向没变）时，作者也必须收到通知。

    发布即审核下机器封禁发生在入队阶段且不发通知，管理员这道确认是作者唯一能收到的
    处置结果；漏掉的话作者只会看到内容无声消失。
    """
    am.mkuser("nb1")
    am.login("nb1")
    _post(am, _board(am), "求萝莉资源，未成年裸照")
    queue_id = _queue_rows(am.app)[0]["id"]
    uid = _user_id(am.app, "nb1")

    def bodies() -> list[str]:
        with am.app.state.db.request_conn() as conn:
            return [
                r[0]
                for r in conn.execute(select(notifications.c.body).where(notifications.c.user_id == uid))
            ]

    assert bodies() == [], "机器封禁入队时不该发通知"

    am.c.post("/api/auth/logout")
    am.mkuser("nbadmin", role="admin")
    am.login("nbadmin")
    confirmed = am.c.post(f"/api/admin/moderation/queue/{queue_id}/reject", json={"note": "确认封禁"})
    assert confirmed.status_code == 200
    assert confirmed.json()["overturned"] is False
    assert bodies(), "管理员确认机器封禁后，作者没有收到任何通知"


def test_cross_post_duplicate_is_flagged():
    """与本人近期内容完全相同的重复粘贴要产生规则信号（此前该检测是死代码）。"""
    from samryetha.automod_rules import evaluate_rules

    same = "这是一段完全正常的帖子内容，用来测试跨帖查重是否真的生效"
    assert evaluate_rules(same).decision == "allow"
    repeated = evaluate_rules(same, recent_bodies=[same])
    assert repeated.decision == "review"
    assert "duplicate" in {s.rule for s in repeated.signals}


# ---------------------------------------------------------------- 审核状态标记


def test_dto_exposes_moderation_status_for_author_and_admin(am, monkeypatch):
    """作者与管理员要能拿到审核状态，界面才能标"审核中"。

    非管理员根本读不到别人 pending 的内容，所以这个字段不会造成额外泄漏。
    """
    _install_provider(monkeypatch, [60])  # 模型判 review → pending
    am.mkuser("badge1")
    am.login("badge1")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")

    # 作者：详情里带 pending
    detail = am.c.get(f"/api/discussions/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["moderationStatus"] == "pending"
    # 作者自己的列表里也带
    listed = am.c.get("/api/discussions").json()["items"]
    assert [i["moderationStatus"] for i in listed if i["id"] == created["id"]] == ["pending"]

    # 外人看不到这条 → 列表里没有它，也不会有 rejected 之类的泄漏
    am.c.post("/api/auth/logout")
    am.mkuser("badge2")
    am.login("badge2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    assert all(i["id"] != created["id"] for i in am.c.get("/api/discussions").json()["items"])


def test_dto_marks_rejected_for_admin(am):
    """管理员浏览时要能看出哪些是已封禁（rejected）。"""
    am.mkuser("badge3")
    am.login("badge3")
    created = _post(am, _board(am), "求萝莉资源，未成年裸照")  # 规则命中 → rejected

    am.c.post("/api/auth/logout")
    am.mkuser("badgeadmin", role="admin")
    am.login("badgeadmin")
    detail = am.c.get(f"/api/discussions/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["moderationStatus"] == "rejected"


def test_approved_content_reports_approved(am, monkeypatch):
    """正常内容报 approved，前端据此不渲染任何标记。"""
    _install_provider(monkeypatch, [5])
    am.mkuser("badge4")
    am.login("badge4")
    created = _post(am, _board(am), "今天食堂的红烧肉有点咸")
    r = am.c.get(f"/api/discussions/{created['id']}")
    assert r.json()["moderationStatus"] == "approved"
