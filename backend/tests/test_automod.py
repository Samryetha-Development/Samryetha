"""自动审核端到端：规则层判定 → 入队 → 可见性 → 确认窗口 → AI 复审 → 人工维持/推翻。

这些用例覆盖"机器只标记，AI 复审落定，人工追认"最关键的不变量：
  - 正常内容立即公开，且**不进队列**（否则队列会被正常内容淹没）；
  - 可疑内容只被**标记**（pending，机器不定案），作者自己看得到，外人与版主之外的
    角色看不到；
  - 版主可在确认窗口内定案；窗口超时则由 AI 复审落定：复审放行才公开，否则封禁；
  - 人工可以维持 AI 结论，也可以推翻（AI 放行→封禁，AI 封禁→放行）；
  - **审核失败的原文只有管理员能访问**（版主、作者都不行），但从未被删除；
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


def test_banned_content_is_held_pending_and_queued(am):
    """封禁项：**机器只标记**——内容先压住（pending，不公开）并进队列，等确认窗口。"""
    am.mkuser("bob")
    am.login("bob")
    created = _post(am, _board(am), "出售毒品，需要的私聊，量大从优")
    with am.app.state.db.request_conn() as conn:
        row = conn.execute(select(discussions.c.moderation_status).where(discussions.c.id == created["id"])).first()
    # 不是 rejected：机器认定不等于封禁成立，先压住等复审/人工。
    assert row[0] == "pending"

    queued = _queue_rows(am.app)
    assert len(queued) == 1
    assert queued[0]["content_type"] == "discussion"
    assert queued[0]["content_id"] == created["id"]
    assert queued[0]["decision"] == "block"
    assert queued[0]["score"] >= 90
    assert "drug_guns" in queued[0]["signals"]
    # 确认窗口必须设上，否则这条会永远卡在队列里没人管。
    assert queued[0]["hold_until"] is not None
    assert queued[0]["resolution"] is None


def test_author_gets_the_created_post_back_and_can_still_see_it(am):
    """发布接口必须回显刚创建的内容，即使它被拦——否则前端在"发布成功"后查不到，
    看起来就是发布失败。待审期间作者本人也应该还能打开它（否则只会反复重发）。"""
    am.mkuser("carol")
    am.login("carol")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    assert created["id"]
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


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


def test_clear_violation_is_held_then_blocked_by_recheck(am):
    """规则层明确命中：先压住；窗口超时后复审（规则层仍命中）→ 封禁。"""
    am.mkuser("frank")
    am.login("frank")
    created = _post(am, _board(am), "求萝莉资源，未成年裸照")
    queued = _queue_rows(am.app)
    assert queued and queued[0]["decision"] == "block"
    assert queued[0]["resolution"] is None

    # 把时钟推到窗口之后：复审（这里没有接模型，等于再跑一遍规则层）仍然命中 → 封禁。
    am.app.state.finalize_moderation(now=queued[0]["hold_until"] + 1)

    row = _queue_rows(am.app)[0]
    assert row["resolution"] == "blocked"
    assert row["resolved_at"] is not None
    # reviewer_id 为空 = 机器先行处置，不是人工定案（人工仍可放行）。
    assert row["reviewer_id"] is None
    assert row["review_state"] == "pending"
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


def test_approve_makes_content_visible_and_reject_keeps_it_hidden(am):
    am.mkuser("henry")
    am.login("henry")
    created = _post(am, _board(am), "出售毒品，需要的私聊")
    queued = _queue_rows(am.app)
    queue_id = queued[0]["id"]

    # 普通用户看不到
    am.c.post("/api/auth/logout")
    am.mkuser("ivy")
    am.login("ivy")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    # 版主可以处置
    am.mkuser("mod", role="moderator")
    am.login("mod")
    listing = am.c.get("/api/admin/moderation/queue").json()
    assert listing["items"] and listing["counts"]["pending"] == 1
    assert listing["items"][0]["signals"]

    approved = am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={"note": "看起来没问题"})
    assert approved.status_code == 200
    assert approved.json()["reviewState"] == "approved"
    assert approved.json()["resolution"] == "published_by_human"
    # 窗口内人工定案，谈不上推翻 AI（AI 还没落定过）。
    assert approved.json()["overturned"] is False

    # 批准后普通用户可见
    am.c.post("/api/auth/logout")
    am.login("ivy")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_reject_keeps_content_hidden_from_everyone_but_admins(am):
    """封禁后原文只有管理员能访问：作者、甚至处置它的版主都看不到。"""
    am.mkuser("jack")
    am.login("jack")
    created = _post(am, _board(am), "有没有血腥视频，发我看看")
    queue_id = _queue_rows(am.app)[0]["id"]

    am.c.post("/api/auth/logout")
    am.mkuser("mod2", role="moderator")
    am.login("mod2")
    rejected = am.c.post(f"/api/admin/moderation/queue/{queue_id}/reject", json={"note": "血腥暴力"})
    assert rejected.status_code == 200
    assert rejected.json()["reviewState"] == "rejected"
    assert rejected.json()["resolution"] == "blocked"
    # 版主也看不到被封禁的原文——它不在"版主可见"的范围内。
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 作者自己也看不到了（封禁成立，正文只留给管理员）。
    am.c.post("/api/auth/logout")
    am.login("jack")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 管理员能拿到原文。
    am.c.post("/api/auth/logout")
    am.mkuser("boss", role="admin")
    am.login("boss")
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
    am.mkuser("mod3", role="moderator")
    am.login("mod3")
    assert am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={}).status_code == 200
    again = am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={})
    assert again.status_code == 400


# ---------------------------------------------------------------- 确认窗口 + AI 复审


class _StepProvider:
    """按调用顺序依次吐出 risk 的假 provider：第一次=初审，第二次=复审。

    规则层没有命中时才轮到模型决定，所以用例里都用不含关键词的正常文本，
    这样"初审送审、复审放行"才是可复现的（不会被规则层再一次盖成 block）。
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


def test_timeout_recheck_publishes_when_second_pass_clears(am, monkeypatch):
    """复审放行 → 先行公开，标记为 AI 放行，人工可事后推翻。

    值班版主没在 1 分钟内处理时，内容不应该无限期卡住：AI 独立复审一次，
    复审认为没问题就先行公开。
    """
    provider = _install_provider(monkeypatch, [90, 5])
    am.mkuser("owen")
    am.login("owen")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]
    assert queued["decision"] == "review" and queued["resolution"] is None

    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

    row = _queue_rows(am.app)[0]
    assert row["resolution"] == "published_by_ai"
    assert row["reviewer_id"] is None  # 机器先行处置
    assert row["review_state"] == "pending"  # 仍等人工追认
    assert provider.calls == 2  # 确实跑了复审，而不是直接放行
    assert "复审" in row["recheck"] or "published" in row["recheck"]
    # 复审放行后对外可见
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


def test_timeout_recheck_blocks_when_second_pass_still_flags(am, monkeypatch):
    """复审仍不放行 → 封禁（哪怕初审只是 review）。"""
    _install_provider(monkeypatch, [90, 90])
    am.mkuser("pam")
    am.login("pam")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]

    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

    row = _queue_rows(am.app)[0]
    assert row["resolution"] == "blocked"
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_finalize_is_idempotent_and_skips_items_still_in_window(am, monkeypatch):
    """没到期的不能动；已落定的不能重复落定（否则会被反复通知）。"""
    _install_provider(monkeypatch, [90, 5, 5])
    am.mkuser("quinn")
    am.login("quinn")
    _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]

    # 窗口还没到：什么也不做
    assert am.app.state.finalize_moderation(now=queued["hold_until"] - 1) == []
    assert _queue_rows(am.app)[0]["resolution"] is None

    assert len(am.app.state.finalize_moderation(now=queued["hold_until"] + 1)) == 1
    # 再跑一次不该重复处理
    assert am.app.state.finalize_moderation(now=queued["hold_until"] + 2) == []
    assert _queue_rows(am.app)[0]["resolution"] == "published_by_ai"


def test_auto_finalize_disabled_keeps_content_waiting(tmp_path):
    """关掉 AUTOMOD_AUTO_FINALIZE：只压住等人，不做复审、也不自动落定（旧行为）。

    这是留给"人力充足、想全部人工定案"的部署形态的开关，必须真的能关掉，
    否则一开总开关就会悄悄开始自动落定。
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
        assert status == "pending"
        # 窗口照样设上（队列 UI 要能显示），但到期也不会被自动处理。
        assert queued["hold_until"] is not None
        assert app.state.finalize_moderation(now=queued["hold_until"] + 1) == []
        with app.state.db.request_conn() as conn:
            row = conn.execute(select(moderation_queue)).first()._mapping
            status_after = conn.execute(
                select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
            ).first()[0]
        assert row["resolution"] is None
        assert status_after == "pending"


def test_moderator_can_uphold_ai_publication(am, monkeypatch):
    """人工追认 AI 的先行公开：维持放行，不算推翻；作者不再收到第二条同义通知。"""
    _install_provider(monkeypatch, [90, 5])
    am.mkuser("rita")
    am.login("rita")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

    def moderation_notes() -> list[str]:
        with am.app.state.db.request_conn() as conn:
            rows = conn.execute(
                select(notifications.c.body).where(notifications.c.user_id == _user_id(am.app, "rita"))
            ).all()
        return [row[0] for row in rows]

    after_ai = moderation_notes()
    assert len(after_ai) == 1, after_ai  # AI 落定时已经通知过

    am.c.post("/api/auth/logout")
    am.mkuser("mod4", role="moderator")
    am.login("mod4")
    # 队列里要能筛出"等追认的 AI 放行"
    listing = am.c.get("/api/admin/moderation/queue?resolution=published_by_ai").json()
    assert listing["items"] and listing["counts"]["aiPublished"] == 1
    assert listing["items"][0]["needsUphold"] is True

    kept = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={"note": "追认"})
    assert kept.status_code == 200
    assert kept.json()["resolution"] == "published_by_human"
    assert kept.json()["overturned"] is False
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200
    # 维持 AI 结论不该再发一条通知（结局没变）
    assert moderation_notes() == after_ai


def test_moderator_can_overturn_ai_publication(am, monkeypatch):
    """人工推翻 AI 的先行公开 → 改为封禁，记 overturned=1。"""
    _install_provider(monkeypatch, [90, 5])
    am.mkuser("sam")
    am.login("sam")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200

    am.c.post("/api/auth/logout")
    am.mkuser("mod5", role="moderator")
    am.login("mod5")
    overturned = am.c.post(
        f"/api/admin/moderation/queue/{queued['id']}/reject", json={"note": "还是不合适"}
    )
    assert overturned.status_code == 200
    assert overturned.json()["resolution"] == "blocked"
    assert overturned.json()["overturned"] is True
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_blocked_discussion_disappears_from_saved(am, monkeypatch):
    """收藏也是可见性的一个出口：封禁后不能还从"我的收藏"读到标题和摘要。"""
    _install_provider(monkeypatch, [90, 90])
    am.mkuser("xavier")
    am.login("xavier")
    body = "我昨天跟同桌吵了一架，现在有点后悔"
    created = _post(am, _board(am), body)
    assert am.c.post(f"/api/discussions/{created['id']}/save").status_code == 200

    def saved_ids() -> list[int]:
        data = am.c.get("/api/users/xavier/saved").json()
        return [item["id"] for item in data["items"]]

    # 待审期间作者自己看得到 → 收藏里也在
    assert created["id"] in saved_ids()

    queued = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)
    # 封禁后从收藏里消失（详情/列表/搜索都已经挡住，别留这个旁路）
    assert created["id"] not in saved_ids()


def test_one_poison_row_does_not_block_the_batch(am, monkeypatch):
    """单条处理失败不能连累整批，也不能留下"队列已落定、内容没改"的半成品。

    扫描顺序是 hold_until asc / id asc，所以坏数据会一直排在队首；没有隔离的话
    所有逾期内容都永远落定不了。
    """
    from samryetha import automod as automod_module

    _install_provider(monkeypatch, [90, 90, 90, 90])
    am.mkuser("yuri")
    am.login("yuri")
    board = _board(am)
    first = _post(am, board, "我昨天跟同桌吵了一架，现在有点后悔")
    second = _post(am, board, "今天食堂的红烧肉有点咸，别的都挺好")
    rows = sorted(_queue_rows(am.app), key=lambda row: row["id"])
    assert [row["content_id"] for row in rows] == [first["id"], second["id"]]

    real_apply = automod_module.apply_review_state

    def flaky(conn, *, content_type, content_id, status):
        if content_id == first["id"]:
            raise RuntimeError("boom")
        return real_apply(conn, content_type=content_type, content_id=content_id, status=status)

    monkeypatch.setattr(automod_module, "apply_review_state", flaky)
    # 两行入队时间不同、窗口截止时间也不同，要推到两条都过期。
    expired_at = max(row["hold_until"] for row in rows) + 1
    finalized = am.app.state.finalize_moderation(now=expired_at)

    # 坏的那条被跳过，好的那条照常落定
    assert [item["contentId"] for item in finalized] == [second["id"]]
    after = {row["content_id"]: row for row in _queue_rows(am.app)}
    # 坏行必须保持"没落定过"，否则它永远不会被重试，内容会永久卡在待审
    assert after[first["id"]]["resolution"] is None
    assert after[second["id"]]["resolution"] == "blocked"
    with am.app.state.db.request_conn() as conn:
        statuses = {
            row[0]: row[1]
            for row in conn.execute(
                select(discussions.c.id, discussions.c.moderation_status).where(
                    discussions.c.id.in_([first["id"], second["id"]])
                )
            ).all()
        }
    # 坏行既没有被半途改可见性，也没有被误放行
    assert statuses[first["id"]] == "pending"
    assert statuses[second["id"]] == "rejected"
    # 修好之后下一轮能把坏行补上
    monkeypatch.setattr(automod_module, "apply_review_state", real_apply)
    assert [item["contentId"] for item in am.app.state.finalize_moderation(now=expired_at + 1)] == [
        first["id"]
    ]


def test_admin_can_release_ai_block_but_moderator_cannot(am, monkeypatch):
    """推翻 AI 的先行封禁 → 重新放行；但封禁原文仅管理员可访问，所以只有管理员能处置。"""
    _install_provider(monkeypatch, [90, 90])
    am.mkuser("tara")
    am.login("tara")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404

    # 版主：看不到封禁摘要，也不能处置（否则是在对自己看不到原文的记录做决定）
    am.c.post("/api/auth/logout")
    am.mkuser("mod6", role="moderator")
    am.login("mod6")
    listing = am.c.get("/api/admin/moderation/queue?resolution=blocked").json()
    item = listing["items"][0]
    assert item["needsRelease"] is True
    assert item["excerptRestricted"] is True
    assert item["excerpt"] == ""
    assert am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={}).status_code == 403

    # 管理员：摘要可见，可以推翻 AI 的封禁
    am.c.post("/api/auth/logout")
    am.mkuser("root3", role="admin")
    am.login("root3")
    admin_listing = am.c.get("/api/admin/moderation/queue?resolution=blocked").json()
    assert admin_listing["items"][0]["excerptRestricted"] is False
    assert admin_listing["items"][0]["excerpt"]

    released = am.c.post(f"/api/admin/moderation/queue/{queued['id']}/approve", json={"note": "误封，放行"})
    assert released.status_code == 200
    assert released.json()["resolution"] == "published_by_human"
    assert released.json()["overturned"] is True
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 200


# ---------------------------------------------------------------- 管理员留存库


def test_retained_archive_is_admin_only_and_keeps_the_body(am, monkeypatch):
    """审核失败的原文全部留存，但只有管理员能访问。"""
    _install_provider(monkeypatch, [90, 90])
    am.mkuser("uma")
    am.login("uma")
    body = "我昨天跟同桌吵了一架，现在有点后悔"
    created = _post(am, _board(am), body)
    queued = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

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
    assert item["resolution"] == "blocked"
    assert item["author"] and item["author"]["username"] == "uma"
    # 复核记录要留痕，申诉时才有依据可调
    assert item["recheck"] and item["recheck"]["decision"]


def test_finalize_endpoint_is_admin_only(am, monkeypatch):
    from sqlalchemy import update

    from samryetha.db import now_ms

    _install_provider(monkeypatch, [90, 90])
    am.mkuser("vito")
    am.login("vito")
    _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    # 接口用的是真实时钟，所以把窗口直接推到过去——否则要真等 1 分钟。
    with am.app.state.db.request_conn() as conn:
        conn.execute(update(moderation_queue).values(hold_until=now_ms() - 1))

    am.c.post("/api/auth/logout")
    am.mkuser("mod8", role="moderator")
    am.login("mod8")
    assert am.c.post("/api/admin/moderation/finalize").status_code == 403

    am.c.post("/api/auth/logout")
    am.mkuser("root2", role="admin")
    am.login("root2")
    forced = am.c.post("/api/admin/moderation/finalize")
    assert forced.status_code == 200
    assert forced.json()["count"] == 1
    assert forced.json()["blocked"] == 1


def test_held_and_blocked_content_never_leaks_through_search(am, monkeypatch):
    """搜索也必须挡住待审/封禁内容——漏了这一条，搜索就是绕过封禁的后门。"""
    _install_provider(monkeypatch, [90, 90])
    am.mkuser("wade")
    am.login("wade")
    created = _post(am, _board(am), "我昨天跟同桌吵了一架，现在有点后悔")
    queued = _queue_rows(am.app)[0]

    am.c.post("/api/auth/logout")
    am.mkuser("xena")
    am.login("xena")
    # 待审期间：搜不到
    pending_hits = am.c.get("/api/search", params={"q": "同桌"}).json()
    assert all(item["id"] != created["id"] for item in pending_hits["items"])

    # 封禁之后：同样搜不到
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)
    blocked_hits = am.c.get("/api/search", params={"q": "同桌"}).json()
    assert all(item["id"] != created["id"] for item in blocked_hits["items"])


# ---------------------------------------------------------------- 个人资料


def _patch_profile(api, body: dict) -> dict:
    response = api.c.patch("/api/me/profile", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_edit_is_held_and_keeps_the_old_public_text(am, monkeypatch):
    """资料被标记时对外仍展示旧资料（规范 §31），新资料进待审、公开面读不到。"""
    am.mkuser("pat")
    am.login("pat")
    # 第一次改成正常资料：模型判低分 → 放行并提升
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "好人", "bio": "正常简介"})
    public = am.c.get("/api/users/pat").json()
    assert public["displayName"] == "好人" and public["bio"] == "正常简介"
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is False

    # 再改成可疑资料：模型判高分 → 压住（规则层不命中，由模型决定）
    _install_provider(monkeypatch, [90])
    _patch_profile(am, {"displayName": "新名字", "bio": "新简介"})

    # 对外（含自己）看到的仍是旧资料
    public = am.c.get("/api/users/pat").json()
    assert public["displayName"] == "好人"
    assert public["bio"] == "正常简介"
    # 但界面要能知道有新版压着待审
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is True
    # 待审原文进了队列
    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"]
    assert queued and queued[0]["decision"] == "review"


def test_profile_recheck_publish_promotes_the_new_text(am, monkeypatch):
    """复审放行 → 新资料生效（提升），状态回到 approved。"""
    am.mkuser("pip")
    am.login("pip")
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "旧名字", "bio": "旧简介"})
    # 初审 90 → 压住；复审 5 → 放行
    _install_provider(monkeypatch, [90, 5])
    _patch_profile(am, {"displayName": "新名字", "bio": "新简介"})
    assert am.c.get("/api/users/pip").json()["displayName"] == "旧名字"

    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"][0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

    public = am.c.get("/api/users/pip").json()
    assert public["displayName"] == "新名字" and public["bio"] == "新简介"
    assert am.c.get("/api/auth/me").json()["user"]["profilePending"] is False


def test_blocked_profile_text_is_retained_for_admins_only(am, monkeypatch):
    """审核失败的资料原文：公开面读不到、没有删除，只有管理员能从留存库调取。"""
    am.mkuser("quill")
    am.login("quill")
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "正经名字", "bio": "正经简介"})

    # 初审 90、复审 90 → 封禁
    _install_provider(monkeypatch, [90, 90])
    _patch_profile(am, {"displayName": "新名字", "bio": "新简介"})
    queued = [row for row in _queue_rows(am.app) if row["content_type"] == "profile"][0]
    am.app.state.finalize_moderation(now=queued["hold_until"] + 1)

    # 公开面（含本人）看不到失败原文
    public = am.c.get("/api/users/quill").json()
    assert "新名字" not in public["displayName"] and "新简介" not in (public["bio"] or "")
    assert public["displayName"] == "正经名字"

    # 管理员留存库拿得到原文，且内容确实还在库里（只是不在公开字段上）
    am.c.post("/api/auth/logout")
    am.mkuser("root4", role="admin")
    am.login("root4")
    archive = am.c.get("/api/admin/moderation/retained").json()
    profile_items = [item for item in archive["items"] if item["contentType"] == "profile"]
    assert profile_items, archive["items"]
    assert "新名字" in profile_items[0]["body"] and "新简介" in profile_items[0]["body"]
    assert profile_items[0]["contentExists"] is True

    with am.app.state.db.request_conn() as conn:
        row = conn.execute(select(users).where(users.c.username == "quill")).first()
    assert row.pending_display_name == "新名字"
    assert row.pending_bio == "新简介"


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
    # 模型挂了也不能放行：规则层照样把它压住（pending），并进队列等复审/人工。
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "pending"
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
    assert status == "pending", "编辑后没有重新过审"
    queued = [r for r in _queue_rows(am.app) if r["content_id"] == created["id"]]
    assert queued, "编辑后的内容没有进队列"
    # 非作者看不到
    am.c.post("/api/auth/logout")
    am.mkuser("ed2")
    am.login("ed2")
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404


def test_editing_a_reply_reenters_moderation(am, monkeypatch):
    """审查 #2：回复同样要重审。"""
    _install_provider(monkeypatch, [5])
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
    assert status == "pending"


def test_title_participates_in_review(am, monkeypatch):
    """审查 #3：违规词只放标题、正文正常，也要被拦。"""
    _install_provider(monkeypatch, [5])
    am.mkuser("ti1")
    am.login("ti1")
    created = _post(am, _board(am), "这是一个完全正常的正文内容", title="求萝莉资源，未成年裸照")
    with am.app.state.db.request_conn() as conn:
        status = conn.execute(
            select(discussions.c.moderation_status).where(discussions.c.id == created["id"])
        ).first()[0]
    assert status == "pending", "标题没有参与判定"
    assert any(r["content_id"] == created["id"] for r in _queue_rows(am.app))


def test_conversation_preview_hides_held_message(am, monkeypatch):
    """审查 #4：会话预览与未读数不能泄漏待审/封禁私信的完整正文。"""
    _install_provider(monkeypatch, [5])
    am.mkuser("cv1")
    am.mkuser("cv2")
    am.login("cv1")
    am.c.post("/api/messages", json={"username": "cv2", "body": "先发一条正常的"})

    _install_provider(monkeypatch, [90])
    am.c.post("/api/messages", json={"username": "cv2", "body": "违规的第二条私信"})

    am.c.post("/api/auth/logout")
    am.login("cv2")
    convs = am.c.get("/api/messages/conversations").json()
    item = convs["items"][0]
    assert item["lastMessage"]["body"] == "先发一条正常的", "会话预览泄漏了待审私信正文"
    # 收件人侧：待审那条不算未读——会话里的未读数只该是那条已放行、尚未读的正常私信。
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


def test_queue_href_for_reply_points_at_parent_discussion(am, monkeypatch):
    """审查 #16：reply 的 href 要用父帖 id，不是回复 id。"""
    _install_provider(monkeypatch, [5])
    am.mkuser("hr1")
    am.login("hr1")
    board = _board(am)
    _post(am, board, "第一帖")
    second = _post(am, board, "第二帖")

    _install_provider(monkeypatch, [90])
    am.c.post(f"/api/discussions/{second['id']}/replies", json={"bodyMarkdown": "违规回复"})

    am.c.post("/api/auth/logout")
    am.mkuser("modhr", role="moderator")
    am.login("modhr")
    listing = am.c.get("/api/admin/moderation/queue?status=pending").json()
    replies_items = [i for i in listing["items"] if i["contentType"] == "reply"]
    assert replies_items
    item = replies_items[0]
    assert item["href"].startswith(f"/d/{second['id']}#reply-"), item["href"]


def test_resubmitted_profile_is_not_stuck_by_old_verdict(am, monkeypatch):
    """审查 #8：AI 落定后重提的资料不能被上一版结论顶住。"""
    am.mkuser("rs1")
    am.login("rs1")
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "正常名字", "bio": "正常简介"})

    _install_provider(monkeypatch, [90, 90])
    _patch_profile(am, {"displayName": "被拒名字", "bio": "被拒简介"})
    row = [r for r in _queue_rows(am.app) if r["content_type"] == "profile"][0]
    am.app.state.finalize_moderation(now=row["hold_until"] + 1)
    assert [r for r in _queue_rows(am.app) if r["content_type"] == "profile"][0]["resolution"] == "blocked"

    # 再提交一版新的：必须能重新走完窗口→复审
    _install_provider(monkeypatch, [90])
    _patch_profile(am, {"displayName": "又一名", "bio": "又一分"})
    fresh = [r for r in _queue_rows(am.app) if r["content_type"] == "profile" and r["superseded_at"] is None][0]
    assert fresh["resolution"] is None, "旧结论残留在队列行上，新版永远不会被复审"
    assert fresh["recheck"] == ""

    # 窗口到期后能被复审处理（说明 worker 扫得到它）
    _install_provider(monkeypatch, [5])
    assert am.app.state.finalize_moderation(now=fresh["hold_until"] + 1)


def test_retained_body_survives_later_profile_edits(am, monkeypatch):
    """审查 #9：被拒原文必须留存，后续修改不能改变历史证据。"""
    am.mkuser("ev1")
    am.login("ev1")
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "旧名", "bio": "旧简介"})

    _install_provider(monkeypatch, [90, 90])
    _patch_profile(am, {"displayName": "被拒名字", "bio": "被拒简介"})
    row = [r for r in _queue_rows(am.app) if r["content_type"] == "profile"][0]
    am.app.state.finalize_moderation(now=row["hold_until"] + 1)

    # 用户随后正常改资料
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "全新正常名", "bio": "全新正常简介"})

    am.c.post("/api/auth/logout")
    am.mkuser("root9b", role="admin")
    am.login("root9b")
    archive = am.c.get("/api/admin/moderation/retained").json()
    item = [i for i in archive["items"] if i["contentType"] == "profile"][0]
    assert "被拒名字" in item["body"], "留存库里的被拒原文被后来的修改覆盖了"
    assert "被拒简介" in item["body"]


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


def test_blocked_profile_versions_remain_retained_and_cannot_decide_new_version(am, monkeypatch):
    am.mkuser("versioned")
    am.login("versioned")
    _install_provider(monkeypatch, [90, 90])
    _patch_profile(am, {"displayName": "First version", "bio": "First rejected bio"})
    old = _queue_rows(am.app)[0]
    am.app.state.finalize_moderation(now=old["hold_until"] + 1)
    _install_provider(monkeypatch, [90])
    _patch_profile(am, {"displayName": "Second version", "bio": "Second flagged bio"})
    rows = _queue_rows(am.app)
    assert len(rows) == 2
    assert rows[0]["superseded_at"] is not None and rows[0]["resolution"] == "blocked"
    assert "First rejected bio" in rows[0]["submitted_text"]
    am.mkuser("versionadmin", role="admin")
    am.login("versionadmin")
    retained = am.c.get("/api/admin/moderation/retained").json()
    assert "First rejected bio" in retained["items"][0]["body"]
    assert am.c.post(f"/api/admin/moderation/queue/{old['id']}/approve", json={}).status_code == 400
    queue = am.c.get("/api/admin/moderation/queue").json()
    assert [item["id"] for item in queue["items"]] == [rows[1]["id"]]
    assert queue["counts"]["pending"] == 1
    am.login("versioned")
    _install_provider(monkeypatch, [5])
    _patch_profile(am, {"displayName": "Allowed version", "bio": "Allowed bio"})
    assert am.app.state.finalize_moderation(now=rows[1]["hold_until"] + 1) == []
    with am.app.state.db.request_conn() as conn:
        assert conn.execute(select(users.c.profile_moderation_status).where(users.c.username == "versioned")).scalar_one() == "approved"
