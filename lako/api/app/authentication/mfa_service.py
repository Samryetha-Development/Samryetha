import base64
import hmac
import io
import secrets
from datetime import timedelta

import pyotp
import segno
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import audit
from app.common.errors import ApiError
from app.common.models import (
    AssuranceLevel,
    AuthenticationChallenge,
    Credential,
    CredentialType,
    Identity,
    IdentityType,
    Session,
    User,
    utcnow,
)
from app.security.core import decrypt_credential, encrypt_credential, random_token, token_hash

MFA_CHALLENGE_COOKIE = "lako_mfa_challenge"
CHALLENGE_TTL_SECONDS = 300
RECOVERY_CODE_COUNT = 10


async def active_totp(db: AsyncSession, user_id) -> Credential | None:
    return (
        await db.execute(
            select(Credential).where(
                Credential.user_id == user_id,
                Credential.type == CredentialType.TOTP,
                Credential.enabled_at.is_not(None),
            )
        )
    ).scalar_one_or_none()


def recovery_code() -> str:
    raw = secrets.token_hex(8).upper()
    return "-".join(raw[index : index + 4] for index in range(0, 16, 4))


def normalize_recovery_code(value: str) -> str:
    return value.replace("-", "").replace(" ", "").upper()


async def replace_recovery_codes(db: AsyncSession, user_id) -> list[str]:
    await db.execute(
        delete(Credential).where(
            Credential.user_id == user_id,
            Credential.type == CredentialType.RECOVERY_CODE,
        )
    )
    raw_codes = [recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    db.add_all(
        Credential(
            user_id=user_id,
            type=CredentialType.RECOVERY_CODE,
            secret_data=token_hash(normalize_recovery_code(code)),
            enabled_at=utcnow(),
        )
        for code in raw_codes
    )
    return raw_codes


async def begin_totp_setup(db: AsyncSession, user: User) -> dict:
    if await active_totp(db, user.id):
        raise ApiError(409, "MFA_ALREADY_ENABLED", "Two-factor authentication is already enabled")
    await db.execute(
        delete(Credential).where(
            Credential.user_id == user.id,
            Credential.type == CredentialType.TOTP,
            Credential.enabled_at.is_(None),
        )
    )
    identity = (
        await db.execute(select(Identity).where(Identity.user_id == user.id, Identity.type == IdentityType.EMAIL))
    ).scalar_one_or_none()
    secret = pyotp.random_base32()
    db.add(Credential(user_id=user.id, type=CredentialType.TOTP, secret_data=encrypt_credential(secret)))
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=identity.identifier if identity else str(user.id), issuer_name="Lako"
    )
    qr = segno.make(uri, error="m")
    stream = io.BytesIO()
    qr.save(stream, kind="svg", scale=5, border=2)
    qr_data_uri = "data:image/svg+xml;base64," + base64.b64encode(stream.getvalue()).decode()
    await audit(db, "mfa.totp.setup_started", actor_user_id=user.id, target_user_id=user.id)
    await db.commit()
    return {"secret": secret, "provisioning_uri": uri, "qr_code": qr_data_uri}


async def verify_second_factor(db: AsyncSession, user_id, code: str) -> str:
    totp = await active_totp(db, user_id)
    if not totp:
        raise ApiError(409, "MFA_NOT_ENABLED", "Two-factor authentication is not enabled")
    compact = code.strip().replace(" ", "")
    if compact.isdigit() and pyotp.TOTP(decrypt_credential(totp.secret_data)).verify(compact, valid_window=1):
        totp.last_used_at = utcnow()
        return "TOTP"
    normalized = normalize_recovery_code(compact)
    recovery_credentials = (
        await db.execute(
            select(Credential).where(
                Credential.user_id == user_id,
                Credential.type == CredentialType.RECOVERY_CODE,
                Credential.last_used_at.is_(None),
            )
        )
    ).scalars()
    supplied_hash = token_hash(normalized)
    for recovery in recovery_credentials:
        if hmac.compare_digest(recovery.secret_data, supplied_hash):
            recovery.last_used_at = utcnow()
            return "RECOVERY_CODE"
    raise ApiError(401, "INVALID_MFA_CODE", "Invalid verification code")


async def confirm_totp_setup(db: AsyncSession, user: User, session: Session, code: str) -> list[str]:
    credential = (
        await db.execute(
            select(Credential).where(
                Credential.user_id == user.id,
                Credential.type == CredentialType.TOTP,
                Credential.enabled_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if not credential or not pyotp.TOTP(decrypt_credential(credential.secret_data)).verify(
        code.strip(), valid_window=1
    ):
        await audit(db, "mfa.totp.setup_failed", actor_user_id=user.id, target_user_id=user.id)
        await db.commit()
        raise ApiError(401, "INVALID_MFA_CODE", "Invalid verification code")
    credential.enabled_at = utcnow()
    credential.last_used_at = utcnow()
    session.assurance_level = AssuranceLevel.AAL2
    session.assurance_verified_at = utcnow()
    session.authentication_method = "PASSWORD_TOTP"
    codes = await replace_recovery_codes(db, user.id)
    await audit(db, "mfa.totp.enabled", actor_user_id=user.id, target_user_id=user.id, session_id=session.id)
    await db.commit()
    return codes


async def create_login_challenge(db: AsyncSession, user: User) -> str:
    raw = random_token()
    db.add(
        AuthenticationChallenge(
            token_hash=token_hash(raw),
            user_id=user.id,
            purpose="LOGIN_MFA",
            expires_at=utcnow() + timedelta(seconds=CHALLENGE_TTL_SECONDS),
        )
    )
    await audit(db, "mfa.login.challenge_created", actor_user_id=user.id, target_user_id=user.id)
    await db.commit()
    return raw


async def consume_login_challenge(db: AsyncSession, raw: str, code: str) -> tuple[User, str]:
    challenge = (
        await db.execute(select(AuthenticationChallenge).where(AuthenticationChallenge.token_hash == token_hash(raw)))
    ).scalar_one_or_none()
    now = utcnow()
    if (
        not challenge
        or challenge.purpose != "LOGIN_MFA"
        or challenge.used_at
        or challenge.attempt_count >= 5
        or challenge.expires_at.replace(tzinfo=challenge.expires_at.tzinfo or now.tzinfo) <= now
    ):
        raise ApiError(401, "MFA_CHALLENGE_INVALID", "Verification challenge is invalid or expired")
    try:
        method = await verify_second_factor(db, challenge.user_id, code)
    except ApiError:
        challenge.attempt_count += 1
        if challenge.attempt_count >= 5:
            challenge.used_at = now
        await audit(db, "mfa.login.failed", target_user_id=challenge.user_id)
        await db.commit()
        raise
    challenge.used_at = now
    user = await db.get(User, challenge.user_id)
    await audit(
        db, "mfa.login.success", actor_user_id=user.id, target_user_id=user.id, metadata_json={"method": method}
    )
    return user, method
