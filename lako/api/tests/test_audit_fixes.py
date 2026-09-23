"""Regression tests for the audit fix batch: token atomicity, mailer failures,
OAuth revocation, latest-address verify, single deliverable address, import
extension, register stripping, invite mismatch, and token-rejection auditing."""

import base64
import hashlib
import re
import smtplib
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from app.authentication.email_service import consume_token, issue_token
from app.authentication.service import register as register_user
from app.common.config import get_settings
from app.common.database import SessionFactory
from app.common.mailer import DummyMailer, SmtpMailer
from app.common.models import AuditEvent, EmailTokenPurpose, Identity, IdentityType, User, utcnow
from app.main import app


@pytest.fixture(autouse=True)
def mailer():
    dummy = DummyMailer()
    app.state.mailer = dummy
    yield dummy


@pytest.fixture
def import_token(monkeypatch):
    monkeypatch.setenv("ADMIN_IMPORT_TOKEN", "audit-token")
    get_settings.cache_clear()
    yield "audit-token"
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


async def _set_verified(normalized_email: str) -> None:
    async with SessionFactory() as db:
        row = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == normalized_email,
                    Identity.type == IdentityType.EMAIL,
                )
            )
        ).scalar_one()
        row.verified = True
        await db.commit()


async def _user_count() -> int:
    async with SessionFactory() as db:
        return (await db.execute(select(func.count()).select_from(User))).scalar_one()


# H5: atomic consume — second claim must fail via the rowcount path.
async def test_consume_token_single_use_and_expiry():
    async with SessionFactory() as db:
        user = await register_user(db, "singleuse", "singleuse@example.com", "a-very-long-password", "Single")
        user_id = user.id
    async with SessionFactory() as db:
        raw = await issue_token(db, user_id, EmailTokenPurpose.VERIFY)
        await db.commit()
    async with SessionFactory() as db:
        assert await consume_token(db, raw, {EmailTokenPurpose.VERIFY}) is not None
        await db.commit()
    async with SessionFactory() as db:
        assert await consume_token(db, raw, {EmailTokenPurpose.VERIFY}) is None
        assert await consume_token(db, "not-a-real-token", {EmailTokenPurpose.VERIFY}) is None
        assert await consume_token(db, raw, {EmailTokenPurpose.RESET}) is None
    async with SessionFactory() as db:
        raw2 = await issue_token(db, user_id, EmailTokenPurpose.RESET)
        await db.flush()
        token = await consume_token(db, raw2, {EmailTokenPurpose.RESET})
        assert token is not None
        token.expires_at = utcnow()
        await db.commit()
    async with SessionFactory() as db:
        assert await consume_token(db, raw2, {EmailTokenPurpose.RESET}) is None


# M17: SMTP constructor failure must surface, not NameError from finally.
def test_deliver_smtp_constructor_error_propagates(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    mailer_impl = SmtpMailer(host="127.0.0.1", port=1, sender="noreply@example.com", timeout_seconds=1)
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = "noreply@example.com"
    message["To"] = "to@example.com"
    message["Subject"] = "hi"
    message.set_content("hi")
    with pytest.raises(OSError):
        mailer_impl._deliver(message)


class _BrokenMailer:
    async def send(self, **kwargs):
        raise smtplib.SMTPException("boom")


# M18: mail outage → identical 503 for known and unknown reset accounts.
async def test_reset_mail_outage_same_503_for_known_and_unknown(client, mailer, monkeypatch):
    await _register(client)
    await _set_verified("plug@example.com")
    app.state.mailer = SmtpMailer(host="127.0.0.1", port=1, sender="noreply@example.com", timeout_seconds=1)

    def _boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(smtplib, "SMTP", _boom)
    known = await client.post("/api/auth/password/reset/request", json={"login": "plug"})
    unknown = await client.post("/api/auth/password/reset/request", json={"login": "ghost"})
    assert known.status_code == unknown.status_code == 503
    assert known.json() == unknown.json()
    assert known.json()["error"]["code"] == "MAIL_UNAVAILABLE"


async def test_verify_request_mail_outage_503(client, mailer):
    await _register(client)
    await _login(client)
    app.state.mailer = _BrokenMailer()
    response = await client.post("/api/account/email/verify/request", headers=csrf(client), json={})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MAIL_UNAVAILABLE"


async def test_change_email_mail_outage_503(client, mailer):
    await _register(client)
    await _login(client)
    app.state.mailer = _BrokenMailer()
    response = await client.post(
        "/api/account/email/change", headers=csrf(client), json={"email": "new@example.com"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MAIL_UNAVAILABLE"


async def test_invite_mail_outage_503(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    created = await client.post(
        "/api/admin/users/import", json={"users": [{"username": "mailed", "email": "mailed@example.com"}]}, headers=headers
    )
    assert created.status_code == 200
    app.state.mailer = _BrokenMailer()
    invited = await client.post("/api/admin/users/invite", json={"username": "mailed"}, headers=headers)
    assert invited.status_code == 503
    assert invited.json()["error"]["code"] == "MAIL_UNAVAILABLE"


# M19: password change revokes OAuth access tokens (no "current" bearer).
def _verifier_and_challenge():
    verifier = "b" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def _mint_access_token(client) -> str:
    verifier, challenge = _verifier_and_challenge()
    response = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "samryetha",
            "redirect_uri": "http://localhost:4000/auth/callback",
            "scope": "openid profile email",
            "state": "s",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 302
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
    exchanged = await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "samryetha",
            "redirect_uri": "http://localhost:4000/auth/callback",
            "code_verifier": verifier,
        },
    )
    assert exchanged.status_code == 200
    return exchanged.json()["access_token"]


async def test_password_change_revokes_oauth_access_token(client, mailer):
    await _register(client)
    await _login(client)
    access = await _mint_access_token(client)
    assert (await client.get("/oauth/userinfo", headers={"authorization": f"Bearer {access}"})).status_code == 200
    changed = await client.post(
        "/api/account/password/change",
        headers=csrf(client),
        json={"current_password": "a-very-long-password", "new_password": "another-long-password"},
    )
    assert changed.json() == {"ok": True}
    denied = await client.get("/oauth/userinfo", headers={"authorization": f"Bearer {access}"})
    assert denied.status_code == 401


# M20: verify re-send targets the latest unverified address.
async def test_verify_request_targets_latest_unverified(client, mailer):
    await _register(client)
    await _login(client)
    await client.post("/api/account/email/change", headers=csrf(client), json={"email": "first@example.com"})
    await client.post("/api/account/email/change", headers=csrf(client), json={"email": "second@example.com"})
    assert len(mailer.outbox) == 2
    assert (await client.post("/api/account/email/verify/request", headers=csrf(client), json={})).json() == {"ok": True}
    assert mailer.outbox[-1]["to"] == "second@example.com"
    token = _token_from(mailer.outbox[-1]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token})).json() == {"ok": True}
    async with SessionFactory() as db:
        rows = (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        assert [(row.identifier, row.verified) for row in rows] == [("second@example.com", True)]


# M21: one account keeps exactly one verified (deliverable) address.
async def test_verify_demotes_other_verified_addresses(client, mailer):
    await _register(client)
    await _login(client)
    first = await client.post("/api/account/email/verify/request", headers=csrf(client), json={})
    assert first.json() == {"ok": True}
    token = _token_from(mailer.outbox[0]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token})).json() == {"ok": True}
    assert (await client.post("/api/account/email/change", headers=csrf(client), json={"email": "b@example.com"})).json() == {
        "ok": True
    }
    token2 = _token_from(mailer.outbox[-1]["text"])
    assert (await client.post("/api/account/email/verify/confirm", json={"token": token2})).json() == {"ok": True}
    async with SessionFactory() as db:
        rows = (
            (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        )
        by_address = {row.identifier: row.verified for row in rows}
        assert by_address == {"plug@example.com": False, "b@example.com": True}


# M22: owner with a missing identity gets extended, not silent exists.
async def test_import_extends_missing_email(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    created = await client.post(
        "/api/admin/users/import", json={"users": [{"username": "half", "email": "half@example.com"}]}, headers=headers
    )
    user_id = created.json()["results"][0]["lako_user_id"]
    rerun = await client.post(
        "/api/admin/users/import", json={"users": [{"username": "half", "email": "extra@example.com"}]}, headers=headers
    )
    payload = rerun.json()
    assert payload["results"][0]["status"] == "extended"
    assert payload["results"][0]["lako_user_id"] == user_id
    assert payload["extended"] == 1
    async with SessionFactory() as db:
        rows = (
            (await db.execute(select(Identity).where(Identity.user_id == uuid.UUID(user_id)))).scalars().all()
        )
        emails = {row.identifier: row.verified for row in rows if row.type == IdentityType.EMAIL}
        assert emails == {"half@example.com": False, "extra@example.com": False}


async def test_import_extends_missing_username(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    created = await client.post(
        "/api/admin/users/import", json={"users": [{"username": "solo", "email": "solo@example.com"}]}, headers=headers
    )
    user_id = created.json()["results"][0]["lako_user_id"]
    rerun = await client.post(
        "/api/admin/users/import", json={"users": [{"username": "alias", "email": "solo@example.com"}]}, headers=headers
    )
    assert rerun.json()["results"][0] == {"username": "alias", "status": "extended", "lako_user_id": user_id}
    async with SessionFactory() as db:
        rows = (
            (await db.execute(select(Identity).where(Identity.user_id == uuid.UUID(user_id)))).scalars().all()
        )
        usernames = sorted(row.identifier for row in rows if row.type == IdentityType.USERNAME)
        assert usernames == ["alias", "solo"]


async def test_import_dry_run_extended_writes_nothing(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    await client.post(
        "/api/admin/users/import", json={"users": [{"username": "dry", "email": "dry@example.com"}]}, headers=headers
    )
    before = await _user_count()
    dry = await client.post(
        "/api/admin/users/import",
        json={"dry_run": True, "users": [{"username": "dry", "email": "newdry@example.com"}]},
        headers=headers,
    )
    assert dry.json()["results"][0]["status"] == "extended"
    assert await _user_count() == before
    async with SessionFactory() as db:
        assert (
            await db.execute(
                select(Identity).where(Identity.normalized_identifier == "newdry@example.com")
            )
        ).scalar_one_or_none() is None


# L1: surrounding whitespace must not fork identity lookups.
async def test_register_strips_whitespace(client, mailer):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "  spaced  ",
            "email": "  spaced@example.com  ",
            "password": "a-very-long-password",
            "display_name": "  Spaced  ",
        },
    )
    assert response.status_code in (200, 201)
    assert response.json()["display_name"] == "Spaced"
    async with SessionFactory() as db:
        row = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == "spaced@example.com",
                    Identity.type == IdentityType.EMAIL,
                )
            )
        ).scalar_one()
        assert row.identifier == "spaced@example.com"
    await _login(client, login="spaced")
    await _set_verified("spaced@example.com")
    assert (await client.post("/api/auth/password/reset/request", json={"login": "  spaced  "})).json() == {"ok": True}
    assert len(mailer.outbox) == 1
    assert mailer.outbox[0]["to"] == "spaced@example.com"


# L2: conflicting user_id + username must not silently pick one side.
async def test_invite_mismatched_user_and_username_422(client, mailer, import_token):
    headers = {"authorization": f"Bearer {import_token}"}
    first = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": "one", "email": "one@example.com"}, {"username": "two", "email": "two@example.com"}]},
        headers=headers,
    )
    ids = [r["lako_user_id"] for r in first.json()["results"]]
    clash = await client.post(
        "/api/admin/users/invite", json={"user_id": ids[0], "username": "two"}, headers=headers
    )
    assert clash.status_code == 422
    agree = await client.post(
        "/api/admin/users/invite", json={"user_id": ids[0], "username": "one"}, headers=headers
    )
    assert agree.json() == {"ok": True, "email": "one@example.com"}


# L3: rejected tokens are audited without recording the token itself.
async def test_token_rejected_is_audited_without_token_plaintext(client, mailer):
    probe = "rejected-token-probe-xyz"
    bad_verify = await client.post("/api/account/email/verify/confirm", json={"token": probe})
    assert bad_verify.status_code == 400
    bad_reset = await client.post(
        "/api/auth/password/reset/confirm", json={"token": probe, "new_password": "another-long-password"}
    )
    assert bad_reset.status_code == 400
    async with SessionFactory() as db:
        events = (
            (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "email.token_rejected")))
            .scalars()
            .all()
        )
        purposes = sorted(event.metadata_json.get("purpose") for event in events)
        assert purposes == ["reset", "verify"]
        for event in events:
            assert event.ip is not None
        assert probe not in repr([(event.event_type, event.metadata_json) for event in events])
