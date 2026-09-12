import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import jwt
import pyotp
from sqlalchemy import select

from app.common.database import SessionFactory
from app.common.models import AssuranceLevel, Credential, CredentialType, Session


def csrf(client) -> dict:
    return {"x-csrf-token": client.cookies["lako_csrf"]}


async def enable_totp(client, logged_in):
    setup = await client.post("/api/account/mfa/totp/setup", headers=csrf(client), json={})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["qr_code"].startswith("data:image/svg+xml;base64,")
    confirmation = await client.post(
        "/api/account/mfa/totp/confirm",
        headers=csrf(client),
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmation.status_code == 200
    return secret, confirmation.json()["recovery_codes"]


async def test_totp_setup_encrypts_secret_and_generates_recovery_codes(client, logged_in):
    secret, codes = await enable_totp(client, logged_in)
    assert len(codes) == 10 and len(set(codes)) == 10
    status = (await client.get("/api/account/mfa")).json()
    assert status == {"totp_enabled": True, "recovery_codes_remaining": 10, "assurance_level": "AAL2"}
    async with SessionFactory() as db:
        credential = (await db.execute(select(Credential).where(Credential.type == CredentialType.TOTP))).scalar_one()
        assert secret not in credential.secret_data


async def test_login_requires_totp_and_creates_aal2_session(client, logged_in):
    secret, _ = await enable_totp(client, logged_in)
    await client.post("/api/sessions/logout", headers=csrf(client))
    response = await client.post("/api/auth/login", json={"login": "avocado", "password": "correct horse battery"})
    assert response.status_code == 202 and response.json() == {"mfa_required": True}
    verified = await client.post("/api/auth/login/mfa", json={"code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200 and verified.json()["assurance_level"] == "AAL2"
    assert (await client.get("/api/auth/me")).json()["assurance_level"] == "AAL2"


async def test_recovery_code_is_single_use(client, logged_in):
    _, codes = await enable_totp(client, logged_in)
    await client.post("/api/sessions/logout", headers=csrf(client))
    await client.post("/api/auth/login", json={"login": "avocado", "password": "correct horse battery"})
    assert (await client.post("/api/auth/login/mfa", json={"code": codes[0]})).status_code == 200
    await client.post("/api/sessions/logout", headers=csrf(client))
    await client.post("/api/auth/login", json={"login": "avocado", "password": "correct horse battery"})
    reused = await client.post("/api/auth/login/mfa", json={"code": codes[0]})
    assert reused.status_code == 401 and reused.json()["error"]["code"] == "INVALID_MFA_CODE"


async def test_step_up_and_aal2_protected_changes(client, logged_in):
    secret, _ = await enable_totp(client, logged_in)
    async with SessionFactory() as db:
        session = (await db.execute(select(Session).order_by(Session.created_at.desc()))).scalars().first()
        session.assurance_level = AssuranceLevel.AAL1
        await db.commit()
    denied = await client.post("/api/account/mfa/recovery-codes/regenerate", headers=csrf(client), json={})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "STEP_UP_REQUIRED"
    elevated = await client.post(
        "/api/account/mfa/step-up", headers=csrf(client), json={"code": pyotp.TOTP(secret).now()}
    )
    assert elevated.status_code == 200 and elevated.json()["assurance_level"] == "AAL2"
    regenerated = await client.post("/api/account/mfa/recovery-codes/regenerate", headers=csrf(client), json={})
    assert regenerated.status_code == 200 and len(regenerated.json()["recovery_codes"]) == 10


async def test_mfa_challenge_is_single_use(client, logged_in):
    secret, _ = await enable_totp(client, logged_in)
    await client.post("/api/sessions/logout", headers=csrf(client))
    await client.post("/api/auth/login", json={"login": "avocado", "password": "correct horse battery"})
    challenge = client.cookies["lako_mfa_challenge"]
    assert (await client.post("/api/auth/login/mfa", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    client.cookies.set("lako_mfa_challenge", challenge, path="/api/auth/login/mfa")
    replay = await client.post("/api/auth/login/mfa", json={"code": pyotp.TOTP(secret).now()})
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "MFA_CHALLENGE_INVALID"


async def test_mfa_challenge_locks_after_five_failed_attempts(client, logged_in):
    secret, _ = await enable_totp(client, logged_in)
    await client.post("/api/sessions/logout", headers=csrf(client))
    await client.post("/api/auth/login", json={"login": "avocado", "password": "correct horse battery"})
    for _ in range(5):
        assert (await client.post("/api/auth/login/mfa", json={"code": "000000"})).status_code == 401
    locked = await client.post("/api/auth/login/mfa", json={"code": pyotp.TOTP(secret).now()})
    assert locked.status_code == 401 and locked.json()["error"]["code"] == "MFA_CHALLENGE_INVALID"


async def test_disable_removes_totp_and_recovery_codes(client, logged_in):
    await enable_totp(client, logged_in)
    response = await client.post("/api/account/mfa/totp/disable", headers=csrf(client), json={})
    assert response.status_code == 200
    async with SessionFactory() as db:
        credentials = (
            (
                await db.execute(
                    select(Credential).where(Credential.type.in_([CredentialType.TOTP, CredentialType.RECOVERY_CODE]))
                )
            )
            .scalars()
            .all()
        )
        assert credentials == []


async def test_oidc_id_token_carries_aal2_after_mfa(client, logged_in):
    await enable_totp(client, logged_in)
    verifier = "z" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = await client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "samryetha",
            "redirect_uri": "http://localhost:4000/auth/callback",
            "scope": "openid profile email",
            "state": "aal2-state",
            "nonce": "aal2-nonce",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
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
    claims = jwt.decode(tokens["id_token"], options={"verify_signature": False})
    assert claims["acr"] == "urn:lako:aal:2"
    assert claims["amr"] == ["password", "totp"]
