"""Passkey / WebAuthn: options, registration, usernameless sign-in, step-up policy.

A synthetic ES256 authenticator (cbor2 + cryptography) produces real
attestation/assertion objects, so the full server verification path is exercised.
"""

from __future__ import annotations

import hashlib
import json
import os

import cbor2
import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from webauthn.helpers import bytes_to_base64url

from app.common.config import get_settings
from app.common.database import SessionFactory
from app.common.models import AuditEvent
from app.main import app


def csrf(client: httpx.AsyncClient) -> dict:
    return {"x-csrf-token": client.cookies["lako_csrf"]}


def _b64(data: bytes) -> str:
    return bytes_to_base64url(data)


def _authenticator() -> tuple[object, bytes, dict]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    numbers = private_key.public_key().public_numbers()
    cose = cbor2.dumps(
        {
            1: 2,  # EC2
            3: -7,  # ES256
            -1: 1,  # P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )
    credential_id = os.urandom(32)
    return private_key, credential_id, {"cose": cose, "credential_id": credential_id}


def _registration_credential(private_key, credential_id: bytes, cose: bytes, rp_id: str, origin: str, challenge: str) -> dict:
    auth_data = (
        hashlib.sha256(rp_id.encode()).digest()
        + bytes([0x01 | 0x40])  # UP | AT
        + (0).to_bytes(4, "big")
        + b"\x00" * 16
        + len(credential_id).to_bytes(2, "big")
        + credential_id
        + cose
    )
    client_data = json.dumps(
        {"type": "webauthn.create", "challenge": challenge, "origin": origin, "crossOrigin": False}
    ).encode()
    attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
    return {
        "id": _b64(credential_id),
        "rawId": _b64(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64(client_data),
            "attestationObject": _b64(attestation),
            "transports": ["internal"],
        },
    }


def _assertion(private_key, credential_id: bytes, rp_id: str, origin: str, challenge: str, sign_count: int = 1) -> dict:
    auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([0x01]) + sign_count.to_bytes(4, "big")
    client_data = json.dumps(
        {"type": "webauthn.get", "challenge": challenge, "origin": origin, "crossOrigin": False}
    ).encode()
    signature = private_key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    return {
        "id": _b64(credential_id),
        "rawId": _b64(credential_id),
        "type": "public-key",
        "response": {
            "clientDataJSON": _b64(client_data),
            "authenticatorData": _b64(auth_data),
            "signature": _b64(signature),
            "userHandle": None,
        },
    }


async def _add_passkey(client: httpx.AsyncClient, name: str = "Test key") -> dict:
    settings = get_settings()
    options = await client.post("/api/account/webauthn/register/options", headers=csrf(client))
    assert options.status_code == 200, options.text
    private_key, credential_id, material = _authenticator()
    credential = _registration_credential(
        private_key, credential_id, material["cose"], settings.rp_id, settings.app_origin, options.json()["challenge"]
    )
    verify = await client.post(
        "/api/account/webauthn/register/verify",
        headers=csrf(client),
        json={"credential": credential, "name": name},
    )
    return {"response": verify, "private_key": private_key, "credential_id": credential_id}


def _fresh_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000", follow_redirects=False
    )


async def test_first_passkey_issues_recovery_codes_and_gates_further_changes(client: httpx.AsyncClient, logged_in):
    # First passkey is allowed from an AAL1 (password) session and returns the
    # one-time recovery file.
    added = await _add_passkey(client, "First key")
    assert added["response"].status_code == 200, added["response"].text
    codes = added["response"].json()["recoveryCodes"]
    assert codes and len(codes) == 10

    # Second enrollment now requires recent AAL2.
    blocked = await client.post("/api/account/webauthn/register/options", headers=csrf(client))
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "STEP_UP_REQUIRED"

    # A recovery code steps the password session up to AAL2…
    stepped = await client.post("/api/account/mfa/step-up", headers=csrf(client), json={"code": codes[0]})
    assert stepped.status_code == 200, stepped.text
    assert stepped.json()["method"] == "RECOVERY_CODE"

    # …after which adding another passkey and removing the first both work.
    second = await _add_passkey(client, "Second key")
    assert second["response"].status_code == 200, second["response"].text
    assert second["response"].json()["recoveryCodes"] is None

    listed = (await client.get("/api/account/webauthn/credentials")).json()["items"]
    assert len(listed) == 2
    removed = await client.delete(f"/api/account/webauthn/credentials/{listed[0]['id']}", headers=csrf(client))
    assert removed.json() == {"ok": True}


async def test_delete_requires_recent_aal2(client: httpx.AsyncClient, logged_in):
    added = await _add_passkey(client)
    assert added["response"].status_code == 200
    credential_pk = (await client.get("/api/account/webauthn/credentials")).json()["items"][0]["id"]
    denied = await client.delete(f"/api/account/webauthn/credentials/{credential_pk}", headers=csrf(client))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "STEP_UP_REQUIRED"


async def test_usernameless_sign_in_creates_aal2_session(client: httpx.AsyncClient, logged_in):
    settings = get_settings()
    added = await _add_passkey(client)
    assert added["response"].status_code == 200

    async with _fresh_client() as fresh:
        options = await fresh.post("/api/auth/webauthn/options")
        assert options.status_code == 200, options.text
        assertion = _assertion(
            added["private_key"], added["credential_id"], settings.rp_id, settings.app_origin, options.json()["challenge"], 1
        )
        logged = await fresh.post("/api/auth/webauthn/verify", json={"credential": assertion})
        assert logged.status_code == 200, logged.text
        assert logged.json()["assurance_level"] == "AAL2"
        me = await fresh.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "avocado"

        # The AAL2 passkey session can remove the credential directly.
        credential_pk = (await fresh.get("/api/account/webauthn/credentials")).json()["items"][0]["id"]
        removed = await fresh.delete(f"/api/account/webauthn/credentials/{credential_pk}", headers=csrf(fresh))
        assert removed.json() == {"ok": True}


async def test_failed_passkey_sign_in_is_audited(client: httpx.AsyncClient, logged_in):
    settings = get_settings()
    await _add_passkey(client)

    async with _fresh_client() as anon:
        options = await anon.post("/api/auth/webauthn/options")
        challenge = options.json()["challenge"]
        # An unregistered credential id: signature is valid but unknown.
        private_key, credential_id, _material = _authenticator()
        assertion = _assertion(private_key, credential_id, settings.rp_id, settings.app_origin, challenge, 1)
        response = await anon.post("/api/auth/webauthn/verify", json={"credential": assertion})
        assert response.status_code == 401

    async with SessionFactory() as db:
        rows = (
            (await db.execute(select(AuditEvent).where(AuditEvent.event_type == "authentication.failed")))
            .scalars()
            .all()
        )
    assert any(
        (row.metadata_json or {}).get("method") == "PASSKEY"
        and (row.metadata_json or {}).get("reason") == "unknown_credential"
        for row in rows
    )


async def test_register_options_requires_auth_and_csrf(client: httpx.AsyncClient, logged_in):
    without_csrf = await client.post("/api/account/webauthn/register/options")
    assert without_csrf.status_code == 403

    async with _fresh_client() as anonymous:
        assert (await anonymous.post("/api/auth/webauthn/options")).status_code == 200
        assert (await anonymous.post("/api/auth/webauthn/verify", json={"credential": {}})).status_code == 401


async def test_bad_attestation_rejected(client: httpx.AsyncClient, logged_in):
    options = await client.post("/api/account/webauthn/register/options", headers=csrf(client))
    challenge = options.json()["challenge"]
    settings = get_settings()
    private_key, credential_id, material = _authenticator()
    credential = _registration_credential(
        private_key, credential_id, material["cose"], settings.rp_id, settings.app_origin, challenge
    )
    credential["response"]["clientDataJSON"] = _b64(
        json.dumps({"type": "webauthn.create", "challenge": "AAAA", "origin": settings.app_origin}).encode()
    )
    rejected = await client.post(
        "/api/account/webauthn/register/verify", headers=csrf(client), json={"credential": credential}
    )
    assert rejected.status_code == 400
