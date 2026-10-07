"""Request-scoped session lifecycle and persistence orchestration."""

from __future__ import annotations

from samryetha.auth.repository import AuthRepository

import base64
import os

from sqlalchemy.engine import Connection

from ..core.db import now_ms
from ..core.ids import UserID
from .repository import SessionUserRecord
from .security import hash_token

_DEFAULT_TTL_MS = 30 * 24 * 3600 * 1000


class SessionService:
    """Session operations using the caller-owned transaction."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._repository = AuthRepository(self._conn)

    def create_session(
        self,
        user_id: int,
        ctx: dict[str, str | None] | None = None,
        ttl_ms: int | None = None,
    ) -> tuple[str, int]:
        """落库会话，返回 (token, expires_at_ms)。ttl_ms 缺省用 30 天。"""
        ctx = ctx or {}
        _now = now_ms()
        token = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
        expires = _now + (ttl_ms if ttl_ms is not None else _DEFAULT_TTL_MS)
        self._repository.insert_session(
            token_hash=hash_token(token),
            user_id=UserID(user_id),
            expires_at=expires,
            ip=ctx.get("ip"),
            user_agent=ctx.get("user_agent"),
            created_at=_now,
        )
        return token, expires

    def get_session_user(self, token: str) -> SessionUserRecord | None:
        """Return the typed user record for a valid, unexpired session."""
        return self._repository.active_session_user(token_hash=hash_token(token), now=now_ms())

    def delete_session(self, token: str) -> None:
        self._repository.delete_session(hash_token(token))

    def delete_user_sessions(self, user_id: int) -> None:
        self._repository.delete_user_sessions(UserID(user_id))
