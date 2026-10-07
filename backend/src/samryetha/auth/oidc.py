"""OIDC authorization-code + PKCE integration.

OIDC proves identity; Samryetha still issues its own server-side session cookie.
Login transactions and tokens never live in browser storage.
"""

from __future__ import annotations

from samryetha.auth.oidc_repository import OidcRepository

from .sessions import SessionService

import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TypedDict
from urllib.parse import urlencode, urlparse

import httpx
from pydantic import TypeAdapter, ValidationError
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..core.config import Settings
from ..core.db import Database, now_ms
from ..core.errors import APIError, bad_request, banned, conflict, forbidden, invalid_credentials, service_unavailable
from .security import hash_password, hash_token, verify_against_dummy, verify_password
from ..users import FAKE_EMAIL_DOMAIN, UserService, to_dto
from ..core.ids import UserID
from ..core.records import require_str
from . import oidc_repository as repository

OIDC_TRANSACTION_COOKIE = "samryetha_oidc_state"
TRANSACTION_TTL_MS = 10 * 60 * 1000
_USERNAME_CHARS = re.compile(r"[^a-z0-9_]+")
# settings.role_source：admin 角色的授予来源。只有 IdP 授予的（"oidc"）才允许在
# 后续 IdP 登录缺失 admin 组时被降级；本地/后台授予的（"local" 或无标记）不动。
ROLE_SOURCE_KEY = "role_source"
ROLE_SOURCE_OIDC = "oidc"

logger = logging.getLogger("samryetha.auth.oidc")
_OBJECT_DICT = TypeAdapter(dict[str, object])


class OidcSessionResult(TypedDict):
    user: dict[str, object]
    token: str
    expiresAt: int


class ClaimRequiredResult(TypedDict):
    status: str
    ticket: str
    email: str | None


@dataclass(frozen=True, slots=True)
class CompletedOidcLogin:
    result: OidcSessionResult | ClaimRequiredResult
    return_to: str


class OidcLoginService:
    """Keep one-time state consumption committed before outbound IdP exchange."""

    def __init__(self, db: Database, settings: Settings, client: OidcClient) -> None:
        self._db = db
        self._settings = settings
        self._client = client

    def complete(self, code: str, state: str, *, ip: str | None, user_agent: str | None) -> CompletedOidcLogin:
        with self._db.request_conn() as conn:
            transaction = OidcService(conn).consume_login(state)
        claims = self._client.exchange_and_validate(code, transaction["code_verifier"], transaction["nonce"])
        with self._db.request_conn() as conn:
            result = OidcService(conn, self._settings).login_identity(
                claims, ip=ip, user_agent=user_agent, auto_create=False
            )
        return CompletedOidcLogin(result=result, return_to=transaction["return_to"])


class _Jwks(TypedDict):
    keys: list[dict[str, str | list[str]]]


_OBJECT_LIST = TypeAdapter(list[object])
_JWK_LIST = TypeAdapter(list[dict[str, str | list[str]]])


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


class OidcClient:
    """Small discovery/JWKS client with bounded in-process caching."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._discovery: dict[str, object] | None = None
        self._discovery_at = 0.0
        self._jwks: dict[str, object] | None = None
        self._jwks_at = 0.0

    def _get_json(self, url: str) -> dict[str, object]:
        try:
            response = httpx.get(url, timeout=10.0, follow_redirects=False)
            response.raise_for_status()
            payload = _OBJECT_DICT.validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as exc:
            raise service_unavailable("Identity provider is unavailable") from exc
        return payload

    def discovery(self) -> dict[str, object]:
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
                endpoint = require_str(document[field], field)
                if self.settings.is_production and urlparse(endpoint).scheme != "https":
                    raise service_unavailable("Identity provider endpoints must use HTTPS")
            self._discovery = document
            self._discovery_at = time.monotonic()
            return document

    def authorization_params(self, state: str, nonce: str, challenge: str, *, embedded: bool = False) -> dict[str, str]:
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
        endpoint = require_str(self.discovery()["authorization_endpoint"], "authorization_endpoint")
        return f"{endpoint}?{urlencode(params)}"

    def _get_jwks(self, *, force: bool = False) -> dict[str, object]:
        with self._lock:
            if not force and self._jwks is not None and time.monotonic() - self._jwks_at < 300:
                return self._jwks
            jwks = self._get_json(require_str(self.discovery()["jwks_uri"], "jwks_uri"))
            if not isinstance(jwks.get("keys"), list):
                raise service_unavailable("Identity provider JWKS is invalid")
            self._jwks = jwks
            self._jwks_at = time.monotonic()
            return jwks

    def _typed_jwks(self, *, force: bool = False) -> _Jwks:
        try:
            keys = _JWK_LIST.validate_python(self._get_jwks(force=force).get("keys"))
        except ValidationError as exc:
            raise service_unavailable("Identity provider JWKS is invalid") from exc
        return {"keys": keys}

    def exchange_and_validate(self, code: str, verifier: str, nonce: str) -> dict[str, object]:
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
            response = httpx.post(
                require_str(self.discovery()["token_endpoint"], "token_endpoint"), data=data, auth=auth, timeout=10.0
            )
        except httpx.RequestError as exc:
            raise service_unavailable("Identity provider is unavailable") from exc
        if response.status_code >= 500:
            raise service_unavailable("Identity provider is unavailable")
        if response.status_code >= 400:
            raise invalid_credentials("OIDC authorization code could not be exchanged")
        try:
            token_response = _OBJECT_DICT.validate_json(response.content)
        except ValidationError as exc:
            raise service_unavailable("Identity provider returned an invalid token response") from exc
        id_token = token_response.get("id_token")
        if not isinstance(id_token, str):
            raise invalid_credentials("Identity provider did not return an ID token")

        registry = JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "value": self.settings.oidc_issuer or ""},
            aud={"essential": True, "value": self.settings.oidc_client_id or ""},
            exp={"essential": True},
            sub={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        last_error: Exception | None = None
        for force in (False, True):
            try:
                key_set = KeySet.import_key_set(self._typed_jwks(force=force))
                token = jwt.decode(id_token, key_set, algorithms=["RS256", "PS256", "ES256"])
                registry.validate(token.claims)
                claims = _OBJECT_DICT.validate_python(token.claims)
                audience = claims.get("aud")
                authorized_party = claims.get("azp")
                audience_values = _OBJECT_LIST.validate_python(audience) if isinstance(audience, list) else []
                if len(audience_values) > 1 and authorized_party != self.settings.oidc_client_id:
                    raise ValueError("azp is required for an ID token with multiple audiences")
                if authorized_party is not None and authorized_party != self.settings.oidc_client_id:
                    raise ValueError("azp does not match the configured client")
                return claims
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


def _claim_groups(claims: dict[str, object]) -> set[str]:
    raw = claims.get("groups", [])
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {item for item in _OBJECT_LIST.validate_python(raw) if isinstance(item, str)}
    return set()


CLAIM_TTL_MS = 30 * 60 * 1000
CLAIM_MAX_ATTEMPTS = 5
DISPLAY_NAME_SOURCE_OIDC = "oidc"


def _claim_display_name(claims: dict[str, object]) -> str:
    return str(claims.get("name") or claims.get("preferred_username") or "")[:100]


def _read_settings(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = _OBJECT_DICT.validate_json(raw)
    except ValidationError:
        return {}
    return parsed


class OidcService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None, *, db: Database | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._db = db
        self._repository = OidcRepository(self._conn)

    def _require_settings(self) -> Settings:
        if self._settings is None:
            raise RuntimeError("OidcService operation requires settings")
        return self._settings

    def begin_login(self, return_to: str | None) -> tuple[str, str, str]:
        settings = self._require_settings()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        _now = now_ms()
        self._repository.insert_login_transaction(
            state_hash=hash_token(state),
            nonce=nonce,
            code_verifier=verifier,
            return_to=safe_return_to(return_to, settings),
            expires_at=_now + TRANSACTION_TTL_MS,
            created_at=_now,
        )
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return state, nonce, challenge

    def consume_login(self, state: str) -> repository.LoginTransactionRecord:
        state_hash = hash_token(state)
        row = self._repository.consume_login_transaction(state_hash, now=now_ms())
        if row is None:
            raise bad_request("OIDC login transaction is invalid or expired")
        return row

    def _available_username(self, claims: dict[str, object], subject: str) -> str:
        raw = claims.get("preferred_username") or claims.get("name") or f"oidc_{subject[:10]}"
        base = _USERNAME_CHARS.sub("_", str(raw).lower()).strip("_")[:24]
        if len(base) < 3:
            base = "user_" + hashlib.sha256(subject.encode()).hexdigest()[:10]
        candidate = base
        for suffix in range(1000):
            if suffix:
                candidate = f"{base[:24]}_{suffix}"
            if not self._repository.username_exists(candidate):
                return candidate
        raise service_unavailable("Could not allocate a local username")

    def login_identity(
        self, claims: dict[str, object], *, ip: str | None, user_agent: str | None, auto_create: bool = True
    ) -> OidcSessionResult | ClaimRequiredResult:
        settings = self._require_settings()
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

        user_id, identity_id = self._resolve_user_id(issuer, subject, email, email_verified)
        if user_id is None:
            if not auto_create:
                ticket = self.begin_claim(
                    issuer=issuer, subject=subject, email=email, display_name=_claim_display_name(claims)
                )
                return {"status": "claim_required", "ticket": ticket, "email": email}
            user_id = self._create_user(claims, issuer, subject, email, email_verified, groups, _now)
        elif identity_id is not None:
            self._repository.touch_identity(identity_id, now=_now)
        else:
            # 可信邮箱命中的老账号：把映射持久化（否则每次都要重新匹配，改邮箱即失联）
            try:
                self._repository.insert_identity(user_id=user_id, issuer=issuer, subject=subject, email=email, now=_now)
            except IntegrityError:
                # 并发同身份首登竞态：另一事务已建映射，本事务回滚转 409 提示重试登录。
                raise conflict("This identity is already linked — please sign in")
        return self._finish_login(user_id, claims, ip=ip, user_agent=user_agent)

    def _resolve_user_id(
        self, issuer: str, subject: str, email: str | None, email_verified: bool
    ) -> tuple[UserID | None, int | None]:
        """Return (user_id, oidc_identity_id); identity id is None for email matches."""
        identity = self._repository.identity(issuer, subject)
        if identity is not None:
            return identity.user_id, identity.id
        if email and email_verified:
            existing = self._repository.verified_email_user_id(email)
            if existing is not None:
                return existing, None
        return None, None

    def _create_user(
        self,
        claims: dict[str, object],
        issuer: str,
        subject: str,
        email: str | None,
        email_verified: bool,
        groups: set[str],
        now: int,
    ) -> UserID:
        settings = self._require_settings()
        username = self._available_username(claims, subject)
        usable_email = email if email and email_verified else None
        if usable_email and self._repository.email_exists(usable_email):
            usable_email = None
        identity_key = issuer.encode() + b"\0" + subject.encode()
        usable_email = usable_email or f"oidc-{hashlib.sha256(identity_key).hexdigest()[:20]}@{FAKE_EMAIL_DOMAIN}"
        display_name = _claim_display_name(claims) or username
        is_admin = settings.oidc_admin_group in groups
        account_settings = {"display_name_source": "oidc"}
        if is_admin:
            account_settings[ROLE_SOURCE_KEY] = ROLE_SOURCE_OIDC
        try:
            user_id = self._repository.insert_user(
                {
                    "username": username,
                    "email": usable_email,
                    "display_name": display_name,
                    "password_hash": hash_password(secrets.token_urlsafe(48)),
                    "role": "admin" if is_admin else "student",
                    "status": "active",
                    "discriminator": UserService(self._conn).next_discriminator(),
                    "email_domain": usable_email.rsplit("@", 1)[-1],
                    "email_verified_at": now if email_verified else None,
                    "bio": "",
                    "settings": json.dumps(account_settings, ensure_ascii=False),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        except IntegrityError as exc:
            # SELECT-then-INSERT 竞态（用户名/邮箱/鉴别号并发撞车）：唯一约束兜底转 409，
            # 调用方可重试（_available_username 下次会跳过已被占的候选）。
            raise conflict("Could not allocate a local account — please try again") from exc
        try:
            self._repository.insert_identity(user_id=user_id, issuer=issuer, subject=subject, email=email, now=now)
        except IntegrityError as exc:
            # 同一 (issuer, subject) 并发建号：另一事务已绑定，转 409 提示走登录重试。
            raise conflict("This identity is already linked — please sign in") from exc
        return user_id

    def _finish_login(
        self,
        user_id: UserID,
        claims: dict[str, object],
        *,
        ip: str | None,
        user_agent: str | None,
        sync_admin_role: bool = True,
    ) -> OidcSessionResult:
        settings = self._require_settings()
        _now = now_ms()
        row = self._repository.user(user_id)
        if row is None:
            raise forbidden("The linked Samryetha account is unavailable")
        if row["status"] == "banned":
            raise banned()
        if row["status"] != "active":
            raise forbidden("This account is not active")
        if sync_admin_role:
            # 每次 IdP 登录全量同步 admin 组成员关系：只升不降是旧行为，现双向同步。
            # 降级仅针对 IdP 授予的 admin（settings.role_source == "oidc"）；本地/后台
            # 授予的 admin 不带该标记，IdP 登录不应擅自剥夺。
            in_admin_group = settings.oidc_admin_group in _claim_groups(claims)
            if in_admin_group and row["role"] != "admin":
                new_settings = _read_settings(row["settings"])
                new_settings[ROLE_SOURCE_KEY] = ROLE_SOURCE_OIDC
                encoded = json.dumps(new_settings, ensure_ascii=False)
                self._repository.update_user(user_id, {"role": "admin", "settings": encoded, "updated_at": _now})
                row["role"] = "admin"
                row["settings"] = encoded
            elif (
                not in_admin_group
                and row["role"] == "admin"
                and _read_settings(row["settings"]).get(ROLE_SOURCE_KEY) == ROLE_SOURCE_OIDC
            ):
                if self._repository.has_other_active_admin(user_id):
                    self._repository.update_user(user_id, {"role": "student", "updated_at": _now})
                    row["role"] = "student"
                else:
                    # 最后一个 admin：保留权限并告警，避免全站锁死无人可管。
                    logger.warning("oidc demotion skipped: user_id=%s is the last admin", user_id)
        # 复用首行 row：maybe_sync 返回新展示名时内存更新，免第二次 SELECT。
        new_name = self.maybe_sync_display_name(user_id, claims)
        if new_name is not None:
            row["display_name"] = new_name
        token, expires = SessionService(self._conn).create_session(
            user_id, {"ip": ip, "user_agent": user_agent}, ttl_ms=settings.session_ttl_ms
        )
        return {"user": to_dto(row).model_dump(by_alias=True), "token": token, "expiresAt": expires}

    def maybe_sync_display_name(self, user_id: UserID, claims: dict[str, object]) -> str | None:
        """Follow the IdP display name only for accounts that never renamed locally.

        OIDC-provisioned accounts carry settings.display_name_source="oidc"; the
        flag is cleared the moment the user edits their display name, after which
        the local value wins permanently. Returns the new name when updated
        (so callers can reuse their in-memory row instead of re-SELECTing).
        """
        name = _claim_display_name(claims).strip()
        if not name:
            return None
        row = self._repository.display_profile(user_id)
        if row is None:
            return None
        settings_now = _read_settings(row.settings)
        if settings_now.get("display_name_source") != DISPLAY_NAME_SOURCE_OIDC:
            return None
        if row.display_name == name:
            return None
        self._repository.update_user(user_id, {"display_name": name, "updated_at": now_ms()})
        return name

    def begin_claim(self, *, issuer: str, subject: str, email: str | None, display_name: str) -> str:
        """Mint a one-time ticket binding an OIDC identity to a future password proof."""
        raw = secrets.token_urlsafe(32)
        now = now_ms()
        self._repository.insert_claim_ticket(
            ticket_hash=hash_token(raw),
            issuer=issuer,
            subject=subject,
            email=email,
            display_name=display_name,
            expires_at=now + CLAIM_TTL_MS,
            created_at=now,
        )
        return raw

    def peek_claim(self, ticket: str) -> dict[str, object] | None:
        """Read a claim ticket without consuming it (for rendering the claim page)."""
        row = self._repository.claim_ticket(hash_token(ticket))
        if row is None or row.expires_at <= now_ms():
            return None
        return {"email": row.email, "display_name": row.display_name, "expiresAt": row.expires_at}

    def _bump_claim_attempts(self, ticket_id: int, attempts: int) -> None:
        if attempts + 1 >= CLAIM_MAX_ATTEMPTS:
            self._repository.delete_claim_ticket(ticket_id)
        else:
            self._repository.update_claim_attempts(ticket_id, attempts + 1)

    def bump_claim_attempts(self, ticket: str) -> None:
        """Persist a failed password proof in its own transaction.

        Must be called from a fresh request_conn AFTER the claiming transaction
        rolled back: raising inside the claim transaction would roll the bump
        back with it, making lockout unenforceable.
        """
        row = self._repository.claim_ticket(hash_token(ticket))
        if row is None:
            return
        self._bump_claim_attempts(row.id, row.attempts)

    def claim_account_with_attempts(
        self, *, ticket: str, username: str, password: str, ip: str | None, user_agent: str | None
    ) -> OidcSessionResult:
        """Commit failed password attempts independently of the caller's rollback."""
        if self._db is None:
            raise RuntimeError("Claim attempt accounting requires Database")
        try:
            return self.claim_account(ticket=ticket, username=username, password=password, ip=ip, user_agent=user_agent)
        except APIError as exc:
            if exc.code == "INVALID_CREDENTIALS":
                with self._db.request_conn() as bump_conn:
                    OidcService(bump_conn).bump_claim_attempts(ticket)
            raise

    def claim_account(
        self, *, ticket: str, username: str, password: str, ip: str | None, user_agent: str | None
    ) -> OidcSessionResult:
        """Bind the ticket's OIDC identity to an existing account after a password proof."""
        self._require_settings()
        row = self._repository.claim_ticket(hash_token(ticket))
        if row is None or row.expires_at <= now_ms() or row.attempts >= CLAIM_MAX_ATTEMPTS:
            if row is not None:
                self._repository.delete_claim_ticket(row.id)
            raise bad_request("This claim link is invalid or has expired")
        user = UserService(self._conn).get_by_username(username.strip())
        if user is None:
            # 防枚举时序：不存在的账号也跑一次 dummy 校验（与 auth.login 同一口径）。
            verify_against_dummy(password)
            raise invalid_credentials("Invalid username or password")
        if not verify_password(password, user["password_hash"]):
            raise invalid_credentials("Invalid username or password")
        # Same identity claimed twice (e.g. double submit): just finish the login.
        already = self._repository.identity(row.issuer, row.subject)
        if already is None:
            try:
                self._repository.insert_identity(
                    user_id=UserID(user["id"]), issuer=row.issuer, subject=row.subject, email=row.email, now=now_ms()
                )
            except IntegrityError as exc:
                # 并发双提交：另一请求已先绑定同一身份，视为已绑定→提示走登录（409）。
                # SQLite 下写串行化，先提交者赢，后者落到这里。
                raise conflict("This identity is already linked — please sign in") from exc
            # 绑定成功即作废旧密码：该账号以后只走 OAuth，密码链路不再可用。
            # 已绑定分支跳过轮换：密码早已作废，无需重复执行。
            self._repository.update_user(
                UserID(user["id"]), {"password_hash": hash_password(secrets.token_urlsafe(48)), "updated_at": now_ms()}
            )
        self._repository.delete_claim_ticket(row.id)
        # 认领流程没有 IdP claims（空 dict 无 group 信号），跳过 admin 同步以免误降级。
        return self._finish_login(UserID(user["id"]), {}, ip=ip, user_agent=user_agent, sync_admin_role=False)

    def claim_create_account(self, *, ticket: str, ip: str | None, user_agent: str | None) -> OidcSessionResult:
        """Consume a claim ticket by creating a brand-new account (for users with no existing one)."""
        self._require_settings()
        row = self._repository.claim_ticket(hash_token(ticket))
        # 无密码证明可试：attempts 在此路径恒为 0（只在 claim 口令失败时累加），仍做同口径检查，
        # 防未来复用票据类型时出现无上限票据。
        if row is None or row.expires_at <= now_ms() or row.attempts >= CLAIM_MAX_ATTEMPTS:
            if row is not None:
                self._repository.delete_claim_ticket(row.id)
            raise bad_request("This claim link is invalid or has expired")
        email_prefix = row.email.split("@")[0] if row.email else ""
        pseudo_claims: dict[str, object] = {
            "preferred_username": email_prefix or None,
            "name": row.display_name,
        }
        user_id = self._create_user(pseudo_claims, row.issuer, row.subject, None, False, set(), now_ms())
        self._repository.delete_claim_ticket(row.id)
        # 同 claim_account：无 group 信号，跳过 admin 同步。
        return self._finish_login(user_id, pseudo_claims, ip=ip, user_agent=user_agent, sync_admin_role=False)
