"""Content publishes directly; legacy review data is released without data loss."""

from sqlalchemy import inspect, select, update

from samryetha.core.config import Settings
from samryetha.core.schema import app_settings, discussions, outbox_events, users


def _post(api, title="Direct publication", body="Hello"):
    response = api.c.post(
        "/api/discussions", json={"boardSlug": "review-removal", "title": title, "bodyMarkdown": body}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup(api):
    api.login_dev()
    response = api.c.post("/api/boards", json={"slug": "review-removal", "name": "Direct publication"})
    assert response.status_code == 201, response.text


def test_fresh_schema_and_contract_have_no_content_review(api):
    assert "moderation_queue" not in inspect(api.app.state.db.engine).get_table_names()
    for table in ("discussions", "replies", "direct_messages", "users"):
        columns = {column["name"] for column in inspect(api.app.state.db.engine).get_columns(table)}
        assert "moderation_status" not in columns
        assert "profile_moderation_status" not in columns
        assert "pending_bio" not in columns
    assert not any(name.startswith("automod_") for name in Settings.model_fields)
    schemas = api.app.openapi()["components"]["schemas"]
    assert "ModerationStatus" not in schemas
    assert "QueueItemResponse" not in schemas
    for endpoint in ("/api/admin/moderation/queue", "/api/admin/moderation/retained", "/api/admin/moderation/counts"):
        assert api.c.get(endpoint).status_code == 404


def test_posts_replies_edits_profiles_and_messages_publish_directly(api):
    _setup(api)
    api.mkuser("directreader")
    api.login_dev()
    posted = _post(api, body="Contact me at https://example.com @directreader")
    assert "moderationStatus" not in posted
    reply = api.c.post(f"/api/discussions/{posted['id']}/replies", json={"bodyMarkdown": "A direct reply"})
    assert reply.status_code == 201 and "moderationStatus" not in reply.json()
    edit = api.c.patch(f"/api/discussions/{posted['id']}", json={"bodyMarkdown": "Updated directly"})
    assert edit.status_code == 200 and edit.json()["bodyMarkdown"] == "Updated directly"
    profile = api.c.patch("/api/me/profile", json={"displayName": "Direct name", "bio": "Direct bio"})
    assert profile.status_code == 200, profile.text
    assert profile.json()["user"]["displayName"] == "Direct name"
    assert "profilePending" not in profile.json()["user"]
    sent = api.c.post("/api/messages", json={"username": "directreader", "body": "Direct message"})
    assert sent.status_code in (200, 201), sent.text
    api.c.post("/api/auth/logout")
    assert api.c.get(f"/api/discussions/{posted['id']}").status_code == 200


def test_legacy_upgrade_releases_content_profiles_and_held_events_once(api):
    _setup(api)
    posted = _post(api)
    reply = api.c.post(f"/api/discussions/{posted['id']}/replies", json={"bodyMarkdown": "Legacy reply"})
    assert reply.status_code == 201
    api.mkuser("legacyreader")
    sent = api.c.post("/api/messages", json={"username": "legacyreader", "body": "Legacy message"})
    assert sent.status_code == 201, sent.text
    deleted = _post(api, title="Explicitly deleted")
    assert api.c.delete(f"/api/discussions/{deleted['id']}").status_code == 200
    db = api.app.state.db
    with db.request_conn() as conn:
        conn.execute(app_settings.delete().where(app_settings.c.key == "content_review_removed_v1"))
        conn.exec_driver_sql("ALTER TABLE discussions ADD COLUMN moderation_status TEXT NOT NULL DEFAULT 'approved'")
        conn.exec_driver_sql("UPDATE discussions SET moderation_status = 'rejected'")
        for table in ("replies", "direct_messages"):
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN moderation_status TEXT NOT NULL DEFAULT 'approved'")
            conn.exec_driver_sql(f"UPDATE {table} SET moderation_status = 'pending'")
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN profile_moderation_status TEXT NOT NULL DEFAULT 'approved'")
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN pending_display_name TEXT")
        conn.exec_driver_sql("ALTER TABLE users ADD COLUMN pending_bio TEXT")
        conn.exec_driver_sql(
            "UPDATE users SET pending_display_name = 'Released profile', pending_bio = '', "
            "profile_moderation_status = 'rejected' WHERE username = 'dev'"
        )
        conn.exec_driver_sql(
            "CREATE TABLE moderation_queue (id INTEGER PRIMARY KEY, content_type TEXT, content_id INTEGER, decision TEXT, review_state TEXT)"
        )
        conn.exec_driver_sql("INSERT INTO moderation_queue VALUES (1, 'discussion', ?, 'block', 'pending')", (posted["id"],))
        conn.execute(
            update(outbox_events)
            .where(outbox_events.c.event_type.in_(["discussion.created", "reply.created", "mention.created"]))
            .values(status="held", available_at=0)
        )
    db.retire_content_review()
    with db.request_conn() as conn:
        assert conn.exec_driver_sql("SELECT moderation_status FROM discussions").scalars().all() == ["approved", "approved"]
        for table in ("replies", "direct_messages"):
            assert conn.exec_driver_sql(f"SELECT moderation_status FROM {table}").scalar_one() == "approved"
        profile = conn.execute(select(users).where(users.c.username == "dev")).mappings().one()
        assert profile["display_name"] == "Released profile" and profile["bio"] == ""
        assert conn.exec_driver_sql("SELECT pending_display_name, pending_bio FROM users WHERE username = 'dev'").one() == (None, None)
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM moderation_queue").scalar_one() == 1
        assert set(conn.execute(select(outbox_events.c.status)).scalars()) == {"pending"}
        conn.execute(update(users).where(users.c.username == "dev").values(display_name="Later edit"))
    db.retire_content_review()
    with db.request_conn() as conn:
        assert conn.execute(select(users.c.display_name).where(users.c.username == "dev")).scalar_one() == "Later edit"
        assert conn.execute(select(discussions.c.deleted_at).where(discussions.c.id == deleted["id"])).scalar_one() is not None
    api.c.post("/api/auth/logout")
    assert api.c.get(f"/api/discussions/{posted['id']}").status_code == 200
    assert api.c.get(f"/api/discussions/{deleted['id']}").status_code == 404
