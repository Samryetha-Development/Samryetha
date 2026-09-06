from __future__ import annotations

from sqlalchemy import select, update

from samryetha.db import now_ms
from samryetha.schema import notifications, outbox_events, users


def test_admin_reset_password_is_audited_and_revokes_sessions(api):
    api.login_dev()
    api.mkuser("alice")
    api.login("alice")
    alice_cookie = api.c.cookies.get("samryetha_session")
    with api.app.state.db.request_conn() as conn:
        target = conn.execute(select(users.c.id).where(users.c.username == "alice")).first()
    api.login_dev()
    result = api.c.post(f"/api/admin/users/{target.id}/reset-password")
    assert result.status_code == 200
    temporary = result.json()["temporaryPassword"]
    assert api.c.get("/api/auth/me").status_code == 200
    api.c.cookies.set("samryetha_session", alice_cookie)
    assert api.c.get("/api/auth/me").status_code == 401
    assert api.login("alice", temporary).status_code == 200
    with api.app.state.db.request_conn() as conn:
        from samryetha.schema import moderation_actions
        row = conn.execute(select(moderation_actions).where(moderation_actions.c.target_id == target.id)).first()
        assert row.action == "user.password.reset"


def test_mentions_emit_notifications_and_preserve_parent_reply(api):
    api.login_dev()
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    api.c.cookies.delete("samryetha_session")
    api.login_dev()
    board = api.c.post("/api/boards", json={"name": "Mentions", "slug": "mentions", "postingPolicy": "everyone"})
    assert board.status_code == 201
    created = api.c.post("/api/discussions", json={"boardSlug": "mentions", "title": "Hello", "bodyMarkdown": "Hi @BoB @bob @ghost"})
    assert created.status_code == 201
    did = created.json()["id"]
    reply = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "Nested @bob", "parentReplyId": None})
    assert reply.status_code == 201
    dispatcher = api.app.state.dispatcher
    api.app.state.flush_outbox()
    with api.app.state.db.request_conn() as conn:
        rows = conn.execute(select(notifications).where(notifications.c.type == "mention")).all()
        assert len(rows) == 2
        assert all(row.user_id != 1 for row in rows)
        assert reply.json()["parentReplyId"] is None
        for row in conn.execute(select(outbox_events).where(outbox_events.c.event_type == "mention.created")).all():
            conn.execute(update(outbox_events).where(outbox_events.c.id == row.id).values(status="pending", available_at=now_ms()))
    api.app.state.flush_outbox()
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(notifications).where(notifications.c.type == "mention")).fetchall().__len__() == 2
