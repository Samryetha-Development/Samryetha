"""会话验证 — 复用主站 samryetha_session cookie 语义。

与主站 backend/src/samryetha/security.py 相同的 cookie 名、token → sha256 → DB 查找逻辑。
本服务只读 sessions + users；不创建会话、不操作密码。

auth DB schema（主站）：
  sessions(token_hash TEXT, user_id INT, expires_at BIGINT, ...)
  users(id INT, username TEXT, display_name TEXT, email TEXT, role TEXT, status TEXT, deleted_at BIGINT, ...)

注意：auth DB 可能没有 SQLAlchemy metadata 定义；直接用 text() 查询，不依赖主站 schema 对象。
"""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Connection

SESSION_COOKIE = "samryetha_session"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_session_user(conn: Connection, token: str) -> dict | None:
    """在 auth DB 中验证 token，返回 users 行 dict 或 None。

    与主站 getSessionUser 语义一致：expires_at > now 且 deleted_at IS NULL。
    """
    import time

    now_ms = int(time.time() * 1000)
    token_hash = hash_token(token)
    row = conn.execute(
        text(
            """
            SELECT u.id, u.username, u.display_name, u.email, u.role, u.status
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token_hash = :token_hash
              AND s.expires_at > :now
              AND u.deleted_at IS NULL
            LIMIT 1
            """
        ),
        {"token_hash": token_hash, "now": now_ms},
    ).first()
    if row is None:
        return None
    return dict(row._mapping)
