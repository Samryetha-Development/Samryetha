import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import Settings
from app.common.errors import ApiError
from app.common.models import (
    AssuranceLevel,
    AuditEvent,
    Credential,
    CredentialType,
    Device,
    Identity,
    IdentityType,
    Session,
    User,
    UserStatus,
    utcnow,
)
from app.security.core import (
    hash_password,
    normalize_email,
    normalize_username,
    random_token,
    token_hash,
    verify_password,
)

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


async def audit(db: AsyncSession, event_type: str, **values: object) -> None:
    db.add(AuditEvent(event_type=event_type, **values))


async def register(db: AsyncSession, username: str, email: str, password: str, display_name: str) -> User:
    username_norm = normalize_username(username)
    email_norm = normalize_email(email)
    if not USERNAME_PATTERN.fullmatch(username):
        raise ApiError(422, "INVALID_USERNAME", "Username must be 3–32 letters, numbers, dots, dashes, or underscores")
    if "@" not in email_norm:
        raise ApiError(422, "INVALID_EMAIL", "Enter a valid email address")
    if len(password) < 12 or len(password) > 256:
        raise ApiError(422, "WEAK_PASSWORD", "Password must be at least 12 characters")
    existing = (
        (await db.execute(select(Identity).where(Identity.normalized_identifier.in_([username_norm, email_norm]))))
        .scalars()
        .all()
    )
    if any(item.type == IdentityType.USERNAME for item in existing):
        raise ApiError(409, "USERNAME_TAKEN", "Username is already in use")
    if any(item.type == IdentityType.EMAIL for item in existing):
        raise ApiError(409, "EMAIL_TAKEN", "Email is already in use")
    user = User(display_name=display_name.strip() or username)
    db.add(user)
    await db.flush()
    db.add_all(
        [
            Identity(
                user_id=user.id,
                type=IdentityType.USERNAME,
                identifier=username,
                normalized_identifier=username_norm,
                verified=True,
            ),
            Identity(
                user_id=user.id,
                type=IdentityType.EMAIL,
                identifier=email,
                normalized_identifier=email_norm,
                verified=False,
            ),
            Credential(user_id=user.id, type=CredentialType.PASSWORD, secret_data=hash_password(password)),
        ]
    )
    await audit(db, "user.created", actor_user_id=user.id, target_user_id=user.id)
    await db.commit()
    return user


def device_name(user_agent: str | None) -> str:
    if not user_agent:
        return "Unknown browser"
    browser = (
        "Safari"
        if "Safari" in user_agent and "Chrome" not in user_agent
        else "Chrome"
        if "Chrome" in user_agent
        else "Firefox"
        if "Firefox" in user_agent
        else "Browser"
    )
    os_name = (
        "Windows"
        if "Windows" in user_agent
        else "macOS"
        if "Macintosh" in user_agent
        else "Linux"
        if "Linux" in user_agent
        else "Device"
    )
    return f"{browser} on {os_name}"


async def authenticate(db: AsyncSession, login: str, password: str, ip: str | None) -> User:
    normalized = normalize_email(login) if "@" in login else normalize_username(login)
    identity = (
        await db.execute(select(Identity).where(Identity.normalized_identifier == normalized))
    ).scalar_one_or_none()
    credential = None
    user = None
    if identity:
        user = await db.get(User, identity.user_id)
        credential = (
            await db.execute(
                select(Credential).where(
                    Credential.user_id == identity.user_id, Credential.type == CredentialType.PASSWORD
                )
            )
        ).scalar_one_or_none()
    if (
        not user
        or user.status != UserStatus.ACTIVE
        or not credential
        or not verify_password(credential.secret_data, password)
    ):
        await audit(
            db,
            "authentication.failed",
            target_user_id=identity.user_id if identity else None,
            ip=ip,
            metadata_json={"reason": "invalid_credentials"},
        )
        await db.commit()
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid credentials")
    credential.last_used_at = utcnow()
    await audit(db, "authentication.success", actor_user_id=user.id, target_user_id=user.id, ip=ip)
    return user


async def create_session(
    db: AsyncSession,
    user: User,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
    device_cookie: str | None,
    assurance_level: AssuranceLevel = AssuranceLevel.AAL1,
    authentication_method: str = "PASSWORD",
) -> tuple[str, Session, Device]:
    device = None
    if device_cookie:
        try:
            candidate = await db.get(Device, uuid.UUID(device_cookie))
            if candidate and candidate.user_id == user.id and candidate.revoked_at is None:
                device = candidate
        except ValueError:
            pass
    if not device:
        device = Device(user_id=user.id, name=device_name(user_agent), last_ip=ip, last_user_agent=user_agent)
        db.add(device)
        await db.flush()
        await audit(db, "device.created", actor_user_id=user.id, target_user_id=user.id, device_id=device.id, ip=ip)
    device.last_seen_at = utcnow()
    device.last_ip = ip
    device.last_user_agent = user_agent
    raw_token = random_token()
    auth_session = Session(
        user_id=user.id,
        device_id=device.id,
        token_hash=token_hash(raw_token),
        expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        ip=ip,
        user_agent=user_agent,
        assurance_level=assurance_level,
        assurance_verified_at=utcnow(),
        authentication_method=authentication_method,
    )
    db.add(auth_session)
    await db.flush()
    await audit(
        db,
        "session.created",
        actor_user_id=user.id,
        target_user_id=user.id,
        device_id=device.id,
        session_id=auth_session.id,
        ip=ip,
    )
    await db.commit()
    return raw_token, auth_session, device
