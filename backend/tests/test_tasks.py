"""独立 /api/tasks：仅管理员可读写、分组 + 优先级 + open/done + 嵌套评论。"""

from __future__ import annotations


def test_tasks_require_admin(api):
    # 未登录：401（列表与写操作都不公开）
    assert api.c.get("/api/tasks").status_code == 401
    assert api.c.post("/api/tasks", json={"title": "nope"}).status_code == 401

    # 已登录的普通用户：403
    api.mkuser("builder")
    api.login("builder")
    assert api.c.get("/api/tasks").status_code == 403
    assert api.c.post("/api/tasks", json={"title": "nope"}).status_code == 403
    assert api.c.get("/api/tasks/1/comments").status_code == 403


def test_tasks_admin_crud(api):
    api.mkuser("root", role="admin")
    api.login("root")

    lst = api.c.get("/api/tasks")
    assert lst.status_code == 200
    body = lst.json()
    assert body["items"] == []
    assert body["categories"] == []
    assert body["canWrite"] is True

    # 默认分组 General、normal、open
    r = api.c.post("/api/tasks", json={"title": "Wire up tasks page", "notes": "independent api", "category": "Frontend", "priority": "urgent"})
    assert r.status_code == 201, r.text
    t1 = r.json()
    assert t1["category"] == "Frontend"
    assert t1["priority"] == "urgent"
    assert t1["status"] == "open"
    assert t1["author"]["username"] == "root"
    assert t1["createdAt"] and t1["doneAt"] is None

    r2 = api.c.post("/api/tasks", json={"title": "Backend parity check"})
    assert r2.status_code == 201, r2.text
    t2 = r2.json()
    assert t2["category"] == "General"
    assert t2["priority"] == "normal"

    lst = api.c.get("/api/tasks").json()
    assert lst["canWrite"] is True
    assert len(lst["items"]) == 2
    cats = {c["category"]: c for c in lst["categories"]}
    assert cats["Frontend"]["open"] == 1 and cats["General"]["open"] == 1

    # 勾掉 / 恢复
    done = api.c.post(f"/api/tasks/{t1['id']}/status", json={"status": "done"})
    assert done.status_code == 200
    assert done.json()["status"] == "done"
    assert done.json()["doneAt"] is not None
    lst = api.c.get("/api/tasks").json()
    cats = {c["category"]: c for c in lst["categories"]}
    assert cats["Frontend"]["open"] == 0 and cats["Frontend"]["done"] == 1
    reopened = api.c.post(f"/api/tasks/{t1['id']}/status", json={"status": "open"})
    assert reopened.json()["status"] == "open"
    assert reopened.json()["doneAt"] is None

    # 编辑
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


def test_task_comments_admin(api):
    api.mkuser("root", role="admin")
    api.login("root")
    task = api.c.post("/api/tasks", json={"title": "Ship comments"}).json()

    # 空评论列表
    assert api.c.get(f"/api/tasks/{task['id']}/comments").json() == {"items": []}

    # 顶层评论
    c1 = api.c.post(f"/api/tasks/{task['id']}/comments", json={"body": "first"}).json()
    assert c1["taskId"] == task["id"]
    assert c1["parentCommentId"] is None
    assert c1["author"]["username"] == "root"
    assert c1["body"] == "first"
    assert c1["isDeleted"] is False

    # 嵌套回复
    c2 = api.c.post(f"/api/tasks/{task['id']}/comments", json={"body": "nested", "parentCommentId": c1["id"]}).json()
    assert c2["parentCommentId"] == c1["id"]

    # 列表按创建顺序
    items = api.c.get(f"/api/tasks/{task['id']}/comments").json()["items"]
    assert [c["body"] for c in items] == ["first", "nested"]

    # 编辑
    upd = api.c.patch(f"/api/tasks/comments/{c1['id']}", json={"body": "edited"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["body"] == "edited"

    # 删除（软删，列表不再返回）
    assert api.c.delete(f"/api/tasks/comments/{c1['id']}").json() == {"ok": True}
    items = api.c.get(f"/api/tasks/{task['id']}/comments").json()["items"]
    assert [c["id"] for c in items] == [c2["id"]]

    # 不存在 / 已删除 的评论：404
    assert api.c.patch(f"/api/tasks/comments/{c1['id']}", json={"body": "x"}).status_code == 404
    assert api.c.delete(f"/api/tasks/comments/{c1['id']}").status_code == 404


def test_task_comment_parent_must_match_task(api):
    api.mkuser("root", role="admin")
    api.login("root")
    a = api.c.post("/api/tasks", json={"title": "A"}).json()
    b = api.c.post("/api/tasks", json={"title": "B"}).json()
    root = api.c.post(f"/api/tasks/{a['id']}/comments", json={"body": "on A"}).json()

    # 父评论必须属于同一任务
    assert api.c.post(f"/api/tasks/{b['id']}/comments", json={"body": "x", "parentCommentId": root["id"]}).status_code == 404
    # 评论不存在的任务
    assert api.c.post("/api/tasks/999999/comments", json={"body": "x"}).status_code == 404
    assert api.c.get("/api/tasks/999999/comments").status_code == 404
