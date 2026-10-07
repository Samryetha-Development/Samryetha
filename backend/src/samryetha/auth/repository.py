"""Typed SQLAlchemy persistence boundary for authentication sessions."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Connection, RowMapping

from ..core.ids import UserID
from ..core.records import require_int, require_str
from ..core.schema import password_reset_tokens, sessions, users
from ..users.models import UserRow
from ..users.repository import user_row_from_mapping


@dataclass(frozen=True, slots=True)
class SessionUserRecord:
    id: UserID
    username: str
    display_name: str
    email: str
    role: str
    status: str


@dataclass(frozen=True, slots=True)
class PasswordResetRecord:
    id: int
    user_id: UserID


def _session_user(row: RowMapping) -> SessionUserRecord:
    return SessionUserRecord(
        id=UserID(require_int(row["id"], "id")),
        username=require_str(row["username"], "username"),
        display_name=require_str(row["display_name"], "display_name"),
        email=require_str(row["email"], "email"),
        role=require_str(row["role"], "role"),
        status=require_str(row["status"], "status"),
    )


class AuthRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_session(
        self,
        *,
        token_hash: str,
        user_id: UserID,
        expires_at: int,
        ip: str | None,
        user_agent: str | None,
        created_at: int,
    ) -> None:
        self._conn.execute(
            insert(sessions).values(
                token_hash=token_hash,
                user_id=user_id,
                expires_at=expires_at,
                ip=ip,
                user_agent=user_agent,
                created_at=created_at,
                last_seen_at=created_at,
            )
        )

    def active_session_user(self, *, token_hash: str, now: int) -> SessionUserRecord | None:
        row = (
            self._conn.execute(
                select(users.c.id, users.c.username, users.c.display_name, users.c.email, users.c.role, users.c.status)
                .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
                .where(
                    and_(sessions.c.token_hash == token_hash, sessions.c.expires_at > now, users.c.deleted_at.is_(None))
                )
            )
            .mappings()
            .first()
        )
        return _session_user(row) if row is not None else None

    def delete_session(self, token_hash: str) -> None:
        self._conn.execute(delete(sessions).where(sessions.c.token_hash == token_hash))

    def delete_user_sessions(self, user_id: UserID) -> None:
        self._conn.execute(delete(sessions).where(sessions.c.user_id == user_id))

    def username_exists(self, username: str) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.username == username)).first() is not None

    def auth_user(self, username: str) -> UserRow | None:
        row = (
            self._conn.execute(select(users).where(users.c.username == username, users.c.deleted_at.is_(None)))
            .mappings()
            .first()
        )
        return user_row_from_mapping(row) if row is not None else None

    def update_password(self, user_id: UserID, password_hash: str, *, updated_at: int) -> None:
        self._conn.execute(
            update(users).where(users.c.id == user_id).values(password_hash=password_hash, updated_at=updated_at)
        )

    def invalidate_password_resets(self, user_id: UserID, *, used_at: int) -> None:
        self._conn.execute(
            update(password_reset_tokens)
            .where(password_reset_tokens.c.user_id == user_id, password_reset_tokens.c.used_at.is_(None))
            .values(used_at=used_at)
        )

    def insert_password_reset(self, *, user_id: UserID, token_hash: str, expires_at: int, created_at: int) -> None:
        self._conn.execute(
            password_reset_tokens.insert().values(
                user_id=user_id, token_hash=token_hash, expires_at=expires_at, created_at=created_at
            )
        )

    def valid_password_reset(self, token_hash: str, *, now: int) -> PasswordResetRecord | None:
        row = (
            self._conn.execute(
                select(password_reset_tokens.c.id, password_reset_tokens.c.user_id).where(
                    password_reset_tokens.c.token_hash == token_hash,
                    password_reset_tokens.c.used_at.is_(None),
                    password_reset_tokens.c.expires_at > now,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return PasswordResetRecord(
            id=require_int(row["id"], "reset id"),
            user_id=UserID(require_int(row["user_id"], "user id")),
        )

    def consume_password_reset(self, reset_id: int, *, now: int) -> bool:
        result = self._conn.execute(
            update(password_reset_tokens)
            .where(
                password_reset_tokens.c.id == reset_id,
                password_reset_tokens.c.used_at.is_(None),
                password_reset_tokens.c.expires_at > now,
            )
            .values(used_at=now)
        )
        return result.rowcount == 1

    def user_id_by_username(self, username: str) -> UserID | None:
        value = self._conn.execute(select(users.c.id).where(users.c.username == username)).scalar_one_or_none()
        return UserID(require_int(value, "user id")) if value is not None else None

    def activate_builtin(self, user_id: UserID, *, verified_at: int) -> None:
        self._conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(role="admin", status="active", email_verified_at=verified_at)
        )

    def insert_builtin(
        self, *, username: str, email: str, email_domain: str, password_hash: str, discriminator: int, now: int
    ) -> None:
        self._conn.execute(
            users.insert().values(
                username=username,
                display_name=username,
                email=email,
                email_domain=email_domain,
                password_hash=password_hash,
                role="admin",
                status="active",
                discriminator=discriminator,
                email_verified_at=now,
                bio="",
                settings="{}",
                created_at=now,
                updated_at=now,
            )
        )

    def merge_moderator_roles(self) -> None:
        self._conn.execute(update(users).where(users.c.role == "moderator").values(role="admin"))
