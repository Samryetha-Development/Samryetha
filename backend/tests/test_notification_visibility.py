"""Moderation and board permissions must hold across content side effects."""

import pytest
from sqlalchemy import select, update

from test_automod import am, automod_app, _board, _post
from samryetha import automod, notifications as notification_service
from samryetha.automod_providers import LLMVerdict
from samryetha.content_events import publish_content
from samryetha.schema import boards, discussions, moderation_queue, notifications, outbox_events, replies, users


def _setup(am):
    slug = _board(am)
    for name in ("notifyauthor", "notifywriter", "notifyreader"):
        am.mkuser(name)
    am.login("notifyauthor")
    did = _post(am, slug, "Original body", title="Original title")["id"]
    am.login("notifyreader")
    assert am.c.post(f"/api/discussions/{did}/follow").status_code == 200
    am.login("notifywriter")
    return slug, did


def _reply(am, did, body="A normal reply"):
    response = am.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": body})
    assert response.status_code == 201, response.text
    return response.json()


def _reader_notes(am, **params):
    am.login("notifyreader")
    return am.c.get("/api/notifications", params=params).json()


def _verdict(monkeypatch, risk):
    class Provider:
        def classify(self, text, *, context="post", recheck=False):
            return LLMVerdict(risk=risk, category="harassment" if risk else "none", reason="test verdict")
    monkeypatch.setattr(automod, "_provider_for", lambda settings: Provider())


def test_rejected_parent_refuses_new_replies_and_does_not_leak_title(am):
    _, did = _setup(am)
    am.login("notifyauthor")
    edited = am.c.patch(f"/api/discussions/{did}", json={"title": "傻逼 NEW_REJECTED_TITLE"})
    assert edited.json()["moderationStatus"] == "rejected"
    response = am.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "A follow-up"})
    assert response.status_code == 404
    am.app.state.flush_outbox()
    assert _reader_notes(am)["items"] == []
    with am.app.state.db.request_conn() as conn:
        assert not conn.execute(select(replies.c.id).where(replies.c.discussion_id == did)).first()


@pytest.mark.parametrize("risk", [60, 95])
def test_held_reply_side_effects_are_released_once_on_approval(am, monkeypatch, risk):
    _, did = _setup(am)
    _verdict(monkeypatch, risk)
    created = _reply(am, did, "Hello @notifyreader")
    rid = created["id"]
    assert created["moderationStatus"] == ("pending" if risk == 60 else "rejected")
    am.app.state.flush_outbox()
    assert _reader_notes(am)["items"] == []
    with am.app.state.db.request_conn() as conn:
        queue_id = conn.execute(select(moderation_queue.c.id).where(
            moderation_queue.c.content_type == "reply", moderation_queue.c.content_id == rid,
        )).scalar_one()
        assert not conn.execute(select(outbox_events.c.id).where(
            outbox_events.c.event_type == "reply.created",
        )).first()
    am.login_dev()
    assert am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={"note": "Allow"}).status_code == 200
    am.app.state.flush_outbox()
    notes = _reader_notes(am)
    assert sorted(n["type"] for n in notes["items"]) == ["mention", "reply"]
    # Approval and lease recovery must not create duplicate notifications.
    with am.app.state.db.request_conn() as conn:
        publish_content(conn, "reply", rid)
        publish_content(conn, "reply", rid)
        conn.execute(update(outbox_events).where(outbox_events.c.event_type.in_(["reply.created", "mention.created"])).values(
            status="pending", available_at=1,
        ))
    am.app.state.flush_outbox()
    assert len(_reader_notes(am)["items"]) == 2


def test_approving_parent_releases_mentions_and_approved_children(am, monkeypatch):
    slug, _ = _setup(am)
    am.login("notifyauthor")

    class Provider:
        def classify(self, text, *, context="post", recheck=False):
            return LLMVerdict(risk=60 if context == "post" else 0, category="spam", reason="test context")
    monkeypatch.setattr(automod, "_provider_for", lambda settings: Provider())
    did = _post(am, slug, "Parent @notifyreader")["id"]
    rid = _reply(am, did, "Child @notifyreader")["id"]
    am.app.state.flush_outbox()
    assert _reader_notes(am)["items"] == []
    with am.app.state.db.request_conn() as conn:
        queue_id = conn.execute(select(moderation_queue.c.id).where(
            moderation_queue.c.content_type == "discussion", moderation_queue.c.content_id == did,
        )).scalar_one()
    am.login_dev()
    assert am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={"note": "Allow"}).status_code == 200
    am.app.state.flush_outbox()
    notes = _reader_notes(am)["items"]
    assert len(notes) == 2 and all(n["type"] == "mention" for n in notes)
    assert {n["replyId"] for n in notes} == {None, rid}


def test_old_event_is_held_and_resumed_with_current_title(am):
    _, did = _setup(am)
    rid = _reply(am, did)["id"]
    with am.app.state.db.request_conn() as conn:
        conn.execute(update(discussions).where(discussions.c.id == did).values(
            title="REJECTED_TITLE", moderation_status="rejected",
        ))
    am.app.state.flush_outbox()
    assert _reader_notes(am)["items"] == []
    with am.app.state.db.request_conn() as conn:
        event = conn.execute(select(outbox_events).where(outbox_events.c.event_type == "reply.created")).one()
        assert event.status == "held" and event.attempts == 0
        conn.execute(update(discussions).where(discussions.c.id == did).values(title="Restored title"))
        automod.apply_review_state(conn, content_type="discussion", content_id=did, status="approved")
    am.app.state.flush_outbox()
    notes = _reader_notes(am)["items"]
    assert len(notes) == 1 and "Restored title" in notes[0]["body"]
    assert "REJECTED_TITLE" not in notes[0]["body"]
    assert notes[0]["replyId"] == rid


def test_hidden_notification_filter_precedes_pagination_and_unread_count(am):
    _, did = _setup(am)
    rid = _reply(am, did)["id"]
    am.app.state.flush_outbox()
    assert _reader_notes(am)["unreadCount"] == 1
    with am.app.state.db.request_conn() as conn:
        reader = conn.execute(select(users.c.id).where(users.c.username == "notifyreader")).scalar_one()
        visible_id = notification_service.create(conn, user_id=reader, type_="system", body="Visible system notice")
        conn.execute(update(discussions).where(discussions.c.id == did).values(moderation_status="rejected"))
        for _ in range(3):
            notification_service.create(conn, user_id=reader, type_="reply", discussion_id=did, reply_id=rid,
                                        body="NEW_REJECTED_TITLE")
    page = am.c.get("/api/notifications", params={"limit": 1}).json()
    assert [n["id"] for n in page["items"]] == [visible_id]
    assert page["unreadCount"] == 1 and page["nextCursor"] is None
    assert am.c.get("/api/notifications/unread-count").json()["unreadCount"] == 1


def test_delivery_and_existing_notifications_honor_board_membership(am):
    _, did = _setup(am)
    _reply(am, did)
    am.app.state.flush_outbox()
    assert _reader_notes(am)["unreadCount"] == 1
    am.login("notifywriter")
    _reply(am, did, "Second reply")
    with am.app.state.db.request_conn() as conn:
        board_id = conn.execute(select(discussions.c.board_id).where(discussions.c.id == did)).scalar_one()
        conn.execute(update(boards).where(boards.c.id == board_id).values(visibility="private"))
    am.app.state.flush_outbox()
    notes = _reader_notes(am)
    assert notes["items"] == [] and notes["unreadCount"] == 0


def test_rejected_reply_hides_existing_reply_and_mention_notifications(am):
    _, did = _setup(am)
    rid = _reply(am, did, "Hi @notifyreader")["id"]
    am.app.state.flush_outbox()
    assert _reader_notes(am)["unreadCount"] == 2
    with am.app.state.db.request_conn() as conn:
        automod.apply_review_state(conn, content_type="reply", content_id=rid, status="rejected")
    assert _reader_notes(am)["items"] == []
    assert am.c.get("/api/notifications/unread-count").json()["unreadCount"] == 0


def test_allowed_edit_releases_a_previously_held_submission(am, monkeypatch):
    slug, _ = _setup(am)
    am.login("notifyauthor")
    _verdict(monkeypatch, 60)
    did = _post(am, slug, "Held @notifyreader")["id"]
    am.app.state.flush_outbox()
    assert _reader_notes(am)["items"] == []
    am.login("notifyauthor")
    _verdict(monkeypatch, 0)
    edited = am.c.patch(f"/api/discussions/{did}", json={"bodyMarkdown": "Allowed @notifyreader"})
    assert edited.status_code == 200 and edited.json()["moderationStatus"] == "approved"
    am.app.state.flush_outbox()
    notes = _reader_notes(am)["items"]
    assert len(notes) == 1 and notes[0]["type"] == "mention"


def test_approval_racing_worker_deferral_cannot_strand_the_event(am, monkeypatch):
    _, did = _setup(am)
    _reply(am, did)
    with am.app.state.db.request_conn() as conn:
        conn.execute(update(discussions).where(discussions.c.id == did).values(moderation_status="pending"))
    from samryetha import outbox_worker
    from samryetha.content_events import ContentAwaitingReview

    original = outbox_worker._public_content
    first = True

    def racing_approval(conn, discussion_id, reply_id=None):
        nonlocal first
        if first:
            first = False
            with am.app.state.db.request_conn() as approval:
                automod.apply_review_state(approval, content_type="discussion", content_id=did, status="approved")
            raise ContentAwaitingReview()
        return original(conn, discussion_id, reply_id)

    monkeypatch.setattr(outbox_worker, "_public_content", racing_approval)
    am.app.state.flush_outbox()
    with am.app.state.db.request_conn() as conn:
        event = conn.execute(select(outbox_events).where(outbox_events.c.event_type == "reply.created")).one()
        assert event.status == "pending" and event.attempts == 0
    am.app.state.flush_outbox()
    assert len(_reader_notes(am)["items"]) == 1


def test_existing_database_gains_the_outbox_lookup_index(db):
    from sqlalchemy import inspect

    with db.request_conn() as conn:
        conn.exec_driver_sql("DROP INDEX outbox_aggregate_event_idx")
    db.ensure_schema_drift()
    db.ensure_schema_drift()
    assert "outbox_aggregate_event_idx" in {i["name"] for i in inspect(db.engine).get_indexes("outbox_events")}
