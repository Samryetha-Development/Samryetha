"""OIDC authorization-code + PKCE integration.

OIDC proves identity; Samryetha still issues its own server-side session cookie.
Login transactions and tokens never live in browser storage.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Connection

from .config import Settings
from .db import now_ms
from .errors import bad_request, banned, forbidden, invalid_credentials, service_unavailable
from .schema import oidc_claim_tickets, oidc_identities, oidc_login_transactions, users
from .security import create_session, hash_password, hash_token, verify_password
from .users import FAKE_EMAIL_DOMAIN, get_by_username, next_discriminator, to_dto

OIDC_TRANSACTION_COOKIE = "samryetha_oidc_state"
TRANSACTION_TTL_MS = 10 * 60 * 1000
_USERNAME_CHARS = re.compile(r"[^a-z0-9_]+")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def safe_return_to(value: str | None, settings: Settings) -> str:
    """只放行站内绝对路径，外加 SIGNIN_RETURN_ORIGINS 里精确匹配的站外 origin。

    站外跳转是给兄弟站点用的：登录入口统一走 Lako，签完要能回到自己的
    域名。规则刻意收紧，任何一条不满足都回落 "/"：

    - 拒绝空值、反斜杠（`/\\evil.com` 这类绕过）、以及 CR/LF/Tab（可用来拆响应头）
    - `/` 开头且非 `//` → 站内路径，放行（`//evil.com` 是协议相对 URL，必须拦）
    - 其余必须是 http/https 绝对 URL，且它的 origin 与白名单**逐字符相等**
      ——故意不做后缀匹配，`https://tasks.samryetha.com.evil.com` 必须被拒
    """
    if not value or "\\" in value or any(ch in value for ch in "\r\n\t"):
        return "/"
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "/"
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return value if origin in settings.signin_return_origin_list else "/"


def resolve_return_to(settings: Settings, return_to: str) -> str:
    """把 safe_return_to 的结果变成可跳转的绝对 URL。

    站内路径拼 app_origin；白名单站外 origin 原样返回（它本身已是绝对 URL）。
    两者不能混着拼，否则会得到 `https://samryetha.comhttps://...` 这种畸形地址。
    """
    if return_to.startswith("/") and not return_to.startswith("//"):
        return settings.app_origin.rstrip("/") + return_to
    return return_to


def begin_login(conn: Connection, settings: Settings, return_to: str | None) -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    _now = now_ms()
    conn.execute(delete(oidc_login_transactions).where(oidc_login_transactions.c.expires_at <= _now))
    conn.execute(
        insert(oidc_login_transactions).values(
            state_hash=hash_token(state),
            nonce=nonce,
            code_verifier=verifier,
            return_to=safe_return_to(return_to, settings),
            expires_at=_now + TRANSACTION_TTL_MS,
            created_at=_now,
        )
    )
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return state, nonce, challenge


def consume_login(conn: Connection, state: str) -> dict:
    state_hash = hash_token(state)
    row = conn.execute(
        select(oidc_login_transactions).where(
            and_(
                oidc_login_transactions.c.state_hash == state_hash,
                oidc_login_transactions.c.expires_at > now_ms(),
            )
        )
    ).first()
    if row is None:
        raise bad_request("OIDC login transaction is invalid or expired")
    conn.execute(delete(oidc_login_transactions).where(oidc_login_transactions.c.state_hash == state_hash))
    return dict(row._mapping)


class OidcClient:
    """Small discovery/JWKS client with bounded in-process caching."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._discovery: dict | None = None
        self._discovery_at = 0.0
        self._jwks: dict | None = None
        self._jwks_at = 0.0

    def _get_json(self, url: str) -> dict:
        try:
            response = httpx.get(url, timeout=10.0, follow_redirects=False)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise service_unavailable("Identity provider is unavailable") from exc
        if not isinstance(payload, dict):
            raise service_unavailable("Identity provider returned an invalid response")
        return payload

    def discovery(self) -> dict:
        with self._lock:
            if self._discovery is not None and time.monotonic() - self._discovery_at < 300:
                return self._discovery
            issuer = self.settings.oidc_issuer or ""
            document = self._get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
            if document.get("issuer") != issuer:
                raise service_unavailable("Identity provider issuer does not match configuration")
            for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not isinstance(document.get(field), str):
                    raise service_unavailable("Identity provider discovery document is incomplete")
                if self.settings.is_production and urlparse(document[field]).scheme != "https":
                    raise service_unavailable("Identity provider endpoints must use HTTPS")
            self._discovery = document
            self._discovery_at = time.monotonic()
            return document

    def authorization_params(self, state: str, nonce: str, challenge: str, *, embedded: bool = False) -> dict:
        """授权请求的参数。重定向流拿去拼 URL，嵌入流直接交给调用方。"""
        params = {
            "client_id": self.settings.oidc_client_id or "",
            "redirect_uri": self.settings.oidc_redirect_uri or "",
            "response_type": "code",
            "scope": "openid profile email groups",
            "prompt": "select_account",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if embedded:
            params["display"] = "popup"
        return params

    def authorization_url(self, state: str, nonce: str, challenge: str, *, embedded: bool = False) -> str:
        params = self.authorization_params(state, nonce, challenge, embedded=embedded)
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}"

    def _get_jwks(self, *, force: bool = False) -> dict:
        with self._lock:
            if not force and self._jwks is not None and time.monotonic() - self._jwks_at < 300:
                return self._jwks
            jwks = self._get_json(self.discovery()["jwks_uri"])
            if not isinstance(jwks.get("keys"), list):
                raise service_unavailable("Identity provider JWKS is invalid")
            self._jwks = jwks
            self._jwks_at = time.monotonic()
            return jwks

    def exchange_and_validate(self, code: str, verifier: str, nonce: str) -> dict:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.settings.oidc_redirect_uri or "",
            "client_id": self.settings.oidc_client_id or "",
            "code_verifier": verifier,
        }
        auth = None
        if self.settings.oidc_client_secret:
            auth = httpx.BasicAuth(self.settings.oidc_client_id or "", self.settings.oidc_client_secret)
        try:
            response = httpx.post(self.discovery()["token_endpoint"], data=data, auth=auth, timeout=10.0)
        except httpx.RequestError as exc:
            raise service_unavailable("Identity provider is unavailable") from exc
        if response.status_code >= 500:
            raise service_unavailable("Identity provider is unavailable")
        if response.status_code >= 400:
            raise invalid_credentials("OIDC authorization code could not be exchanged")
        try:
            token_response = response.json()
        except ValueError as exc:
            raise service_unavailable("Identity provider returned an invalid token response") from exc
        id_token = token_response.get("id_token") if isinstance(token_response, dict) else None
        if not isinstance(id_token, str):
            raise invalid_credentials("Identity provider did not return an ID token")

        registry = JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": self.settings.oidc_issuer},
            aud={"essential": True, "value": self.settings.oidc_client_id},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        last_error: Exception | None = None
        for force in (False, True):
            try:
                key_set = KeySet.import_key_set(self._get_jwks(force=force))
                token = jwt.decode(id_token, key_set, algorithms=["RS256", "PS256", "ES256"])
                registry.validate(token.claims)
                audience = token.claims.get("aud")
                authorized_party = token.claims.get("azp")
                if isinstance(audience, list) and len(audience) > 1 and authorized_party != self.settings.oidc_client_id:
                    raise ValueError("azp is required for an ID token with multiple audiences")
                if authorized_party is not None and authorized_party != self.settings.oidc_client_id:
                    raise ValueError("azp does not match the configured client")
                return dict(token.claims)
            except (JoseError, ValueError, TypeError) as exc:
                last_error = exc
        raise invalid_credentials("OIDC ID token validation failed") from last_error

    def end_session_url(self) -> str | None:
        endpoint = self.discovery().get("end_session_endpoint")
        if not isinstance(endpoint, str):
            return None
        redirect_uri = self.settings.oidc_post_logout_redirect_uri
        params = {"client_id": self.settings.oidc_client_id or ""}
        if redirect_uri:
            params["post_logout_redirect_uri"] = redirect_uri
        return f"{endpoint}?{urlencode(params)}"


def _claim_groups(claims: dict) -> set[str]:
    raw = claims.get("groups", [])
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str)}
    return set()


def _available_username(conn: Connection, claims: dict, subject: str) -> str:
    raw = claims.get("preferred_username") or claims.get("name") or f"oidc_{subject[:10]}"
    base = _USERNAME_CHARS.sub("_", str(raw).lower()).strip("_")[:24]
    if len(base) < 3:
        base = "user_" + hashlib.sha256(subject.encode()).hexdigest()[:10]
    candidate = base
    for suffix in range(1000):
        if suffix:
            candidate = f"{base[:24]}_{suffix}"
        if conn.execute(select(users.c.id).where(users.c.username == candidate)).first() is None:
            return candidate
    raise service_unavailable("Could not allocate a local username")


def login_identity(
    conn: Connection,
    claims: dict,
    settings: Settings,
    *,
    ip: str | None,
    user_agent: str | None,
    auto_create: bool = True,
) -> dict:
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or not isinstance(subject, str) or not subject:
        raise invalid_credentials("OIDC identity is missing issuer or subject")

    groups = _claim_groups(claims)
    allowed = set(settings.oidc_allowed_group_list)
    if allowed and groups.isdisjoint(allowed):
        raise forbidden("Your identity provider account is not allowed to access Samryetha")

    _now = now_ms()
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    email_verified = claims.get("email_verified") is True

    user_id, identity_id = _resolve_user_id(conn, issuer, subject, email, email_verified)
    if user_id is None:
        if not auto_create:
            ticket = begin_claim(
                conn,
                issuer=issuer,
                subject=subject,
                email=email,
                display_name=_claim_display_name(claims),
            )
            return {"status": "claim_required", "ticket": ticket, "email": email}
        user_id = _create_user(conn, claims, settings, issuer, subject, email, email_verified, groups, _now)
    elif identity_id is not None:
        conn.execute(
            update(oidc_identities).where(oidc_identities.c.id == identity_id).values(last_login_at=_now)
        )
    else:
        # 可信邮箱命中的老账号：把映射持久化（否则每次都要重新匹配，改邮箱即失联）
        conn.execute(
            insert(oidc_identities).values(
                user_id=user_id,
                issuer=issuer,
                subject=subject,
                email_at_link=email,
                created_at=_now,
                last_login_at=_now,
            )
        )
    result = _finish_login(conn, user_id, settings, claims, ip=ip, user_agent=user_agent)
    result["status"] = "ok"
    return result


def _resolve_user_id(
    conn: Connection, issuer: str, subject: str, email: str | None, email_verified: bool
) -> tuple[int | None, int | None]:
    """Return (user_id, oidc_identity_id); identity id is None for email matches."""
    identity = conn.execute(
        select(oidc_identities).where(
            and_(oidc_identities.c.issuer == issuer, oidc_identities.c.subject == subject)
        )
    ).first()
    if identity is not None:
        return identity.user_id, identity.id
    if email and email_verified:
        existing = conn.execute(
            select(users.c.id).where(
                and_(
                    users.c.email == email,
                    users.c.email_verified_at.is_not(None),
                    users.c.deleted_at.is_(None),
                )
            )
        ).first()
        if existing is not None:
            return existing.id, None
    return None, None


def _create_user(
    conn: Connection,
    claims: dict,
    settings: Settings,
    issuer: str,
    subject: str,
    email: str | None,
    email_verified: bool,
    groups: set,
    now: int,
) -> int:
    username = _available_username(conn, claims, subject)
    usable_email = email if email and email_verified else None
    if usable_email and conn.execute(select(users.c.id).where(users.c.email == usable_email)).first():
        usable_email = None
    identity_key = issuer.encode() + b"\0" + subject.encode()
    usable_email = usable_email or f"oidc-{hashlib.sha256(identity_key).hexdigest()[:20]}@{FAKE_EMAIL_DOMAIN}"
    display_name = _claim_display_name(claims) or username
    result = conn.execute(
        insert(users).values(
            username=username,
            email=usable_email,
            display_name=display_name,
            password_hash=hash_password(secrets.token_urlsafe(48)),
            role="admin" if settings.oidc_admin_group in groups else "student",
            status="active",
            discriminator=next_discriminator(conn),
            email_domain=usable_email.rsplit("@", 1)[-1],
            email_verified_at=now if email_verified else None,
            bio="",
            settings=json.dumps({"display_name_source": "oidc"}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
    )
    user_id = result.inserted_primary_key[0]
    conn.execute(
        insert(oidc_identities).values(
            user_id=user_id,
            issuer=issuer,
            subject=subject,
            email_at_link=email,
            created_at=now,
            last_login_at=now,
        )
    )
    return user_id


def _finish_login(
    conn: Connection, user_id: int, settings: Settings, claims: dict, *, ip: str | None, user_agent: str | None
) -> dict:
    _now = now_ms()
    row_result = conn.execute(select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None)))).first()
    if row_result is None:
        raise forbidden("The linked Samryetha account is unavailable")
    row = dict(row_result._mapping)
    if row["status"] == "banned":
        raise banned()
    if row["status"] != "active":
        raise forbidden("This account is not active")
    if settings.oidc_admin_group in _claim_groups(claims) and row["role"] != "admin":
        conn.execute(update(users).where(users.c.id == user_id).values(role="admin", updated_at=_now))
        row["role"] = "admin"
    maybe_sync_display_name(conn, user_id, claims)
    row = dict(conn.execute(select(users).where(users.c.id == user_id)).first()._mapping)
    token, expires = create_session(
        conn,
        user_id,
        {"ip": ip, "user_agent": user_agent},
        ttl_ms=settings.session_ttl_ms,
    )
    return {"user": to_dto(row), "token": token, "expiresAt": expires}


CLAIM_TTL_MS = 30 * 60 * 1000
CLAIM_MAX_ATTEMPTS = 5
DISPLAY_NAME_SOURCE_OIDC = "oidc"


def _claim_display_name(claims: dict) -> str:
    return str(claims.get("name") or claims.get("preferred_username") or "")[:100]


def _read_settings(raw: object) -> dict:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def maybe_sync_display_name(conn: Connection, user_id: int, claims: dict) -> None:
    """Follow the IdP display name only for accounts that never renamed locally.

    OIDC-provisioned accounts carry settings.display_name_source="oidc"; the
    flag is cleared the moment the user edits their display name, after which
    the local value wins permanently.
    """
    name = _claim_display_name(claims).strip()
    if not name:
        return
    row = conn.execute(select(users.c.display_name, users.c.settings).where(users.c.id == user_id)).first()
    if row is None:
        return
    settings_now = _read_settings(row.settings)
    if settings_now.get("display_name_source") != DISPLAY_NAME_SOURCE_OIDC:
        return
    if row.display_name == name:
        return
    conn.execute(
        update(users).where(users.c.id == user_id).values(display_name=name, updated_at=now_ms())
    )


def begin_claim(conn: Connection, *, issuer: str, subject: str, email: str | None, display_name: str) -> str:
    """Mint a one-time ticket binding an OIDC identity to a future password proof."""
    raw = secrets.token_urlsafe(32)
    now = now_ms()
    conn.execute(
        insert(oidc_claim_tickets).values(
            ticket_hash=hash_token(raw),
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            attempts=0,
            expires_at=now + CLAIM_TTL_MS,
            created_at=now,
        )
    )
    return raw


def peek_claim(conn: Connection, ticket: str) -> dict | None:
    """Read a claim ticket without consuming it (for rendering the claim page)."""
    row = (
        conn.execute(select(oidc_claim_tickets).where(oidc_claim_tickets.c.ticket_hash == hash_token(ticket)))
        .first()
    )
    if row is None or row.expires_at <= now_ms():
        return None
    return {"email": row.email, "display_name": row.display_name, "expiresAt": row.expires_at}


def _bump_claim_attempts(conn: Connection, ticket_id: int, attempts: int) -> None:
    if attempts + 1 >= CLAIM_MAX_ATTEMPTS:
        conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == ticket_id))
    else:
        conn.execute(
            update(oidc_claim_tickets).where(oidc_claim_tickets.c.id == ticket_id).values(attempts=attempts + 1)
        )


def bump_claim_attempts(conn: Connection, ticket: str) -> None:
    """Persist a failed password proof in its own transaction.

    Must be called from a fresh request_conn AFTER the claiming transaction
    rolled back: raising inside the claim transaction would roll the bump
    back with it, making lockout unenforceable.
    """
    row = (
        conn.execute(select(oidc_claim_tickets).where(oidc_claim_tickets.c.ticket_hash == hash_token(ticket)))
        .first()
    )
    if row is None:
        return
    _bump_claim_attempts(conn, row.id, row.attempts)


def claim_account(
    conn: Connection,
    *,
    ticket: str,
    username: str,
    password: str,
    settings: Settings,
    ip: str | None,
    user_agent: str | None,
) -> dict:
    """Bind the ticket's OIDC identity to an existing account after a password proof."""
    row = (
        conn.execute(select(oidc_claim_tickets).where(oidc_claim_tickets.c.ticket_hash == hash_token(ticket)))
        .first()
    )
    if row is None or row.expires_at <= now_ms() or row.attempts >= CLAIM_MAX_ATTEMPTS:
        if row is not None:
            conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == row.id))
        raise bad_request("This claim link is invalid or has expired")
    user = get_by_username(conn, username.strip())
    if user is None or not verify_password(password, user["password_hash"]):
        raise invalid_credentials("Invalid username or password")
    # Same identity claimed twice (e.g. double submit): just finish the login.
    already = conn.execute(
        select(oidc_identities).where(
            and_(oidc_identities.c.issuer == row.issuer, oidc_identities.c.subject == row.subject)
        )
    ).first()
    if already is None:
        conn.execute(
            insert(oidc_identities).values(
                user_id=user["id"],
                issuer=row.issuer,
                subject=row.subject,
                email_at_link=row.email,
                created_at=now_ms(),
                last_login_at=now_ms(),
            )
        )
    # 绑定成功即作废旧密码：该账号以后只走 OAuth，密码链路不再可用
    conn.execute(
        update(users)
        .where(users.c.id == user["id"])
        .values(password_hash=hash_password(secrets.token_urlsafe(48)), updated_at=now_ms())
    )
    conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == row.id))
    return _finish_login(conn, user["id"], settings, {}, ip=ip, user_agent=user_agent)


def claim_create_account(
    conn: Connection, *, ticket: str, settings: Settings, ip: str | None, user_agent: str | None
) -> dict:
    """Consume a claim ticket by creating a brand-new account (for users with no existing one)."""
    row = (
        conn.execute(select(oidc_claim_tickets).where(oidc_claim_tickets.c.ticket_hash == hash_token(ticket)))
        .first()
    )
    if row is None or row.expires_at <= now_ms():
        if row is not None:
            conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == row.id))
        raise bad_request("This claim link is invalid or has expired")
    pseudo_claims = {"preferred_username": (row.email or "").split("@")[0] or None, "name": row.display_name}
    user_id = _create_user(
        conn, pseudo_claims, settings, row.issuer, row.subject, None, False, set(), now_ms()
    )
    conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == row.id))
    return _finish_login(conn, user_id, settings, pseudo_claims, ip=ip, user_agent=user_agent)
