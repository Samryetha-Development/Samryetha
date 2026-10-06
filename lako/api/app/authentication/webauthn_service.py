"""Passkey / WebAuthn registration and authentication.

Uses py_webauthn for the crypto. The one-time challenge is carried in a
short-lived Fernet-encrypted, HttpOnly cookie (same pattern as the encrypted
credential store) instead of a DB row, so no extra table is needed.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.authentication.mfa_service import replace_recovery_codes
from app.authentication.service import audit
from app.common.config import Settings
from app.common.errors import ApiError
from app.common.models import (
    Credential,
    CredentialType,
    Identity,
    IdentityType,
    User,
    UserStatus,
    WebAuthnCredential,
    utcnow,
)
from app.security.core import credential_cipher, encrypt_credential

WEBAUTHN_COOKIE = "lako_webauthn"
CHALLENGE_TTL_SECONDS = 300
REGISTER_PURPOSE = "webauthn.register"
LOGIN_PURPOSE = "webauthn.login"
MAX_CREDENTIALS_PER_USER = 20


def _seal(payload: dict[str, Any]) -> str:
    return encrypt_credential(json.dumps(payload))


def _open(value: str, expected_purpose: str, *, user_id: str | None = None) -> bytes:
    try:
        payload = json.loads(credential_cipher().decrypt(value.encode(), ttl=CHALLENGE_TTL_SECONDS))
    except (InvalidToken, ValueError, TypeError):  # invalid ciphertext, expired, wrong key
        raise ApiError(400, "WEBAUTHN_CHALLENGE_INVALID", "This passkey request expired — try again")
    if not isinstance(payload, dict) or payload.get("p") != expected_purpose:
        raise ApiError(400, "WEBAUTHN_CHALLENGE_INVALID", "This passkey request expired — try again")
    if user_id is not None and payload.get("u") != user_id:
        raise ApiError(400, "WEBAUTHN_CHALLENGE_INVALID", "This passkey request expired — try again")
    try:
        return base64url_to_bytes(payload["c"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "WEBAUTHN_CHALLENGE_INVALID", "This passkey request expired — try again")


async def _username(db: AsyncSession, user_id) -> str:
    row = (
        await db.execute(
            select(Identity).where(Identity.user_id == user_id, Identity.type == IdentityType.USERNAME)
        )
    ).scalars().first()
    return row.identifier if row else str(user_id)


def _transports(raw: object) -> list[AuthenticatorTransport]:
    if not isinstance(raw, list):
        return []
    values = []
    for item in raw:
        try:
            values.append(AuthenticatorTransport(item))
        except ValueError:
            continue
    return values


async def list_credentials(db: AsyncSession, user_id) -> list[WebAuthnCredential]:
    rows = (
        await db.execute(
            select(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user_id)
            .order_by(WebAuthnCredential.created_at)
        )
    ).scalars().all()
    return list(rows)


async def delete_credential(db: AsyncSession, user_id, credential_pk: str) -> None:
    import uuid

    try:
        pk = uuid.UUID(credential_pk)
    except ValueError:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "Passkey not found")
    row = (
        await db.execute(
            select(WebAuthnCredential).where(
                WebAuthnCredential.id == pk, WebAuthnCredential.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "CREDENTIAL_NOT_FOUND", "Passkey not found")
    await db.delete(row)
    await audit(db, "webauthn.removed", actor_user_id=user_id, target_user_id=user_id)
    await db.commit()


async def begin_registration(db: AsyncSession, user: User, settings: Settings) -> tuple[str, str]:
    existing = await list_credentials(db, user.id)
    if len(existing) >= MAX_CREDENTIALS_PER_USER:
        raise ApiError(422, "TOO_MANY_CREDENTIALS", "Too many passkeys on this account")
    options = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.bytes,
        user_name=await _username(db, user.id),
        user_display_name=user.display_name,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=row.credential_id, transports=_transports(row.transports.split(",")))
            for row in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    sealed = _seal({"p": REGISTER_PURPOSE, "u": str(user.id), "c": bytes_to_base64url(options.challenge)})
    return options_to_json(options), sealed


async def _has_recovery_codes(db: AsyncSession, user_id) -> bool:
    count = (
        await db.execute(
            select(func.count())
            .select_from(Credential)
            .where(Credential.user_id == user_id, Credential.type == CredentialType.RECOVERY_CODE)
        )
    ).scalar() or 0
    return count > 0


async def finish_registration(
    db: AsyncSession, user: User, settings: Settings, sealed: str, credential: object, name: str | None
) -> tuple[WebAuthnCredential, list[str] | None]:
    challenge = _open(sealed, REGISTER_PURPOSE, user_id=str(user.id))
    existing_count = (
        await db.execute(
            select(func.count())
            .select_from(WebAuthnCredential)
            .where(WebAuthnCredential.user_id == user.id)
        )
    ).scalar() or 0
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.webauthn_origin_list,
            require_user_verification=False,
        )
    except InvalidRegistrationResponse:
        raise ApiError(400, "WEBAUTHN_REGISTRATION_FAILED", "Could not verify this passkey")

    duplicate = (
        await db.execute(
            select(WebAuthnCredential).where(WebAuthnCredential.credential_id == verified.credential_id)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ApiError(409, "WEBAUTHN_ALREADY_REGISTERED", "This passkey is already registered")

    transports = ""
    if isinstance(credential, dict):
        raw_transports = credential.get("response", {}).get("transports") if isinstance(credential.get("response"), dict) else credential.get("transports")
        if isinstance(raw_transports, list):
            transports = ",".join(str(item) for item in raw_transports)
    row = WebAuthnCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        name=(name or "Passkey").strip()[:120] or "Passkey",
        aaguid=str(verified.aaguid) if verified.aaguid else None,
        backed_up=bool(verified.credential_backed_up),
    )
    db.add(row)
    await audit(db, "webauthn.registered", actor_user_id=user.id, target_user_id=user.id)
    await db.flush()
    # Issue the GitHub-style recovery file on the first passkey, so a lost
    # device can still be recovered. Skip if the account already has codes
    # (e.g. TOTP enrollment issued them).
    recovery_codes: list[str] | None = None
    if existing_count == 0 and not await _has_recovery_codes(db, user.id):
        recovery_codes = await replace_recovery_codes(db, user.id)
    await db.commit()
    return row, recovery_codes


def begin_authentication(settings: Settings) -> tuple[str, str]:
    # Usernameless (discoverable credential) by design: no allowCredentials, so
    # the options reveal nothing about which accounts exist.
    options = generate_authentication_options(
        rp_id=settings.rp_id,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    sealed = _seal({"p": LOGIN_PURPOSE, "c": bytes_to_base64url(options.challenge)})
    return options_to_json(options), sealed


async def _fail_passkey(db: AsyncSession, user_id, ip: str | None, reason: str) -> None:
    """Record a failed passkey sign-in (never the credential id) and persist it
    before the request unwinds — the generic 401 returned to the client does not
    reveal which check failed."""
    await audit(
        db,
        "authentication.failed",
        target_user_id=user_id,
        ip=ip,
        metadata_json={"method": "PASSKEY", "reason": reason},
    )
    await db.commit()


async def finish_authentication(
    db: AsyncSession, settings: Settings, sealed: str | None, credential: dict, ip: str | None = None
) -> User:
    if not sealed:
        await _fail_passkey(db, None, ip, "missing_challenge")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    try:
        challenge = _open(sealed, LOGIN_PURPOSE)
    except ApiError:
        await _fail_passkey(db, None, ip, "challenge_invalid")
        raise

    raw_id = credential.get("rawId") or credential.get("id")
    if not isinstance(raw_id, str):
        await _fail_passkey(db, None, ip, "malformed_credential")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    try:
        credential_id = base64url_to_bytes(raw_id)
    except (TypeError, ValueError):
        await _fail_passkey(db, None, ip, "malformed_credential")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")

    row = (
        await db.execute(select(WebAuthnCredential).where(WebAuthnCredential.credential_id == credential_id))
    ).scalar_one_or_none()
    if row is None:
        await _fail_passkey(db, None, ip, "unknown_credential")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    user = await db.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        await _fail_passkey(db, row.user_id, ip, "account_disabled")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")

    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.rp_id,
            expected_origin=settings.webauthn_origin_list,
            credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count,
            require_user_verification=False,
        )
    except InvalidAuthenticationResponse:
        await _fail_passkey(db, row.user_id, ip, "signature_invalid")
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")

    row.sign_count = verified.new_sign_count
    row.last_used_at = utcnow()
    await audit(
        db,
        "authentication.success",
        actor_user_id=user.id,
        target_user_id=user.id,
        ip=ip,
        metadata_json={"method": "PASSKEY"},
    )
    return user
