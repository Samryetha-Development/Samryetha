"""Passkey / WebAuthn endpoints: account registration + usernameless sign-in."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication import webauthn_service as service
from app.authentication.mfa_service import active_totp
from app.authentication.routes import set_authenticated_cookies
from app.authentication.service import create_session
from app.common.client_ip import client_ip
from app.common.config import get_settings
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import AssuranceLevel, WebAuthnCredential
from app.common.ratelimit import check as check_rate_limit
from app.security.csrf import require_csrf
from app.security.stepup import require_recent_aal2
from app.sessions.dependencies import DEVICE_COOKIE, AuthContext, require_auth

router = APIRouter(tags=["webauthn"])

COOKIE_PATH = "/"


class RegisterVerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    credential: dict
    name: str | None = Field(default=None, max_length=120)


class AuthVerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    credential: dict


async def _has_strong_factor(db: AsyncSession, user_id) -> bool:
    """True once the account has a passkey or TOTP — i.e. a second factor that
    can satisfy step-up. The first passkey on a password-only account is allowed
    without AAL2, so enrollment is never locked out."""
    if await active_totp(db, user_id) is not None:
        return True
    count = (
        await db.execute(
            select(func.count()).select_from(WebAuthnCredential).where(WebAuthnCredential.user_id == user_id)
        )
    ).scalar() or 0
    return count > 0


async def _enforce_enrollment_policy(ctx: AuthContext, db: AsyncSession) -> None:
    if await _has_strong_factor(db, ctx.user.id):
        require_recent_aal2(ctx)


def _challenge_response(payload: str, sealed: str) -> RawResponse:
    settings = get_settings()
    response = RawResponse(content=payload, media_type="application/json")
    response.set_cookie(
        service.WEBAUTHN_COOKIE,
        sealed,
        max_age=service.CHALLENGE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=COOKIE_PATH,
    )
    return response


# ================================================================ account (auth + CSRF)


@router.get("/api/account/webauthn/credentials")
async def list_credentials(
    ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    rows = await service.list_credentials(db, ctx.user.id)
    return {
        "items": [
            {
                "id": str(row.id),
                "name": row.name,
                "createdAt": row.created_at.isoformat(),
                "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
                "backedUp": row.backed_up,
            }
            for row in rows
        ]
    }


@router.post("/api/account/webauthn/register/options")
async def register_options(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> RawResponse:
    require_csrf(request)
    await _enforce_enrollment_policy(ctx, db)
    options_json, sealed = await service.begin_registration(db, ctx.user, get_settings())
    return _challenge_response(options_json, sealed)


@router.post("/api/account/webauthn/register/verify")
async def register_verify(
    body: RegisterVerifyBody,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    require_csrf(request)
    await _enforce_enrollment_policy(ctx, db)
    sealed = request.cookies.get(service.WEBAUTHN_COOKIE)
    if not sealed:
        raise ApiError(400, "WEBAUTHN_CHALLENGE_INVALID", "This passkey request expired — try again")
    row, recovery_codes = await service.finish_registration(
        db, ctx.user, get_settings(), sealed, body.credential, body.name
    )
    response.delete_cookie(service.WEBAUTHN_COOKIE, path=COOKIE_PATH)
    return {"id": str(row.id), "name": row.name, "recoveryCodes": recovery_codes}


@router.delete("/api/account/webauthn/credentials/{credential_id}")
async def delete_credential(
    credential_id: str,
    request: Request,
    ctx: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    require_csrf(request)
    # Removing a factor is sensitive: always require recent AAL2 (a recovery
    # code can step up a password-only session if the device is lost).
    require_recent_aal2(ctx)
    await service.delete_credential(db, ctx.user.id, credential_id)
    return {"ok": True}


# ================================================================ sign-in (public)


@router.post("/api/auth/webauthn/options")
async def login_options(request: Request) -> RawResponse:
    await check_rate_limit(f"webauthn-options:{client_ip(request)}", 60)
    options_json, sealed = service.begin_authentication(get_settings())
    return _challenge_response(options_json, sealed)


@router.post("/api/auth/webauthn/verify")
async def login_verify(
    body: AuthVerifyBody, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    await check_rate_limit(f"webauthn-login:{client_ip(request)}", 30)
    settings = get_settings()
    user = await service.finish_authentication(
        db, settings, request.cookies.get(service.WEBAUTHN_COOKIE), body.credential, ip=client_ip(request)
    )
    token, session, device = await create_session(
        db,
        user,
        settings,
        client_ip(request),
        request.headers.get("user-agent"),
        request.cookies.get(DEVICE_COOKIE),
        # A passkey is a possession factor; treat it as AAL2.
        assurance_level=AssuranceLevel.AAL2,
        authentication_method="PASSKEY",
    )
    set_authenticated_cookies(response, token, str(device.id))
    response.delete_cookie(service.WEBAUTHN_COOKIE, path=COOKIE_PATH)
    return {
        "user": {"id": str(user.id), "display_name": user.display_name},
        "session_id": str(session.id),
        "assurance_level": "AAL2",
    }
