"""/api/auth/* — 镜像 backend/src/auth/routes.ts。"""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import auth as auth_service
from ..deps import CurrentUser, DbConn, get_db, require_user
from ..security import SESSION_COOKIE, create_session
from ..errors import ApiError, bad_request, forbidden, gone, internal_error, rate_limited, service_unavailable
from ..oidc import (
    OIDC_TRANSACTION_COOKIE,
    TRANSACTION_TTL_MS,
    begin_login,
    bump_claim_attempts,
    claim_account,
    claim_create_account,
    consume_login,
    login_identity,
    peek_claim,
)
from ..qr_login import (
    begin_ticket,
    decide_ticket,
    exchange_ticket,
    qr_data_uri,
    ticket_info,
    ticket_status,
)
from ..users import get_by_id, get_by_username, to_dto

logger = logging.getLogger("samryetha.auth")

router = APIRouter()


def _client_ip(request: Request) -> str:
    """与 GuardMiddleware._client_ip 同一套规则：仅 TRUST_PROXY 下采信 X-Forwarded-For。"""
    settings = request.app.state.settings
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_auth_rate_limit(request: Request) -> None:
    """登录/注册 per-route 限流（镜像 auth/routes.ts 的 rateLimit max=10/min）。"""
    limiter = request.app.state.auth_limiter
    allowed, retry_after = limiter.allow(_client_ip(request))
    if not allowed:
        raise rate_limited(int(retry_after * 1000))


@router.get("/api/auth/config")
def auth_config(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "oidcEnabled": settings.oidc_enabled,
        "passwordAuthEnabled": not settings.password_auth_disabled,
    }


@router.get("/api/auth/login")
def oidc_login(
    request: Request,
    conn: DbConn,
    return_to: Annotated[str | None, Query(alias="returnTo")] = None,
):
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    if not settings.oidc_enabled or request.app.state.oidc is None:
        raise service_unavailable("OIDC login is not configured")
    state, nonce, challenge = begin_login(conn, settings, return_to)
    location = request.app.state.oidc.authorization_url(state, nonce, challenge)
    response = RedirectResponse(location, status_code=302)
    response.set_cookie(
        key=OIDC_TRANSACTION_COOKIE,
        value=state,
        max_age=TRANSACTION_TTL_MS // 1000,
        path="/api/auth/callback",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/auth/callback")
def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    settings = request.app.state.settings
    if not settings.oidc_enabled or request.app.state.oidc is None:
        raise service_unavailable("OIDC login is not configured")
    cookie_state = request.cookies.get(OIDC_TRANSACTION_COOKIE)
    if error:
        raise bad_request("Identity provider rejected the login request")
    if not code or not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise bad_request("OIDC callback state is invalid")

    # Commit one-time consumption before the outbound token request, preventing replay.
    with request.app.state.db.request_conn() as conn:
        transaction = consume_login(conn, state)
    claims = request.app.state.oidc.exchange_and_validate(
        code,
        transaction["code_verifier"],
        transaction["nonce"],
    )
    with request.app.state.db.request_conn() as conn:
        result = login_identity(
            conn,
            claims,
            settings,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            auto_create=False,
        )

    response = RedirectResponse(settings.app_origin.rstrip("/") + transaction["return_to"], status_code=302)
    if result.get("status") == "claim_required":
        # 无映射、无可信邮箱：不建空号，转认领页凭老密码绑定
        response = RedirectResponse(
            settings.app_origin.rstrip("/") + "/claim?ticket=" + result["ticket"], status_code=302
        )
        response.delete_cookie(OIDC_TRANSACTION_COOKIE, path="/api/auth/callback")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    response.set_cookie(
        key=SESSION_COOKIE,
        value=result["token"],
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(OIDC_TRANSACTION_COOKIE, path="/api/auth/callback")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/auth/oidc/logout")
def oidc_logout(request: Request):
    settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with request.app.state.db.request_conn() as conn:
            auth_service.logout(conn, token)
    location = settings.oidc_post_logout_redirect_uri or settings.app_origin
    if request.app.state.oidc is not None:
        try:
            location = request.app.state.oidc.end_session_url() or location
        except Exception:
            logger.warning("OIDC end-session discovery failed; completing local logout", exc_info=True)
    response = RedirectResponse(location, status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


def _strip(v: Any) -> Any:
    return v.strip() if isinstance(v, str) else v


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")]
    password: Annotated[str, Field(min_length=8, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: Any) -> Any:
        return _strip(v)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    password: Annotated[str, Field(min_length=1)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: Any) -> Any:
        return _strip(v)


class ChangePasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    currentPassword: Annotated[str, Field(min_length=1)]
    newPassword: Annotated[str, Field(min_length=8, max_length=200)]


class ForgotPasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    recoveryEmail: Annotated[str, Field(min_length=3, max_length=200)]

    @field_validator("username", "recoveryEmail", mode="before")
    @classmethod
    def _strip_fields(cls, v: Any) -> Any:
        return _strip(v)


class ResetPasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: Annotated[str, Field(min_length=1, max_length=200)]
    newPassword: Annotated[str, Field(min_length=8, max_length=200)]


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket: Annotated[str, Field(min_length=1, max_length=200)]
    username: Annotated[str, Field(min_length=1, max_length=30)]
    password: Annotated[str, Field(min_length=1)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: Any) -> Any:
        return _strip(v)


class ClaimNewBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket: Annotated[str, Field(min_length=1, max_length=200)]


class QrDecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: Annotated[str, Field(min_length=1, max_length=200)]


class QrExchangeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: Annotated[str, Field(min_length=1, max_length=200)]
    secret: Annotated[str, Field(min_length=1, max_length=200)]


@router.post("/api/auth/qr/start")
def qr_start(conn: DbConn, request: Request) -> dict:
    """PC 发起扫码登录：返回票据 id + 二维码（secret 只留 PC 内存，不进 URL）。"""
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    created = begin_ticket(
        conn,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        ttl_ms=settings.qr_login_ttl_ms,
    )
    approve_url = settings.app_origin.rstrip("/") + "/qr/approve?t=" + created["ticket_id"]
    return {
        "ticket_id": created["ticket_id"],
        "secret": created["secret"],
        "approve_url": approve_url,
        "qr_data_uri": qr_data_uri(approve_url),
        "expiresAt": created["expires_at"],
    }


@router.get("/api/auth/qr/info")
def qr_info(conn: DbConn, request: Request, ticket_id: str | None = None) -> dict:
    """确认页展示的请求上下文（谁在请求登录）。"""
    _check_auth_rate_limit(request)
    if not ticket_id:
        raise bad_request("This QR code is invalid or has expired")
    info = ticket_info(conn, ticket_id)
    if info is None:
        raise bad_request("This QR code is invalid or has expired")
    return {
        "createdAt": info["created_at"],
        "expiresAt": info["expires_at"],
        "ip": info["ip"],
        "userAgent": info["user_agent"],
    }


@router.get("/api/auth/qr/wait")
async def qr_wait(ticket_id: str | None, request: Request) -> Response:
    """SSE：票据决议（approved/denied/expired）即推送后关闭；pending 则保持到过期。"""
    import asyncio

    from fastapi.responses import StreamingResponse

    db = request.app.state.db

    async def stream():
        while True:
            if await request.is_disconnected():
                return
            with db.request_conn() as conn:
                state = ticket_status(conn, ticket_id or "")
            if state is None:
                yield "event: closed\ndata: {}\n\n"
                return
            if state["status"] != "pending":
                yield f"event: {state['status']}\ndata: {{}}\n\n"
                return
            for _ in range(10):
                await asyncio.sleep(0.1)
                if await request.is_disconnected():
                    return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/api/auth/qr/approve")
def qr_approve(
    body: QrDecideBody, conn: DbConn, request: Request, user: CurrentUser = Depends(require_user)
) -> dict:
    """手机端批准（需登录）：把本次登录权授予 PC。"""
    decide_ticket(conn, body.ticket_id, user.id, approve=True)
    return {"ok": True}


@router.post("/api/auth/qr/deny")
def qr_deny(
    body: QrDecideBody, conn: DbConn, request: Request, user: CurrentUser = Depends(require_user)
) -> dict:
    decide_ticket(conn, body.ticket_id, user.id, approve=False)
    return {"ok": True}


@router.post("/api/auth/qr/exchange")
def qr_exchange(body: QrExchangeBody, conn: DbConn, request: Request, response: Response) -> dict:
    """PC 凭 (ticket_id + secret) 兑换会话。单次有效，用后即焚。"""
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    user_id = exchange_ticket(conn, body.ticket_id, body.secret)
    row = get_by_id(conn, user_id)
    if row is None:  # 防御：用户在批准后被删
        raise forbidden("The approving account is unavailable")
    token, expires = create_session(
        conn,
        user_id,
        {"ip": _client_ip(request), "user_agent": request.headers.get("user-agent")},
        ttl_ms=settings.session_ttl_ms,
    )
    _issue_session_cookie(response, settings, token)
    return {"user": to_dto(row), "sessionExpiresAt": expires}


def _issue_session_cookie(response: Response, settings, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/api/auth/claim")
def claim_info(conn: DbConn, ticket: str | None = None) -> dict:
    """Peek at a claim ticket (no consumption) for rendering the claim page."""
    if not ticket:
        raise bad_request("This claim link is invalid or has expired")
    info = peek_claim(conn, ticket)
    if info is None:
        raise bad_request("This claim link is invalid or has expired")
    return {"email": info["email"], "displayName": info["display_name"], "expiresAt": info["expiresAt"]}


@router.post("/api/auth/claim")
def claim_existing(body: ClaimBody, conn: DbConn, request: Request, response: Response) -> dict:
    """Bind the ticket's OIDC identity to an existing account after a password proof."""
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    try:
        result = claim_account(
            conn,
            ticket=body.ticket,
            username=body.username,
            password=body.password,
            settings=settings,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except ApiError as exc:
        # 密码错误计数必须独立提交：认领事务随异常回滚，计数写在里面会被一起滚掉导致锁票永不生效
        if exc.code == "INVALID_CREDENTIALS":
            with request.app.state.db.request_conn() as bump_conn:
                bump_claim_attempts(bump_conn, body.ticket)
        raise
    _issue_session_cookie(response, settings, result["token"])
    return {"user": result["user"], "sessionExpiresAt": result["expiresAt"]}


@router.post("/api/auth/claim/new")
def claim_create_new(body: ClaimNewBody, conn: DbConn, request: Request, response: Response) -> dict:
    """Consume a claim ticket by creating a brand-new account (no existing one to link)."""
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    result = claim_create_account(
        conn,
        ticket=body.ticket,
        settings=settings,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _issue_session_cookie(response, settings, result["token"])
    return {"user": result["user"], "sessionExpiresAt": result["expiresAt"]}


@router.post("/api/auth/register", status_code=201)
def register(body: RegisterBody, conn: DbConn, request: Request) -> dict:
    _check_auth_rate_limit(request)
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password registration has been retired — please sign in with your identity provider")
    # 内测期走假邮箱注册，未校验 ALLOWED_EMAIL_DOMAINS；生产环境打印醒目告警（镜像 auth/service.ts）
    if request.app.state.settings.node_env == "production":
        logger.warning(
            "WARNING: registration uses internal fake email (samryetha.local); ALLOWED_EMAIL_DOMAINS is not enforced"
        )
    user_id = auth_service.register(conn, body.username, body.password)
    return {"userId": user_id, "message": "pending"}


@router.post("/api/auth/login")
def login(body: LoginBody, conn: DbConn, request: Request, response: Response) -> dict:
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    if settings.password_auth_disabled:
        raise gone("Password sign-in has been retired — please sign in with your identity provider")
    result = auth_service.login(
        conn,
        body.username,
        body.password,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        session_ttl_ms=settings.session_ttl_ms,
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=result["token"],
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return {"user": result["user"], "sessionExpiresAt": result["expiresAt"]}


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, conn: DbConn, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth_service.logout(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return None


@router.get("/api/auth/me")
def me(
    conn: DbConn,
    user: CurrentUser = Depends(require_user),
) -> dict:
    row = get_by_id(conn, user.id)
    if row is None:
        # TS: throw new Error("Session user vanished") → 500
        raise internal_error()
    return {"user": to_dto(row)}


@router.post("/api/auth/change-password")
def change_password(
    body: ChangePasswordBody,
    conn: DbConn,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> dict:
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password management has moved to your identity provider")
    auth_service.change_password(conn, user.id, body.currentPassword, body.newPassword)
    return {"ok": True}


@router.post("/api/auth/forgot-password")
def forgot_password(body: ForgotPasswordBody, conn: DbConn, request: Request) -> dict:
    _check_auth_rate_limit(request)
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password recovery has moved to your identity provider")
    auth_service.forgot_password(
        conn,
        body.username,
        body.recoveryEmail,
        mailer=request.app.state.mailer,
        app_origin=request.app.state.settings.app_origin,
    )
    return {"ok": True, "message": auth_service.RESET_MESSAGE}


@router.post("/api/auth/reset-password")
def reset_password(body: ResetPasswordBody, conn: DbConn, request: Request) -> dict:
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password recovery has moved to your identity provider")
    auth_service.reset_password(conn, body.token, body.newPassword)
    return {"ok": True}


class EmergencyLoginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    token: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: Any) -> Any:
        return _strip(v)


@router.post("/api/auth/emergency-login")
def emergency_login(body: EmergencyLoginBody, conn: DbConn, request: Request, response: Response) -> dict:
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    secret = settings.emergency_login_token
    if not secret or not hmac.compare_digest(body.token, secret):
        raise forbidden("Emergency login is not available")
    user = get_by_username(conn, body.username)
    if user is None or user["role"] != "admin" or user["status"] != "active":
        raise forbidden("Emergency login is not available")
    token, expires = create_session(
        conn,
        user["id"],
        {"ip": _client_ip(request), "user_agent": request.headers.get("user-agent")},
        ttl_ms=settings.session_ttl_ms,
    )
    _issue_session_cookie(response, settings, token)
    return {"user": to_dto(user), "sessionExpiresAt": expires}
