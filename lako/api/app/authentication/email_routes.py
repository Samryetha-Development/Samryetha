"""Self-service email flows: password reset, address verification, change email/password.

Password reset links go only to verified addresses (no enumeration: unknown
accounts get the same 200). Migration invites are the exception — they go to
the address on file and verify it on acceptance.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_import_token
from app.authentication.email_service import consume_token, issue_token
from app.authentication.service import audit
from app.common.config import get_settings
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import (
    Credential,
    CredentialType,
    EmailTokenPurpose,
    Identity,
    IdentityType,
    Session,
    User,
    UserStatus,
    utcnow,
)
from app.security.core import hash_password, normalize_email, normalize_username, verify_password
from app.security.csrf import require_csrf
from app.sessions.dependencies import require_auth

router = APIRouter(tags=["email"])

INVITE_TTL_DAYS = 7


class ResetRequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str = Field(min_length=1, max_length=320)


class ResetConfirmBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class TokenBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: str = Field(min_length=1, max_length=256)


class ChangeEmailBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: str = Field(min_length=1, max_length=320)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: str | None = None
    user_id: str | None = None


def _reset_link(raw: str) -> str:
    return f"{get_settings().app_origin}/reset?token={raw}"


def _verify_link(raw: str) -> str:
    return f"{get_settings().app_origin}/verify?token={raw}"


async def _verified_email(db: AsyncSession, user_id: object) -> Identity | None:
    rows = (
        (await db.execute(select(Identity).where(Identity.user_id == user_id, Identity.type == IdentityType.EMAIL)))
        .scalars()
        .all()
    )
    for row in rows:
        if row.verified:
            return row
    return None


async def _set_password(db: AsyncSession, user_id: object, new_password: str) -> None:
    if len(new_password) < 12 or len(new_password) > 256:
        raise ApiError(422, "WEAK_PASSWORD", "Password must be between 12 and 256 characters")
    credential = (
        await db.execute(
            select(Credential).where(Credential.user_id == user_id, Credential.type == CredentialType.PASSWORD)
        )
    ).scalar_one_or_none()
    if credential is None:
        db.add(Credential(user_id=user_id, type=CredentialType.PASSWORD, secret_data=hash_password(new_password)))
    else:
        credential.secret_data = hash_password(new_password)
        credential.updated_at = utcnow()


async def _revoke_sessions(db: AsyncSession, user_id: object, keep_session_id: object | None = None) -> None:
    stmt = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    if keep_session_id is not None:
        stmt = stmt.where(Session.id != keep_session_id)
    await db.execute(stmt.values(revoked_at=utcnow()))


@router.post("/api/auth/password/reset/request")
async def request_password_reset(body: ResetRequestBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    login = body.login.strip()
    norm = normalize_email(login) if "@" in login else normalize_username(login)
    identity = (
        await db.execute(select(Identity).where(Identity.normalized_identifier == norm))
    ).scalar_one_or_none()
    if identity is not None:
        email = await _verified_email(db, identity.user_id)
        if email is not None:
            raw = await issue_token(db, identity.user_id, EmailTokenPurpose.RESET)
            await request.app.state.mailer.send(
                to=email.identifier,
                subject="Reset your Lako password",
                text=(
                    "Someone requested a password reset for your account.\n\n"
                    f"Set a new password here (valid 1 hour):\n{_reset_link(raw)}\n\n"
                    "If this was not you, ignore this email."
                ),
            )
            await audit(db, "password.reset_requested", target_user_id=identity.user_id)
            await db.commit()
    return {"ok": True}


@router.post("/api/auth/password/reset/confirm")
async def confirm_password_reset(body: ResetConfirmBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    if len(body.new_password) < 12 or len(body.new_password) > 256:
        raise ApiError(422, "WEAK_PASSWORD", "Password must be between 12 and 256 characters")
    token = await consume_token(db, body.token.strip(), {EmailTokenPurpose.RESET, EmailTokenPurpose.INVITE})
    if token is None:
        raise ApiError(400, "INVALID_OR_EXPIRED_TOKEN", "This link is invalid or has expired")
    await _set_password(db, token.user_id, body.new_password)
    await _revoke_sessions(db, token.user_id)
    if token.purpose == EmailTokenPurpose.INVITE:
        email = await _verified_email(db, token.user_id)
        if email is None:
            if token.identity_id is not None:
                bound = await db.get(Identity, token.identity_id)
                if bound is not None and bound.user_id == token.user_id and bound.type == IdentityType.EMAIL:
                    bound.verified = True
                    email = bound
            if email is None:
                first = (
                    await db.execute(
                        select(Identity)
                        .where(Identity.user_id == token.user_id, Identity.type == IdentityType.EMAIL)
                        .order_by(Identity.created_at)
                    )
                ).scalars().first()
                if first is not None:
                    first.verified = True
        await audit(db, "user.invite_accepted", target_user_id=token.user_id)
    else:
        await audit(db, "password.reset_completed", target_user_id=token.user_id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/email/verify/request")
async def request_email_verify(
    request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    require_csrf(request)
    rows = (
        (await db.execute(select(Identity).where(Identity.user_id == ctx.user.id, Identity.type == IdentityType.EMAIL)))
        .scalars()
        .all()
    )
    pending = next((row for row in rows if not row.verified), None)
    if pending is None:
        return {"ok": True, "already_verified": True}
    raw = await issue_token(db, ctx.user.id, EmailTokenPurpose.VERIFY, identity_id=pending.id)
    await request.app.state.mailer.send(
        to=pending.identifier,
        subject="Verify your email",
        text=f"Confirm this address for your Lako account (valid 24 hours):\n{_verify_link(raw)}\n",
    )
    await audit(db, "email.verify_requested", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/email/verify/confirm")
async def confirm_email_verify(body: TokenBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    token = await consume_token(db, body.token.strip(), {EmailTokenPurpose.VERIFY})
    if token is None:
        raise ApiError(400, "INVALID_OR_EXPIRED_TOKEN", "This link is invalid or has expired")
    await _verify_token_identity(db, token)
    await audit(db, "email.verified", target_user_id=token.user_id)
    await db.commit()
    return {"ok": True}


async def _verify_token_identity(db: AsyncSession, token) -> None:
    """Verify the exact email identity the token was issued for.

    Falls back to the oldest unverified address only for legacy tokens issued
    without an identity binding, then drops remaining unverified addresses so
    each account keeps exactly one deliverable address.
    """
    target = None
    if token.identity_id is not None:
        candidate = await db.get(Identity, token.identity_id)
        if (
            candidate is not None
            and candidate.user_id == token.user_id
            and candidate.type == IdentityType.EMAIL
        ):
            target = candidate
    if target is None:
        rows = (
            (
                await db.execute(
                    select(Identity)
                    .where(Identity.user_id == token.user_id, Identity.type == IdentityType.EMAIL)
                    .order_by(Identity.created_at)
                )
            )
            .scalars()
            .all()
        )
        target = next((row for row in rows if not row.verified), rows[0] if rows else None)
    if target is None:
        return
    target.verified = True
    await db.execute(
        delete(Identity).where(
            Identity.user_id == token.user_id,
            Identity.type == IdentityType.EMAIL,
            Identity.id != target.id,
            Identity.verified.is_(False),
        )
    )


@router.post("/api/account/email/change")
async def change_email(body: ChangeEmailBody, request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)) -> dict:
    require_csrf(request)
    email = body.email.strip()
    email_norm = normalize_email(email)
    if "@" not in email_norm:
        raise ApiError(422, "INVALID_EMAIL", "Enter a valid email address")
    taken = (
        await db.execute(select(Identity).where(Identity.normalized_identifier == email_norm))
    ).scalar_one_or_none()
    if taken is not None and taken.user_id != ctx.user.id:
        raise ApiError(409, "EMAIL_TAKEN", "Email is already in use")
    existing = (
        await db.execute(
            select(Identity).where(
                Identity.user_id == ctx.user.id,
                Identity.type == IdentityType.EMAIL,
                Identity.normalized_identifier == email_norm,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = Identity(
            user_id=ctx.user.id, type=IdentityType.EMAIL, identifier=email, normalized_identifier=email_norm
        )
        db.add(existing)
        await db.flush()
    raw = await issue_token(db, ctx.user.id, EmailTokenPurpose.VERIFY, identity_id=existing.id)
    await request.app.state.mailer.send(
        to=existing.identifier,
        subject="Verify your email",
        text=f"Confirm this address for your Lako account (valid 24 hours):\n{_verify_link(raw)}\n",
    )
    await audit(db, "email.changed", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/password/change")
async def change_password(
    body: ChangePasswordBody, request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    require_csrf(request)
    credential = (
        await db.execute(
            select(Credential).where(Credential.user_id == ctx.user.id, Credential.type == CredentialType.PASSWORD)
        )
    ).scalar_one_or_none()
    if credential is None or not verify_password(credential.secret_data, body.current_password):
        raise ApiError(401, "INVALID_CREDENTIALS", "Current password is incorrect")
    await _set_password(db, ctx.user.id, body.new_password)
    await _revoke_sessions(db, ctx.user.id, keep_session_id=ctx.session.id)
    await audit(db, "password.changed", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/admin/users/invite")
async def invite_user(body: InviteBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Send a set-password invite (migration onboarding). Service-token authed."""
    require_import_token(request)
    user = None
    if body.user_id is not None:
        try:
            import uuid as _uuid

            user = await db.get(User, _uuid.UUID(str(body.user_id)))
        except (ValueError, AttributeError):
            user = None
    elif body.username is not None:
        identity = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == normalize_username(body.username.strip()),
                    Identity.type == IdentityType.USERNAME,
                )
            )
        ).scalar_one_or_none()
        user = await db.get(User, identity.user_id) if identity else None
    if user is None or user.status != UserStatus.ACTIVE:
        raise ApiError(404, "USER_NOT_FOUND", "User not found")
    email = (
        (
            await db.execute(
                select(Identity)
                .where(Identity.user_id == user.id, Identity.type == IdentityType.EMAIL)
                .order_by(Identity.created_at)
            )
        )
        .scalars()
        .first()
    )
    if email is None:
        raise ApiError(422, "NO_EMAIL_ON_FILE", "User has no email address on file")
    raw = await issue_token(db, user.id, EmailTokenPurpose.INVITE, identity_id=email.id)
    await request.app.state.mailer.send(
        to=email.identifier,
        subject="Set up your Lako password",
        text=(
            f"Hi {user.display_name or email.identifier},\n\n"
            "An account was created for you. Set your password here "
            f"(valid {INVITE_TTL_DAYS} days):\n{_reset_link(raw)}\n\n"
            "If this was not you, ignore this email."
        ),
    )
    await audit(db, "admin.user_invited", target_user_id=user.id)
    await db.commit()
    return {"ok": True, "email": email.identifier}
