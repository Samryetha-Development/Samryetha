from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import select, update

from samryetha.auth import AuthService
from samryetha.auth.security import hash_token
from samryetha.core.config import Settings, load_settings
from samryetha.core.db import Database
from samryetha.core.errors import APIError
from samryetha.core.middleware import SlidingWindowLimiter
from samryetha.core.schema import users, recovery_keys
from samryetha.feedback.backup import BackupService, apply_pending_restore


def test_no_email_key_recovery_rotates_and_revokes(api):
    registered = api.register("keyuser").json()
    key = registered["recoveryKey"]
    assert len(key) >= 43
    api.activate("keyuser")
    api.login("keyuser")
    with api.app.state.db.request_conn() as conn:
        saved = conn.execute(select(recovery_keys.c.key_hash)).scalar_one()
        assert saved == hash_token(key) and key not in saved
    response = api.c.post("/api/auth/recover-with-key", json={
        "username": "keyuser", "recoveryKey": key, "newPassword": "replacement123",
    })
    assert response.status_code == 200, response.text
    assert response.json()["recoveryKey"] != key
    assert api.c.get("/api/auth/me").status_code == 401
    assert api.c.post("/api/auth/recover-with-key", json={
        "username": "keyuser", "recoveryKey": key, "newPassword": "anotherpass123",
    }).status_code == 400
    api.login("keyuser", "replacement123")
    assert "recoveryKey" not in api.c.get("/api/auth/me").json()["user"]


def test_existing_account_key_requires_password_and_active_session(api):
    api.mkuser("existing")
    assert api.c.post("/api/auth/recovery-key", json={"currentPassword": "password123"}).status_code == 401
    api.login("existing")
    assert api.c.post("/api/auth/recovery-key", json={"currentPassword": "wrong"}).status_code == 401
    first = api.c.post("/api/auth/recovery-key", json={"currentPassword": "password123"}).json()["recoveryKey"]
    second = api.c.post("/api/auth/recovery-key", json={"currentPassword": "password123"}).json()["recoveryKey"]
    assert first != second
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "existing").values(status="banned"))
    assert api.c.post("/api/auth/recover-with-key", json={"username": "existing", "recoveryKey": second, "newPassword": "newpassword123"}).status_code == 400


def test_concurrent_key_consumption_exactly_once(api):
    registration = api.register("concurrent").json()
    key = registration["recoveryKey"]
    database = api.app.state.db

    def attempt():
        try:
            with database.request_conn() as conn:
                AuthService(conn).recover_with_key("concurrent", key, "nextpassword123")
            return "ok"
        except APIError:
            return "invalid"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: attempt(), range(2))) == ["invalid", "ok"]


def test_health_database_failure_is_503(api, monkeypatch):
    def unavailable():
        raise RuntimeError("private database path")
    monkeypatch.setattr(api.app.state.db.engine, "connect", unavailable)
    result = api.c.get("/api/health")
    assert result.status_code == 503
    assert result.json() == {"status": "error"}


def test_limiter_reclaims_expired_buckets_and_bounds_memory(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("samryetha.core.middleware.time.monotonic", lambda: clock[0])
    limiter = SlidingWindowLimiter(max_hits=2, window_seconds=60)
    for i in range(10_000):
        assert limiter.allow(str(i))[0]
    assert not limiter.allow("overflow")[0]
    clock[0] += 61
    assert limiter.allow("new")[0]
    assert len(limiter._hits) == 1


def test_builtin_bootstrap_does_not_reactivate_or_promote_existing(api):
    api.register("dev")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "dev").values(status="banned"))
        AuthService(conn, api.settings).ensure_builtin_accounts()
        row = conn.execute(select(users).where(users.c.username == "dev")).first()
        assert row.status == "banned" and row.role == "student"


def test_backup_restore_roundtrip_and_corrupt_candidate_preserves_database(tmp_path):
    db = Database(str(tmp_path / "app.db"))
    db.create_schema()
    with db.request_conn() as conn:
        AuthService(conn).register("saved", "password123")
    backup = BackupService(db).create_backup()
    with db.request_conn() as conn:
        AuthService(conn).register("after", "password123")
    BackupService(db).restore_backup(backup["name"])
    db.close()
    settings = Settings(_env_file=None, database_url=str(tmp_path / "app.db"))
    apply_pending_restore(settings)
    with closing(sqlite3.connect(settings.database_url)) as restored:
        assert restored.execute("SELECT username FROM users").fetchall() == [("saved",)]
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert Path(settings.database_url + ".pre-restore.sqlite").exists()
    (tmp_path / "backups" / backup["name"]).write_bytes(b"corrupt")
    (tmp_path / ".restore_pending").write_text(backup["name"])
    with pytest.raises(RuntimeError):
        apply_pending_restore(settings)
    with closing(sqlite3.connect(settings.database_url)) as preserved:
        assert preserved.execute("SELECT username FROM users").fetchall() == [("saved",)]
    assert (tmp_path / ".restore_pending").exists()


def test_production_requires_https_and_secure_cookies(monkeypatch):
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("STORAGE_SECRET", "independent-long-production-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "different-administrator-password")
    monkeypatch.setenv("DEV_PASSWORD", "different-development-password")
    monkeypatch.setenv("APP_ORIGIN", "http://example.test")
    with pytest.raises(RuntimeError, match="HTTPS"):
        load_settings()
    monkeypatch.setenv("APP_ORIGIN", "https://example.test")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        load_settings()


def test_profile_privacy_and_online_timestamp_are_enforced(api):
    api.mkuser("private")
    api.login("private")
    assert api.c.patch("/api/me/profile", json={"settings": {"public_profile": False, "show_online_status": False}}).status_code == 200
    assert api.c.get("/api/users/private").status_code == 200
    api.c.post("/api/auth/logout")
    assert api.c.get("/api/users/private").status_code == 404
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "private").values(settings='{"show_online_status":false}', last_seen_at=123456))
    assert api.c.get("/api/users/private").json()["lastSeenAt"] is None


def test_message_pagination_and_nonparticipant_access(api):
    from samryetha.messages.service import MessageService
    from samryetha.core.ids import UserID

    api.mkuser("sender")
    api.mkuser("recipient")
    api.mkuser("unrelated")
    with api.app.state.db.request_conn() as conn:
        sender = conn.execute(select(users.c.id).where(users.c.username == "sender")).scalar_one()
        service = MessageService(conn)
        for number in range(55):
            conversation = service.send(UserID(sender), "recipient", f"message {number}").conversation_id
    api.login("recipient")
    first = api.c.get(f"/api/messages/conversations/{conversation}").json()
    assert len(first["items"]) == 50 and first["items"][0]["body"] == "message 5"
    second = api.c.get(f"/api/messages/conversations/{conversation}?before={first['nextCursor']}").json()
    assert len(second["items"]) == 5 and second["nextCursor"] is None
    assert not ({m["id"] for m in first["items"]} & {m["id"] for m in second["items"]})
    summaries = api.c.get("/api/messages/conversations").json()["items"]
    assert summaries[0]["lastMessage"]["body"] == "message 54"
    api.login("unrelated")
    assert api.c.get(f"/api/messages/conversations/{conversation}").status_code == 404
    assert api.c.get(f"/api/messages/conversations/{conversation}?before={first['nextCursor']}").status_code == 404


def test_auth_secrets_not_cached_and_cross_site_mutation_rejected(api):
    result = api.register("uncached")
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert api.c.post("/api/auth/register", headers={"Sec-Fetch-Site": "cross-site"}, json={"username": "other", "password": "password123"}).status_code == 403


def test_schema_upgrade_preserves_existing_account(tmp_path):
    db = Database(str(tmp_path / "old.db"))
    db.create_schema()
    with db.request_conn() as conn:
        uid = AuthService(conn).register("legacy", "password123")
    recovery_keys.drop(db.engine)
    db.create_schema()
    db.ensure_schema_drift()
    db.ensure_schema_drift()
    with db.request_conn() as conn:
        assert conn.execute(select(users.c.username).where(users.c.id == uid)).scalar_one() == "legacy"
        assert AuthService(conn).rotate_recovery_key(uid, "password123")
    db.close()


def test_email_recovery_rotates_keys_revokes_qr_and_rejects_replay(api):
    import re
    from samryetha.auth.qr_login import QrAuthService

    delivered = []
    class Mailbox:
        def send(self, **mail):
            delivered.append(mail)
    api.app.state.mailer = Mailbox()
    old_key = api.register("mailuser").json()["recoveryKey"]
    api.activate("mailuser")
    api.login("mailuser")
    assert api.c.patch("/api/me/profile", json={"recoveryEmail": "owner@example.test", "currentPassword": "password123"}).status_code == 200
    with api.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "mailuser")).scalar_one()
        qr = QrAuthService(conn)
        ticket = qr.begin_ticket(ip=None, user_agent=None, ttl_ms=60000)
        qr.decide_ticket(ticket["ticket_id"], uid, True)
    request = {"username": "mailuser", "recoveryEmail": "owner@example.test"}
    known = api.c.post("/api/auth/forgot-password", json=request)
    unknown = api.c.post("/api/auth/forgot-password", json={**request, "username": "unknown"})
    assert known.json() == unknown.json() and len(delivered) == 1
    token = re.search(r"token=([A-Za-z0-9_-]+)", delivered[0]["text"]).group(1)
    result = api.c.post("/api/auth/reset-password", json={"token": token, "newPassword": "replacement123"})
    assert result.status_code == 200 and result.json()["ok"]
    assert result.json()["recoveryKey"] != old_key
    assert api.c.get("/api/auth/me").status_code == 401
    assert api.c.post("/api/auth/reset-password", json={"token": token, "newPassword": "replacement456"}).status_code == 400
    assert api.c.post("/api/auth/recover-with-key", json={"username": "mailuser", "recoveryKey": old_key, "newPassword": "replacement456"}).status_code == 400
    with pytest.raises(APIError):
        with api.app.state.db.request_conn() as conn:
            QrAuthService(conn).exchange_ticket(ticket["ticket_id"], ticket["secret"])


def test_published_default_builtin_password_blocks_production_start(api):
    api.seed_builtin()
    api.settings.node_env = "production"
    with pytest.raises(RuntimeError, match="published development password"):
        with api.app.state.db.request_conn() as conn:
            AuthService(conn, api.settings).ensure_builtin_accounts()


@pytest.mark.parametrize("streamed", [False, True])
def test_oversized_json_rejected_before_parsing(api, streamed):
    data = b'"' + b'x' * (1024 * 1024) + b'"'
    result = api.c.post("/api/auth/register", content=iter([data[:200], data[200:]]) if streamed else data,
                        headers={"Content-Type": "application/json"})
    assert result.status_code == 413
    assert data[:200].decode() not in result.text


def test_auth_rate_limit_cannot_be_bypassed_by_forwarded_header(api):
    api.app.state.auth_limiter = SlidingWindowLimiter(max_hits=2, window_seconds=60)
    for i in range(2):
        response = api.c.post("/api/auth/login", headers={"X-Forwarded-For": f"192.0.2.{i}"}, json={"username": "unknown", "password": "wrong"})
        assert response.status_code == 401
    assert api.c.post("/api/auth/login", headers={"X-Forwarded-For": "198.51.100.10"}, json={"username": "unknown", "password": "wrong"}).status_code == 429


@pytest.mark.parametrize("revocation", ["logout", "ban", "expire"])
def test_open_sse_stops_after_session_revocation(api, revocation):
    import asyncio
    from starlette.requests import Request
    from samryetha.core.schema import sessions
    from samryetha.events.realtime_router import events
    from samryetha.notifications.models import PresenceChangedEvent, RealtimePresenceData

    api.mkuser("streamuser")
    api.login("streamuser")
    token = api.c.cookies.get("samryetha_session")

    async def exercise():
        request = Request({"type": "http", "app": api.app, "headers": [(b"cookie", f"samryetha_session={token}".encode())]})
        response = await events(request)
        iterator = response.body_iterator.__aiter__()
        assert "connected" in await anext(iterator)
        assert "unread-count" in await anext(iterator)
        assert "presence" in await anext(iterator)
        with api.app.state.db.request_conn() as conn:
            if revocation == "logout":
                AuthService(conn).logout(token)
            elif revocation == "ban":
                conn.execute(update(users).where(users.c.username == "streamuser").values(status="banned"))
            else:
                conn.execute(update(sessions).values(expires_at=1))
        api.app.state.events.publish(PresenceChangedEvent(data=RealtimePresenceData(online_count=10)))
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(iterator), timeout=1)
        assert api.app.state.presence.online_count() == 0

    asyncio.run(exercise())


def test_recovery_email_change_needs_password_and_can_be_removed(api):
    api.mkuser("mailproof")
    api.login("mailproof")
    patch = {"recoveryEmail": "attacker@example.test"}
    assert api.c.patch("/api/me/profile", json=patch).status_code == 403
    assert api.c.patch("/api/me/profile", json={**patch, "currentPassword": "wrong"}).status_code == 403
    assert api.c.get("/api/auth/me").json()["user"]["recoveryEmail"] is None
    assert api.c.patch("/api/me/profile", json={**patch, "currentPassword": "password123"}).status_code == 200
    assert api.c.patch("/api/me/profile", json={"recoveryEmail": "invalid", "currentPassword": "password123"}).status_code == 422
    assert api.c.patch("/api/me/profile", json={"recoveryEmail": ""}).status_code == 403
    from samryetha.auth.repository import AuthRepository
    from samryetha.core.db import now_ms
    from samryetha.core.ids import UserID
    from samryetha.core.schema import password_reset_tokens

    with api.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "mailproof")).scalar_one()
        AuthRepository(conn).insert_password_reset(user_id=UserID(uid), token_hash=hash_token("old-mail-proof"), expires_at=now_ms()+60000, created_at=now_ms())
    cleared = api.c.patch("/api/me/profile", json={"recoveryEmail": "", "currentPassword": "password123"})
    assert cleared.status_code == 200 and cleared.json()["user"]["recoveryEmail"] is None
    assert api.c.post("/api/auth/reset-password", json={"token": "old-mail-proof", "newPassword": "anotherpassword123"}).status_code == 400
    # Deletion anonymizes the account and removes its remaining recovery capability.
    with api.app.state.db.request_conn() as conn:
        AuthService(conn).issue_recovery_key(uid)
        AuthRepository(conn).insert_password_reset(user_id=UserID(uid), token_hash=hash_token("pending-delete-proof"), expires_at=now_ms()+60000, created_at=now_ms())
        conn.execute(update(users).where(users.c.id == uid).values(recovery_email="private@example.test"))
    api.login_dev()
    assert api.c.delete(f"/api/admin/users/{uid}").status_code == 200
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(recovery_keys).where(recovery_keys.c.user_id == uid)).first() is None
        assert conn.execute(select(users.c.recovery_email).where(users.c.id == uid)).scalar_one() is None
        assert conn.execute(select(password_reset_tokens.c.used_at).where(password_reset_tokens.c.token_hash == hash_token("pending-delete-proof"))).scalar_one() is not None


def test_production_access_logs_redact_auth_and_signed_queries():
    import logging
    from samryetha.core.logging import RedactAccessQuery, production_logging_config

    for target in ["/api/auth/callback?code=secret-code&state=private-state", "/api/attachments/serve/file?sig=secret-signature&expires=123"]:
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("127.0.0.1", "GET", target, "1.1", 200), None)
        assert RedactAccessQuery().filter(record)
        assert "?" not in record.getMessage() and "secret-" not in record.getMessage()
        assert "GET /api/" in record.getMessage() and "200" in record.getMessage()
    assert production_logging_config()["handlers"]["access"]["filters"] == ["redact_query"]
