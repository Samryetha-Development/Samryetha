"""Email flows: reset (enumeration-safe), verify, change email/password, admin invite."""

import re

import pytest
from sqlalchemy import select

from app.common.database import SessionFactory
from app.common.mailer import DummyMailer
from app.common.models import Identity, IdentityType
from app.main import app


@pytest.fixture(autouse=True)
def mailer():
    dummy = DummyMailer()
    app.state.mailer = dummy
    yield dummy


def csrf(client) -> dict:
    return {"x-csrf-token": client.cookies["lako_csrf"]}


def _token_from(text: str) -> str:
    match = re.search(r"token=([A-Za-z0-9_-]+)", text)
    assert match is not None
    return match.group(1)


async def _register(client, username="plug", email="plug@example.com", password="a-very-long-password"):
    response = await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": password, "display_name": username},
    )
    assert response.status_code in (200, 201)
    return response.json()


async def _login(client, login="plug", password="a-very-long-password"):
    response = await client.post("/api/auth/login", json={"login": login, "password": password})
    assert response.status_code == 200
    return response


async def _set_verified(username: str) -> None:
    async with SessionFactory() as db:
        identity = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == username,
                    Identity.type == IdentityType.USERNAME,
                )
            )
        ).scalar_one()
        email = (
            await db.execute(
                select(Identity).where(
                    Identity.user_id == identity.user_id, Identity.type == IdentityType.EMAIL
                )
            )
        ).scalar_one()
        email.verified = True
        await db.commit()


async def test_reset_request_unknown_login_is_silent(client, mailer):
    response = await client.post("/api/auth/password/reset/request", json={"login": "ghost"})
    assert response.json() == {"ok": True}
    assert mailer.outbox == []


async def test_reset_requires_verified_email(client, mailer):
    await _register(client)
    response = await client.post("/api/auth/password/reset/request", json={"login": "plug"})
    assert response.json() == {"ok": True}
    assert mailer.outbox == []


async def test_reset_full_flow_revokes_sessions(client, mailer):
    await _register(client)
    await _set_verified("plug")
    await _login(client)
    assert (await client.post("/api/auth/password/reset/request", json={"login": "plug"})).json() == {"ok": True}
    assert len(mailer.outbox) == 1
    token = _token_from(mailer.outbox[0]["text"])

    weak = await client.post(
        "/api/auth/password/reset/confirm", json={"token": token, "new_password": "short"}
    )
    assert weak.status_code == 422

    good = await client.post(
        "/api/auth/password/reset/confirm", json={"token": token, "new_password": "a-brand-new-password"}
    )
    assert good.json() == {"ok": True}
    # single-use and sessions revoked
    again = await client.post(
        "/api/auth/password/reset/confirm", json={"token": token, "new_password": "another-long-password"}
    )
    assert again.status_code == 400
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.post("/api/auth/login", json={"login": "plug", "password": "a-very-long-password"})).status_code == 401
    assert (await client.post("/api/auth/login", json={"login": "plug", "password": "a-brand-new-password"})).status_code == 200


async def test_verify_flow_marks_single_address(client, mailer):
    await _register(client)
    await _login(client)
    first = await client.post("/api/account/email/verify/request", headers=csrf(client), json={})
    assert first.json() == {"ok": True}
    assert len(mailer.outbox) == 1
    token = _token_from(mailer.outbox[0]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token})).json() == {"ok": True}
    repeat = await client.post("/api/account/email/verify/request", headers=csrf(client), json={})
    assert repeat.json() == {"ok": True, "already_verified": True}
    assert len(mailer.outbox) == 1


async def test_change_email_and_password(client, mailer):
    await _register(client)
    await _login(client)
    change = await client.post(
        "/api/account/email/change", headers=csrf(client), json={"email": "newmail@example.com"}
    )
    assert change.json() == {"ok": True}
    assert len(mailer.outbox) == 1
    token = _token_from(mailer.outbox[0]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token})).json() == {"ok": True}
    async with SessionFactory() as db:
        rows = (
            (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        )
        assert [row.identifier for row in rows] == ["newmail@example.com"]
        assert rows[0].verified is True

    bad_current = await client.post(
        "/api/account/password/change",
        headers=csrf(client),
        json={"current_password": "wrong-password-here", "new_password": "another-long-password"},
    )
    assert bad_current.status_code == 401
    good = await client.post(
        "/api/account/password/change",
        headers=csrf(client),
        json={"current_password": "a-very-long-password", "new_password": "another-long-password"},
    )
    assert good.json() == {"ok": True}
    assert (await client.post("/api/auth/login", json={"login": "plug", "password": "another-long-password"})).status_code == 200


async def test_change_email_rejects_taken(client, mailer):
    await _register(client, username="alpha", email="alpha@example.com")
    await _register(client, username="beta", email="beta@example.com")
    await _login(client, login="alpha")
    taken = await client.post(
        "/api/account/email/change", headers=csrf(client), json={"email": "beta@example.com"}
    )
    assert taken.status_code == 409


async def test_admin_invite_accepts_and_verifies(client, mailer, monkeypatch):
    from app.common.config import get_settings

    monkeypatch.setenv("ADMIN_IMPORT_TOKEN", "invite-token")
    get_settings.cache_clear()
    try:
        headers = {"authorization": "Bearer invite-token"}
        created = await client.post(
            "/api/admin/users/import",
            json={"users": [{"username": "invited", "email": "invited@example.com"}]},
            headers=headers,
        )
        assert created.status_code == 200
        invited = await client.post(
            "/api/admin/users/invite", json={"username": "invited"}, headers=headers
        )
        assert invited.json() == {"ok": True, "email": "invited@example.com"}
        assert len(mailer.outbox) == 1
        token = _token_from(mailer.outbox[0]["text"])
        confirm = await client.post(
            "/api/auth/password/reset/confirm", json={"token": token, "new_password": "invited-user-password"}
        )
        assert confirm.json() == {"ok": True}
        async with SessionFactory() as db:
            row = (
                await db.execute(
                    select(Identity).where(
                        Identity.normalized_identifier == "invited@example.com",
                        Identity.type == IdentityType.EMAIL,
                    )
                )
            ).scalar_one()
            assert row.verified is True
        assert (
            await client.post("/api/auth/login", json={"login": "invited", "password": "invited-user-password"})
        ).status_code == 200
    finally:
        get_settings.cache_clear()


async def test_invite_unknown_user_404(client, monkeypatch):
    from app.common.config import get_settings

    monkeypatch.setenv("ADMIN_IMPORT_TOKEN", "invite-token")
    get_settings.cache_clear()
    try:
        response = await client.post(
            "/api/admin/users/invite",
            json={"username": "nobody"},
            headers={"authorization": "Bearer invite-token"},
        )
        assert response.status_code == 404
    finally:
        get_settings.cache_clear()
