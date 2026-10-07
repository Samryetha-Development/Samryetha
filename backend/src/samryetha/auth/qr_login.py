"""扫码登录：PC 展示二维码，手机确认后 PC 换会话。

流程：POST /qr/start 建票据（ticket_id 公开进二维码，secret 只留 PC 内存）
→ 手机扫码打开确认页（需登录态）批准/拒绝
→ PC 经 SSE 得知批准 → POST /qr/exchange(ticket_id + secret) 换 HttpOnly 会话。

安全要点：ticket_id 本身换不到会话（必须 secret）；单次有效；2 分钟 TTL；
确认页展示请求方 IP/UA/时间供核对；secret 全程不进 URL/日志。
"""

from __future__ import annotations

from samryetha.auth.qr_repository import QrAuthRepository

import base64
import hmac
import io
import logging
import secrets
from typing import TypedDict

import segno
from sqlalchemy.engine import Connection

from . import qr_repository
from ..core.db import now_ms
from ..core.errors import bad_request, forbidden
from .security import hash_token
from ..users import FAKE_EMAIL_DOMAIN, UserService
from ..adapters.mailer import Mailer, qr_signin_confirmation_email
from ..core.config import Settings
from ..users.models import UserRow
from ..core.ids import UserID
from .models import QrInfoResponse

DEFAULT_TTL_MS = 2 * 60 * 1000

# 邮箱确认码：比票据 TTL 长得多——读邮件、抄 6 位码通常远超 2 分钟。
EMAIL_CODE_TTL_MS = 10 * 60 * 1000
EMAIL_CODE_MAX_ATTEMPTS = 5
# 校验失败一律同一句话，绝不区分"没有码""码过期""码错了"，免得给暴力尝试提供信号。
EMAIL_CODE_INVALID_MESSAGE = "This confirmation code is invalid or has expired"


class NewTicket(TypedDict):
    ticket_id: str
    secret: str
    expires_at: int


class TicketStatus(TypedDict):
    status: str


class TicketInfo(TypedDict):
    created_at: int
    expires_at: int
    ip: str | None
    user_agent: str | None


class OkResult(TypedDict):
    ok: bool


def generateQRCodeByURL(url: str) -> str:
    code = segno.make(url, error="m")
    stream = io.BytesIO()
    code.save(stream, kind="svg", scale=5, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(stream.getvalue()).decode()


# ---------------------------------------------------------------- email confirmation


def requires_email_confirmation(row: UserRow | None) -> bool:
    """True 仅当账号有真实可投递、且经 IdP 校验的邮箱。

    本地注册的邮箱是 username@samryetha.local 占位（且没有 email_verified_at）；
    对这类账号要求确认码会把人锁在门外，所以只有非占位 + 已验证才走邮件确认。
    """
    if row is None:
        return False
    email = row["email"].strip()
    if not email or row["email_verified_at"] is None:
        return False
    return not email.lower().endswith("@" + FAKE_EMAIL_DOMAIN)


def mask_email(email: str | None) -> str | None:
    """Never expose a complete approving account email in QR responses."""
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


class QrAuthService:
    """Application use-case implementations in a caller-owned transaction."""

    def __init__(self, conn: Connection, settings: Settings | None = None, mailer: Mailer | None = None) -> None:
        self._conn = conn
        self._settings = settings
        self._mailer = mailer
        self._repository = QrAuthRepository(self._conn)

    def approval_info(self, ticket_id: str | None, user_id: int) -> QrInfoResponse:
        if not ticket_id:
            raise bad_request("This QR code is invalid or has expired")
        info = self.ticket_info(ticket_id)
        if info is None:
            raise bad_request("This QR code is invalid or has expired")
        row = UserService(self._conn).get_by_id(user_id)
        if row is None:
            raise forbidden("The approving account is unavailable")
        needs_code = requires_email_confirmation(row)
        return QrInfoResponse(
            created_at=info["created_at"],
            expires_at=info["expires_at"],
            ip=info["ip"],
            user_agent=info["user_agent"],
            email_confirmation_required=needs_code,
            email_hint=mask_email(row["email"]) if needs_code else None,
        )

    def request_confirmation(self, ticket_id: str, user_id: int) -> str | None:
        """Issue and deliver an email code when the approving account requires it."""
        if self.ticket_info(ticket_id) is None:
            raise bad_request("This QR code is invalid or has expired")
        user = UserService(self._conn).get_by_id(user_id)
        if user is None:
            raise forbidden("The approving account is unavailable")
        if not requires_email_confirmation(user):
            return None
        settings, mailer = self._settings, self._mailer
        if settings is None or mailer is None:
            raise RuntimeError("QR email confirmation requires settings and mailer")
        code = self.begin_confirmation_code(ticket_id, user_id, ttl_ms=max(settings.qr_login_ttl_ms, EMAIL_CODE_TTL_MS))
        subject, text, html = qr_signin_confirmation_email(code=code, display_name=user["display_name"])
        try:
            mailer.send(to=user["email"], subject=subject, text=text, html=html)
        except Exception:
            logging.getLogger("samryetha.auth").warning(
                "qr-confirm email failed for user_id=%s", user_id, exc_info=True
            )
        return user["email"]

    def approve(self, ticket_id: str, user_id: int, code: str | None) -> OkResult:
        user = UserService(self._conn).get_by_id(user_id)
        if user is None:
            raise forbidden("The approving account is unavailable")
        if requires_email_confirmation(user):
            if not code:
                raise forbidden("EMAIL_CODE_REQUIRED")
            self.verify_confirmation_code(ticket_id, user_id, code)
        return self.decide_ticket(ticket_id, user_id, True)

    def _prune_expired(self, now: int) -> None:
        self._repository.prune_expired(now=now)

    def begin_ticket(self, *, ip: str | None, user_agent: str | None, ttl_ms: int = DEFAULT_TTL_MS) -> NewTicket:
        now = now_ms()
        self._prune_expired(now)
        ticket_id = secrets.token_urlsafe(16)
        secret = secrets.token_urlsafe(32)
        self._repository.insert_ticket(
            ticket_id_hash=hash_token(ticket_id),
            secret_hash=hash_token(secret),
            ip=ip,
            user_agent=user_agent,
            expires_at=now + ttl_ms,
            created_at=now,
        )
        return {"ticket_id": ticket_id, "secret": secret, "expires_at": now + ttl_ms}

    def _find(self, ticket_id: str) -> qr_repository.TicketRecord | None:
        return self._repository.ticket(hash_token(ticket_id))

    def ticket_status(self, ticket_id: str) -> TicketStatus | None:
        """PC 轮询/SSE 用的公开状态机：pending/approved/denied/expired/unknown。"""
        row = self._find(ticket_id)
        if row is None:
            return None
        now = now_ms()
        if row.expires_at <= now:
            self._repository.delete_ticket(row.id)
            return {"status": "expired"}
        return {"status": row.status}

    def ticket_info(self, ticket_id: str) -> TicketInfo | None:
        """确认页展示的上下文（谁在请求登录）。"""
        row = self._find(ticket_id)
        if row is None or row.expires_at <= now_ms() or row.status != "pending":
            return None
        return {
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "ip": row.ip,
            "user_agent": row.user_agent,
        }

    def decide_ticket(self, ticket_id: str, approver_id: int, approve: bool) -> OkResult:
        """手机端批准/拒绝（需登录）。只有 pending 且未过期的票据可决议。"""
        row = self._find(ticket_id)
        if row is None or row.expires_at <= now_ms() or row.status != "pending":
            raise bad_request("This QR code is invalid or has expired")
        self._repository.decide_ticket(
            row.id, approved_by=UserID(approver_id) if approve else None, approved=approve, decided_at=now_ms()
        )
        return {"ok": True}

    def exchange_ticket(self, ticket_id: str, secret: str) -> int:
        """PC 凭 (ticket_id + secret) 换取批准者 user_id。单次有效，用后即焚。"""
        row = self._find(ticket_id)
        if row is None or row.expires_at <= now_ms():
            if row is not None:
                self._repository.delete_ticket(row.id)
            raise bad_request("This QR code is invalid or has expired")
        secret_hash = row.secret_hash
        if row.status != "approved" or not hmac.compare_digest(secret_hash, hash_token(secret)):
            raise bad_request("This QR code is invalid or has expired")
        user_id = row.approved_by
        if user_id is None:
            raise bad_request("This QR code is invalid or has expired")
        # 先查审批者状态、再删票：同事务内保持单次性（任一分支抛错即整体回滚，票据不丢）。
        user = self._repository.active_user(user_id)
        if user is None:
            raise forbidden("The approving account is unavailable")
        if user["status"] == "banned":
            from ..core.errors import banned

            raise banned()
        if user["status"] != "active":
            raise forbidden("This account is not active")
        self._repository.delete_ticket(row.id)
        return int(user_id)

    def begin_confirmation_code(self, ticket_id: str, user_id: int, ttl_ms: int = EMAIL_CODE_TTL_MS) -> str:
        """为票据生成 6 位确认码：作废旧码、落库、并把 pending 票据续到同一 TTL。

        票据默认只有 2 分钟，读邮件根本来不及；把 expires_at 一起推后，PC 端的
        SSE 等待才不会被提前掐断。返回原始码（仅交给邮件，不落库）。
        """
        now = now_ms()
        ticket_id_hash = hash_token(ticket_id)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._repository.replace_confirmation_code(
            ticket_id_hash=ticket_id_hash,
            user_id=UserID(user_id),
            code_hash=hash_token(f"{user_id}:{code}"),
            expires_at=now + ttl_ms,
            created_at=now,
        )
        return code

    def verify_confirmation_code(self, ticket_id: str, user_id: int, code: str) -> None:
        """校验票据 + 用户 + 6 位码；任何失败都抛同一句通用错误。用后置 consumed_at。"""
        ticket_id_hash = hash_token(ticket_id)
        row = self._repository.active_confirmation_code(
            ticket_id_hash=ticket_id_hash, user_id=UserID(user_id), now=now_ms()
        )
        if row is None:
            raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
        code_row_id = row.id
        if row.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            self._invalidate_code(code_row_id)
            raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
        if not hmac.compare_digest(row.code_hash, hash_token(f"{user_id}:{code}")):
            attempts = row.attempts + 1
            if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
                # 到顶即作废：之后即便猜中也换不出会话
                self._invalidate_code(code_row_id)
            else:
                self._bump_code_attempts(code_row_id, attempts)
            raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
        self._repository.consume_confirmation_code(code_row_id, consumed_at=now_ms())

    def _bump_code_attempts(self, code_row_id: int, attempts: int) -> None:
        """失败计数必须另起短事务提交：调用方随后抛 bad_request，请求事务会整体回滚，
        计数若写在里面会被一起滚掉，限次就形同虚设（同 claim_existing 的 bump 处理）。"""
        self._repository.bump_confirmation_attempts(code_row_id, attempts)

    def _invalidate_code(self, code_row_id: int) -> None:
        """到达尝试上限即删除确认码，同样另起短事务，避免被请求回滚复活。"""
        self._repository.invalidate_confirmation_code(code_row_id)
