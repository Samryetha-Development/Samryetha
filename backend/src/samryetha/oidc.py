"""OIDC authorization-code + PKCE integration.

OIDC proves identity; Samryetha still issues its own server-side session cookie.
Login transactions and tokens never live in browser storage.
"""

from __future__ import annotations

import base64
import hashlib
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
from .schema import oidc_identities, oidc_login_transactions, users
from .security import create_session, hash_password, hash_token
from .users import FAKE_EMAIL_DOMAIN, next_discriminator, to_dto

OIDC_TRANSACTION_COOKIE = "samryetha_oidc_state"
TRANSACTION_TTL_MS = 10 * 60 * 1000
_USERNAME_CHARS = re.compile(r"[^a-z0-9_]+")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def safe_return_to(value: str | None) -> str:
    """Allow only an application-local absolute path."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


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
            return_to=safe_return_to(return_to),
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

    def authorization_url(self, state: str, nonce: str, challenge: str) -> str:
        params = {
            "client_id": self.settings.oidc_client_id or "",
            "redirect_uri": self.settings.oidc_redirect_uri or "",
            "response_type": "code",
            "scope": "openid profile email groups",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
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
) -> dict:
    issuer = claims.get("iss")
    subject = claims.get("sub")
    if not isinstance(issuer, str) or not isinstance(subject, str) or not subject:
        raise invalid_credentials("OIDC identity is missing issuer or subject")

    groups = _claim_groups(claims)
    allowed = set(settings.oidc_allowed_group_list)
    if allowed and groups.isdisjoint(allowed):
        raise forbidden("Your identity provider account is not allowed to access Samryetha")

    identity = conn.execute(
        select(oidc_identities).where(
            and_(oidc_identities.c.issuer == issuer, oidc_identities.c.subject == subject)
        )
    ).first()
    _now = now_ms()
    email = claims.get("email")
    email = email.strip().lower() if isinstance(email, str) and email.strip() else None
    email_verified = claims.get("email_verified") is True

    if identity is not None:
        user_id = identity.user_id
        conn.execute(
            update(oidc_identities)
            .where(oidc_identities.c.id == identity.id)
            .values(last_login_at=_now)
        )
    else:
        user_id = None
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
                user_id = existing.id
        if user_id is None:
            username = _available_username(conn, claims, subject)
            usable_email = email if email and email_verified else None
            if usable_email and conn.execute(select(users.c.id).where(users.c.email == usable_email)).first():
                usable_email = None
            identity_key = issuer.encode() + b"\0" + subject.encode()
            usable_email = usable_email or f"oidc-{hashlib.sha256(identity_key).hexdigest()[:20]}@{FAKE_EMAIL_DOMAIN}"
            display_name = str(claims.get("name") or claims.get("preferred_username") or username)[:100]
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
                    email_verified_at=_now if email_verified else None,
                    bio="",
                    settings="{}",
                    created_at=_now,
                    updated_at=_now,
                )
            )
            user_id = result.inserted_primary_key[0]
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

    row_result = conn.execute(select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None)))).first()
    if row_result is None:
        raise forbidden("The linked Samryetha account is unavailable")
    row = dict(row_result._mapping)
    if row["status"] == "banned":
        raise banned()
    if row["status"] != "active":
        raise forbidden("This account is not active")
    if settings.oidc_admin_group in groups and row["role"] != "admin":
        conn.execute(update(users).where(users.c.id == user_id).values(role="admin", updated_at=_now))
        row["role"] = "admin"
    token, expires = create_session(
        conn,
        user_id,
        {"ip": ip, "user_agent": user_agent},
        ttl_ms=settings.session_ttl_ms,
    )
    return {"user": to_dto(row), "token": token, "expiresAt": expires}
