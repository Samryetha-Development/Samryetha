"""独立 /api/tasks：仅管理员可读可写，分组 + 优先级 + open/done。"""

from __future__ import annotations


def test_tasks_admin_only(api):
    # 未登录：读/写均 401
    assert api.c.get("/api/tasks").status_code == 401
    assert api.c.post("/api/tasks", json={"title": "nope"}).status_code == 401

    # 非管理员（普通 student）：读/写均 403
    api.mkuser("builder")
    api.login("builder")
    assert api.c.get("/api/tasks").status_code == 403
    assert api.c.post("/api/tasks", json={"title": "nope"}).status_code == 403

    # 管理员建任务（默认分组 General、normal、open）
    api.login_dev()
    r = api.c.post(
        "/api/tasks",
        json={"title": "Wire up tasks page", "notes": "independent api", "category": "Frontend", "priority": "urgent"},
    )
    assert r.status_code == 201, r.text
    t1 = r.json()
    assert t1["category"] == "Frontend"
    assert t1["priority"] == "urgent"
    assert t1["status"] == "open"
    assert t1["author"]["username"] == "dev"
    assert t1["createdAt"] and t1["doneAt"] is None

    # 再建一条默认分组，供分组统计
    r2 = api.c.post("/api/tasks", json={"title": "Backend parity check"})
    assert r2.status_code == 201, r2.text
    t2 = r2.json()
    assert t2["category"] == "General"
    assert t2["priority"] == "normal"

    # 管理员 read → canWrite True + 分组统计
    lst = api.c.get("/api/tasks").json()
    assert lst["canWrite"] is True
    assert len(lst["items"]) == 2
    cats = {c["category"]: c for c in lst["categories"]}
    assert cats["Frontend"]["open"] == 1 and cats["General"]["open"] == 1

    # 勾掉 → doneAt 记录，分组统计 open 减少、done 增加
    done = api.c.post(f"/api/tasks/{t1['id']}/status", json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["doneAt"] is not None

    lst = api.c.get("/api/tasks").json()
    cats = {c["category"]: c for c in lst["categories"]}
    assert cats["Frontend"]["open"] == 0 and cats["Frontend"]["done"] == 1

    # 恢复 open → doneAt 清空
    reopened = api.c.post(f"/api/tasks/{t1['id']}/status", json={"status": "open"})
    assert reopened.json()["status"] == "open"
    assert reopened.json()["doneAt"] is None

    # 编辑标题
    upd = api.c.patch(f"/api/tasks/{t2['id']}", json={"title": "Backend parity check v2", "priority": "urgent"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["title"] == "Backend parity check v2"
    assert upd.json()["priority"] == "urgent"

    # 删除
    assert api.c.delete(f"/api/tasks/{t2['id']}").json() == {"ok": True}
    assert api.c.delete(f"/api/tasks/{t2['id']}").status_code == 404

    # 校验：空 title / 非法 priority 被 422 拦下
    assert api.c.post("/api/tasks", json={"title": ""}).status_code == 422
    assert api.c.post("/api/tasks", json={"title": "x", "priority": "critical"}).status_code == 422
