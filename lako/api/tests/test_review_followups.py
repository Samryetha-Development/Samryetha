"""Regression tests for the audit-review follow-ups:

* SmtpMailer.ping must run the same connection setup as send (no enumeration
  oracle from a partial TLS/auth outage).
* Verifying an address must not demote an existing verified address, and
  _verified_email must be deterministic (most recently created wins).
* CR/LF in an email must be rejected at the boundary (422), and a ValueError
  from header assignment maps to 503 as defense in depth.
* Invites prefer a verified address and acceptance converges without demoting.
"""

import re
import smtplib
from typing import ClassVar

import pytest
from sqlalchemy import select

from app.authentication.email_routes import _verified_email
from app.common.config import get_settings
from app.common.database import SessionFactory
from app.common.mailer import DummyMailer, SmtpMailer
from app.common.models import Identity, IdentityType
from app.main import app


@pytest.fixture(autouse=True)
def mailer():
    dummy = DummyMailer()
    app.state.mailer = dummy
    yield dummy


@pytest.fixture
def import_token(monkeypatch):
    monkeypatch.setenv("ADMIN_IMPORT_TOKEN", "review-token")
    get_settings.cache_clear()
    yield "review-token"
    get_settings.cache_clear()


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


class _RecordingSMTP:
    """Fake smtplib.SMTP that records the method call order."""

    instances: ClassVar[list["_RecordingSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list = []
        _RecordingSMTP.instances.append(self)

    def ehlo(self) -> None:
        self.calls.append("ehlo")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def send_message(self, message) -> None:
        self.calls.append("send_message")

    def quit(self) -> None:
        self.calls.append("quit")


async def test_smtp_ping_shares_connection_setup_with_send(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    _RecordingSMTP.instances.clear()
    mailer_impl = SmtpMailer(
        host="smtp.example.com",
        port=587,
        username="smtp-user",
        password="smtp-secret",
        sender="noreply@example.com",
        use_tls=True,
    )
    await mailer_impl.ping()
    ping_calls = _RecordingSMTP.instances[-1].calls
    assert ping_calls == ["ehlo", "starttls", "ehlo", ("login", "smtp-user", "smtp-secret"), "quit"]
    assert "send_message" not in ping_calls

    _RecordingSMTP.instances.clear()
    await mailer_impl.send(to="to@example.com", subject="hi", text="body")
    send_calls = _RecordingSMTP.instances[-1].calls
    assert send_calls == [
        "ehlo",
        "starttls",
        "ehlo",
        ("login", "smtp-user", "smtp-secret"),
        "send_message",
        "quit",
    ]
    # ping runs the identical setup prefix as send, then stops before delivering.
    assert ping_calls[:-1] == send_calls[:-2]


async def test_smtp_ping_skips_starttls_and_login_when_unconfigured(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP", _RecordingSMTP)
    _RecordingSMTP.instances.clear()
    mailer_impl = SmtpMailer(host="smtp.example.com", port=25, sender="noreply@example.com", use_tls=False)
    await mailer_impl.ping()
    assert _RecordingSMTP.instances[-1].calls == ["ehlo", "quit"]


async def test_verify_does_not_demote_existing_verified_and_prefers_latest(client, mailer):
    await _register(client)
    await _login(client)
    assert (await client.post("/api/account/email/verify/request", headers=csrf(client), json={})).json() == {"ok": True}
    token = _token_from(mailer.outbox[0]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token})).json() == {"ok": True}
    assert (await client.post("/api/account/email/change", headers=csrf(client), json={"email": "b@example.com"})).json() == {
        "ok": True
    }
    token2 = _token_from(mailer.outbox[-1]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token2})).json() == {"ok": True}
    async with SessionFactory() as db:
        rows = (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        assert {row.identifier: row.verified for row in rows} == {"plug@example.com": True, "b@example.com": True}
        user_identity = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == "plug",
                    Identity.type == IdentityType.USERNAME,
                )
            )
        ).scalar_one()
        chosen = await _verified_email(db, user_identity.user_id)
        assert chosen is not None
        assert chosen.identifier == "b@example.com"


async def test_change_email_rejects_crlf(client, mailer):
    await _register(client)
    await _login(client)
    response = await client.post(
        "/api/account/email/change",
        headers=csrf(client),
        json={"email": "attacker@example.com\r\nBcc: victim@example.com"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_EMAIL"
    assert mailer.outbox == []


class _ValueErrorMailer:
    async def send(self, **kwargs):
        raise ValueError("Header values may not contain linefeed or carriage return characters")


async def test_send_value_error_maps_to_503(client, mailer):
    await _register(client)
    await _login(client)
    app.state.mailer = _ValueErrorMailer()
    response = await client.post("/api/account/email/verify/request", headers=csrf(client), json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MAIL_UNAVAILABLE"


async def _import_user(client, headers, username: str, email: str) -> None:
    response = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": username, "email": email}]},
        headers=headers,
    )
    assert response.status_code == 200


async def _mark_verified(address: str) -> None:
    async with SessionFactory() as db:
        row = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == address,
                    Identity.type == IdentityType.EMAIL,
                )
            )
        ).scalar_one()
        row.verified = True
        await db.commit()


async def test_invite_prefers_verified_over_older_address(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    await _import_user(client, headers, "multi", "old@example.com")
    await _import_user(client, headers, "multi", "new@example.com")
    await _mark_verified("new@example.com")
    invited = await client.post("/api/admin/users/invite", json={"username": "multi"}, headers=headers)
    assert invited.json() == {"ok": True, "email": "new@example.com"}
    assert mailer.outbox[-1]["to"] == "new@example.com"


async def test_invite_acceptance_does_not_demote_verified(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    await _import_user(client, headers, "keep", "old@example.com")
    await _import_user(client, headers, "keep", "new@example.com")
    await _mark_verified("old@example.com")
    await _mark_verified("new@example.com")
    invited = await client.post("/api/admin/users/invite", json={"username": "keep"}, headers=headers)
    # _verified_email is deterministic: the most recently created verified wins.
    assert invited.json() == {"ok": True, "email": "new@example.com"}
    token = _token_from(mailer.outbox[-1]["text"])
    accepted = await client.post(
        "/api/auth/password/reset/confirm", json={"token": token, "new_password": "keep-user-password"}
    )
    assert accepted.json() == {"ok": True}
    async with SessionFactory() as db:
        rows = (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        assert {row.identifier: row.verified for row in rows} == {"old@example.com": True, "new@example.com": True}
