"""S2 论坛核心：板块 / 讨论 / 回复 / save/follow/pin/lock。"""

from __future__ import annotations


def _make_private_board(api) -> dict:
    api.login_dev()
    r = api.c.post("/api/boards", json={"name": "Book Club", "slug": "book-club", "visibility": "members"})
    assert r.status_code == 201, r.text
    return r.json()


def test_board_crud(api):
    api.login_dev()
    res = api.c.post("/api/boards", json={"name": "Announcements", "slug": "announcements"})
    assert res.status_code == 201
    b = res.json()
    # slug 小写；创建者自动成为 moderator
    assert b["slug"] == "announcements"
    assert b["name"] == "Announcements"
    assert b["postingPolicy"] == "members"
    # TS createBoard 以 viewer=null 载入 summary → currentUserRole null
    assert b["currentUserRole"] is None
    assert b["memberCount"] == 1
    assert isinstance(b["id"], int)
    # dev 再读该板块 → 自己是 moderator
    got = api.c.get("/api/boards/announcements").json()
    assert got["currentUserRole"] == "moderator"

    # 重复 slug → 409
    dup = api.c.post("/api/boards", json={"name": "Announcements 2", "slug": "announcements"})
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CONFLICT"

    # PATCH
    patch = api.c.patch("/api/boards/announcements", json={"description": "hello"})
    assert patch.status_code == 200
    assert patch.json()["description"] == "hello"

    # 列表
    listed = api.c.get("/api/boards")
    assert listed.status_code == 200
    assert any(x["slug"] == "announcements" for x in listed.json()["items"])

    # members 列表（仅自己）
    mem = api.c.get("/api/boards/announcements/members")
    assert mem.status_code == 200
    assert len(mem.json()["items"]) == 1
    assert mem.json()["items"][0]["role"] == "moderator"

    # 非 admin 不能建板块
    api.mkuser("student1")
    api.login("student1")
    forb = api.c.post("/api/boards", json={"name": "Hack", "slug": "hack"})
    assert forb.status_code == 403
    assert forb.json()["error"]["code"] == "FORBIDDEN"
    # 大写 slug 会被 zod 校验拒绝（422）
    bad = api.c.post("/api/boards", json={"name": "X", "slug": "UPPER"})
    assert bad.status_code == 422


def test_membership_and_discussion_flow(api):
    _make_private_board(api)
    # student 未加入 members 板块，发帖被拒
    api.mkuser("stu1")
    api.login("stu1")
    denied = api.c.post(
        "/api/discussions",
        json={"boardSlug": "book-club", "title": "Hello", "bodyMarkdown": "world"},
    )
    assert denied.status_code == 403

    # 加入后可以发
    join = api.c.post("/api/boards/book-club/join")
    assert join.status_code == 200
    assert join.json() == {"member": True}
    # 重复加入 → 409
    assert api.c.post("/api/boards/book-club/join").status_code == 409

    created = api.c.post(
        "/api/discussions",
        json={"boardSlug": "book-club", "title": "My first thread", "bodyMarkdown": "**hello** world"},
    )
    assert created.status_code == 201, created.text
    disc = created.json()
    assert disc["board"]["slug"] == "book-club"
    assert disc["author"]["username"] == "stu1"
    assert disc["replyCount"] == 0
    assert disc["saveCount"] == 0
    assert disc["isPinned"] is False
    assert disc["isLocked"] is False
    assert "<strong>hello</strong>" in disc["bodyHtml"]
    assert disc["can"]["update"] is True
    assert disc["can"]["delete"] is True
    did = disc["id"]

    # feed 里能看到（student 已加入）
    feed = api.c.get("/api/discussions")
    assert feed.status_code == 200
    assert any(t["id"] == did for t in feed.json()["items"])
    # 匿名看不到（members 板块）
    api.c.post("/api/auth/logout")
    anon_feed = api.c.get("/api/discussions")
    assert all(t["board"]["slug"] != "book-club" for t in anon_feed.json()["items"])

    # 回复流程
    api.login("stu1")
    r1 = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "first!"})
    assert r1.status_code == 201
    r1j = r1.json()
    assert r1j["discussionId"] == did
    assert r1j["isDeleted"] is False
    detail = api.c.get(f"/api/discussions/{did}").json()
    assert detail["replyCount"] == 1
    replies = api.c.get(f"/api/discussions/{did}/replies").json()["items"]
    assert len(replies) == 1

    # 子回复（parentReplyId）
    r2 = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "reply to first", "parentReplyId": r1j["id"]})
    assert r2.status_code == 201
    assert r2.json()["parentReplyId"] == r1j["id"]

    # 编辑回复
    upd = api.c.patch(f"/api/replies/{r1j['id']}", json={"bodyMarkdown": "edited!"})
    assert upd.status_code == 200
    assert upd.json()["bodyMarkdown"] == "edited!"

    # 删除回复 → reply_count 减
    assert api.c.delete(f"/api/replies/{r1j['id']}").status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 1
    # 已删回复在列表中掩码
    items = api.c.get(f"/api/discussions/{did}/replies").json()["items"]
    assert items[0]["isDeleted"] is True
    assert items[0]["bodyMarkdown"] == ""
    assert items[0]["bodyHtml"] is None


def test_save_follow_pin_lock(api):
    _make_private_board(api)
    api.mkuser("stu1")
    api.login("stu1")
    api.c.post("/api/boards/book-club/join")
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "book-club", "title": "Title", "bodyMarkdown": "body"}
    ).json()["id"]

    # save/unsave 幂等
    assert api.c.post(f"/api/discussions/{did}/save").json() == {"saved": True}
    assert api.c.post(f"/api/discussions/{did}/save").json() == {"saved": True}
    assert api.c.get(f"/api/discussions/{did}").json()["saveCount"] == 1
    assert api.c.delete(f"/api/discussions/{did}/save").json() == {"saved": False}

    # follow/unfollow
    assert api.c.post(f"/api/discussions/{did}/follow").json() == {"following": True}
    assert api.c.get(f"/api/discussions/{did}").json()["isFollowing"] is True
    assert api.c.delete(f"/api/discussions/{did}/follow").json() == {"following": False}

    # 普通成员不能 pin/lock
    assert api.c.post(f"/api/discussions/{did}/pin").status_code == 403
    # dev（板块 moderator）可以
    api.login_dev()
    assert api.c.post(f"/api/discussions/{did}/pin").json() == {"pinned": True}
    assert api.c.get(f"/api/discussions/{did}").json()["isPinned"] is True
    assert api.c.post(f"/api/discussions/{did}/lock").json() == {"locked": True}
    # 锁定后普通成员不能回
    api.login("stu1")
    locked = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "nope"})
    assert locked.status_code == 403


def test_discussion_cursor_pagination(api):
    _make_private_board(api)
    api.mkuser("stu1")
    api.login("stu1")
    api.c.post("/api/boards/book-club/join")
    ids = []
    for i in range(3):
        ids.append(
            api.c.post(
                "/api/discussions",
                json={"boardSlug": "book-club", "title": f"post {i}", "bodyMarkdown": f"body {i}"},
            ).json()["id"]
        )
    page1 = api.c.get("/api/discussions", params={"limit": 2}).json()
    assert len(page1["items"]) == 2
    assert page1["nextCursor"] is not None
    page2 = api.c.get("/api/discussions", params={"limit": 2, "cursor": page1["nextCursor"]}).json()
    assert len(page2["items"]) == 1
    # 两页不重叠且覆盖全部
    got = [t["id"] for t in page1["items"] + page2["items"]]
    assert sorted(got) == sorted(ids)


def test_discussion_update_delete_and_permissions(api):
    _make_private_board(api)
    api.mkuser("stu1")
    api.mkuser("stu2")
    api.login("stu1")
    api.c.post("/api/boards/book-club/join")
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "book-club", "title": "Title", "bodyMarkdown": "b"}
    ).json()["id"]

    # 他人不能改/删
    api.login("stu2")
    api.c.post("/api/boards/book-club/join")
    assert api.c.patch(f"/api/discussions/{did}", json={"title": "Xxx"}).status_code == 403
    assert api.c.delete(f"/api/discussions/{did}").status_code == 403

    # 作者可改；软删后不可再改
    api.login("stu1")
    upd = api.c.patch(f"/api/discussions/{did}", json={"title": "new title"})
    assert upd.status_code == 200
    assert upd.json()["title"] == "new title"
    assert api.c.delete(f"/api/discussions/{did}").json() == {"ok": True}
    assert api.c.get(f"/api/discussions/{did}").status_code == 404
    # 软删后作者再改：can(update)=false → 403（不是 404）
    assert api.c.patch(f"/api/discussions/{did}", json={"title": "Zzz"}).status_code == 403


def test_user_feeds(api):
    _make_private_board(api)
    api.mkuser("stu1")
    api.mkuser("stu2")
    api.login("stu1")
    api.c.post("/api/boards/book-club/join")
    api.login("stu2")
    api.c.post("/api/boards/book-club/join")
    api.login("stu1")
    did1 = api.c.post(
        "/api/discussions", json={"boardSlug": "book-club", "title": "one", "bodyMarkdown": "a"}
    ).json()["id"]
    api.c.post("/api/discussions", json={"boardSlug": "book-club", "title": "two", "bodyMarkdown": "b"})
    # stu1 收藏第一个
    api.c.post(f"/api/discussions/{did1}/save")

    # posts feed
    posts = api.c.get("/api/users/stu1/posts").json()
    assert len(posts["items"]) == 2
    # replies feed（stu1 在 two 里留一条）
    api.c.post(f"/api/discussions/{did1}/replies", json={"bodyMarkdown": "reply by stu1"})
    api.login("stu2")
    api.c.post(f"/api/discussions/{did1}/replies", json={"bodyMarkdown": "reply by stu2"})
    rep = api.c.get("/api/users/stu1/replies").json()
    assert len(rep["items"]) == 1
    assert rep["items"][0]["author"]["username"] == "stu1"
    assert rep["items"][0]["discussionTitle"] == "one"

    # saved feed：本人可看；他人看 403
    api.login("stu1")
    saved = api.c.get("/api/users/stu1/saved")
    assert saved.status_code == 200
    assert len(saved.json()["items"]) == 1
    assert saved.json()["items"][0]["id"] == did1
    # stu2 看 stu1 的 saved → 403（隐私）
    api.login("stu2")
    other_saved = api.c.get("/api/users/stu1/saved")
    assert other_saved.status_code == 403
    # stu2 自己的 saved 为空
    empty = api.c.get("/api/users/stu2/saved").json()
    assert empty["items"] == []


def test_delete_reply_is_idempotent(api):
    """删除已删回复是幂等的：不重复递减 reply_count 至负数。"""
    _make_private_board(api)
    api.mkuser("stu1")
    api.login("stu1")
    api.c.post("/api/boards/book-club/join")
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "book-club", "title": "Count me", "bodyMarkdown": "b"}
    ).json()["id"]

    r1 = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "one"}).json()
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 1

    # 删一次 → 0
    assert api.c.delete(f"/api/replies/{r1['id']}").status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 0

    # 重复删 → 幂等 200，不把计数减成负数
    assert api.c.delete(f"/api/replies/{r1['id']}").status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 0

    # 计数未被拖垮：新回复回到 1
    api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "two"})
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 1
