"""/api/auth/* — 镜像 backend/src/auth/routes.ts。"""

import hmac
import logging
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import auth as auth_service
from ..auth.models import (
    AuthConfigResponse,
    AuthOperationOkResponse,
    AuthSessionResponse,
    ClaimInfoResponse,
    PasswordResetRequestResponse,
    QrConfirmationResponse,
    QrInfoResponse,
    QrStartResponse,
    RegisterResponse,
)
from ..config import Settings
from ..deps import CurrentUser, DbConn, require_active_user, require_user
from ..auth.security import SESSION_COOKIE, create_session
from ..errors import APIError, bad_request, forbidden, gone, internal_error, rate_limited, service_unavailable
from ..auth.oidc import (
    OIDC_TRANSACTION_COOKIE,
    ClaimRequiredResult,
    OidcClient,
    OidcSessionResult,
    TRANSACTION_TTL_MS,
    begin_login,
    bump_claim_attempts,
    claim_account,
    claim_create_account,
    consume_login,
    login_identity,
    peek_claim,
    resolve_return_to,
    safe_return_to,
)
from ..adapters.mailer import qr_signin_confirmation_email
from ..ids import UserID
from ..auth.qr_login import (
    EMAIL_CODE_TTL_MS,
    begin_confirmation_code,
    begin_ticket,
    decide_ticket,
    exchange_ticket,
    generateQRCodeByURL,
    requires_email_confirmation,
    ticket_info,
    ticket_status,
    verify_confirmation_code,
)
from ..users import get_by_id, get_by_username, to_dto
from ..users.models import UserEnvelopeResponse

logger = logging.getLogger("samryetha.auth")

router = APIRouter()

# 事务 cookie 的 path。必须同时覆盖重定向流的 /api/auth/callback 和嵌入流的
# /api/auth/oidc/complete，所以放在共同前缀上。
# 三处 set/delete 用同一个常量——漏一个，complete 那条路就会永远报 state invalid。
OIDC_TRANSACTION_COOKIE_PATH = "/api/auth"


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


@router.get("/api/auth/config", response_model=AuthConfigResponse)
def auth_config(request: Request) -> AuthConfigResponse:
    settings: Settings = request.app.state.settings
    return AuthConfigResponse(
        oidc_enabled=settings.oidc_enabled,
        password_auth_enabled=not settings.password_auth_disabled,
        # 只有 OIDC 真的开着、且值确实是 "json" 才走新路径；其余一律回退 redirect。
        # 故意不做宽容解析——写错了要能立刻看出来，而不是半新半旧地跑。
        oidc_mode="json" if settings.oidc_mode == "json" and settings.oidc_enabled else "redirect",
        # 嵌入流里组件要拿它拼跨源绝对地址。就是 OIDC_ISSUER——那个源同时托管
        # 授权页面并反代 Lako api 的 /api/* 与 /oauth/*。本来就是公开值（discovery 里也有）。
        lako_origin=settings.oidc_issuer if settings.oidc_enabled else None,
    )


class OidcStartRequest(BaseModel):
    """嵌入流第一步的请求体。与 GET 的 query 参数同义，只是换了个承载方式。"""

    model_config = ConfigDict(populate_by_name=True)

    return_to: str | None = Field(default=None, alias="returnTo")


class OidcCompleteRequest(BaseModel):
    code: str | None = None
    state: str | None = None
    error: str | None = None


def _require_oidc(request: Request) -> None:
    settings = request.app.state.settings
    if not settings.oidc_enabled or request.app.state.oidc is None:
        raise service_unavailable("OIDC login is not configured")


def _set_transaction_cookie(response: Response, settings: Settings, state: str) -> None:
    response.set_cookie(
        key=OIDC_TRANSACTION_COOKIE,
        value=state,
        max_age=TRANSACTION_TTL_MS // 1000,
        path=OIDC_TRANSACTION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@dataclass(frozen=True, slots=True)
class _CompletedLogin:
    result: OidcSessionResult | ClaimRequiredResult
    return_to: str


def _complete_login(request: Request, code: str, state: str, error: str | None) -> _CompletedLogin:
    """两条流共用的后半段：消费事务 → 换 token → 认身份。

    返回 login_identity 的结果，外加解析好的 `return_to`。
    刻意**不发响应**——重定向流要 302，嵌入流要 JSON，两边各自组装。
    """
    settings: Settings = request.app.state.settings
    _require_oidc(request)
    cookie_state = request.cookies.get(OIDC_TRANSACTION_COOKIE)
    if error:
        raise bad_request("Identity provider rejected the login request")
    if not code or not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise bad_request("OIDC callback state is invalid")

    # Commit one-time consumption before the outbound token request, preventing replay.
    with request.app.state.db.request_conn() as conn:
        transaction = consume_login(conn, state)
    oidc: OidcClient = request.app.state.oidc
    claims = oidc.exchange_and_validate(
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
    return _CompletedLogin(result=result, return_to=transaction["return_to"])


def _issue_session(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        domain=settings.cookie_domain or None,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _claim_url(settings: Settings, result: ClaimRequiredResult, return_to: str) -> str:
    query = urlencode({"ticket": result["ticket"], "returnTo": return_to})
    return settings.app_origin.rstrip("/") + "/claim?" + query


@router.get("/api/auth/login")
def oidc_login(
    request: Request,
    conn: DbConn,
    return_to: Annotated[str | None, Query(alias="returnTo")] = None,
    embedded: bool = False,
):
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    _require_oidc(request)
    state, nonce, challenge = begin_login(conn, settings, return_to)
    location = (
        request.app.state.oidc.authorization_url(state, nonce, challenge, embedded=True)
        if embedded
        else request.app.state.oidc.authorization_url(state, nonce, challenge)
    )
    response = RedirectResponse(location, status_code=302)
    _set_transaction_cookie(response, settings, state)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/api/auth/oidc/start")
def oidc_start(
    request: Request,
    conn: DbConn,
    payload: OidcStartRequest | None = None,
) -> Response:
    """嵌入流第一步：建事务，把授权参数**交回去**而不是 302 跳过去。

    参数与 GET /api/auth/login 完全一致，差别只在终点：那边是浏览器跟着跳，
    这边是宿主页面（论坛弹层里渲染的 Lako 组件）自己带着凭据跨源去调 Lako。
    两条流共用 begin_login，事务与 PKCE 的处理不会有分叉。
    """
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    _require_oidc(request)
    return_to = payload.return_to if payload else None
    state, nonce, challenge = begin_login(conn, settings, return_to)
    response = JSONResponse({"params": request.app.state.oidc.authorization_params(state, nonce, challenge)})
    _set_transaction_cookie(response, settings, state)
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
    completed = _complete_login(request, code or "", state or "", error)
    settings: Settings = request.app.state.settings
    result = completed.result
    if "ticket" in result:
        # 无映射、无可信邮箱：不建空号，转认领页凭老密码绑定
        response = RedirectResponse(_claim_url(settings, result, completed.return_to), status_code=302)
        response.delete_cookie(OIDC_TRANSACTION_COOKIE, path=OIDC_TRANSACTION_COOKIE_PATH)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    response = RedirectResponse(resolve_return_to(settings, completed.return_to), status_code=302)
    _issue_session(response, settings, result["token"])
    response.delete_cookie(OIDC_TRANSACTION_COOKIE, path=OIDC_TRANSACTION_COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/api/auth/oidc/complete")
def oidc_complete(request: Request, payload: OidcCompleteRequest) -> Response:
    """嵌入流第三步：拿 code+state 换论坛会话，返回 JSON 而不是跳转。

    同源请求，所以事务 cookie 会带上（path=/api/auth）。发出去的会话 cookie 与
    重定向流一字不差——`_issue_session` 是同一个函数。
    """
    completed = _complete_login(request, payload.code or "", payload.state or "", payload.error)
    settings: Settings = request.app.state.settings
    result = completed.result
    if "ticket" in result:
        # ticket 一次性、只有 5 次尝试，交给前端立刻导航过去，不要在这里丢掉。
        response = JSONResponse(
            {
                "status": "claim_required",
                "ticket": result["ticket"],
                "claimUrl": _claim_url(settings, result, completed.return_to),
            }
        )
    else:
        response = JSONResponse({"status": "ok"})
        _issue_session(response, settings, result["token"])
    response.delete_cookie(OIDC_TRANSACTION_COOKIE, path=OIDC_TRANSACTION_COOKIE_PATH)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/api/auth/oidc/logout")
def oidc_logout(request: Request, returnTo: str | None = None):
    """清掉论坛本地会话，并（IdP 支持时）结束 IdP 会话。

    returnTo 只影响**本地兜底跳转**，照样过 safe_return_to 白名单——翻译站登出后
    要能回到自己，而不是被甩到论坛首页。IdP 真配了 end_session_endpoint 时，
    跳转以 IdP 为准，它那边的 post_logout_redirect_uri 是全局配置、不随请求变。
    """
    settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with request.app.state.db.request_conn() as conn:
            auth_service.logout(conn, token)
    location = settings.oidc_post_logout_redirect_uri or settings.app_origin
    if returnTo:
        location = resolve_return_to(settings, safe_return_to(returnTo, settings))
    if request.app.state.oidc is not None:
        try:
            location = request.app.state.oidc.end_session_url() or location
        except Exception:
            logger.warning("OIDC end-session discovery failed; completing local logout", exc_info=True)
    response = RedirectResponse(location, status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/", domain=settings.cookie_domain or None)
    response.headers["Cache-Control"] = "no-store"
    return response


def _strip(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


class RegisterBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")]
    password: Annotated[str, Field(min_length=8, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: object) -> object:
        return _strip(v)


class LoginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    password: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: object) -> object:
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
    def _strip_fields(cls, v: object) -> object:
        return _strip(v)


class ResetPasswordBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: Annotated[str, Field(min_length=1, max_length=200)]
    newPassword: Annotated[str, Field(min_length=8, max_length=200)]


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket: Annotated[str, Field(min_length=1, max_length=200)]
    username: Annotated[str, Field(min_length=1, max_length=30)]
    password: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: object) -> object:
        return _strip(v)


class ClaimNewBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket: Annotated[str, Field(min_length=1, max_length=200)]


class QrDecideBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: Annotated[str, Field(min_length=1, max_length=200)]
    # 仅当账号需邮箱二次确认时才有值（见 qr_login.requires_email_confirmation）。
    code: Annotated[str | None, Field(default=None, max_length=12)] = None


def _mask_email(email: str | None) -> str | None:
    """只回显掩码地址（a***@example.com）：qr/info 任何已登录用户凭 ticket_id 就能读，
    绝不能把完整邮箱从这里漏出去。"""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


class QrExchangeBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticket_id: Annotated[str, Field(min_length=1, max_length=200)]
    secret: Annotated[str, Field(min_length=1, max_length=200)]


@router.post("/api/auth/qr/start", response_model=QrStartResponse)
def qr_start(conn: DbConn, request: Request) -> QrStartResponse:
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
    return QrStartResponse(
        ticket_id=created["ticket_id"],
        secret=created["secret"],
        approve_url=approve_url,
        qr_data_uri=generateQRCodeByURL(approve_url),
        expiresAt=created["expires_at"],
    )


@router.get("/api/auth/qr/info", response_model=QrInfoResponse)
def qr_info(
    conn: DbConn, request: Request, ticket_id: str | None = None, user: CurrentUser = Depends(require_active_user)
) -> QrInfoResponse:
    """确认页展示的请求上下文（谁在请求登录）。需登录：回显的 IP/UA 只给扫码审批者看。"""
    _check_auth_rate_limit(request)
    if not ticket_id:
        raise bad_request("This QR code is invalid or has expired")
    info = ticket_info(conn, ticket_id)
    if info is None:
        raise bad_request("This QR code is invalid or has expired")
    row = get_by_id(conn, user.id)
    if row is None:
        raise forbidden("The approving account is unavailable")
    needs_code = requires_email_confirmation(row)
    return QrInfoResponse(
        created_at=info["created_at"],
        expires_at=info["expires_at"],
        ip=info["ip"],
        user_agent=info["user_agent"],
        # 需二次确认的账号：前端据此先发码再输码；地址只回掩码。
        email_confirmation_required=needs_code,
        email_hint=_mask_email(row["email"]) if needs_code else None,
    )


@router.post("/api/auth/qr/confirm/request", response_model=QrConfirmationResponse, response_model_exclude_none=True)
def qr_confirm_request(
    body: QrDecideBody, conn: DbConn, request: Request, user: CurrentUser = Depends(require_active_user)
) -> QrConfirmationResponse:
    """给需二次确认的账号发一封带 6 位码的邮件；不需要时直接告诉前端。"""
    _check_auth_rate_limit(request)
    if ticket_info(conn, body.ticket_id) is None:
        raise bad_request("This QR code is invalid or has expired")
    row = get_by_id(conn, user.id)
    if row is None:
        raise forbidden("The approving account is unavailable")
    if not requires_email_confirmation(row):
        return QrConfirmationResponse(required=False)
    settings = request.app.state.settings
    # 票据/验证码的有效期取「扫码 TTL」与「邮件码 TTL」的较大者：邮件里写的是
    # 10 分钟，扫码默认只有 2 分钟，取小值会让用户按邮件提示输入时已经过期。
    # 上限同时受 QR_LOGIN_TTL_MS 约束——运维可把它调到与邮件一致。
    code_ttl_ms = max(settings.qr_login_ttl_ms, EMAIL_CODE_TTL_MS)
    code = begin_confirmation_code(conn, body.ticket_id, user.id, ttl_ms=code_ttl_ms)
    subject, text, html = qr_signin_confirmation_email(code=code, display_name=row["display_name"])
    try:
        request.app.state.mailer.send(to=row["email"], subject=subject, text=text, html=html)
    except Exception:
        # 与 forgot_password 一致：码已落库，发信失败不把这半程变成错误响应，只记日志。
        logger.warning("qr-confirm email failed for user_id=%s", user.id, exc_info=True)
    return QrConfirmationResponse(required=True, email_hint=_mask_email(row["email"]))


@router.get("/api/auth/qr/wait")
async def qr_wait(ticket_id: str | None, request: Request) -> Response:
    """SSE：票据决议（approved/denied/expired）即推送后关闭；pending 则保持到过期。

    保持无鉴权（现状取舍）：ticket_id 是 128bit 随机票据（token_urlsafe(16)），不可猜；
    该端点只回显票据状态机（pending/approved/denied/expired），不含任何 PII；真正的
    兑换仍需 secret。加鉴权反而会逼 PC 在未登录态下持会话轮询，得不偿失。
    """
    _check_auth_rate_limit(request)
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
            # 每 2s 查一次（0.5s 粒度断开检测），降低单连接 DB 轮询频率防资源耗尽
            for _ in range(4):
                await asyncio.sleep(0.5)
                if await request.is_disconnected():
                    return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/api/auth/qr/approve", response_model=AuthOperationOkResponse)
def qr_approve(
    body: QrDecideBody, conn: DbConn, request: Request, user: CurrentUser = Depends(require_active_user)
) -> AuthOperationOkResponse:
    """手机端批准（需登录）：把本次登录权授予 PC。真实邮箱账号需先过邮件确认码。"""
    row = get_by_id(conn, user.id)
    if row is None:
        raise forbidden("The approving account is unavailable")
    if requires_email_confirmation(row):
        if not body.code:
            # 前端据此切换到输码步骤；这是流程信号，不是死路错误。
            raise forbidden("EMAIL_CODE_REQUIRED")
        verify_confirmation_code(conn, body.ticket_id, user.id, body.code)
    decide_ticket(conn, body.ticket_id, user.id, approve=True)
    return AuthOperationOkResponse()


@router.post("/api/auth/qr/deny", response_model=AuthOperationOkResponse)
def qr_deny(
    body: QrDecideBody, conn: DbConn, request: Request, user: CurrentUser = Depends(require_active_user)
) -> AuthOperationOkResponse:
    decide_ticket(conn, body.ticket_id, user.id, approve=False)
    return AuthOperationOkResponse()


@router.post("/api/auth/qr/exchange", response_model=AuthSessionResponse)
def qr_exchange(body: QrExchangeBody, conn: DbConn, request: Request, response: Response) -> AuthSessionResponse:
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
    return AuthSessionResponse(user=to_dto(row), session_expires_at=expires)


def _issue_session_cookie(response: Response, settings: Settings, token: str) -> None:
    # domain 必须跟另外两处写 cookie 的地方（OIDC 回调、密码登录）保持一致：
    # 漏了它这几条路径（QR 兑换 / 认领 / 认领建号 / 紧急登录）会发成 host-only，
    # 跨子域部署时表现为"登录看着成功，换个域名不认"。
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        domain=settings.cookie_domain or None,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/api/auth/claim", response_model=ClaimInfoResponse)
def claim_info(conn: DbConn, ticket: str | None = None) -> ClaimInfoResponse:
    """Peek at a claim ticket (no consumption) for rendering the claim page."""
    if not ticket:
        raise bad_request("This claim link is invalid or has expired")
    info = peek_claim(conn, ticket)
    if info is None:
        raise bad_request("This claim link is invalid or has expired")
    expires_at = info["expiresAt"]
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise bad_request("This claim link is invalid or has expired")
    return ClaimInfoResponse(
        email=info["email"] if isinstance(info["email"], str) else None,
        display_name=info["display_name"] if isinstance(info["display_name"], str) else None,
        expires_at=expires_at,
    )


@router.post("/api/auth/claim", response_model=AuthSessionResponse)
def claim_existing(body: ClaimBody, conn: DbConn, request: Request, response: Response) -> AuthSessionResponse:
    """Bind the ticket's OIDC identity to an existing account after a password proof.

    有意保留的迁移通道：IdP 首登无映射时，老用户凭原用户名+密码把身份认领到旧号。
    """
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
    except APIError as exc:
        # 密码错误计数必须独立提交：认领事务随异常回滚，计数写在里面会被一起滚掉导致锁票永不生效
        if exc.code == "INVALID_CREDENTIALS":
            with request.app.state.db.request_conn() as bump_conn:
                bump_claim_attempts(bump_conn, body.ticket)
        raise
    _issue_session_cookie(response, settings, result["token"])
    return AuthSessionResponse.model_validate({"user": result["user"], "sessionExpiresAt": result["expiresAt"]})


@router.post("/api/auth/claim/new", response_model=AuthSessionResponse)
def claim_create_new(body: ClaimNewBody, conn: DbConn, request: Request, response: Response) -> AuthSessionResponse:
    """Consume a claim ticket by creating a brand-new account (no existing one to link).

    有意保留的迁移通道：老用户无账号可绑时凭票建号（替代 OIDC 自动建空号）。
    """
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
    return AuthSessionResponse.model_validate({"user": result["user"], "sessionExpiresAt": result["expiresAt"]})


@router.post("/api/auth/register", status_code=201, response_model=RegisterResponse)
def register(body: RegisterBody, conn: DbConn, request: Request) -> RegisterResponse:
    _check_auth_rate_limit(request)
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password registration has been retired — please sign in with your identity provider")
    # 内测期走假邮箱注册，未校验 ALLOWED_EMAIL_DOMAINS；生产环境打印醒目告警（镜像 auth/service.ts）
    if request.app.state.settings.node_env == "production":
        logger.warning(
            "WARNING: registration uses internal fake email (samryetha.local); ALLOWED_EMAIL_DOMAINS is not enforced"
        )
    user_id = auth_service.register(conn, body.username, body.password)
    return RegisterResponse(user_id=UserID(user_id), message="pending")


@router.post("/api/auth/login", response_model=AuthSessionResponse)
def login(body: LoginBody, conn: DbConn, request: Request, response: Response) -> AuthSessionResponse:
    _check_auth_rate_limit(request)
    settings = request.app.state.settings
    if settings.password_auth_disabled:
        raise gone("Password sign-in has been retired — please sign in with your identity provider")
    result = auth_service.login(
        conn,
        body.username,
        body.password,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        session_ttl_ms=settings.session_ttl_ms,
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=result["token"],
        max_age=settings.session_ttl_ms // 1000,
        path="/",
        domain=settings.cookie_domain or None,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return AuthSessionResponse.model_validate({"user": result["user"], "sessionExpiresAt": result["expiresAt"]})


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, conn: DbConn, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        auth_service.logout(conn, token)
    settings = request.app.state.settings
    response.delete_cookie(SESSION_COOKIE, path="/", domain=settings.cookie_domain or None)
    return None


@router.get("/api/auth/me", response_model=UserEnvelopeResponse)
def me(
    conn: DbConn,
    user: CurrentUser = Depends(require_user),
) -> UserEnvelopeResponse:
    row = get_by_id(conn, user.id)
    if row is None:
        # TS: throw new Error("Session user vanished") → 500
        raise internal_error()
    return UserEnvelopeResponse(user=to_dto(row))


@router.post("/api/auth/change-password", response_model=AuthOperationOkResponse)
def change_password(
    body: ChangePasswordBody,
    conn: DbConn,
    request: Request,
    user: CurrentUser = Depends(require_user),
) -> AuthOperationOkResponse:
    _check_auth_rate_limit(request)
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password management has moved to your identity provider")
    auth_service.change_password(conn, user.id, body.currentPassword, body.newPassword)
    return AuthOperationOkResponse()


@router.post("/api/auth/forgot-password", response_model=PasswordResetRequestResponse)
def forgot_password(body: ForgotPasswordBody, conn: DbConn, request: Request) -> PasswordResetRequestResponse:
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
    return PasswordResetRequestResponse(message=auth_service.RESET_MESSAGE)


@router.post("/api/auth/reset-password", response_model=AuthOperationOkResponse)
def reset_password(body: ResetPasswordBody, conn: DbConn, request: Request) -> AuthOperationOkResponse:
    _check_auth_rate_limit(request)
    if request.app.state.settings.password_auth_disabled:
        raise gone("Password recovery has moved to your identity provider")
    auth_service.reset_password(conn, body.token, body.newPassword)
    return AuthOperationOkResponse()


class EmergencyLoginBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: Annotated[str, Field(min_length=1, max_length=30)]
    token: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator("username", mode="before")
    @classmethod
    def _strip_u(cls, v: object) -> object:
        return _strip(v)


@router.post("/api/auth/emergency-login", response_model=AuthSessionResponse)
def emergency_login(
    body: EmergencyLoginBody, conn: DbConn, request: Request, response: Response
) -> AuthSessionResponse:
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
    return AuthSessionResponse(user=to_dto(user), session_expires_at=expires)
