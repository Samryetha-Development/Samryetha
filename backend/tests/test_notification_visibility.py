"""Deleted content and board permissions still protect notifications."""

from sqlalchemy import select, update

from samryetha import notifications as notification_service
from samryetha.core.schema import boards, discussions, users


def _board(api):
    api.login_dev()
    response = api.c.post("/api/boards", json={"name": "Notifications", "slug": "notify-board", "postingPolicy": "everyone"})
    assert response.status_code == 201
    return "notify-board"


def _post(api, slug, body, title="Test"):
    response = api.c.post("/api/discussions", json={"boardSlug": slug, "title": title, "bodyMarkdown": body})
    assert response.status_code == 201
    return response.json()


def _setup(api):
    slug = _board(api)
    for name in ("notifyauthor", "notifywriter", "notifyreader"):
        api.mkuser(name)
    api.login("notifyauthor")
    did = _post(api, slug, "Original body", title="Original title")["id"]
    api.login("notifyreader")
    assert api.c.post(f"/api/discussions/{did}/follow").status_code == 200
    api.login("notifywriter")
    return slug, did


def _reply(api, did, body="A normal reply"):
    response = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": body})
    assert response.status_code == 201, response.text
    return response.json()


def _reader_notes(api, **params):
    api.login("notifyreader")
    return api.c.get("/api/notifications", params=params).json()


def test_hidden_notification_filter_precedes_pagination_and_unread_count(api):
    _, did = _setup(api)
    rid = _reply(api, did)["id"]
    api.app.state.flush_outbox()
    assert _reader_notes(api)["unreadCount"] == 1
    with api.app.state.db.request_conn() as conn:
        reader = conn.execute(select(users.c.id).where(users.c.username == "notifyreader")).scalar_one()
        visible_id = notification_service.NotificationService(conn).create(user_id=reader, type_='system', body='Visible system notice')
        conn.execute(update(discussions).where(discussions.c.id == did).values(deleted_at=1))
        for _ in range(3):
            notification_service.NotificationService(conn).create(user_id=reader, type_='reply', discussion_id=did, reply_id=rid, body='DELETED_TITLE')
    page = api.c.get("/api/notifications", params={"limit": 1}).json()
    assert [n["id"] for n in page["items"]] == [visible_id]
    assert page["unreadCount"] == 1 and page["nextCursor"] is None
    assert api.c.get("/api/notifications/unread-count").json()["unreadCount"] == 1


def test_delivery_and_existing_notifications_honor_board_membership(api):
    _, did = _setup(api)
    _reply(api, did)
    api.app.state.flush_outbox()
    assert _reader_notes(api)["unreadCount"] == 1
    api.login("notifywriter")
    _reply(api, did, "Second reply")
    with api.app.state.db.request_conn() as conn:
        board_id = conn.execute(select(discussions.c.board_id).where(discussions.c.id == did)).scalar_one()
        conn.execute(update(boards).where(boards.c.id == board_id).values(visibility="private"))
    api.app.state.flush_outbox()
    notes = _reader_notes(api)
    assert notes["items"] == [] and notes["unreadCount"] == 0


def test_existing_database_gains_the_outbox_lookup_index(db):
    from sqlalchemy import inspect

    with db.request_conn() as conn:
        conn.exec_driver_sql("DROP INDEX outbox_aggregate_event_idx")
    db.ensure_schema_drift()
    db.ensure_schema_drift()
    assert "outbox_aggregate_event_idx" in {i["name"] for i in inspect(db.engine).get_indexes("outbox_events")}
