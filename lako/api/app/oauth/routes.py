import base64
import hmac
from datetime import timedelta
from urllib.parse import urlencode

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
from app.sessions.dependencies import SESSION_COOKIE

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
    }


@router.get("/.well-known/jwks.json")
async def jwks() -> dict:
    return {"keys": [get_signing_keys().jwk]}


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
    db: AsyncSession = Depends(get_db),
):
    client = await valid_client(db, client_id, redirect_uri)
    if not client:
        return oauth_error("invalid_request", "Invalid client or redirect_uri")
    if response_type != "code" or not code_challenge or code_challenge_method != "S256":
        query = urlencode(
            {
                "error": "invalid_request",
                "error_description": "Authorization Code with PKCE S256 is required",
                "state": state,
            }
        )
        return RedirectResponse(f"{redirect_uri}?{query}", status_code=302)
    scopes = set(scope.split())
    if "openid" not in scopes or not scopes.issubset(SUPPORTED_SCOPES):
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'error': 'invalid_scope', 'state': state})}", status_code=302
        )
    raw_session = request.cookies.get(SESSION_COOKIE)
    auth_session = None
    if raw_session:
        auth_session = (
            await db.execute(
                select(Session).where(Session.token_hash == token_hash(raw_session), Session.revoked_at.is_(None))
            )
        ).scalar_one_or_none()
        if (
            auth_session
            and auth_session.expires_at.replace(tzinfo=auth_session.expires_at.tzinfo or utcnow().tzinfo) <= utcnow()
        ):
            auth_session = None
    if not auth_session:
        return_to = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?{urlencode({'return_to': return_to})}", status_code=302)
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
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302)


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
