"""S4 实时与社会：通知 + outbox→通知/SSE + presence + SSE 通道。"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import select, update

from samryetha.auth import ensure_builtin_accounts
from samryetha.config import Settings
from samryetha.db import now_ms
from samryetha.main import create_app
from samryetha.outbox import emit_event
from samryetha.outbox_worker import OutboxDispatcher, poll_once
from samryetha.schema import outbox_events, users


class _LiveUvicorn(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        pass


def _start_live(app) -> tuple[int, _LiveUvicorn]:
    import samryetha.routers.realtime as rt

    rt._KEEPALIVE_SECONDS = 0.3
    server = _LiveUvicorn(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(server, "servers", None):
            break
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return port, server


def _activate(app, username: str) -> None:
    with app.state.db.request_conn() as conn:
        conn.execute(
            update(users)
            .where(users.c.username == username)
            .values(status="active", email_verified_at=now_ms())
        )


def _user_id(app, username: str) -> int:
    with app.state.db.request_conn() as conn:
        row = conn.execute(select(users.c.id).where(users.c.username == username)).first()
    return row[0]


# ---------------------------------------------------------------- helpers


def _flush(api) -> None:
    api.app.state.flush_outbox()


def _mk_private_thread(api) -> tuple[str, str, int]:
    """建 members 板块；alice(作者) 与 bob 都加入；返回 (author,other,discussion_id)。"""
    api.login_dev()
    r = api.c.post(
        "/api/boards",
        json={"name": "Members Only", "slug": "member-thread", "visibility": "members"},
    )
    assert r.status_code == 201, r.text
    for u in ("alice", "bob"):
        api.mkuser(u)
    api.login("alice")
    assert api.c.post("/api/boards/member-thread/join").status_code == 200
    created = api.c.post(
        "/api/discussions",
        json={"boardSlug": "member-thread", "title": "Alice's thread", "bodyMarkdown": "hello"},
    )
    assert created.status_code == 201, created.text
    did = created.json()["id"]
    api.login("bob")
    assert api.c.post("/api/boards/member-thread/join").status_code == 200
    return "alice", "bob", did


def _parse_frame(raw: str | None) -> dict | None:
    if not raw:
        return None
    event = ""
    data_lines = []
    for line in raw.split("\n"):
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
    if not event and not data_lines:
        return None
    raw_data = "\n".join(data_lines)
    try:
        parsed = json.loads(raw_data) if raw_data else None
    except ValueError:
        parsed = raw_data
    return {"event": event, "data": parsed}


# ---------------------------------------------------------------- notifications


def test_reply_notifies_author(api):
    author, other, did = _mk_private_thread(api)
    r = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "hi from bob"})
    assert r.status_code == 201, r.text
    reply_id = r.json()["id"]
    _flush(api)

    api.login(author)
    lst = api.c.get("/api/notifications").json()
    assert lst["unreadCount"] == 1
    assert len(lst["items"]) == 1
    assert lst["nextCursor"] is None
    n = lst["items"][0]
    assert n["type"] == "reply"
    assert n["isRead"] is False
    assert n["actor"]["username"] == other
    assert n["body"] == "bob 回复了「Alice's thread」"
    assert n["discussionId"] == did
    assert n["replyId"] == reply_id
    assert isinstance(n["createdAt"], int)

    # unread-count 路由
    assert api.c.get("/api/notifications/unread-count").json() == {"unreadCount": 1}

    # mark read → unreadOnly 为空
    assert api.c.post(f"/api/notifications/{n['id']}/read").json() == {"ok": True}
    assert api.c.get("/api/notifications/unread-count").json() == {"unreadCount": 0}
    empty = api.c.get("/api/notifications", params={"unreadOnly": "true"}).json()
    assert empty["items"] == []
    assert empty["unreadCount"] == 0


def test_follow_notifies_followee(api):
    api.mkuser("carol")
    api.mkuser("dave")
    api.login("carol")
    assert api.c.post("/api/users/dave/follow").json() == {"following": True}
    _flush(api)

    api.login("dave")
    lst = api.c.get("/api/notifications").json()
    assert lst["unreadCount"] == 1
    n = lst["items"][0]
    assert n["type"] == "follow"
    assert n["actor"]["username"] == "carol"
    assert n["body"] == "carol 关注了你"
    assert n["discussionId"] is None
    assert n["replyId"] is None
    # 关注自己不发通知（handler 里 self 排除）
    api.login("carol")
    assert api.c.get("/api/notifications").json()["items"] == []


def test_mark_read_permissions_and_read_all(api):
    api.mkuser("eve")
    api.mkuser("frank")
    api.login("eve")
    api.c.post("/api/users/frank/follow")
    _flush(api)
    # eve 没有通知
    assert api.c.get("/api/notifications").json()["items"] == []

    api.login("frank")
    nid = api.c.get("/api/notifications").json()["items"][0]["id"]
    # eve 不能把 frank 的通知标已读 → 404
    api.login("eve")
    r = api.c.post(f"/api/notifications/{nid}/read")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    # 不存在 id → 404
    assert api.c.post("/api/notifications/999999/read").status_code == 404
    # read-all
    api.login("frank")
    assert api.c.post("/api/notifications/read-all").json() == {"ok": True}
    assert api.c.get("/api/notifications/unread-count").json() == {"unreadCount": 0}
    lst = api.c.get("/api/notifications").json()
    assert all(n["isRead"] is True for n in lst["items"])


def test_notifications_cursor_pagination(api):
    author, other, did = _mk_private_thread(api)
    for i in range(3):
        api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": f"reply {i}"})
    _flush(api)

    api.login(author)
    p1 = api.c.get("/api/notifications", params={"limit": 2}).json()
    assert len(p1["items"]) == 2
    assert p1["nextCursor"] is not None
    p2 = api.c.get("/api/notifications", params={"limit": 2, "cursor": p1["nextCursor"]}).json()
    assert len(p2["items"]) == 1
    assert p2["nextCursor"] is None
    ids = [n["id"] for n in p1["items"] + p2["items"]]
    assert len(set(ids)) == 3
    assert ids == sorted(ids, reverse=True)  # id desc


# ---------------------------------------------------------------- presence


def test_presence_heartbeat_and_online_list(api):
    # 匿名 heartbeat → 401
    assert api.c.post("/api/presence/heartbeat").status_code == 401

    api.mkuser("grace")
    api.login("grace")
    assert api.c.post("/api/presence/heartbeat").json() == {"onlineCount": 1}
    lst = api.c.get("/api/presence").json()
    assert lst["onlineCount"] == 1
    # L1：不再回显在线用户名单，仅返回人数
    assert "onlineUsers" not in lst

    # 第二人在线
    api.mkuser("heidi")
    api.login("heidi")
    api.c.post("/api/presence/heartbeat")
    lst2 = api.c.get("/api/presence").json()
    assert lst2["onlineCount"] == 2
    assert "onlineUsers" not in lst2

    # 列表无需登录
    api.c.post("/api/auth/logout")
    assert api.c.get("/api/presence").json()["onlineCount"] == 2


# ---------------------------------------------------------------- SSE


def test_events_sse_requires_active_user(api):
    r = api.c.get("/api/events")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def _read_frame(it, timeout: float = 5.0) -> dict:
    parts: list[str] = []
    deadline = time.time() + timeout
    for line in it:
        if time.time() > deadline:
            raise TimeoutError("SSE 帧超时")
        if line:
            parts.append(line)
            continue
        if parts:
            ev = _parse_frame("\n".join(parts))
            parts = []
            if ev is not None:
                return ev
    raise AssertionError("SSE 连接提前关闭")


def test_events_sse_streams_and_filters(tmp_path):
    """真实 socket（uvicorn 线程）验证：connected + 按 userId 过滤推送。

    TestClient/httpx 的 ASGI 传输不推流式字节，故这里起一个真实 uvicorn。
    本机开系统代理(Clash)时 httpx 会把发往 127.0.0.1 的请求也塞进代理 → 502，故显式 trust_env=False。
    With a system proxy (Clash) on, httpx routes even 127.0.0.1 through it → 502, so set trust_env=False.
    """
    app = create_app(
        Settings(
            _env_file=None,
            database_url=str(tmp_path / "sse.db"),
            upload_dir=str(tmp_path / "u"),
        )
    )
    app.state.db.create_schema()
    port, server = _start_live(app)
    try:
        with app.state.db.request_conn() as conn:
            ensure_builtin_accounts(conn, app.state.settings)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
            # 建两个 active 用户
            for u in ("user1", "user2"):
                assert client.post(
                    "/api/auth/register", json={"username": u, "password": "password123"}
                ).status_code == 201
                _activate(app, u)
            assert client.post(
                "/api/auth/login", json={"username": "user1", "password": "password123"}
            ).status_code == 200
            uid1, uid2 = _user_id(app, "user1"), _user_id(app, "user2")

            with client.stream("GET", "/api/events") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                it = resp.iter_lines()

                connected = _read_frame(it)
                assert connected["event"] == "connected"
                assert connected["data"]["userId"] == uid1
                assert isinstance(connected["data"]["at"], int)

                # 推给别人的事件：user1 不该收到
                app.state.events.publish(
                    {"type": "notification.created", "data": {"userId": uid2}}
                )
                with pytest.raises(TimeoutError):
                    _read_frame(it, timeout=0.8)

                # 推给自己的事件：收到
                app.state.events.publish(
                    {"type": "notification.created", "data": {"userId": uid1}}
                )
                got = _read_frame(it, timeout=5.0)
                assert got["event"] == "notification.created"
                assert got["data"] == {"userId": uid1}
    finally:
        server.should_exit = True


# ---------------------------------------------------------------- outbox 语义


def test_outbox_done_retry_and_failed(db):
    dispatcher = OutboxDispatcher()
    calls: list = []

    def ok_handler(conn, payload):
        calls.append(payload)
        return []

    def boom(conn, payload):
        raise RuntimeError("handler boom")

    dispatcher.on("ok", ok_handler)
    dispatcher.on("boom", boom)
    with db.request_conn() as c:
        emit_event(c, "ok", payload={"a": 1})
        emit_event(c, "boom", payload={"x": 1})
        emit_event(c, "nohandler", payload={})  # 无 handler 也要置 done

    poll_once(db, dispatcher)
    with db.request_conn() as c:
        rows = {
            r.event_type: dict(r._mapping)
            for r in c.execute(select(outbox_events)).all()
        }
    assert rows["ok"]["status"] == "done"
    assert rows["nohandler"]["status"] == "done"
    assert calls == [{"a": 1}]
    # boom：第一次失败 → pending + 退避（available_at 在未来）
    assert rows["boom"]["status"] == "pending"
    assert rows["boom"]["attempts"] == 1
    assert rows["boom"]["available_at"] > now_ms()

    # 逼到 maxAttempts → failed
    with db.request_conn() as c:
        c.execute(
            update(outbox_events)
            .where(outbox_events.c.event_type == "boom")
            .values(attempts=9, available_at=now_ms() - 1)
        )
    poll_once(db, dispatcher)
    with db.request_conn() as c:
        row = c.execute(
            select(outbox_events).where(outbox_events.c.event_type == "boom")
        ).first()
    assert row.status == "failed"
    assert row.attempts == 10
