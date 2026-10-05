from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.email_codes import (
    PURPOSE_LOGIN,
    consume_code,
    issue_code,
    render_code_email,
)
from app.authentication.email_service import deliverable_email
from app.authentication.mfa_service import (
    CHALLENGE_TTL_SECONDS,
    MFA_CHALLENGE_COOKIE,
    active_totp,
    consume_login_challenge,
    create_login_challenge,
    load_login_challenge,
)
from app.authentication.service import audit, authenticate, create_session
from app.common.client_ip import client_ip
from app.common.config import get_settings
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.mailer import MAIL_SEND_ERRORS, ensure_available
from app.common.models import AssuranceLevel, Identity, IdentityType, User, UserStatus
from app.common.ratelimit import check as check_rate_limit
from app.security.core import normalize_email, normalize_username, random_token
from app.security.csrf import CSRF_COOKIE
from app.sessions.dependencies import DEVICE_COOKIE, SESSION_COOKIE, AuthContext, require_auth

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def masked_email(address: str) -> str:
    """``a***@example.com`` — safe to show in a response body."""
    local, separator, domain = address.partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


class LoginInput(BaseModel):
    login: str
    password: str


class MfaCodeInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    code: str


class EmailCodeRequestInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str


class EmailCodeVerifyInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str
    code: str


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
    await check_rate_limit(f"login:{client_ip(request)}", 30)
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


# ---------------------------------------------------------------- email codes
#
# 邮箱验证码有两条入口：
#   1. 已输入密码/通行密钥、但账号开了 TOTP 时的第二因素替代（上面的 /login/mfa 已认它）；
#   2. 免密码登录（下面两个端点）——给"密码链路已退役 / 手上没有验证器"的账号兜底。
# 两条路都只把验证码发给**已验证**地址，绝不发到未验证或占位地址。

async def _email_code_target(db: AsyncSession, login: str) -> tuple[object | None, Identity | None]:
    """Resolve login → (user, verified email). Never tells the caller which part failed."""
    cleaned = login.strip()
    if not cleaned:
        return None, None
    normalized = normalize_email(cleaned) if "@" in cleaned else normalize_username(cleaned)
    identity = (
        await db.execute(select(Identity).where(Identity.normalized_identifier == normalized))
    ).scalar_one_or_none()
    if identity is None:
        return None, None
    user = await db.get(User, identity.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return user, None
    return user, await deliverable_email(db, user.id)


async def _deliver_login_code(request: Request, db: AsyncSession, user, email: Identity) -> None:
    """Issue a sign-in code for ``user`` and mail it to the verified address."""
    code = await issue_code(db, user_id=user.id, purpose=PURPOSE_LOGIN)
    text, html = render_code_email(
        heading="Your Samryetha sign-in code",
        intro=f"Enter this code to sign in to {email.identifier}.",
        code=code,
        outro="The code is valid for 10 minutes and can be used once. If you didn't try to sign in, change your password.",
    )
    try:
        await request.app.state.mailer.send(to=email.identifier, subject="Your Samryetha sign-in code", text=text, html=html)
    except MAIL_SEND_ERRORS:
        raise ApiError(503, "MAIL_UNAVAILABLE", "Email service is temporarily unavailable")


async def _audit_email_code_failure(db: AsyncSession, user_id: object | None, request: Request) -> None:
    """Rejected codes are audited without the code itself (it is a bearer secret)."""
    await audit(db, "auth.email_code_failed", target_user_id=user_id, ip=client_ip(request))
    await db.commit()


@router.post("/email-code/request")
async def request_email_code(body: EmailCodeRequestInput, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Mail a sign-in code. Unknown accounts get the same answer as known ones."""
    await check_rate_limit(f"email-code-request:{client_ip(request)}", 5)
    user, email = await _email_code_target(db, body.login)
    if user is not None and email is not None:
        await _deliver_login_code(request, db, user, email)
        await audit(db, "auth.email_code_requested", target_user_id=user.id, ip=client_ip(request))
        await db.commit()
        return {"ok": True}
    # Same 200 for an unknown account, a disabled account, and an account without
    # a verified address — otherwise this endpoint enumerates accounts. A mail
    # outage still surfaces identically (see request_password_reset).
    try:
        await ensure_available(request.app.state.mailer)
    except MAIL_SEND_ERRORS:
        raise ApiError(503, "MAIL_UNAVAILABLE", "Email service is temporarily unavailable")
    return {"ok": True}


@router.post("/login/mfa/email-code")
async def login_mfa_email_code(
    request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    """Second step of a password sign-in: mail the code that /login/mfa will accept."""
    await check_rate_limit(f"login-mfa-code:{client_ip(request)}", 5)
    raw = request.cookies.get(MFA_CHALLENGE_COOKIE)
    if not raw:
        raise ApiError(401, "MFA_CHALLENGE_INVALID", "Verification challenge is invalid or expired")
    challenge = await load_login_challenge(db, raw)
    user = await db.get(User, challenge.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise ApiError(401, "MFA_CHALLENGE_INVALID", "Verification challenge is invalid or expired")
    email = await deliverable_email(db, user.id)
    if email is None:
        raise ApiError(409, "NO_VERIFIED_EMAIL", "Add and verify an email address first")
    await _deliver_login_code(request, db, user, email)
    await audit(db, "auth.email_code_requested", target_user_id=user.id, ip=client_ip(request))
    await db.commit()
    return {"ok": True, "email": masked_email(email.identifier)}


@router.post("/login/email-code")
async def login_with_email_code(
    body: EmailCodeVerifyInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict:
    """Passwordless sign-in with a mailed code (AAL1: possession of the mailbox)."""
    settings = get_settings()
    await check_rate_limit(f"login-email-code:{client_ip(request)}", 30)
    user, email = await _email_code_target(db, body.login)
    if user is None or email is None:
        await _audit_email_code_failure(db, None, request)
        raise ApiError(401, "INVALID_EMAIL_CODE", "Invalid or expired verification code")
    try:
        await consume_code(
            db,
            user_id=user.id,
            purpose=PURPOSE_LOGIN,
            code=body.code,
            # Reachable from the passwordless form and the second-factor form:
            # both redeem the same code, so one error code covers both.
            error_code="INVALID_MFA_CODE",
            error_status=401,
        )
    except ApiError:
        await _audit_email_code_failure(db, user.id, request)
        raise
    token, session, device = await create_session(
        db,
        user,
        settings,
        client_ip(request),
        request.headers.get("user-agent"),
        request.cookies.get(DEVICE_COOKIE),
        authentication_method="EMAIL_CODE",
    )
    await audit(db, "authentication.success", actor_user_id=user.id, target_user_id=user.id, ip=client_ip(request))
    await db.commit()
    set_authenticated_cookies(response, token, str(device.id))
    return {
        "user": {"id": str(user.id), "display_name": user.display_name},
        "session_id": str(session.id),
        "assurance_level": "AAL1",
        "method": "EMAIL_CODE",
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
