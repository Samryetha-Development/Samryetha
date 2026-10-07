"""Typed persistence boundary for OIDC login and account-claim flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Connection

from ..core.ids import UserID
from ..core.records import opt_str, require_int, require_str
from ..core.schema import oidc_claim_tickets, oidc_identities, oidc_login_transactions, users
from ..users.models import UserRow
from ..users.repository import user_row_from_mapping


class LoginTransactionRecord(TypedDict):
    nonce: str
    code_verifier: str
    return_to: str


@dataclass(frozen=True, slots=True)
class IdentityRecord:
    id: int
    user_id: UserID


@dataclass(frozen=True, slots=True)
class ClaimTicketRecord:
    id: int
    issuer: str
    subject: str
    email: str | None
    display_name: str
    attempts: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class DisplayProfileRecord:
    display_name: str
    settings: str


class OidcRepository:
    """Typed persistence operations; transaction ownership remains with the caller."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def insert_login_transaction(
        self, *, state_hash: str, nonce: str, code_verifier: str, return_to: str, expires_at: int, created_at: int
    ) -> None:
        self._conn.execute(delete(oidc_login_transactions).where(oidc_login_transactions.c.expires_at <= created_at))
        self._conn.execute(
            oidc_login_transactions.insert().values(
                state_hash=state_hash,
                nonce=nonce,
                code_verifier=code_verifier,
                return_to=return_to,
                expires_at=expires_at,
                created_at=created_at,
            )
        )

    def consume_login_transaction(self, state_hash: str, *, now: int) -> LoginTransactionRecord | None:
        row = (
            self._conn.execute(
                select(oidc_login_transactions).where(
                    oidc_login_transactions.c.state_hash == state_hash, oidc_login_transactions.c.expires_at > now
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        self._conn.execute(delete(oidc_login_transactions).where(oidc_login_transactions.c.state_hash == state_hash))
        return {
            "nonce": require_str(row["nonce"], "nonce"),
            "code_verifier": require_str(row["code_verifier"], "code_verifier"),
            "return_to": require_str(row["return_to"], "return_to"),
        }

    def username_exists(self, username: str) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.username == username)).first() is not None

    def identity(self, issuer: str, subject: str) -> IdentityRecord | None:
        row = (
            self._conn.execute(
                select(oidc_identities.c.id, oidc_identities.c.user_id).where(
                    oidc_identities.c.issuer == issuer, oidc_identities.c.subject == subject
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return IdentityRecord(
            id=require_int(row["id"], "identity id"),
            user_id=UserID(require_int(row["user_id"], "user id")),
        )

    def verified_email_user_id(self, email: str) -> UserID | None:
        value = self._conn.execute(
            select(users.c.id).where(
                users.c.email == email, users.c.email_verified_at.is_not(None), users.c.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        return UserID(require_int(value, "user id")) if value is not None else None

    def email_exists(self, email: str) -> bool:
        return self._conn.execute(select(users.c.id).where(users.c.email == email)).first() is not None

    def insert_identity(self, *, user_id: UserID, issuer: str, subject: str, email: str | None, now: int) -> None:
        self._conn.execute(
            oidc_identities.insert().values(
                user_id=user_id, issuer=issuer, subject=subject, email_at_link=email, created_at=now, last_login_at=now
            )
        )

    def touch_identity(self, identity_id: int, *, now: int) -> None:
        self._conn.execute(update(oidc_identities).where(oidc_identities.c.id == identity_id).values(last_login_at=now))

    def insert_user(self, values: dict[str, object]) -> UserID:
        result = self._conn.execute(users.insert().values(**values))
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("user insert did not return a primary key")
        return UserID(require_int(primary_key[0], "inserted user id"))

    def user(self, user_id: UserID) -> UserRow | None:
        row = (
            self._conn.execute(select(users).where(users.c.id == user_id, users.c.deleted_at.is_(None)))
            .mappings()
            .first()
        )
        return user_row_from_mapping(row) if row is not None else None

    def update_user(self, user_id: UserID, values: dict[str, object]) -> None:
        self._conn.execute(update(users).where(users.c.id == user_id).values(**values))

    def has_other_active_admin(self, user_id: UserID) -> bool:
        return (
            self._conn.execute(
                select(users.c.id).where(
                    users.c.role == "admin",
                    users.c.status == "active",
                    users.c.id != user_id,
                    users.c.deleted_at.is_(None),
                )
            ).first()
            is not None
        )

    def display_profile(self, user_id: UserID) -> DisplayProfileRecord | None:
        row = (
            self._conn.execute(select(users.c.display_name, users.c.settings).where(users.c.id == user_id))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return DisplayProfileRecord(
            display_name=require_str(row["display_name"], "display name"),
            settings=require_str(row["settings"], "settings"),
        )

    def insert_claim_ticket(
        self,
        *,
        ticket_hash: str,
        issuer: str,
        subject: str,
        email: str | None,
        display_name: str,
        expires_at: int,
        created_at: int,
    ) -> None:
        self._conn.execute(
            oidc_claim_tickets.insert().values(
                ticket_hash=ticket_hash,
                issuer=issuer,
                subject=subject,
                email=email,
                display_name=display_name,
                attempts=0,
                expires_at=expires_at,
                created_at=created_at,
            )
        )

    def claim_ticket(self, ticket_hash: str) -> ClaimTicketRecord | None:
        row = (
            self._conn.execute(select(oidc_claim_tickets).where(oidc_claim_tickets.c.ticket_hash == ticket_hash))
            .mappings()
            .first()
        )
        if row is None:
            return None
        return ClaimTicketRecord(
            id=require_int(row["id"], "claim ticket id"),
            issuer=require_str(row["issuer"], "issuer"),
            subject=require_str(row["subject"], "subject"),
            email=opt_str(row["email"], "email"),
            display_name=require_str(row["display_name"], "display name"),
            attempts=require_int(row["attempts"], "attempts"),
            expires_at=require_int(row["expires_at"], "expires at"),
        )

    def delete_claim_ticket(self, ticket_id: int) -> None:
        self._conn.execute(delete(oidc_claim_tickets).where(oidc_claim_tickets.c.id == ticket_id))

    def update_claim_attempts(self, ticket_id: int, attempts: int) -> None:
        self._conn.execute(
            update(oidc_claim_tickets).where(oidc_claim_tickets.c.id == ticket_id).values(attempts=attempts)
        )
