from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import audit
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import AssuranceLevel, Device, Session, User, UserStatus, utcnow
from app.security.core import token_hash

SESSION_COOKIE = "lako_session"
DEVICE_COOKIE = "lako_device"


@dataclass
class AuthContext:
    user: User
    session: Session
    device: Device


async def get_current_session(request: Request, db: AsyncSession = Depends(get_db)) -> AuthContext:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        raise ApiError(401, "UNAUTHORIZED", "Authentication required")
    session = (await db.execute(select(Session).where(Session.token_hash == token_hash(raw)))).scalar_one_or_none()
    if not session:
        raise ApiError(401, "UNAUTHORIZED", "Authentication required")
    if session.revoked_at:
        await audit(db, "security.revoked_session_used", target_user_id=session.user_id, session_id=session.id)
        await db.commit()
        raise ApiError(401, "SESSION_REVOKED", "Session has been revoked")
    if session.expires_at.replace(tzinfo=session.expires_at.tzinfo or utcnow().tzinfo) <= utcnow():
        raise ApiError(401, "SESSION_EXPIRED", "Session has expired")
    device = await db.get(Device, session.device_id)
    user = await db.get(User, session.user_id)
    if not device or device.revoked_at or not user or user.status != UserStatus.ACTIVE:
        raise ApiError(401, "SESSION_REVOKED", "Session has been revoked")
    session.last_seen_at = utcnow()
    device.last_seen_at = utcnow()
    await db.commit()
    return AuthContext(user=user, session=session, device=device)


get_current_user = get_current_session
require_auth = get_current_session


def require_aal(minimum: AssuranceLevel):
    levels = {AssuranceLevel.AAL1: 1, AssuranceLevel.AAL2: 2, AssuranceLevel.AAL3: 3}

    async def dependency(ctx: AuthContext = Depends(require_auth)) -> AuthContext:
        if levels[ctx.session.assurance_level] < levels[minimum]:
            raise ApiError(403, "STEP_UP_REQUIRED", f"{minimum.value} authentication required")
        return ctx

    return dependency
