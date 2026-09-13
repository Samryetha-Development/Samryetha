"""One-time email tokens: address verification, password reset, migration invites.

Invite acceptance also verifies the address (completing the flow proves
mailbox control), which is what lets migrated users onboard without a
pre-verified address on file.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
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
    row = (
        await db.execute(
            select(EmailToken).where(
                EmailToken.token_hash == token_hash(raw_token),
                EmailToken.purpose.in_(purposes),
                EmailToken.consumed_at.is_(None),
                EmailToken.expires_at > utcnow(),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.consumed_at = utcnow()
    return row
