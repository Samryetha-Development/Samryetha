"""Guard the overlapping public-beta and security-audit changes in PR #130."""

import pytest
from sqlalchemy import select, update

from samryetha.auth import AuthService
from samryetha.core.config import Settings
from samryetha.core.schema import outbox_events, users
from samryetha.events.outbox_worker import OutboxDeliveryService, OutboxDispatcher, register_outbox_handlers
from samryetha.notifications.models import UnreadChangedEvent


@pytest.mark.parametrize("username", ["admin", "dev"])
@pytest.mark.parametrize("status,role", [("banned", "admin"), ("active", "student")])
def test_builtin_restart_preserves_explicit_ban_or_demotion(db, username, status, role):
    settings = Settings(_env_file=None)
    with db.request_conn() as conn:
        AuthService(conn, settings).ensure_builtin_accounts()
        conn.execute(update(users).where(users.c.username == username).values(status=status, role=role))
        before = conn.execute(select(users).where(users.c.username == username)).one()

    with db.request_conn() as conn:
        AuthService(conn, settings).ensure_builtin_accounts()
        after = conn.execute(select(users).where(users.c.username == username)).one()
        assert after.is_builtin == 1
        assert (after.status, after.role) == (status, role)
        assert after.password_hash == before.password_hash
        assert after.updated_at == before.updated_at


def test_password_reset_mail_and_unread_updates_are_both_delivered(api):
    api.mkuser("combined")
    api.login("combined")
    assert api.c.patch("/api/me/profile", json={
        "recoveryEmail": "combined@example.test", "currentPassword": "password123",
    }).status_code == 200

    delivered = []

    class Mailbox:
        def send(self, **mail):
            # A second writer must succeed while the worker sends the mail.
            with api.app.state.db.request_conn() as conn:
                conn.execute(update(users).where(users.c.username == "combined").values(bio="mail delivered"))
            delivered.append(mail)

    dispatcher = OutboxDispatcher()
    register_outbox_handlers(dispatcher, Mailbox())
    assert api.c.post("/api/auth/forgot-password", json={
        "username": "combined", "recoveryEmail": "combined@example.test",
    }).status_code == 200
    assert delivered == []
    assert api.c.post("/api/notifications/read-all").status_code == 200

    events = OutboxDeliveryService(api.app.state.db, dispatcher=dispatcher).poll_once()
    assert len(delivered) == 1
    with api.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "combined")).scalar_one()
        assert conn.execute(select(users.c.bio).where(users.c.id == uid)).scalar_one() == "mail delivered"
        rows = conn.execute(select(outbox_events.c.event_type, outbox_events.c.status).where(
            outbox_events.c.event_type.in_(["password-reset.requested", "unread.changed"]),
        )).all()
    assert sorted(rows) == [("password-reset.requested", "done"), ("unread.changed", "done")]
    assert [event.data.user_id for event in events if isinstance(event, UnreadChangedEvent)] == [uid]
