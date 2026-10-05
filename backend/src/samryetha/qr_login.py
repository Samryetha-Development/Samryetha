"""扫码登录：PC 展示二维码，手机确认后 PC 换会话。

流程：POST /qr/start 建票据（ticket_id 公开进二维码，secret 只留 PC 内存）
→ 手机扫码打开确认页（需登录态）批准/拒绝
→ PC 经 SSE 得知批准 → POST /qr/exchange(ticket_id + secret) 换 HttpOnly 会话。

安全要点：ticket_id 本身换不到会话（必须 secret）；单次有效；2 分钟 TTL；
确认页展示请求方 IP/UA/时间供核对；secret 全程不进 URL/日志。
"""

from __future__ import annotations

import base64
import hmac
import io
import secrets

import segno
from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Connection

from .db import now_ms
from .errors import bad_request, forbidden
from .schema import qr_login_confirmation_codes, qr_login_tickets, users
from .security import hash_token
from .users import FAKE_EMAIL_DOMAIN

DEFAULT_TTL_MS = 2 * 60 * 1000

# 邮箱确认码：比票据 TTL 长得多——读邮件、抄 6 位码通常远超 2 分钟。
EMAIL_CODE_TTL_MS = 10 * 60 * 1000
EMAIL_CODE_MAX_ATTEMPTS = 5
# 校验失败一律同一句话，绝不区分"没有码""码过期""码错了"，免得给暴力尝试提供信号。
EMAIL_CODE_INVALID_MESSAGE = "This confirmation code is invalid or has expired"


def qr_data_uri(url: str) -> str:
    code = segno.make(url, error="m")
    stream = io.BytesIO()
    code.save(stream, kind="svg", scale=5, border=2)
    return "data:image/svg+xml;base64," + base64.b64encode(stream.getvalue()).decode()


def _prune_expired(conn: Connection, now: int) -> None:
    conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.expires_at <= now))


def begin_ticket(
    conn: Connection, *, ip: str | None, user_agent: str | None, ttl_ms: int = DEFAULT_TTL_MS
) -> dict:
    now = now_ms()
    _prune_expired(conn, now)
    ticket_id = secrets.token_urlsafe(16)
    secret = secrets.token_urlsafe(32)
    conn.execute(
        insert(qr_login_tickets).values(
            ticket_id_hash=hash_token(ticket_id),
            secret_hash=hash_token(secret),
            status="pending",
            approved_by=None,
            ip=ip,
            user_agent=user_agent,
            expires_at=now + ttl_ms,
            created_at=now,
            decided_at=None,
        )
    )
    return {"ticket_id": ticket_id, "secret": secret, "expires_at": now + ttl_ms}


def _find(conn: Connection, ticket_id: str):
    return conn.execute(
        select(qr_login_tickets).where(qr_login_tickets.c.ticket_id_hash == hash_token(ticket_id))
    ).first()


def ticket_status(conn: Connection, ticket_id: str) -> dict | None:
    """PC 轮询/SSE 用的公开状态机：pending/approved/denied/expired/unknown。"""
    row = _find(conn, ticket_id)
    if row is None:
        return None
    now = now_ms()
    if row.expires_at <= now:
        conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.id == row.id))
        return {"status": "expired"}
    return {"status": row.status}


def ticket_info(conn: Connection, ticket_id: str) -> dict | None:
    """确认页展示的上下文（谁在请求登录）。"""
    row = _find(conn, ticket_id)
    if row is None or row.expires_at <= now_ms() or row.status != "pending":
        return None
    return {
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "ip": row.ip,
        "user_agent": row.user_agent,
    }


def decide_ticket(conn: Connection, ticket_id: str, approver_id: int, approve: bool) -> dict:
    """手机端批准/拒绝（需登录）。只有 pending 且未过期的票据可决议。"""
    row = _find(conn, ticket_id)
    if row is None or row.expires_at <= now_ms() or row.status != "pending":
        raise bad_request("This QR code is invalid or has expired")
    conn.execute(
        update(qr_login_tickets)
        .where(qr_login_tickets.c.id == row.id)
        .values(
            status="approved" if approve else "denied",
            approved_by=approver_id if approve else None,
            decided_at=now_ms(),
        )
    )
    return {"ok": True}


def exchange_ticket(conn: Connection, ticket_id: str, secret: str) -> int:
    """PC 凭 (ticket_id + secret) 换取批准者 user_id。单次有效，用后即焚。"""
    row = _find(conn, ticket_id)
    if row is None or row.expires_at <= now_ms():
        if row is not None:
            conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.id == row.id))
        raise bad_request("This QR code is invalid or has expired")
    if row.status != "approved" or not hmac.compare_digest(row.secret_hash, hash_token(secret)):
        raise bad_request("This QR code is invalid or has expired")
    user_id = row.approved_by
    # 先查审批者状态、再删票：同事务内保持单次性（任一分支抛错即整体回滚，票据不丢）。
    user = conn.execute(
        select(users).where(and_(users.c.id == user_id, users.c.deleted_at.is_(None)))
    ).first()
    if user is None:
        raise forbidden("The approving account is unavailable")
    row_map = dict(user._mapping)
    if row_map["status"] == "banned":
        from .errors import banned

        raise banned()
    if row_map["status"] != "active":
        raise forbidden("This account is not active")
    conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.id == row.id))
    return user_id


# ---------------------------------------------------------------- email confirmation


def requires_email_confirmation(row) -> bool:
    """True 仅当账号有真实可投递、且经 IdP 校验的邮箱。

    本地注册的邮箱是 username@samryetha.local 占位（且没有 email_verified_at）；
    对这类账号要求确认码会把人锁在门外，所以只有非占位 + 已验证才走邮件确认。
    """
    if row is None:
        return False
    mapping = row if isinstance(row, dict) else dict(row._mapping)
    email = (mapping.get("email") or "").strip()
    if not email or mapping.get("email_verified_at") is None:
        return False
    return not email.lower().endswith("@" + FAKE_EMAIL_DOMAIN)


def begin_confirmation_code(
    conn: Connection, ticket_id: str, user_id: int, ttl_ms: int = EMAIL_CODE_TTL_MS
) -> str:
    """为票据生成 6 位确认码：作废旧码、落库、并把 pending 票据续到同一 TTL。

    票据默认只有 2 分钟，读邮件根本来不及；把 expires_at 一起推后，PC 端的
    SSE 等待才不会被提前掐断。返回原始码（仅交给邮件，不落库）。
    """
    now = now_ms()
    ticket_id_hash = hash_token(ticket_id)
    conn.execute(
        delete(qr_login_confirmation_codes).where(
            qr_login_confirmation_codes.c.ticket_id_hash == ticket_id_hash
        )
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    conn.execute(
        insert(qr_login_confirmation_codes).values(
            ticket_id_hash=ticket_id_hash,
            user_id=user_id,
            code_hash=hash_token(f"{user_id}:{code}"),
            attempts=0,
            expires_at=now + ttl_ms,
            created_at=now,
            consumed_at=None,
        )
    )
    conn.execute(
        update(qr_login_tickets)
        .where(qr_login_tickets.c.ticket_id_hash == ticket_id_hash)
        .values(expires_at=now + ttl_ms)
    )
    return code


def verify_confirmation_code(conn: Connection, ticket_id: str, user_id: int, code: str) -> None:
    """校验票据 + 用户 + 6 位码；任何失败都抛同一句通用错误。用后置 consumed_at。"""
    ticket_id_hash = hash_token(ticket_id)
    row = conn.execute(
        select(qr_login_confirmation_codes)
        .where(
            and_(
                qr_login_confirmation_codes.c.ticket_id_hash == ticket_id_hash,
                qr_login_confirmation_codes.c.user_id == user_id,
                qr_login_confirmation_codes.c.consumed_at.is_(None),
                qr_login_confirmation_codes.c.expires_at > now_ms(),
            )
        )
        .order_by(qr_login_confirmation_codes.c.id.desc())
    ).first()
    if row is None:
        raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
    code_row_id = row.id
    if row.attempts >= EMAIL_CODE_MAX_ATTEMPTS:
        _invalidate_code(conn, code_row_id)
        raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
    if not hmac.compare_digest(row.code_hash, hash_token(f"{user_id}:{code}")):
        attempts = row.attempts + 1
        if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            # 到顶即作废：之后即便猜中也换不出会话
            _invalidate_code(conn, code_row_id)
        else:
            _bump_code_attempts(conn, code_row_id, attempts)
        raise bad_request(EMAIL_CODE_INVALID_MESSAGE)
    conn.execute(
        update(qr_login_confirmation_codes)
        .where(
            and_(
                qr_login_confirmation_codes.c.id == code_row_id,
                qr_login_confirmation_codes.c.consumed_at.is_(None),
            )
        )
        .values(consumed_at=now_ms())
    )


def _bump_code_attempts(conn: Connection, code_row_id: int, attempts: int) -> None:
    """失败计数必须另起短事务提交：调用方随后抛 bad_request，请求事务会整体回滚，
    计数若写在里面会被一起滚掉，限次就形同虚设（同 claim_existing 的 bump 处理）。"""
    with conn.engine.begin() as side:
        side.execute(
            update(qr_login_confirmation_codes)
            .where(qr_login_confirmation_codes.c.id == code_row_id)
            .values(attempts=attempts)
        )


def _invalidate_code(conn: Connection, code_row_id: int) -> None:
    """到达尝试上限即删除确认码，同样另起短事务，避免被请求回滚复活。"""
    with conn.engine.begin() as side:
        side.execute(
            delete(qr_login_confirmation_codes).where(
                qr_login_confirmation_codes.c.id == code_row_id
            )
        )
