import base64
import hmac
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import audit
from app.common.config import get_settings
from app.common.database import get_db
from app.common.models import (
    AccessToken,
    AuthorizationCode,
    Identity,
    IdentityType,
    OAuthClient,
    OAuthRedirectURI,
    Role,
    Session,
    User,
    user_roles,
    utcnow,
)
from app.oauth.jwt_keys import get_signing_keys
from app.security.core import pkce_challenge, random_token, token_hash
from app.security.csrf import CSRF_COOKIE
from app.sessions.dependencies import DEVICE_COOKIE, SESSION_COOKIE

router = APIRouter(tags=["oauth"])
SUPPORTED_SCOPES = {"openid", "profile", "email", "groups"}


def oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description}, status_code=status, headers={"Cache-Control": "no-store"}
    )


async def valid_client(db: AsyncSession, client_id: str, redirect_uri: str) -> OAuthClient | None:
    client = (await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))).scalar_one_or_none()
    if not client:
        return None
    exact = (
        await db.execute(
            select(OAuthRedirectURI.id).where(
                OAuthRedirectURI.oauth_client_id == client.id, OAuthRedirectURI.uri == redirect_uri
            )
        )
    ).first()
    return client if exact else None


def client_authenticated(request: Request, client: OAuthClient) -> bool:
    if client.is_public:
        return True
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Basic ") or not client.client_secret_hash:
        return False
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode()
        client_id, client_secret = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(client_id, client.client_id) and hmac.compare_digest(
        token_hash(client_secret), client.client_secret_hash
    )


async def scoped_identity_claims(db: AsyncSession, user_id, scopes: set[str]) -> dict:
    user = await db.get(User, user_id)
    identities = (await db.execute(select(Identity).where(Identity.user_id == user_id))).scalars().all()
    result: dict = {}
    if "profile" in scopes:
        result["name"] = user.display_name
        result["preferred_username"] = next(
            (identity.identifier for identity in identities if identity.type == IdentityType.USERNAME), None
        )
    if "email" in scopes:
        email = next((identity for identity in identities if identity.type == IdentityType.EMAIL), None)
        result["email"] = email.identifier if email else None
        result["email_verified"] = email.verified if email else False
    if "groups" in scopes:
        result["groups"] = list(
            (await db.execute(select(Role.name).join(user_roles).where(user_roles.c.user_id == user_id))).scalars()
        )
    return result


@router.get("/.well-known/openid-configuration")
async def discovery() -> dict:
    issuer = get_settings().oidc_issuer
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "userinfo_endpoint": f"{issuer}/oauth/userinfo",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": sorted(SUPPORTED_SCOPES),
        "claims_supported": ["sub", "name", "preferred_username", "email", "email_verified", "groups"],
        "code_challenge_methods_supported": ["S256"],
        "prompt_values_supported": ["select_account"],
        "end_session_endpoint": f"{issuer}/oauth/end-session",
    }


@router.get("/.well-known/jwks.json")
async def jwks() -> dict:
    return {"keys": [get_signing_keys().jwk]}


def _allowed_post_logout_uri(uri: str | None) -> str | None:
    """Only allow a post-logout redirect whose origin is a trusted browser origin."""
    if not uri:
        return None
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"
    settings = get_settings()
    allowed = set(settings.cors_origins)
    for registered in settings.samryetha_redirect_uri_list:
        registered_parts = urlsplit(registered)
        if registered_parts.scheme and registered_parts.netloc:
            allowed.add(f"{registered_parts.scheme}://{registered_parts.netloc}")
    return uri if origin in allowed else None


@router.get("/oauth/end-session")
async def end_session(
    request: Request,
    post_logout_redirect_uri: str | None = None,
    state: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """OIDC RP-initiated logout: revoke the current session and return the user
    to a trusted post-logout origin (or the issuer home). Must be a top-level
    navigation so the SameSite=Lax session cookie is sent."""
    auth_session = await _load_session(db, request.cookies.get(SESSION_COOKIE))
    if auth_session is not None:
        auth_session.revoked_at = utcnow()
        await audit(
            db,
            "session.revoked",
            actor_user_id=auth_session.user_id,
            target_user_id=auth_session.user_id,
            session_id=auth_session.id,
            metadata_json={"scope": "logout"},
        )
        await db.commit()
    target = _allowed_post_logout_uri(post_logout_redirect_uri) or get_settings().app_origin
    if state:
        target = f"{target}{'&' if '?' in target else '?'}{urlencode({'state': state})}"
    response = RedirectResponse(target, status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(DEVICE_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@dataclass(frozen=True)
class _AuthorizeOutcome:
    """
    authorize 的**判定结果**，与呈现方式无关。

    `GET /oauth/authorize` 把它渲染成 302（重定向流），
    `POST /api/oauth/authorize` 把它渲染成 JSON（嵌入流）。
    两条流共用同一份校验与铸码逻辑——否则迟早有一条漏掉安全检查。
    """

    status: Literal["invalid_client", "error", "login_required", "select_account", "code"]
    error: str | None = None
    error_description: str | None = None
    redirect_uri: str | None = None
    state: str | None = None
    return_to: str | None = None
    embedded: bool = False
    user_id: UUID | None = None
    code: str | None = None


def _continuation(path: str, query: str) -> str:
    return path + (f"?{query}" if query else "")


def _stripped_continuation(path: str, params: Sequence[tuple[str, str]]) -> str:
    """去掉 prompt / display，避免续跳时再次触发选号而形成死循环。"""
    kept = [(key, value) for key, value in params if key not in {"prompt", "display"}]
    return path + (f"?{urlencode(kept)}" if kept else "")


async def _load_session(db: AsyncSession, raw_session: str | None) -> Session | None:
    if not raw_session:
        return None
    auth_session = (
        await db.execute(
            select(Session).where(Session.token_hash == token_hash(raw_session), Session.revoked_at.is_(None))
        )
    ).scalar_one_or_none()
    if auth_session and (
        auth_session.expires_at.replace(tzinfo=auth_session.expires_at.tzinfo or utcnow().tzinfo) <= utcnow()
    ):
        return None
    return auth_session


async def _authorize_core(
    db: AsyncSession,
    *,
    request_path: str,
    raw_query: str,
    query_params: Sequence[tuple[str, str]],
    raw_session: str | None,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str | None,
    code_challenge_method: str | None,
    nonce: str | None,
    prompt: str | None,
    display: str | None,
) -> _AuthorizeOutcome:
    client = await valid_client(db, client_id, redirect_uri)
    if not client:
        return _AuthorizeOutcome("invalid_client", error="invalid_request", error_description="Invalid client or redirect_uri")
    if response_type != "code" or not code_challenge or code_challenge_method != "S256":
        return _AuthorizeOutcome(
            "error",
            error="invalid_request",
            error_description="Authorization Code with PKCE S256 is required",
            redirect_uri=redirect_uri,
            state=state,
        )
    scopes = set(scope.split())
    if "openid" not in scopes or not scopes.issubset(SUPPORTED_SCOPES):
        return _AuthorizeOutcome("error", error="invalid_scope", redirect_uri=redirect_uri, state=state)
    prompt_values = set(prompt.split()) if prompt else set()
    if prompt_values - {"select_account"}:
        return _AuthorizeOutcome("error", error="invalid_request", redirect_uri=redirect_uri, state=state)
    if display not in {None, "popup"}:
        return _AuthorizeOutcome("error", error="invalid_request", redirect_uri=redirect_uri, state=state)

    auth_session = await _load_session(db, raw_session)
    return_to = _continuation(request_path, raw_query)
    if "select_account" in prompt_values:
        return_to = _stripped_continuation(request_path, query_params)
        if auth_session:
            return _AuthorizeOutcome(
                "select_account",
                return_to=return_to,
                embedded=display == "popup",
                user_id=auth_session.user_id,
            )
    if not auth_session:
        return _AuthorizeOutcome("login_required", return_to=return_to)

    code = random_token()
    record = AuthorizationCode(
        code_hash=token_hash(code),
        oauth_client_id=client.id,
        user_id=auth_session.user_id,
        redirect_uri=redirect_uri,
        scope=" ".join(sorted(scopes)),
        code_challenge=code_challenge,
        nonce=nonce,
        assurance_level=auth_session.assurance_level,
        authentication_method=auth_session.authentication_method,
        auth_time=auth_session.assurance_verified_at or auth_session.created_at,
        expires_at=utcnow() + timedelta(seconds=get_settings().auth_code_ttl_seconds),
    )
    db.add(record)
    await db.flush()
    await audit(
        db,
        "oauth.authorization.created",
        actor_user_id=auth_session.user_id,
        target_user_id=auth_session.user_id,
        session_id=auth_session.id,
        metadata_json={"client_id": client_id, "scopes": sorted(scopes)},
    )
    await db.commit()
    return _AuthorizeOutcome(
        "code", redirect_uri=redirect_uri, state=state, code=code, return_to=return_to
    )


async def _account_summary(db: AsyncSession, user_id: UUID) -> dict:
    user = await db.get(User, user_id)
    identities = (await db.execute(select(Identity).where(Identity.user_id == user_id))).scalars().all()
    return {
        "display_name": user.display_name,
        "username": next((i.identifier for i in identities if i.type == IdentityType.USERNAME), None),
        "email": next((i.identifier for i in identities if i.type == IdentityType.EMAIL), None),
    }


@router.get("/oauth/authorize")
async def authorize(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    nonce: str | None = None,
    prompt: str | None = None,
    display: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """重定向流：浏览器跟着 302 走，由 OIDC_ISSUER 的宿主渲染 /login 与 /select-account。"""
    outcome = await _authorize_core(
        db,
        request_path=request.url.path,
        raw_query=request.url.query,
        query_params=request.query_params.multi_items(),
        raw_session=request.cookies.get(SESSION_COOKIE),
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        nonce=nonce,
        prompt=prompt,
        display=display,
    )
    if outcome.status == "invalid_client":
        return oauth_error(outcome.error or "invalid_request", outcome.error_description or "")
    if outcome.status == "error":
        params = {"error": outcome.error, "state": outcome.state}
        if outcome.error_description is not None:
            params["error_description"] = outcome.error_description
        return RedirectResponse(f"{outcome.redirect_uri}?{urlencode(params)}", status_code=302)
    if outcome.status == "select_account":
        chooser_params = {"return_to": outcome.return_to}
        if outcome.embedded:
            chooser_params["embedded"] = "1"
        return RedirectResponse(f"/select-account?{urlencode(chooser_params)}", status_code=302)
    if outcome.status == "login_required":
        return RedirectResponse(f"/login?{urlencode({'return_to': outcome.return_to})}", status_code=302)
    return RedirectResponse(
        f"{outcome.redirect_uri}?{urlencode({'code': outcome.code, 'state': outcome.state})}", status_code=302
    )


@router.post("/api/oauth/authorize")
async def authorize_json(
    request: Request,
    response_type: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    nonce: str | None = None,
    prompt: str | None = None,
    display: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    嵌入流：给跑在别处的共享 UI 组件用，不靠跳转。

    参数与 GET 完全一致（走 query string），只是呈现方式不同——这样两条流
    对调用方是同一份契约，也保证 `_authorize_core` 的校验不会被绕过。

    **CSRF 立场（有意为之，不是遗漏）**：本端点会铸出一枚 AuthorizationCode，
    但 `/oauth/*` 一族在本次改动前就没有 CSRF 保护，这里维持原状。理由是
    这个动作的防护来自 PKCE + `redirect_uri` 精确匹配 + 一次性 code，而不是
    CSRF token：攻击者即便能发起跨站带凭据的请求，也必须是一个已注册的 client、
    `redirect_uri` 一字不差，且拿不到 code_verifier；而响应受 CORS 限制，
    非白名单源读不到那个 `redirect`。GET 版本同样可被跨站触发（iframe），
    同样读不到结果——两条流的暴露面是一致的，没有因此扩大。
    """
    outcome = await _authorize_core(
        db,
        request_path=request.url.path,
        raw_query=request.url.query,
        query_params=request.query_params.multi_items(),
        raw_session=request.cookies.get(SESSION_COOKIE),
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        nonce=nonce,
        prompt=prompt,
        display=display,
    )
    headers = {"Cache-Control": "no-store"}
    if outcome.status == "invalid_client":
        return JSONResponse(
            {"error": outcome.error, "error_description": outcome.error_description},
            status_code=400,
            headers=headers,
        )
    if outcome.status == "error":
        body = {"status": "error", "error": outcome.error}
        if outcome.error_description is not None:
            body["error_description"] = outcome.error_description
        return JSONResponse(body, status_code=400, headers=headers)
    if outcome.status == "select_account":
        account = await _account_summary(db, outcome.user_id)
        return JSONResponse({"status": "select_account", "account": account}, headers=headers)
    if outcome.status == "login_required":
        return JSONResponse({"status": "login_required"}, headers=headers)
    return JSONResponse(
        {
            "status": "code",
            "redirect": f"{outcome.redirect_uri}?{urlencode({'code': outcome.code, 'state': outcome.state})}",
        },
        headers=headers,
    )


@router.post("/oauth/token")
async def exchange(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_verifier: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if grant_type != "authorization_code":
        return oauth_error("unsupported_grant_type", "Only authorization_code is supported")
    client = await valid_client(db, client_id, redirect_uri)
    if not client:
        return oauth_error("invalid_grant", "Invalid authorization grant")
    if not client_authenticated(request, client):
        return oauth_error("invalid_client", "Client authentication failed", 401)
    record = (
        await db.execute(select(AuthorizationCode).where(AuthorizationCode.code_hash == token_hash(code)))
    ).scalar_one_or_none()
    now = utcnow()
    if not record or record.oauth_client_id != client.id or record.redirect_uri != redirect_uri or record.used_at:
        await audit(db, "security.authorization_code_replay", metadata_json={"client_id": client_id})
        await db.commit()
        return oauth_error("invalid_grant", "Invalid authorization grant")
    expires = record.expires_at.replace(tzinfo=record.expires_at.tzinfo or now.tzinfo)
    if expires <= now:
        return oauth_error("invalid_grant", "Authorization code expired")
    if not hmac.compare_digest(pkce_challenge(code_verifier), record.code_challenge):
        await audit(db, "security.invalid_pkce", target_user_id=record.user_id, metadata_json={"client_id": client_id})
        await db.commit()
        return oauth_error("invalid_grant", "PKCE verification failed")
    record.used_at = now
    access = random_token()
    access_exp = now + timedelta(seconds=get_settings().access_token_ttl_seconds)
    db.add(
        AccessToken(
            token_hash=token_hash(access),
            user_id=record.user_id,
            oauth_client_id=client.id,
            scope=record.scope,
            expires_at=access_exp,
        )
    )
    claims = {
        "iss": get_settings().oidc_issuer,
        "aud": client_id,
        "sub": str(record.user_id),
        "iat": int(now.timestamp()),
        "exp": int(access_exp.timestamp()),
        "auth_time": int(record.auth_time.replace(tzinfo=record.auth_time.tzinfo or now.tzinfo).timestamp()),
        "acr": f"urn:lako:aal:{record.assurance_level.value[-1]}",
        "amr": [part.lower() for part in record.authentication_method.split("_")],
    }
    if record.nonce:
        claims["nonce"] = record.nonce
    claims.update(await scoped_identity_claims(db, record.user_id, set(record.scope.split())))
    await audit(
        db,
        "oauth.code.exchanged",
        actor_user_id=record.user_id,
        target_user_id=record.user_id,
        metadata_json={"client_id": client_id},
    )
    await db.commit()
    return {
        "access_token": access,
        "id_token": get_signing_keys().sign(claims),
        "token_type": "Bearer",
        "expires_in": get_settings().access_token_ttl_seconds,
        "scope": record.scope,
    }


@router.get("/oauth/userinfo")
async def userinfo(request: Request, db: AsyncSession = Depends(get_db)):
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return oauth_error("invalid_token", "Bearer token required", 401)
    token = (
        await db.execute(
            select(AccessToken).where(
                AccessToken.token_hash == token_hash(authorization[7:]), AccessToken.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if not token or token.expires_at.replace(tzinfo=token.expires_at.tzinfo or utcnow().tzinfo) <= utcnow():
        return oauth_error("invalid_token", "Access token is invalid", 401)
    result = {"sub": str(token.user_id)}
    scopes = set(token.scope.split())
    result.update(await scoped_identity_claims(db, token.user_id, scopes))
    return result
