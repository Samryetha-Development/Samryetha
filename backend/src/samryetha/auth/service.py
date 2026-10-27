"""Auth service — 镜像 backend/src/auth/service.ts + bootstrap.ts。

注册建 pending 用户；登录发会话；启动时 ensure_builtin admin/dev。
密码用 argon2id（m=19456,t=2,p=1），存量哈希直接可验。
"""

from __future__ import annotations

from samryetha.auth.repository import AuthRepository

from .sessions import SessionService

import logging
import hmac
import secrets
from typing import TypedDict

from sqlalchemy import select, delete, insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from ..core.config import Settings, PROD_FORBIDDEN_DEFAULTS
from ..core.db import now_ms
from ..core.ids import UserID
from ..core.errors import (
    banned,
    conflict,
    forbidden,
    invalid_credentials,
    token_invalid,
)
from ..moderation.service import ModerationService
from .security import (
    hash_password,
    verify_against_dummy,
    verify_password,
)
from ..users import FAKE_EMAIL_DOMAIN, is_reserved_username, normalize_username, UserService, to_dto
from .security import hash_token
from ..adapters.mailer import Mailer, password_reset_email
from ..events.outbox import OutboxWriter
from ..notifications.models import PasswordResetRequestedPayload
from .qr_login import QrAuthService
from ..core.schema import recovery_keys, qr_login_tickets

logger = logging.getLogger("samryetha.auth")


class AuthLoginResult(TypedDict):
    user: dict[str, object]
    token: str
    expiresAt: int


# ---------------------------------------------------------------- register


# ---------------------------------------------------------------- login


# ---------------------------------------------------------------- session mgmt


# ---------------------------------------------------------------- password reset

RESET_MESSAGE = "If the account and recovery email match, a reset link has been sent."
RESET_INVALID_MESSAGE = "Reset link is invalid or expired"
RESET_TTL_MS = 60 * 60 * 1000


# ---------------------------------------------------------------- bootstrap


class AuthService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None, mailer: Mailer | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._mailer = mailer
        self._repository = AuthRepository(self._conn)
        self._outbox = OutboxWriter(self._conn)

    def _require_settings(self) -> Settings:
        if self._settings is None:
            raise RuntimeError("AuthService operation requires settings")
        return self._settings

    def register(self, username: str, password: str) -> int:
        wanted = normalize_username(username)
        if is_reserved_username(wanted):
            raise conflict("That username is reserved")
        if self._repository.username_exists(wanted):
            raise conflict("That username is already taken")
        password_hash = hash_password(password)
        try:
            return UserService(self._conn).register_user_row(wanted, wanted, password_hash)
        except IntegrityError as exc:
            # SELECT-then-INSERT 竞态：并发双注册同时通过存在性检查，唯一约束兜底转 409。
            # SQLite 下写串行化，先提交者赢，后者落到这里（与串行路径同一口径）。
            raise conflict("That username is already taken") from exc

    def login(self, identifier: str, password: str, *, ip: str | None, user_agent: str | None) -> AuthLoginResult:
        session_ttl_ms = self._require_settings().session_ttl_ms
        wanted = normalize_username(identifier)
        row = self._repository.auth_user(wanted)
        if row is None:
            # 防枚举时序：对不存在的账号也跑一次 dummy 校验
            verify_against_dummy(secrets.token_urlsafe(16))
            raise invalid_credentials()
        # 有意为之（UX 优先）：先验密码、再判状态。封禁/待审账号输错密码时同样报
        # invalid_credentials，不泄露"账号存在但状态异常"——与时序防护目标一致。
        if not verify_password(password, row["password_hash"]):
            raise invalid_credentials()
        if row["status"] == "banned":
            # 临时封禁到期 → 自动解封继续登录；否则维持封禁
            if not ModerationService(self._conn).lift_ban_if_expired(row["id"]):
                raise banned()
            row["status"] = "active"
        if row["status"] == "pending":
            raise forbidden("Your account is awaiting admin approval")
        if row["status"] != "active":
            raise forbidden("This account is not active")

        token, expires = SessionService(self._conn).create_session(
            row["id"], {"ip": ip, "user_agent": user_agent}, ttl_ms=session_ttl_ms
        )
        return {"user": to_dto(row).model_dump(by_alias=True), "token": token, "expiresAt": expires}

    def logout(self, token: str) -> None:
        if token:
            SessionService(self._conn).delete_session(token)

    def exchange_qr_session(
        self, ticket_id: str, secret: str, *, ip: str | None, user_agent: str | None
    ) -> AuthLoginResult:
        settings = self._require_settings()
        user_id = QrAuthService(self._conn).exchange_ticket(ticket_id, secret)
        user = UserService(self._conn).get_by_id(user_id)
        if user is None:
            raise forbidden("The approving account is unavailable")
        token, expires = SessionService(self._conn).create_session(
            user_id, {"ip": ip, "user_agent": user_agent}, ttl_ms=settings.session_ttl_ms
        )
        return {"user": to_dto(user).model_dump(by_alias=True), "token": token, "expiresAt": expires}

    def emergency_login(self, username: str, proof: str, *, ip: str | None, user_agent: str | None) -> AuthLoginResult:
        settings = self._require_settings()
        secret = settings.emergency_login_token
        if not secret or not hmac.compare_digest(proof, secret):
            raise forbidden("Emergency login is not available")
        user = UserService(self._conn).get_by_username(username)
        if user is None or user["role"] != "admin" or user["status"] != "active":
            raise forbidden("Emergency login is not available")
        token, expires = SessionService(self._conn).create_session(
            user["id"], {"ip": ip, "user_agent": user_agent}, ttl_ms=settings.session_ttl_ms
        )
        return {"user": to_dto(user).model_dump(by_alias=True), "token": token, "expiresAt": expires}

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        user = UserService(self._conn).get_by_id(user_id)
        if user is None:
            raise invalid_credentials()
        if not verify_password(current_password, user["password_hash"]):
            raise invalid_credentials("Current password is incorrect")
        new_hash = hash_password(new_password)
        self._repository.update_password(UserID(user_id), new_hash, updated_at=now_ms())
        self._revoke_recovery_access(UserID(user_id))

    def issue_recovery_key(self, user_id: int) -> str:
        """Caller must prove ownership. Store only a digest of 256 random bits."""
        key = secrets.token_urlsafe(32)
        self._conn.execute(delete(recovery_keys).where(recovery_keys.c.user_id == user_id))
        self._conn.execute(insert(recovery_keys).values(user_id=user_id, key_hash=hash_token(key), created_at=now_ms()))
        return key

    def rotate_recovery_key(self, user_id: int, current_password: str) -> str:
        user = UserService(self._conn).get_by_id(user_id)
        if user is None or not verify_password(current_password, user["password_hash"]):
            raise invalid_credentials()
        return self.issue_recovery_key(user_id)

    def recover_with_key(self, username: str, key: str, new_password: str) -> str:
        row = self._repository.auth_user(normalize_username(username))
        # Fixed-cost digest comparison; the key itself has 256 bits of entropy.
        saved = self._conn.execute(select(recovery_keys.c.key_hash).where(
            recovery_keys.c.user_id == (row["id"] if row else -1)
        )).scalar_one_or_none()
        digest = hash_token(key)
        if not hmac.compare_digest(saved or "0" * 64, digest) or row is None or row["status"] not in ("active", "pending"):
            raise token_invalid("Recovery credentials are invalid or unavailable")
        password_hash = hash_password(new_password)
        consumed = self._conn.execute(delete(recovery_keys).where(
            recovery_keys.c.user_id == row["id"], recovery_keys.c.key_hash == digest
        ))
        if consumed.rowcount != 1:
            raise token_invalid("Recovery credentials are invalid or unavailable")
        self._repository.update_password(UserID(row["id"]), password_hash, updated_at=now_ms())
        self._revoke_recovery_access(UserID(row["id"]))
        return self.issue_recovery_key(row["id"])

    def _revoke_recovery_access(self, user_id: UserID) -> None:
        SessionService(self._conn).delete_user_sessions(user_id)
        self._repository.invalidate_password_resets(user_id, used_at=now_ms())
        # An already approved QR ticket must not mint a new session after recovery.
        self._conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.approved_by == user_id))

    def forgot_password(self, username: str, recovery_email: str) -> None:
        settings = self._require_settings()
        app_origin = settings.app_origin
        wanted = normalize_username(username)
        email = recovery_email.strip().lower()
        row = self._repository.auth_user(wanted)
        if row is None or row["status"] not in ("active", "pending") or not row["recovery_email"] or row["recovery_email"].strip().lower() != email:
            return

        raw_token = secrets.token_urlsafe(32)
        token_hash = hash_token(raw_token)
        _now = now_ms()
        user_id = UserID(row["id"])
        self._repository.invalidate_password_resets(user_id, used_at=_now)
        self._repository.insert_password_reset(
            user_id=user_id, token_hash=token_hash, expires_at=_now + RESET_TTL_MS, created_at=_now
        )
        link = f"{app_origin}/reset-password?token={raw_token}"
        subject, text, html = password_reset_email(link=link, display_name=row["display_name"])
        # 发信移出写事务（F11）：令牌 + 邮件任务同事务原子落 outbox，由后台 worker 在事务外发送。
        # 慢 SMTP 不再占住全库写锁；发信失败也不回滚已发出的令牌（worker 重试/告警）。
        # Email delivery moves out of the write transaction (F11): the token and the email job are
        # committed atomically to the outbox, and a background worker sends it outside the request
        # transaction. A slow SMTP no longer holds the whole-database write lock, and a failed send
        # never rolls back the token (the worker retries/warns).
        self._outbox.emit(
            "password-reset.requested",
            aggregate_type="user",
            aggregate_id=str(int(user_id)),
            payload=PasswordResetRequestedPayload(
                user_id=user_id,
                to=row["recovery_email"],
                subject=subject,
                text=text,
                html=html,
            ),
        )

    def reset_password(self, token: str, new_password: str) -> str:
        token_hash = hash_token(token)
        row = self._repository.valid_password_reset(token_hash, now=now_ms())
        if row is None:
            raise token_invalid(RESET_INVALID_MESSAGE)
        user = UserService(self._conn).get_by_id(row.user_id)
        if user is None or user["status"] not in ("active", "pending") or user["deleted_at"] is not None:
            raise token_invalid(RESET_INVALID_MESSAGE)

        password_hash = hash_password(new_password)
        if not self._repository.consume_password_reset(row.id, now=now_ms()):
            raise token_invalid(RESET_INVALID_MESSAGE)
        self._repository.update_password(row.user_id, password_hash, updated_at=now_ms())
        self._revoke_recovery_access(row.user_id)
        return self.issue_recovery_key(row.user_id)

    def ensure_builtin_accounts(self) -> None:
        """幂等确保 admin / dev 存在，启动时调用。镜像 auth/bootstrap.ts。

        内建身份以不可变的 is_builtin 标记识别，而非可变用户名：同名冒名账号（注册/改名/
        OIDC 建号占用 admin/dev）绝不提升为管理员，只记录显著告警（F07）。
        """
        settings = self._require_settings()
        accounts = [
            ("admin", settings.admin_password),
            ("dev", settings.dev_password),
        ]
        for username, password in accounts:
            existing = self._repository.builtin_account(username)
            if existing is not None:
                user_id, is_builtin = existing
                if not is_builtin:
                    # 同名冒名账号：拒绝提升并显著告警（F07）。
                    logger.error(
                        "[SECURITY][F07] refusing to promote non-builtin account %r (user_id=%s) to admin",
                        username,
                        user_id,
                    )
                # Never promote/reactivate an existing name or overwrite its password.
                row = self._repository.auth_user(username)
                if settings.is_production and row and row["status"] == "active" and verify_password(
                    PROD_FORBIDDEN_DEFAULTS[f"{username.upper()}_PASSWORD"], row["password_hash"]
                ):
                    raise RuntimeError(f"Built-in account {username} still uses a published development password; rotate or deactivate it before production")
                continue
            if settings.is_production and username == "dev":
                continue
            password_hash = hash_password(password)
            disc = UserService(self._conn).next_discriminator()
            email = f"{username}@{FAKE_EMAIL_DOMAIN}"
            _now = now_ms()
            self._repository.insert_builtin(
                username=username,
                email=email,
                email_domain=FAKE_EMAIL_DOMAIN,
                password_hash=password_hash,
                discriminator=disc,
                now=_now,
            )
