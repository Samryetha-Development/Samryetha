"""One-time email tokens: address verification, password reset, migration invites.

Invite acceptance also verifies the address (completing the flow proves
mailbox control), which is what lets migrated users onboard without a
pre-verified address on file.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import EmailToken, EmailTokenPurpose, utcnow
from app.security.core import random_token, token_hash

RESET_TTL = timedelta(hours=1)
VERIFY_TTL = timedelta(hours=24)
INVITE_TTL = timedelta(days=7)

PURPOSE_TTL = {
    EmailTokenPurpose.VERIFY: VERIFY_TTL,
    EmailTokenPurpose.RESET: RESET_TTL,
    EmailTokenPurpose.INVITE: INVITE_TTL,
}


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
