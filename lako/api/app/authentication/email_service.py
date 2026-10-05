"""One-time email tokens: address verification, password reset, migration invites.

Invite acceptance also verifies the address (completing the flow proves
mailbox control), which is what lets migrated users onboard without a
pre-verified address on file.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import EmailToken, EmailTokenPurpose, Identity, IdentityType, utcnow
from app.security.core import random_token, token_hash

RESET_TTL = timedelta(hours=1)
VERIFY_TTL = timedelta(hours=24)
INVITE_TTL = timedelta(days=7)

# Addresses the platform itself fabricated (see the migration import path and
# registration placeholders). Nobody can receive mail there, so flows that
# require a *deliverable* address — the authorize-time verification gate, the
# sign-in code — must treat them as "nothing on file" rather than as a pending
# address the user could verify.
PLACEHOLDER_EMAIL_DOMAIN = "migrated.invalid"


def is_placeholder_email(address: str | None) -> bool:
    return bool(address) and address.strip().casefold().endswith("@" + PLACEHOLDER_EMAIL_DOMAIN)


async def verified_email(db: AsyncSession, user_id: object) -> Identity | None:
    """The single verified address, whatever its provenance (no placeholder filter)."""
    return (
        (
            await db.execute(
                select(Identity)
                .where(
                    Identity.user_id == user_id,
                    Identity.type == IdentityType.EMAIL,
                    Identity.verified.is_(True),
                )
                .order_by(Identity.created_at.desc(), Identity.id.desc())
            )
        )
        .scalars()
        .first()
    )


async def has_deliverable_email(db: AsyncSession, user_id: object) -> bool:
    """True when the account owns a verified address a human can actually read."""
    identity = await verified_email(db, user_id)
    return identity is not None and not is_placeholder_email(identity.identifier)

PURPOSE_TTL = {
    EmailTokenPurpose.VERIFY: VERIFY_TTL,
    EmailTokenPurpose.RESET: RESET_TTL,
    EmailTokenPurpose.INVITE: INVITE_TTL,
}


async def deliverable_email(db: AsyncSession, user_id: object) -> Identity | None:
    """Return the account's deliverable address, deterministically.

    There is no ``verified_at`` column, so the most recently created verified
    address wins (``created_at desc, id desc``); anything that mails a secret
    must always target the same address rather than an arbitrary DB-ordered row.
    """
    return (
        (
            await db.execute(
                select(Identity)
                .where(
                    Identity.user_id == user_id,
                    Identity.type == IdentityType.EMAIL,
                    Identity.verified.is_(True),
                )
                .order_by(Identity.created_at.desc(), Identity.id.desc())
            )
        )
        .scalars()
        .first()
    )


async def pending_email(db: AsyncSession, user_id: object) -> Identity | None:
    """The newest *unverified* address — the only one a verification code can target."""
    return (
        (
            await db.execute(
                select(Identity)
                .where(
                    Identity.user_id == user_id,
                    Identity.type == IdentityType.EMAIL,
                    Identity.verified.is_(False),
                )
                .order_by(Identity.created_at.desc(), Identity.id.desc())
            )
        )
        .scalars()
        .first()
    )


async def unverified_emails(db: AsyncSession, user_id: object) -> list[Identity]:
    """Every unverified address, newest first (candidate scopes for a code)."""
    return list(
        (
            await db.execute(
                select(Identity)
                .where(
                    Identity.user_id == user_id,
                    Identity.type == IdentityType.EMAIL,
                    Identity.verified.is_(False),
                )
                .order_by(Identity.created_at.desc(), Identity.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def issue_token(db: AsyncSession, user_id: object, purpose: str, identity_id: object | None = None) -> str:
    raw = random_token(32)
    db.add(
        EmailToken(
            user_id=user_id,
            identity_id=identity_id,
            purpose=purpose,
            token_hash=token_hash(raw),
            expires_at=utcnow() + PURPOSE_TTL[purpose],
        )
    )
    await db.flush()
    return raw


async def consume_token(db: AsyncSession, raw_token: str, purposes: set[str]) -> EmailToken | None:
    """Atomically consume a one-time token.

    A single guarded UPDATE claims the token; ``rowcount == 0`` means it is
    unknown, already consumed, of another purpose, or expired — the caller
    maps all of these to the same rejection without distinguishing them.
    The UPDATE (not SELECT-then-write) is what makes double-submit safe:
    two concurrent consumers cannot both claim the row.
    """
    now = utcnow()
    claimed = await db.execute(
        update(EmailToken)
        .where(
            EmailToken.token_hash == token_hash(raw_token),
            EmailToken.purpose.in_(purposes),
            EmailToken.consumed_at.is_(None),
            EmailToken.expires_at > now,
        )
        .values(consumed_at=now)
    )
    if claimed.rowcount == 0:
        return None
    return (
        await db.execute(select(EmailToken).where(EmailToken.token_hash == token_hash(raw_token)))
    ).scalar_one_or_none()
