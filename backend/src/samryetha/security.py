"""密码哈希(argon2id)与会话 — 镜像 backend/src/auth/session.ts + @node-rs/argon2 用法。

- 存量哈希格式 $argon2id$v=19$m=19456,t=2,p=1$... 可直接用 argon2-cffi 验证（无需迁移）。
- 新建哈希用相同参数，保证新老一致。
- 会话 token = 32 随机字节 base64url（无 padding）；DB 只存 sha256 hex。
- 时间戳为毫秒 int。
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

from sqlalchemy import and_, delete, insert, select
from sqlalchemy.engine import Connection

from .schema import sessions, users
from .db import now_ms

SESSION_COOKIE = "samryetha_session"
_DEFAULT_TTL_MS = 30 * 24 * 3600 * 1000  # 与 session.ts 常量一致


# ---------------------------------------------------------------- argon2id

def _hasher():
    from argon2 import PasswordHasher

    # 与 @node-rs/argon2 同参：m=19456 KiB, t=2, p=1, 16B salt, 32B tag
    return PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # argon2-cffi 新版签名 verify(hash, password)；keyword 传参在两代都成立
    try:
        return _hasher().verify(password=password, hash=password_hash)
    except Exception:
        return False


_dummy_hash_cache: str | None = None


def _dummy_hash() -> str:
    """防枚举时序用的固定合法 argon2 哈希（校验必然失败，成本与真验一致）。"""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password(secrets.token_urlsafe(32))
    return _dummy_hash_cache


def verify_against_dummy(password: str) -> bool:
    """用户不存在时对 dummy 跑一次校验，耗时可比，避免时序枚举。返回 False。"""
    try:
        return _hasher().verify(password=password, hash=_dummy_hash())
    except Exception:
        return False


# ---------------------------------------------------------------- session token

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    conn: Connection,
    user_id: int,
    ctx: dict | None = None,
    ttl_ms: int | None = None,
) -> tuple[str, int]:
    """落库会话，返回 (token, expires_at_ms)。ttl_ms 缺省用 30 天。"""
    ctx = ctx or {}
    _now = now_ms()
    token = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    expires = _now + (ttl_ms if ttl_ms is not None else _DEFAULT_TTL_MS)
    conn.execute(
        insert(sessions).values(
            token_hash=hash_token(token),
            user_id=user_id,
            expires_at=expires,
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            created_at=_now,
            last_seen_at=_now,
        )
    )
    return token, expires


def row_to_dict(row) -> dict | None:  # noqa: ANN001
    if row is None:
        return None
    return dict(row._mapping)


def get_session_user(conn: Connection, token: str) -> dict | None:
    """有有效会话则返回 users 行(dict)，否则 None。镜像 getSessionUser：expires>now 且用户未删。"""
    _now = now_ms()
    row = conn.execute(
        select(users)
        .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
        .where(
            and_(
                sessions.c.token_hash == hash_token(token),
                sessions.c.expires_at > _now,
                users.c.deleted_at.is_(None),
            )
        )
    ).first()
    return row_to_dict(row)


def delete_session(conn: Connection, token: str) -> None:
    conn.execute(delete(sessions).where(sessions.c.token_hash == hash_token(token)))


def delete_user_sessions(conn: Connection, user_id: int) -> None:
    conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
