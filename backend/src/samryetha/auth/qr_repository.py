"""Typed SQLAlchemy persistence boundary for QR login tickets and confirmation codes."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.engine import Connection

from ..core.ids import UserID
from ..core.records import opt_str, require_int, require_str
from ..core.schema import qr_login_confirmation_codes, qr_login_tickets
from ..users.models import UserRow
from ..users.repository import UserRepository


@dataclass(frozen=True, slots=True)
class TicketRecord:
    id: int
    secret_hash: str
    status: str
    approved_by: UserID | None
    ip: str | None
    user_agent: str | None
    expires_at: int
    created_at: int


@dataclass(frozen=True, slots=True)
class ConfirmationCodeRecord:
    id: int
    code_hash: str
    attempts: int


class QrAuthRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def prune_expired(self, *, now: int) -> None:
        self._conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.expires_at <= now))

    def insert_ticket(
        self,
        *,
        ticket_id_hash: str,
        secret_hash: str,
        ip: str | None,
        user_agent: str | None,
        expires_at: int,
        created_at: int,
    ) -> None:
        self._conn.execute(
            insert(qr_login_tickets).values(
                ticket_id_hash=ticket_id_hash,
                secret_hash=secret_hash,
                status="pending",
                approved_by=None,
                ip=ip,
                user_agent=user_agent,
                expires_at=expires_at,
                created_at=created_at,
                decided_at=None,
            )
        )

    def ticket(self, ticket_id_hash: str) -> TicketRecord | None:
        row = (
            self._conn.execute(select(qr_login_tickets).where(qr_login_tickets.c.ticket_id_hash == ticket_id_hash))
            .mappings()
            .first()
        )
        if row is None:
            return None
        approved = row["approved_by"]
        return TicketRecord(
            id=require_int(row["id"], "id"),
            secret_hash=require_str(row["secret_hash"], "secret_hash"),
            status=require_str(row["status"], "status"),
            approved_by=UserID(require_int(approved, "approved_by")) if approved is not None else None,
            ip=opt_str(row["ip"], "ip"),
            user_agent=opt_str(row["user_agent"], "user_agent"),
            expires_at=require_int(row["expires_at"], "expires_at"),
            created_at=require_int(row["created_at"], "created_at"),
        )

    def delete_ticket(self, ticket_id: int) -> None:
        self._conn.execute(delete(qr_login_tickets).where(qr_login_tickets.c.id == ticket_id))

    def decide_ticket(self, ticket_id: int, *, approved_by: UserID | None, approved: bool, decided_at: int) -> None:
        self._conn.execute(
            update(qr_login_tickets)
            .where(qr_login_tickets.c.id == ticket_id)
            .values(status="approved" if approved else "denied", approved_by=approved_by, decided_at=decided_at)
        )

    def active_user(self, user_id: UserID) -> UserRow | None:
        return UserRepository(self._conn).get_by_id(user_id)

    def replace_confirmation_code(
        self, *, ticket_id_hash: str, user_id: UserID, code_hash: str, expires_at: int, created_at: int
    ) -> None:
        self._conn.execute(
            delete(qr_login_confirmation_codes).where(qr_login_confirmation_codes.c.ticket_id_hash == ticket_id_hash)
        )
        self._conn.execute(
            insert(qr_login_confirmation_codes).values(
                ticket_id_hash=ticket_id_hash,
                user_id=user_id,
                code_hash=code_hash,
                attempts=0,
                expires_at=expires_at,
                created_at=created_at,
                consumed_at=None,
            )
        )
        self._conn.execute(
            update(qr_login_tickets)
            .where(qr_login_tickets.c.ticket_id_hash == ticket_id_hash)
            .values(expires_at=expires_at)
        )

    def active_confirmation_code(
        self, *, ticket_id_hash: str, user_id: UserID, now: int
    ) -> ConfirmationCodeRecord | None:
        row = (
            self._conn.execute(
                select(qr_login_confirmation_codes)
                .where(
                    and_(
                        qr_login_confirmation_codes.c.ticket_id_hash == ticket_id_hash,
                        qr_login_confirmation_codes.c.user_id == user_id,
                        qr_login_confirmation_codes.c.consumed_at.is_(None),
                        qr_login_confirmation_codes.c.expires_at > now,
                    )
                )
                .order_by(qr_login_confirmation_codes.c.id.desc())
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return ConfirmationCodeRecord(
            id=require_int(row["id"], "code id"),
            code_hash=require_str(row["code_hash"], "code hash"),
            attempts=require_int(row["attempts"], "attempts"),
        )

    def consume_confirmation_code(self, code_id: int, *, consumed_at: int) -> None:
        self._conn.execute(
            update(qr_login_confirmation_codes)
            .where(qr_login_confirmation_codes.c.id == code_id, qr_login_confirmation_codes.c.consumed_at.is_(None))
            .values(consumed_at=consumed_at)
        )

    def bump_confirmation_attempts(self, code_id: int, attempts: int) -> None:
        with self._conn.engine.begin() as side:
            side.execute(
                update(qr_login_confirmation_codes)
                .where(qr_login_confirmation_codes.c.id == code_id)
                .values(attempts=attempts)
            )

    def invalidate_confirmation_code(self, code_id: int) -> None:
        with self._conn.engine.begin() as side:
            side.execute(delete(qr_login_confirmation_codes).where(qr_login_confirmation_codes.c.id == code_id))
