"""S5 审核/管理/反馈 + Agent API + 备份。"""

from __future__ import annotations

import os


def _flush(api) -> None:
    api.app.state.flush_outbox()


def _id_of(api, username: str) -> int:
    api.login(username)
    return api.c.get("/api/auth/me").json()["user"]["id"]


def _members_thread(api) -> tuple[str, str, int]:
    """dev 建 members 板块；alice/bob 加入并都 active；alice 建帖。返回(alice,bob,did)。"""
    api.login_dev()
    r = api.c.post(
        "/api/boards",
        json={"name": "S5 Board", "slug": "s5-board", "visibility": "members"},
    )
    assert r.status_code == 201, r.text
    for u in ("alice", "bob"):
        api.mkuser(u)
    api.login("alice")
    assert api.c.post("/api/boards/s5-board/join").status_code == 200
    did = api.c.post(
        "/api/discussions",
        json={"boardSlug": "s5-board", "title": "Alice thread", "bodyMarkdown": "hello"},
    ).json()["id"]
    api.login("bob")
    assert api.c.post("/api/boards/s5-board/join").status_code == 200
    return "alice", "bob", did


def _user_id(api, username: str) -> int:
    api.login(username)
    return api.c.get("/api/auth/me").json()["user"]["id"]


# ================================================================ moderation


def test_reports_flow(api):
    author, other, did = _members_thread(api)
    # bob(active 普通用户) 举报 alice 的帖子
    r = api.c.post(
        "/api/moderation/reports",
        json={"reportableType": "discussion", "reportableId": did, "reason": "spam"},
    )
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "open"
    assert rep["reporter"]["username"] == "bob"
    assert rep["target"]["title"] == "Alice thread"
    assert rep["target"]["type"] == "discussion"

    # 普通学生不能看/处理
    api.login(author)
    assert api.c.get("/api/moderation/reports").status_code == 403

    # admin 能列（moderator 已并入 admin，#18）
    api.mkuser("adm1", role="admin")
    api.login("adm1")
    lst = api.c.get("/api/moderation/reports").json()
    assert len(lst["items"]) == 1
    assert lst["items"][0]["id"] == rep["id"]
    # 处理为 resolved
    res = api.c.patch(
        f"/api/moderation/reports/{rep['id']}",
        json={"status": "resolved", "reason": "taken down"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "resolved"
    # actions 日志含 report.resolved
    acts = api.c.get("/api/moderation/actions").json()
    assert any(a["action"] == "report.resolved" for a in acts["items"])


def test_ban_unban_restore_real(api):
    author, other, did = _members_thread(api)
    # adm1 (admin) ban bob（moderator 已并入 admin，#18）
    api.mkuser("adm1", role="admin")
    api.login("adm1")
    r = api.c.post(
        "/api/moderation/bans",
        json={"username": "bob", "reason": "spamming", "durationHours": 24},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    # flush → user.banned outbox handler（邮件/广播）执行且不报错
    _flush(api)
    # bob 被封后不能登录
    assert api.c.post("/api/auth/login", json={"username": "bob", "password": "password123"}).status_code == 403
    # admin unban → bob 恢复登录
    api.login_dev()
    assert api.c.request("DELETE", "/api/moderation/bans/bob", json={"reason": "ok"}).json() == {"ok": True}
    api.login("bob")
    assert api.c.get("/api/auth/me").status_code == 200

    # restore：alice 删自己帖子 → admin 删除清单可见 → adm2 (admin) 恢复
    api.login(author)
    assert api.c.request("DELETE", f"/api/discussions/{did}", json={"reason": "cleanup"}).json() == {"ok": True}
    api.login_dev()
    deleted = api.c.get("/api/admin/moderation/deleted").json()
    assert any(d["id"] == did for d in deleted["discussions"])
    api.mkuser("adm2", role="admin")
    api.login("adm2")
    rest = api.c.post(
        "/api/moderation/restore",
        json={"targetType": "discussion", "targetId": did, "reason": "mistaken"},
    )
    assert rest.status_code == 200
    assert rest.json() == {"ok": True}
    api.login(author)
    assert api.c.get(f"/api/discussions/{did}").status_code == 200


# ================================================================ admin


def test_admin_stats_users_and_actions(api):
    api.mkuser("stu1")
    api.mkuser("stu2")
    # 一个 pending
    api.register("pend1")
    # dev = admin
    api.login_dev()
    s = api.c.get("/api/admin/stats").json()
    assert set(s["users"]) == {"total", "pending", "active", "banned", "deactivated"}
    assert s["users"]["pending"] >= 1
    assert s["users"]["active"] >= 3
    assert set(s["content"]) == {"discussions", "replies", "boards"}
    assert set(s["moderation"]) == {"openReports", "activeBans"}
    assert set(s["activity"]) == {
        "activeToday",
        "newUsersToday",
        "newDiscussionsToday",
        "newRepliesToday",
        "onlineNow",
    }
    # 普通用户不能访问 stats
    api.login("stu1")
    assert api.c.get("/api/admin/stats").status_code == 403

    # users 列表 + 过滤
    api.login_dev()
    lu = api.c.get("/api/admin/users", params={"status": "pending"}).json()
    assert all(u["status"] == "pending" for u in lu["items"])
    assert any(u["username"] == "pend1" for u in lu["items"])
    # verify pend1
    pend_id = api.c.get("/api/admin/users", params={"q": "pend1"}).json()["items"][0]["id"]
    ver = api.c.post(f"/api/admin/users/{pend_id}/verify").json()
    assert ver["status"] == "active"
    assert ver["emailVerified"] is True
    # 已 verify 再 verify → conflict
    assert api.c.post(f"/api/admin/users/{pend_id}/verify").status_code == 409

    # 改角色 → 升级 stu2 为 admin，再改回已设角色 → 409（moderator 已并入 admin，#18，角色枚举仅 student/admin）
    stu2_id = api.c.get("/api/admin/users", params={"q": "stu2"}).json()["items"][0]["id"]
    up = api.c.patch(f"/api/admin/users/{stu2_id}/role", json={"role": "admin"}).json()
    assert up["role"] == "admin"
    assert api.c.patch(f"/api/admin/users/{stu2_id}/role", json={"role": "admin"}).status_code == 409
    # 不能改自己
    me = api.c.get("/api/auth/me").json()["user"]
    assert api.c.patch(f"/api/admin/users/{me['id']}/role", json={"role": "student"}).status_code == 409

    # deactivate → 会话删除 → 登录被拒
    stu1_id = api.c.get("/api/admin/users", params={"q": "stu1"}).json()["items"][0]["id"]
    assert api.c.patch(f"/api/admin/users/{stu1_id}/status", json={"status": "deactivated"}).json()["status"] == "deactivated"
    # 会话已删 + deactivated → 再登录被拒
    r = api.c.post("/api/auth/login", json={"username": "stu1", "password": "password123"})
    assert r.status_code == 403

    # 删除用户
    api.mkuser("goner")
    goner_id = api.c.get("/api/admin/users", params={"q": "goner"}).json()["items"][0]["id"]
    api.login_dev()
    assert api.c.delete(f"/api/admin/users/{goner_id}").json() == {"ok": True}
    after = api.c.get("/api/admin/users", params={"q": "goner"}).json()
    assert after["items"] == []  # 软删(重命名)后不再出现
    assert api.c.delete(f"/api/admin/users/{goner_id}").status_code == 409


# ================================================================ feedback


def _mk_project(api, name: str) -> int:
    r = api.c.post("/api/feedback/projects", json={"name": name, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_feedback_flow(api):
    api.login_dev()
    pid = _mk_project(api, "Web App")
    api.mkuser("devp")  # programmer
    api.mkuser("tester")
    devp_id = _id_of(api, "devp")
    tester_id = _id_of(api, "tester")
    # 设成员（需 admin）
    api.login_dev()
    # devp 是程序员
    r = api.c.put(
        f"/api/feedback/projects/{pid}/members",
        json={"members": [{"userId": devp_id, "isProgrammer": True}, {"userId": tester_id, "isProgrammer": False}]},
    )
    assert r.status_code == 200, r.text
    # tester 提交一个 bug
    api.login("tester")
    item = api.c.post(
        "/api/feedback",
        json={"projectId": pid, "title": "Login broken", "detail": "500 on submit", "type": "bug", "urgency": "urgent"},
    )
    assert item.status_code == 201, item.text
    it = item.json()
    assert it["seq"] == 1
    assert it["author"]["username"] == "tester"
    assert it["status"] == "open"
    assert it["urgency"] == "urgent"
    # 成员能看，但 isProgrammer=False 的 tester 不能改状态
    lst = api.c.get("/api/feedback", params={"projectId": pid}).json()
    assert lst["canManage"] is False
    assert len(lst["items"]) == 1
    assert api.c.post(f"/api/feedback/{it['id']}/status", json={"status": "done"}).status_code == 403
    # 作者能编辑标题
    upd = api.c.patch(f"/api/feedback/{it['id']}", json={"title": "Login broken v2"})
    assert upd.status_code == 200
    assert upd.json()["title"] == "Login broken v2"
    assert upd.json()["editedAt"] is not None
    # programmer 改状态
    api.login("devp")
    done = api.c.post(f"/api/feedback/{it['id']}/status", json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["closedAt"] is not None
    # myProjects
    mine = api.c.get("/api/feedback/projects/mine").json()["items"]
    proj = next(p for p in mine if p["id"] == pid)
    assert proj["isProgrammer"] is True
    api.login("tester")
    mine2 = api.c.get("/api/feedback/projects/mine").json()["items"]
    proj2 = next(p for p in mine2 if p["id"] == pid)
    assert proj2["isProgrammer"] is False


def test_agent_api_and_keys(api):
    api.login_dev()
    pid = _mk_project(api, "Mobile")
    api.mkuser("member")
    member_id = _id_of(api, "member")
    # 把 member 加进项目（普通成员即可提交）
    api.login_dev()
    assert api.c.put(
        f"/api/feedback/projects/{pid}/members",
        json={"members": [{"userId": member_id, "isProgrammer": False}]},
    ).status_code == 200
    api.login("member")
    it = api.c.post(
        "/api/feedback",
        json={"projectId": pid, "title": "Crash", "type": "bug"},
    )
    assert it.status_code == 201, it.text
    itid = it.json()["id"]

    # Agent 密钥 CRUD 需 admin 会话（安全修复：无鉴权 → 仅 admin，镜像 feedback/agent.ts）
    api.login_dev()
    key_body = {"name": "ci", "role": "write", "projectIds": [pid]}
    r = api.c.post("/api/admin/feedback/keys", json=key_body)
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    row = r.json()["keyRow"]
    assert key.startswith("fb_")
    assert row["prefix"] == key[:8]
    kid = row["id"]

    # 超媒体索引免 key
    assert api.c.get("/api/agent/v1").json()["name"]
    assert "GET /api/agent/v1/tasks" in api.c.get("/api/agent/v1/README").text

    # 无效 key → 401
    assert api.c.get("/api/agent/v1/projects", headers={"X-Api-Key": "nope"}).status_code == 401
    # 有效 key
    h = {"Authorization": f"Bearer {key}"}
    projs = api.c.get("/api/agent/v1/projects", headers=h).json()["items"]
    assert any(p["id"] == pid for p in projs)
    tasks = api.c.get("/api/agent/v1/tasks", headers=h).json()
    assert tasks["summary"]["open"] == 1
    assert len(tasks["items"]) == 1
    # 单任务
    one = api.c.get(f"/api/agent/v1/tasks/{itid}", headers=h).json()
    assert one["id"] == itid
    # 状态改 done
    done = api.c.post(f"/api/agent/v1/tasks/{itid}/status", headers=h, json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    # 禁用 key → 401
    assert api.c.put(f"/api/admin/feedback/keys/{kid}", json={"enabled": False}).json() == {"ok": True}
    assert api.c.get("/api/agent/v1/projects", headers=h).status_code == 401
    # 删除 key
    assert api.c.delete(f"/api/admin/feedback/keys/{kid}").json() == {"ok": True}


def test_feedback_backups(api, tmp_path):
    api.login_dev()
    _mk_project(api, "Keep")
    # settings 默认
    base = api.c.get("/api/admin/feedback/backups").json()
    assert base["settings"] == {"backupCron": "", "backupKeep": 5}
    # create backup
    cb = api.c.post("/api/admin/feedback/backups/create")
    assert cb.status_code == 200, cb.text
    name = cb.json()["backup"]["name"]
    assert name.startswith("backup-")
    lst = api.c.get("/api/admin/feedback/backups").json()
    assert any(b["name"] == name for b in lst["backups"])
    # 文件落在 <db目录>/backups
    db_dir = os.path.dirname(os.path.abspath(api.settings.database_url))
    assert os.path.exists(os.path.join(db_dir, "backups", name))
    # 非法 cron → 400
    bad = api.c.put(
        "/api/admin/feedback/backups/settings",
        json={"backupCron": "not a cron", "backupKeep": 5},
    )
    assert bad.status_code == 400
    # 设置合法 cron（保底 6 份）
    ok = api.c.put(
        "/api/admin/feedback/backups/settings",
        json={"backupCron": "0 4 * * *", "backupKeep": 6},
    )
    assert ok.status_code == 200
    got = api.c.get("/api/admin/feedback/backups").json()["settings"]
    assert got["backupKeep"] == 6
    # restore：非 backup 名 → 400；不存在的备份 → 404
    assert api.c.post("/api/admin/feedback/backups/restore", json={"name": "evil"}).status_code == 400
    assert api.c.post("/api/admin/feedback/backups/restore", json={"name": "backup-20240101-000000.sqlite"}).status_code == 404
    # 恢复已存在备份 → ok + restartRequired + 标记文件
    rr = api.c.post("/api/admin/feedback/backups/restore", json={"name": name})
    assert rr.status_code == 200
    assert rr.json() == {"ok": True, "restartRequired": True}
    marker = os.path.join(db_dir, ".restore_pending")
    assert os.path.exists(marker)
    with open(marker, encoding="utf-8") as fh:
        assert fh.read().strip() == name


# ================================================================ M1/M2 回归


def test_temp_ban_expires_on_login(api):
    """临时封禁(banned_until)到期后，登录自动解封；未到期仍 403。"""
    api.mkuser("victim")
    # moderator 已并入 admin，使用独立 admin 账号执行封禁
    api.mkuser("adm1", role="admin")
    api.login("adm1")
    r = api.c.post(
        "/api/moderation/bans",
        json={"username": "victim", "reason": "tmp", "durationHours": 24},
    )
    assert r.status_code == 200, r.text

    # 未到期 → 登录被拒
    r = api.c.post("/api/auth/login", json={"username": "victim", "password": "password123"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "BANNED"

    # 把封禁时间改到过去 → 下次登录应自动解封
    from sqlalchemy import update

    from samryetha.db import now_ms
    from samryetha.schema import bans

    with api.app.state.db.request_conn() as conn:
        conn.execute(update(bans).values(banned_until=now_ms() - 1000))

    r = api.c.post("/api/auth/login", json={"username": "victim", "password": "password123"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["status"] == "active"
    assert api.c.get("/api/auth/me").status_code == 200


def test_permanent_ban_never_auto_lifts(api):
    """永久封禁(banned_until IS NULL)不因时间流逝自动解封。"""
    api.mkuser("victim")
    # moderator 已并入 admin，使用独立 admin 账号执行封禁
    api.mkuser("adm1", role="admin")
    api.login("adm1")
    assert (
        api.c.post("/api/moderation/bans", json={"username": "victim", "reason": "perm"}).status_code == 200
    )
    # 不存在 banned_until 可过期 → 登录恒 403
    r = api.c.post("/api/auth/login", json={"username": "victim", "password": "password123"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "BANNED"


def test_admin_cannot_ban_other_admin(api):
    """admin 不能封另一名 admin；可封普通用户；admin 可解封普通用户。"""
    api.mkuser("adm1", role="admin")
    api.mkuser("adm2", role="admin")
    api.mkuser("stu1")
    api.login("adm1")

    # 横向封 admin → 409
    r = api.c.post("/api/moderation/bans", json={"username": "adm2", "reason": "rival"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"

    # 普通用户仍可封
    assert (
        api.c.post("/api/moderation/bans", json={"username": "stu1", "reason": "spam"}).status_code == 200
    )

    # admin 可解封普通用户
    assert api.c.request("DELETE", "/api/moderation/bans/stu1", json={"reason": "ok"}).status_code == 200
