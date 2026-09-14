from sqlalchemy import select

from app.common.database import SessionFactory
from app.common.models import AuditEvent, Credential, Device, Session
from app.security.core import token_hash


async def test_register_and_password_is_argon2_hash(client):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "Avocado",
            "email": "Avo@Example.com",
            "password": "correct horse battery",
            "display_name": "Avocado",
        },
    )
    assert response.status_code == 200 or response.status_code == 201
    async with SessionFactory() as db:
        credential = (await db.execute(select(Credential))).scalar_one()
        assert credential.secret_data.startswith("$argon2id$")
        assert "correct horse battery" not in credential.secret_data


async def test_duplicate_username_and_email(client, registered):
    body = {
        "username": "AVOCADO",
        "email": "other@example.com",
        "password": "correct horse battery",
        "display_name": "Other",
    }
    assert (await client.post("/api/auth/register", json=body)).json()["error"]["code"] == "USERNAME_TAKEN"
    body["username"] = "other"
    body["email"] = "AVO@example.com"
    assert (await client.post("/api/auth/register", json=body)).json()["error"]["code"] == "EMAIL_TAKEN"


async def test_login_by_username_and_email(client, registered):
    for login in ["AVOCADO", "Avo@Example.com"]:
        response = await client.post("/api/auth/login", json={"login": login, "password": "correct horse battery"})
        assert response.status_code == 200


async def test_invalid_login_does_not_enumerate_accounts(client, registered):
    wrong = await client.post("/api/auth/login", json={"login": "avocado", "password": "not the password"})
    unknown = await client.post("/api/auth/login", json={"login": "nobody", "password": "not the password"})
    assert wrong.status_code == unknown.status_code == 401
    assert (
        wrong.json() == unknown.json() == {"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid credentials"}}
    )


async def test_session_cookie_and_hash_storage(client, logged_in):
    raw = client.cookies["lako_session"]
    assert (await client.get("/api/auth/me")).json()["display_name"] == "Avocado"
    async with SessionFactory() as db:
        session = (await db.execute(select(Session))).scalar_one()
        assert session.token_hash == token_hash(raw) and raw != session.token_hash


async def test_logout_and_revoked_session_rejected(client, logged_in):
    raw = client.cookies["lako_session"]
    csrf = client.cookies["lako_csrf"]
    assert (await client.post("/api/sessions/logout", headers={"x-csrf-token": csrf})).status_code == 200
    client.cookies.set("lako_session", raw)
    response = await client.get("/api/auth/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "SESSION_REVOKED"


async def test_csrf_required(client, logged_in):
    assert (await client.post("/api/sessions/logout")).json()["error"]["code"] == "CSRF_FAILED"


async def test_device_list_revoke_and_sessions_revoked(client, logged_in):
    devices = (await client.get("/api/devices")).json()
    assert len(devices) == 1 and devices[0]["current"]
    response = await client.post(
        f"/api/devices/{devices[0]['id']}/revoke", headers={"x-csrf-token": client.cookies["lako_csrf"]}
    )
    assert response.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401
    async with SessionFactory() as db:
        assert (await db.execute(select(Device))).scalar_one().revoked_at is not None
        assert (await db.execute(select(Session))).scalar_one().revoked_at is not None


async def test_logout_others(client, registered):
    await client.post(
        "/api/auth/login",
        json={"login": "avocado", "password": "correct horse battery"},
        headers={"user-agent": "Browser One"},
    )
    client.cookies.clear()
    await client.post(
        "/api/auth/login",
        json={"login": "avocado", "password": "correct horse battery"},
        headers={"user-agent": "Browser Two"},
    )
    response = await client.post("/api/sessions/logout-others", headers={"x-csrf-token": client.cookies["lako_csrf"]})
    assert response.json()["revoked"] == 1


async def test_secrets_are_not_written_to_audit_log(client, registered):
    secret = "never-log-this-password"
    await client.post("/api/auth/login", json={"login": "avocado", "password": secret})
    async with SessionFactory() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
        serialized = repr([(event.event_type, event.metadata_json) for event in events])
        assert secret not in serialized
        assert "lako_session" not in serialized
