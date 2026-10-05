"""Self-service email flows: password reset, address verification, change email/password.

Password reset links go only to verified addresses (no enumeration: unknown
accounts get the same 200). Migration invites are the exception — they go to
the address on file and verify it on acceptance.

Rate-limit keys are IP-only (see `_ip_key`): clients behind the same NAT
share one quota — a known, accepted tradeoff. Account-scoped keys are
deliberately not used because per-account quota responses would themselves
let callers enumerate accounts.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_import_token
from app.authentication.email_codes import (
    PURPOSE_VERIFY,
    consume_code,
    issue_code,
    render_code_email,
)
from app.authentication.email_service import (
    PLACEHOLDER_EMAIL_DOMAIN,
    consume_token,
    deliverable_email,
    issue_token,
    pending_email,
    unverified_emails,
)
from app.authentication.service import audit
from app.common.client_ip import client_ip
from app.common.config import get_settings
from app.common.database import get_db
from app.common.email_templates import render_email
from app.common.errors import ApiError
from app.common.mailer import MAIL_SEND_ERRORS, ensure_available, send_or_503
from app.common.models import (
    AccessToken,
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
from app.common.ratelimit import check as check_rate_limit
from app.security.core import hash_password, normalize_email, normalize_username, verify_password
from app.security.csrf import require_csrf
from app.sessions.dependencies import require_auth

router = APIRouter(tags=["email"])

INVITE_TTL_DAYS = 7

# Kept as module-level aliases: `MAIL_SEND_ERRORS` / `_send_or_503` are part of
# this module's historical surface (and its tests' monkeypatch points).
_send_or_503 = send_or_503


def _ip_key(request: Request, scope: str) -> str:
    host = client_ip(request) or "unknown"
    return f"{scope}:{host}"


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


class CodeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str = Field(min_length=1, max_length=12)


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


def _has_control_chars(value: str) -> bool:
    """True for C0 controls (incl. CR/LF) or DEL — header-injection material."""
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


async def _verified_email(db: AsyncSession, user_id: object) -> Identity | None:
    """The account's deliverable address (see ``email_service.deliverable_email``)."""
    return await deliverable_email(db, user_id)


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
    # Bearer (OAuth) access tokens carry no "current session" context, so a
    # password change revokes all of them even when the current cookie session
    # is kept for UX continuity.
    await db.execute(
        update(AccessToken)
        .where(AccessToken.user_id == user_id, AccessToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


@router.post("/api/auth/password/reset/request")
async def request_password_reset(body: ResetRequestBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await check_rate_limit(_ip_key(request, "password-reset-request"), 5)
    login = body.login.strip()
    norm = normalize_email(login) if "@" in login else normalize_username(login)
    identity = (
        await db.execute(select(Identity).where(Identity.normalized_identifier == norm))
    ).scalar_one_or_none()
    if identity is not None:
        email = await _verified_email(db, identity.user_id)
        if email is not None:
            raw = await issue_token(db, identity.user_id, EmailTokenPurpose.RESET)
            reset_link = _reset_link(raw)
            await _send_or_503(
                request.app.state.mailer,
                to=email.identifier,
                subject="Reset your Lako password",
                text=(
                    "Someone requested a password reset for your account.\n\n"
                    f"Set a new password here (valid 1 hour):\n{reset_link}\n\n"
                    "If this was not you, ignore this email."
                ),
                html=render_email(
                    heading="Reset your password",
                    intro="Someone requested a password reset for your Samryetha account. This link is valid for 1 hour.",
                    cta_label="Set a new password",
                    cta_url=reset_link,
                    outro="If this wasn't you, you can safely ignore this email — your password will not change.",
                ),
            )
            await audit(db, "password.reset_requested", target_user_id=identity.user_id)
            await db.commit()
            return {"ok": True}
    # Unknown account (or no verified address): stay silent on success, but a
    # mail outage must surface identically — otherwise 200-vs-503 during an
    # outage becomes an account-enumeration oracle.
    try:
        await ensure_available(request.app.state.mailer)
    except MAIL_SEND_ERRORS:
        raise ApiError(503, "MAIL_UNAVAILABLE", "Email service is temporarily unavailable")
    return {"ok": True}


@router.post("/api/auth/password/reset/confirm")
async def confirm_password_reset(body: ResetConfirmBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await check_rate_limit(_ip_key(request, "password-reset-confirm"), 20)
    if len(body.new_password) < 12 or len(body.new_password) > 256:
        raise ApiError(422, "WEAK_PASSWORD", "Password must be between 12 and 256 characters")
    token = await consume_token(db, body.token.strip(), {EmailTokenPurpose.RESET, EmailTokenPurpose.INVITE})
    if token is None:
        # Rejected tokens are audited with ip + flow purpose only — never the
        # token itself (it is a bearer secret) or anything distinguishing why
        # it failed (unknown vs consumed vs expired must stay indistinguishable).
        await audit(
            db, "email.token_rejected", ip=client_ip(request), metadata_json={"purpose": "reset"}
        )
        await db.commit()
        raise ApiError(400, "INVALID_OR_EXPIRED_TOKEN", "This link is invalid or has expired")
    await _set_password(db, token.user_id, body.new_password)
    await _revoke_sessions(db, token.user_id)
    if token.purpose == EmailTokenPurpose.INVITE:
        # Converge the invite like a normal verification: verify the bound
        # address, drop other unverified addresses, and never demote an
        # existing verified address.
        await _verify_token_identity(db, token)
        await audit(db, "user.invite_accepted", target_user_id=token.user_id)
    else:
        await audit(db, "password.reset_completed", target_user_id=token.user_id)
    await db.commit()
    return {"ok": True}


# Migration fills accounts with no known-good address with this RFC 2606
# placeholder (never deliverable). Such accounts cannot verify it; they must set
# a real address first. The domain itself lives in `email_service` so every flow
# that needs a *deliverable* address shares one definition.
PLACEHOLDER_EMAIL_SUFFIX = "@" + PLACEHOLDER_EMAIL_DOMAIN


@router.get("/api/account/email")
async def read_account_email(
    request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    """Current account email + verification state (drives the account email page)."""
    rows = (
        (
            await db.execute(
                select(Identity)
                .where(Identity.user_id == ctx.user.id, Identity.type == IdentityType.EMAIL)
                .order_by(Identity.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return {"email": None, "verified": False, "placeholder": False}
    # Prefer a verified address; otherwise show the latest (most actionable) one.
    primary = next((row for row in rows if row.verified), rows[0])
    return {
        "email": primary.identifier,
        "verified": bool(primary.verified),
        "placeholder": primary.identifier.strip().lower().endswith(PLACEHOLDER_EMAIL_SUFFIX),
    }


@router.post("/api/account/email/verify/request")
async def request_email_verify(
    request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    require_csrf(request)
    await check_rate_limit(_ip_key(request, "email-verify-request"), 20)
    rows = (
        (await db.execute(select(Identity).where(Identity.user_id == ctx.user.id, Identity.type == IdentityType.EMAIL).order_by(Identity.created_at.desc())))
        .scalars()
        .all()
    )
    # Re-send to the latest unverified address: after consecutive email
    # changes only the newest address is still actionable.
    pending = next((row for row in rows if not row.verified), None)
    if pending is None:
        return {"ok": True, "already_verified": True}
    raw = await issue_token(db, ctx.user.id, EmailTokenPurpose.VERIFY, identity_id=pending.id)
    verify_link = _verify_link(raw)
    await _send_or_503(
        request.app.state.mailer,
        to=pending.identifier,
        subject="Verify your email",
        text=f"Confirm this address for your Lako account (valid 24 hours):\n{verify_link}\n",
        html=render_email(
            heading="Verify your email",
            intro="Confirm this address for your Lako account. This link is valid for 24 hours.",
            cta_label="Verify email",
            cta_url=verify_link,
            outro="If you didn't add this address, you can ignore this email.",
        ),
    )
    await audit(db, "email.verify_requested", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/email/verify/confirm")
async def confirm_email_verify(body: TokenBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    await check_rate_limit(_ip_key(request, "email-verify-confirm"), 20)
    token = await consume_token(db, body.token.strip(), {EmailTokenPurpose.VERIFY})
    if token is None:
        # See reset-confirm: audit ip + flow purpose only, never the token.
        await audit(
            db, "email.token_rejected", ip=client_ip(request), metadata_json={"purpose": "verify"}
        )
        await db.commit()
        raise ApiError(400, "INVALID_OR_EXPIRED_TOKEN", "This link is invalid or has expired")
    await _verify_token_identity(db, token)
    await audit(db, "email.verified", target_user_id=token.user_id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/email/verify/code")
async def request_email_verify_code(
    request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    """Mail a six-digit code to the pending address.

    The code is an alternative to the verification link for flows that cannot
    follow a link (the OAuth authorize interruption renders a code form, and the
    link-based round trip would lose the in-flight authorization request).
    """
    require_csrf(request)
    await check_rate_limit(_ip_key(request, "email-verify-code-request"), 10)
    pending = await pending_email(db, ctx.user.id)
    if pending is None:
        return {"ok": True, "already_verified": True}
    code = await issue_code(db, user_id=ctx.user.id, purpose=PURPOSE_VERIFY, scope_id=pending.id)
    text, html = render_code_email(
        heading="Verify your email",
        intro="Enter this code to confirm this address for your Lako account.",
        code=code,
        outro="The code is valid for 10 minutes. If you didn't add this address, you can ignore this email.",
    )
    await _send_or_503(request.app.state.mailer, to=pending.identifier, subject="Your Lako verification code", text=text, html=html)
    await audit(db, "email.verify_code_requested", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/email/verify/code/confirm")
async def confirm_email_verify_code(
    body: CodeBody, request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    """Consume the code and verify the exact address it was issued for."""
    require_csrf(request)
    await check_rate_limit(_ip_key(request, "email-verify-code-confirm"), 20)
    candidates = await unverified_emails(db, ctx.user.id)
    if not candidates:
        raise ApiError(400, "INVALID_OR_EXPIRED_CODE", "Invalid or expired verification code")
    _challenge, matched_id = await consume_code(
        db,
        user_id=ctx.user.id,
        purpose=PURPOSE_VERIFY,
        code=body.code,
        scope_ids=[identity.id for identity in candidates],
        error_code="INVALID_OR_EXPIRED_CODE",
        error_status=400,
    )
    target = next((identity for identity in candidates if identity.id == matched_id), None)
    if target is None:
        # The address was removed between send and confirm; the code proved
        # control of nothing we can still verify.
        await db.commit()
        raise ApiError(400, "INVALID_OR_EXPIRED_CODE", "Invalid or expired verification code")
    target.verified = True
    await db.execute(
        delete(Identity).where(
            Identity.user_id == ctx.user.id,
            Identity.type == IdentityType.EMAIL,
            Identity.id != target.id,
            Identity.verified.is_(False),
        )
    )
    await audit(db, "email.verified", target_user_id=ctx.user.id, metadata_json={"method": "code"})
    await db.commit()
    return {"ok": True, "email": target.identifier}


async def _verify_token_identity(db: AsyncSession, token) -> None:
    """Verify the exact email identity the token was issued for.

    Falls back to the oldest unverified address only for legacy tokens issued
    without an identity binding. Stale unverified addresses are dropped, but
    previously verified addresses are left untouched: a session-only email
    change must not strip the victim's existing recovery address.
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
    await check_rate_limit(_ip_key(request, "email-change"), 30)
    email = body.email.strip()
    if _has_control_chars(email):
        raise ApiError(422, "INVALID_EMAIL", "Enter a valid email address")
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
    verify_link = _verify_link(raw)
    await _send_or_503(
        request.app.state.mailer,
        to=existing.identifier,
        subject="Verify your email",
        text=f"Confirm this address for your Lako account (valid 24 hours):\n{verify_link}\n",
        html=render_email(
            heading="Verify your new email",
            intro="Confirm this address for your Lako account. This link is valid for 24 hours.",
            cta_label="Verify email",
            cta_url=verify_link,
            outro="If you didn't add this address, you can ignore this email.",
        ),
    )
    await audit(db, "email.changed", actor_user_id=ctx.user.id, target_user_id=ctx.user.id)
    await db.commit()
    return {"ok": True}


@router.post("/api/account/password/change")
async def change_password(
    body: ChangePasswordBody, request: Request, db: AsyncSession = Depends(get_db), ctx=Depends(require_auth)
) -> dict:
    require_csrf(request)
    await check_rate_limit(_ip_key(request, "password-change"), 30)
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
    await check_rate_limit(_ip_key(request, "admin-invite"), 60)
    by_id = None
    by_name = None
    if body.user_id is not None:
        try:
            import uuid as _uuid

            by_id = await db.get(User, _uuid.UUID(str(body.user_id)))
        except (ValueError, AttributeError):
            by_id = None
    if body.username is not None:
        identity = (
            await db.execute(
                select(Identity).where(
                    Identity.normalized_identifier == normalize_username(body.username.strip()),
                    Identity.type == IdentityType.USERNAME,
                )
            )
        ).scalar_one_or_none()
        by_name = await db.get(User, identity.user_id) if identity else None
    if (
        body.user_id is not None
        and body.username is not None
        and (by_id is None or by_name is None or by_id.id != by_name.id)
    ):
        raise ApiError(422, "USER_MISMATCH", "user_id and username do not refer to the same user")
    user = by_id if body.user_id is not None else by_name
    if user is None or user.status != UserStatus.ACTIVE:
        # 404 enumeration is acceptable here: the caller already holds the
        # service (import) token, so they are the migration operator, not the public.
        raise ApiError(404, "USER_NOT_FOUND", "User not found")
    email = await _verified_email(db, user.id)
    if email is None:
        # Fall back to the oldest on-file address only when nothing is verified.
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
    invite_link = _reset_link(raw)
    invite_name = user.display_name or email.identifier
    await _send_or_503(
        request.app.state.mailer,
        to=email.identifier,
        subject="Set up your Lako password",
        text=(
            f"Hi {invite_name},\n\n"
            "An account was created for you. Set your password here "
            f"(valid {INVITE_TTL_DAYS} days):\n{invite_link}\n\n"
            "If this was not you, ignore this email."
        ),
        html=render_email(
            heading="Set up your Samryetha account",
            intro=f"Hi {invite_name},\n\nAn account was created for you. Set a password to finish signing in.",
            cta_label="Set your password",
            cta_url=invite_link,
            outro=f"This link is valid for {INVITE_TTL_DAYS} days. If this wasn't you, you can ignore this email.",
        ),
    )
    await audit(db, "admin.user_invited", target_user_id=user.id)
    await db.commit()
    return {"ok": True, "email": email.identifier}
