"""密码哈希与令牌哈希；会话持久化由 SessionService 负责。

- 存量哈希格式 $argon2id$v=19$m=19456,t=2,p=1$... 可直接用 argon2-cffi 验证（无需迁移）。
- 新建哈希用相同参数，保证新老一致。
- 会话 token = 32 随机字节 base64url（无 padding）；DB 只存 sha256 hex。
- 时间戳为毫秒 int。
"""

from __future__ import annotations

import hashlib
import secrets


SESSION_COOKIE = "samryetha_session"


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
