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
from .schema import qr_login_tickets, users
from .security import hash_token

DEFAULT_TTL_MS = 2 * 60 * 1000


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
    conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.id == row.id))
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
    return user_id
