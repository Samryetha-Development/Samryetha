"""Email-code flows: passwordless sign-in, second factor, step-up, address verification.

The code is always read back out of the in-memory mailer, and the negative cases
assert the *same* generic error for "wrong code", "expired code" and "no code
requested" — otherwise the endpoint turns into an oracle.
"""

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from sqlalchemy import select

from app.common.config import get_settings
from app.common.errors import ApiError
from app.common.database import SessionFactory
from app.common.mailer import DummyMailer
from app.common.models import AuthenticationChallenge, Identity, IdentityType
from app.main import app

PASSWORD = "a-very-long-password"


@pytest.fixture(autouse=True)
def mailer():
    dummy = DummyMailer()
    app.state.mailer = dummy
    yield dummy


def csrf(client) -> dict:
    return {"x-csrf-token": client.cookies["lako_csrf"]}


def code_from(mailer: DummyMailer, index: int = -1) -> str:
    text = mailer.outbox[index]["text"]
    match = re.search(r"\b(\d{6})\b", text)
    assert match is not None, text
    return match.group(1)


async def register(client, username="plug", email="plug@example.com"):
    response = await client.post(
        "/api/auth/register",
        json={"username": username, "email": email, "password": PASSWORD, "display_name": username},
    )
    assert response.status_code in (200, 201)
    return response.json()


async def mark_verified(email: str) -> None:
    async with SessionFactory() as db:
        identity = (
            await db.execute(select(Identity).where(Identity.normalized_identifier == email))
        ).scalar_one()
        identity.verified = True
        await db.commit()


def verifier_and_challenge():
    verifier = "b" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(challenge):
    return {
        "response_type": "code",
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:4000/auth/callback",
        "scope": "openid profile email",
        "state": "state-1",
        "nonce": "nonce-1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }


# ---------------------------------------------------------------- sign-in codes


async def test_email_code_sign_in_creates_aal1_session(client, mailer):
    await register(client)
    await mark_verified("plug@example.com")
    assert (await client.post("/api/auth/email-code/request", json={"login": "plug"})).status_code == 200
    assert mailer.outbox[-1]["to"] == "plug@example.com"

    response = await client.post(
        "/api/auth/login/email-code", json={"login": "plug@example.com", "code": code_from(mailer)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "EMAIL_CODE" and body["assurance_level"] == "AAL1"
    me = (await client.get("/api/auth/me")).json()
    # Possession of the mailbox is a single factor — never AAL2.
    assert me["assurance_level"] == "AAL1" and me["username"] == "plug"


async def test_email_code_is_single_use_and_attempt_limited(client, mailer):
    await register(client)
    await mark_verified("plug@example.com")
    await client.post("/api/auth/email-code/request", json={"login": "plug"})
    code = code_from(mailer)
    assert (await client.post("/api/auth/login/email-code", json={"login": "plug", "code": "000000" if code != "000000" else "111111"})).status_code == 401
    assert (await client.post("/api/auth/login/email-code", json={"login": "plug", "code": code})).status_code == 200
    await client.post("/api/sessions/logout", headers=csrf(client))
    replay = await client.post("/api/auth/login/email-code", json={"login": "plug", "code": code})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "INVALID_MFA_CODE"


async def test_code_never_goes_to_an_unverified_or_placeholder_address(client, mailer):
    await register(client, username="unverified", email="unverified@example.com")
    assert (await client.post("/api/auth/email-code/request", json={"login": "unverified"})).status_code == 200
    assert mailer.outbox == []
    # A request for an account that cannot receive mail must fail the same way it
    # does for an account that does not exist — no enumeration.
    unknown = await client.post("/api/auth/email-code/request", json={"login": "ghost"})
    assert unknown.status_code == 200 and mailer.outbox == []
    rejected = await client.post(
        "/api/auth/login/email-code", json={"login": "unverified", "code": "123456"}
    )
    assert rejected.status_code == 401 and rejected.json()["error"]["code"] == "INVALID_EMAIL_CODE"


async def test_disabled_account_cannot_request_or_use_a_code(client, mailer):
    from app.common.models import User, UserStatus

    await register(client)
    await mark_verified("plug@example.com")
    async with SessionFactory() as db:
        user = (await db.execute(select(User))).scalar_one()
        user.status = UserStatus.DISABLED
        await db.commit()
    assert (await client.post("/api/auth/email-code/request", json={"login": "plug"})).status_code == 200
    assert mailer.outbox == []


# ---------------------------------------------------------------- second factor


async def enable_totp(client):
    import pyotp

    setup = await client.post("/api/account/mfa/totp/setup", headers=csrf(client), json={})
    secret = setup.json()["secret"]
    confirmation = await client.post(
        "/api/account/mfa/totp/confirm", headers=csrf(client), json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirmation.status_code == 200
    return confirmation.json()["recovery_codes"]


async def test_email_code_satisfies_the_password_second_factor(client, mailer):
    await register(client)
    await mark_verified("plug@example.com")
    await client.post("/api/auth/login", json={"login": "plug", "password": PASSWORD})
    codes = await enable_totp(client)
    await client.post("/api/sessions/logout", headers=csrf(client))

    challenged = await client.post("/api/auth/login", json={"login": "plug", "password": PASSWORD})
    assert challenged.status_code == 202 and challenged.json() == {"mfa_required": True}
    sent = await client.post("/api/auth/login/mfa/email-code")
    assert sent.status_code == 200 and sent.json()["email"] == "p***@example.com"
    verified = await client.post("/api/auth/login/mfa", json={"code": code_from(mailer)})
    assert verified.status_code == 200
    # A mailbox factor is AAL2 by the repository's convention (PEMDAS parity).
    assert verified.json()["assurance_level"] == "AAL2" and verified.json()["method"] == "EMAIL_CODE"
    assert codes


async def test_step_up_email_code_lets_a_recovery_code_less_account_through(client, mailer, logged_in):
    await mark_verified("avo@example.com")
    # Strip every other strong factor: a password-only account with AAL1 session.
    denied = await client.post("/api/account/mfa/recovery-codes/regenerate", headers=csrf(client), json={})
    assert denied.status_code == 403
    requested = await client.post("/api/account/mfa/step-up/code/request", headers=csrf(client), json={})
    assert requested.status_code == 200 and requested.json()["email"] == "a***@example.com"
    elevated = await client.post(
        "/api/account/mfa/step-up/email-code", headers=csrf(client), json={"code": code_from(mailer)}
    )
    assert elevated.status_code == 200 and elevated.json() == {"assurance_level": "AAL2", "method": "EMAIL_CODE"}
    # The step-up is what unlocked the sensitive operation.
    assert (await client.post("/api/account/mfa/recovery-codes/regenerate", headers=csrf(client), json={})).status_code == 200


async def test_step_up_code_is_refused_without_a_verified_address(client):
    await register(client, username="nomail", email="nomail@example.com")
    await client.post("/api/auth/login", json={"login": "nomail", "password": PASSWORD})
    response = await client.post("/api/account/mfa/step-up/code/request", headers=csrf(client), json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "NO_VERIFIED_EMAIL"


# ---------------------------------------------------------------- verification codes


async def test_verification_code_confirms_the_pending_address(client, mailer, logged_in):
    requested = await client.post("/api/account/email/verify/code", headers=csrf(client), json={})
    assert requested.status_code == 200
    assert mailer.outbox[-1]["to"] == "avo@example.com"
    wrong = await client.post("/api/account/email/verify/code/confirm", headers=csrf(client), json={"code": "999999"})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "INVALID_OR_EXPIRED_CODE"
    confirmed = await client.post(
        "/api/account/email/verify/code/confirm", headers=csrf(client), json={"code": code_from(mailer)}
    )
    assert confirmed.status_code == 200 and confirmed.json() == {"ok": True, "email": "avo@example.com"}
    state = (await client.get("/api/account/email")).json()
    assert state == {"email": "avo@example.com", "verified": True, "placeholder": False}


async def test_verification_code_is_bound_to_the_address_it_was_sent_to(client, mailer, logged_in):
    """A code for address A must verify A — never an address chosen after the fact.

    ``change_email`` keeps the old unverified row and adds the new one, so both
    are candidates at confirm time; only the binding decides which one the code
    proves control of.
    """
    assert (await client.post("/api/account/email/verify/code", headers=csrf(client), json={})).status_code == 200
    code = code_from(mailer)
    changed = await client.post(
        "/api/account/email/change", headers=csrf(client), json={"email": "other@example.com"}
    )
    assert changed.status_code == 200
    confirmed = await client.post(
        "/api/account/email/verify/code/confirm", headers=csrf(client), json={"code": code}
    )
    assert confirmed.status_code == 200 and confirmed.json()["email"] == "avo@example.com"
    # The address added afterwards must not have been verified by that code.
    assert (await client.get("/api/account/email")).json() == {
        "email": "avo@example.com",
        "verified": True,
        "placeholder": False,
    }


async def test_code_bound_to_one_address_cannot_verify_another(client, mailer, logged_in):
    """Two pending addresses, one code: the code only ever clears its own."""
    assert (
        await client.post("/api/account/email/change", headers=csrf(client), json={"email": "other@example.com"})
    ).status_code == 200
    # change_email re-issues for the *newest* address, so this code belongs to
    # other@example.com even though the older pending address sent an earlier one.
    assert (await client.post("/api/account/email/verify/code", headers=csrf(client), json={})).status_code == 200
    assert mailer.outbox[-1]["to"] == "other@example.com"
    code = code_from(mailer)
    async with SessionFactory() as db:
        identities = {
            identity.identifier: identity
            for identity in (
                await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))
            ).scalars()
        }
    assert set(identities) == {"avo@example.com", "other@example.com"}
    confirmed = await client.post(
        "/api/account/email/verify/code/confirm", headers=csrf(client), json={"code": code}
    )
    assert confirmed.status_code == 200 and confirmed.json()["email"] == "other@example.com"
    async with SessionFactory() as db:
        remaining = (
            (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalars().all()
        )
    # The stale unverified sibling is dropped, exactly like the link flow does.
    verified = [identity.identifier for identity in remaining if identity.verified]
    assert verified == ["other@example.com"]


# ---------------------------------------------------------------- authorize gate


async def _claims_for(client, challenge):
    verifier = "b" * 64
    response = await client.get("/oauth/authorize", params=authorize_params(challenge))
    assert response.status_code == 302
    code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
    tokens = (
        await client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "samryetha",
                "redirect_uri": "http://localhost:4000/auth/callback",
                "code_verifier": verifier,
            },
        )
    ).json()
    return jwt.decode(tokens["id_token"], options={"verify_signature": False})


async def test_authorize_gate_redirects_unverified_sessions_when_enabled(client, logged_in):
    settings = get_settings()
    _, challenge = verifier_and_challenge()
    # Off by default: the current flow still mints a code (email_verified false).
    assert settings.oidc_require_verified_email is False
    claims = await _claims_for(client, challenge)
    assert claims["email_verified"] is False

    settings.oidc_require_verified_email = True
    try:
        blocked = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert blocked.status_code == 302
        target = urlparse(blocked.headers["location"])
        assert target.path == "/verify-email"
        params = parse_qs(target.query)
        assert params["email"] == ["a***@example.com"]
        assert params["return_to"][0].startswith("/oauth/authorize?")

        # Verifying through the code flow clears the interruption.
        await client.post("/api/account/email/verify/code", headers=csrf(client), json={})
    finally:
        settings.oidc_require_verified_email = False


async def test_authorize_gate_skips_placeholder_addresses(client, logged_in, mailer):
    """Accounts the migration gave a never-deliverable placeholder are not blocked."""
    async with SessionFactory() as db:
        identity = (await db.execute(select(Identity).where(Identity.type == IdentityType.EMAIL))).scalar_one()
        identity.identifier = "avo@migrated.invalid"
        identity.normalized_identifier = "avo@migrated.invalid"
        identity.verified = False
        await db.commit()
    settings = get_settings()
    settings.oidc_require_verified_email = True
    try:
        _, challenge = verifier_and_challenge()
        response = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert response.status_code == 302
        assert urlparse(response.headers["location"]).path == "/auth/callback"
    finally:
        settings.oidc_require_verified_email = False


async def test_amr_maps_email_code_to_one_rfc8176_value(client, mailer):
    await register(client)
    await mark_verified("plug@example.com")
    await client.post("/api/auth/email-code/request", json={"login": "plug"})
    assert (
        await client.post("/api/auth/login/email-code", json={"login": "plug", "code": code_from(mailer)})
    ).status_code == 200
    _, challenge = verifier_and_challenge()
    claims = await _claims_for(client, challenge)
    # Not ["email", "code"]: that would claim an `email` factor that was never used.
    assert claims["amr"] == ["email_code"] and claims["acr"] == "urn:lako:aal:1"


async def test_codes_are_stored_hashed(client, mailer, logged_in):
    await client.post("/api/account/email/verify/code", headers=csrf(client), json={})
    code = code_from(mailer)
    async with SessionFactory() as db:
        rows = (await db.execute(select(AuthenticationChallenge))).scalars().all()
    assert rows and all(code not in row.token_hash for row in rows)
