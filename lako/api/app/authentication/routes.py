from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.mfa_service import (
    CHALLENGE_TTL_SECONDS,
    MFA_CHALLENGE_COOKIE,
    active_totp,
    consume_login_challenge,
    create_login_challenge,
)
from app.authentication.service import authenticate, create_session
from app.common.config import get_settings
from app.common.database import get_db
from app.common.models import AssuranceLevel, Identity, IdentityType
from app.security.core import random_token
from app.security.csrf import CSRF_COOKIE
from app.sessions.dependencies import DEVICE_COOKIE, SESSION_COOKIE, AuthContext, require_auth

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginInput(BaseModel):
    login: str
    password: str


class MfaCodeInput(BaseModel):
    code: str


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def set_authenticated_cookies(response: Response, token: str, device_id: str) -> None:
    settings = get_settings()
    csrf = random_token(24)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        DEVICE_COOKIE,
        device_id,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=settings.session_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login")
async def login(body: LoginInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    user = await authenticate(db, body.login, body.password, client_ip(request))
    if await active_totp(db, user.id):
        challenge = await create_login_challenge(db, user)
        response.status_code = 202
        response.set_cookie(
            MFA_CHALLENGE_COOKIE,
            challenge,
            max_age=CHALLENGE_TTL_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/api/auth/login/mfa",
        )
        return {"mfa_required": True}
    token, session, device = await create_session(
        db, user, settings, client_ip(request), request.headers.get("user-agent"), request.cookies.get(DEVICE_COOKIE)
    )
    set_authenticated_cookies(response, token, str(device.id))
    return {"user": {"id": str(user.id), "display_name": user.display_name}, "session_id": str(session.id)}


@router.post("/login/mfa")
async def login_mfa(
    body: MfaCodeInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    challenge = request.cookies.get(MFA_CHALLENGE_COOKIE)
    if not challenge:
        from app.common.errors import ApiError

        raise ApiError(401, "MFA_CHALLENGE_INVALID", "Verification challenge is invalid or expired")
    user, method = await consume_login_challenge(db, challenge, body.code)
    token, session, device = await create_session(
        db,
        user,
        get_settings(),
        client_ip(request),
        request.headers.get("user-agent"),
        request.cookies.get(DEVICE_COOKIE),
        assurance_level=AssuranceLevel.AAL2,
        authentication_method=f"PASSWORD_{method}",
    )
    set_authenticated_cookies(response, token, str(device.id))
    response.delete_cookie(MFA_CHALLENGE_COOKIE, path="/api/auth/login/mfa")
    return {
        "user": {"id": str(user.id), "display_name": user.display_name},
        "session_id": str(session.id),
        "assurance_level": "AAL2",
        "method": method,
    }


@router.get("/me")
async def me(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    identities = (await db.execute(select(Identity).where(Identity.user_id == ctx.user.id))).scalars().all()
    return {
        "id": str(ctx.user.id),
        "display_name": ctx.user.display_name,
        "username": next((i.identifier for i in identities if i.type == IdentityType.USERNAME), None),
        "email": next((i.identifier for i in identities if i.type == IdentityType.EMAIL), None),
        "assurance_level": ctx.session.assurance_level.value,
    }
