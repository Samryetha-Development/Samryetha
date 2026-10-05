"""Short numeric codes delivered by email, carried on ``AuthenticationChallenge``.

Two flows share this module:

* **Sign-in / step-up** codes (``PURPOSE_LOGIN``) — bound to the user alone.
* **Address verification** codes (``PURPOSE_VERIFY``) — bound to one specific
  ``Identity`` row, so a code mailed to address X can only ever verify X even
  when the address list changes between "send" and "confirm".

The raw code never reaches the database. ``token_hash`` holds
``sha256(f"{purpose}:{scope_id or ''}:{code}")`` — the same treatment link
tokens get — and the binding is what keeps the row unforgeable and keeps two
users from sharing a lookup key (a 6-digit space has collisions; a hash without
a binding would let one user's code satisfy another's challenge).

Codes are attempt-limited and single-use, and issuing a new code invalidates the
previous one for the same user and purpose: only the newest code ever works.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Iterable, Sequence
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.email_templates import render_email
from app.common.errors import ApiError
from app.common.models import AuthenticationChallenge, utcnow
from app.security.core import token_hash

PURPOSE_LOGIN = "EMAIL_OTP"
PURPOSE_VERIFY = "EMAIL_VERIFY"

CODE_TTL = timedelta(minutes=10)
CODE_MAX_ATTEMPTS = 5


def generate_code() -> str:
    """A uniformly random 6-digit code (leading zeros preserved)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_hash(purpose: str, scope_id: object | None, code: str) -> str:
    return token_hash(f"{purpose}:{scope_id if scope_id is not None else ''}:{code}")


def _expired(challenge: AuthenticationChallenge) -> bool:
    return challenge.expires_at.replace(tzinfo=challenge.expires_at.tzinfo or utcnow().tzinfo) <= utcnow()


async def issue_code(
    db: AsyncSession,
    *,
    user_id: object,
    purpose: str,
    scope_id: object | None = None,
    ttl: timedelta = CODE_TTL,
) -> str:
    """Mint a code for ``purpose`` and drop any previous pending one.

    Dropping the predecessor is deliberate: two live codes for one mailbox turn
    "the newest email wins" into "any code we ever sent still works".
    """
    await db.execute(
        delete(AuthenticationChallenge).where(
            AuthenticationChallenge.user_id == user_id,
            AuthenticationChallenge.purpose == purpose,
        )
    )
    code = generate_code()
    db.add(
        AuthenticationChallenge(
            token_hash=_code_hash(purpose, scope_id, code),
            user_id=user_id,
            purpose=purpose,
            expires_at=utcnow() + ttl,
        )
    )
    await db.flush()
    return code


async def _pending_challenge(
    db: AsyncSession, *, user_id: object, purpose: str
) -> AuthenticationChallenge | None:
    """The newest still-usable challenge for this user and purpose, if any."""
    challenge = (
        await db.execute(
            select(AuthenticationChallenge)
            .where(
                AuthenticationChallenge.user_id == user_id,
                AuthenticationChallenge.purpose == purpose,
                AuthenticationChallenge.used_at.is_(None),
            )
            .order_by(AuthenticationChallenge.created_at.desc())
        )
    ).scalars().first()
    if challenge is None or _expired(challenge) or challenge.attempt_count >= CODE_MAX_ATTEMPTS:
        return None
    return challenge


async def has_pending_code(db: AsyncSession, *, user_id: object, purpose: str) -> bool:
    """True when a live code exists — lets a caller skip a factor nobody asked for."""
    return await _pending_challenge(db, user_id=user_id, purpose=purpose) is not None


async def consume_code(
    db: AsyncSession,
    *,
    user_id: object,
    purpose: str,
    code: str,
    scope_ids: Sequence[object | None] | Iterable[object | None] = (None,),
    error_code: str = "INVALID_MFA_CODE",
    error_status: int = 401,
) -> tuple[AuthenticationChallenge, object | None]:
    """Verify and burn the pending code, or raise.

    ``scope_ids`` lists the bindings that would be acceptable for this submit
    (the verification flow passes every unverified identity id; sign-in passes
    ``None``). Unknown code, expired code and exhausted attempts all raise the
    same error — the caller must not turn the response into an oracle.

    Returns ``(challenge, matched_scope_id)`` so a scope-bound caller learns
    exactly which address the code proved control of. ``matched_scope_id`` is
    ``None`` for the unbound (sign-in) purpose as well as when ``None`` was the
    binding that matched, so callers that care must not test it for ``None``.
    """
    challenge = await _pending_challenge(db, user_id=user_id, purpose=purpose)

    def reject() -> ApiError:
        return ApiError(error_status, error_code, "Invalid or expired verification code")

    if challenge is None:
        raise reject()

    supplied = code.strip()
    matched: object | None = None
    found = False
    if supplied:
        for scope_id in scope_ids:
            if hmac.compare_digest(challenge.token_hash, _code_hash(purpose, scope_id, supplied)):
                matched, found = scope_id, True
                break
    if not found:
        challenge.attempt_count += 1
        if challenge.attempt_count >= CODE_MAX_ATTEMPTS:
            challenge.used_at = utcnow()
        await db.flush()
        raise reject()
    challenge.used_at = utcnow()
    await db.flush()
    return challenge, matched


def render_code_email(*, heading: str, intro: str, code: str, outro: str) -> tuple[str, str]:
    """Return ``(text, html)`` for a code email.

    The code is its own paragraph so it stays greppable in the plain-text part
    (tests extract it from ``mailer.outbox`` and users can read it without HTML).
    """
    text = f"{intro}\n\n{code}\n\n{outro}\n"
    return text, render_email(heading=heading, intro=f"{intro}\n\n{code}\n\n{outro}")
