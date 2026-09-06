"""站内信（私信）测试 — 覆盖 send 的隐私开关、自聊 400、@ 前缀、会话/未读。"""
from __future__ import annotations


def test_send_creates_conversation_and_unread(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    r = api.c.post("/api/messages", json={"username": "bob", "body": "hello bob"})
    assert r.status_code == 201, r.text
    conv_id = r.json()["conversationId"]

    convs = api.c.get("/api/messages/conversations").json()["items"]
    assert len(convs) == 1
    assert convs[0]["id"] == conv_id
    assert convs[0]["otherUser"]["username"] == "bob"

    # bob 侧未读数为 1
    api.login("bob")
    unread = api.c.get("/api/messages/unread-count").json()["unreadCount"]
    assert unread == 1


def test_self_message_is_400(api):
    api.mkuser("alice")
    api.login("alice")
    r = api.c.post("/api/messages", json={"username": "alice", "body": "hi"})
    assert r.status_code == 400, r.text


def test_dm_disabled_is_403(api):
    from sqlalchemy import update

    from samryetha.schema import users

    api.mkuser("alice")
    api.mkuser("bob")
    with api.app.state.db.request_conn() as conn:
        conn.execute(
            update(users).where(users.c.username == "bob").values(settings='{"direct_messages": false}')
        )
    api.login("alice")
    r = api.c.post("/api/messages", json={"username": "bob", "body": "hi"})
    assert r.status_code == 403, r.text


def test_send_accepts_at_prefix(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    r = api.c.post("/api/messages", json={"username": "@bob", "body": "hi"})
    assert r.status_code == 201, r.text
