"""Recent-AAL2 step-up guard, shared by sensitive account operations."""

from app.common.errors import ApiError
from app.common.models import AssuranceLevel, utcnow
from app.sessions.dependencies import AuthContext

STEP_UP_MAX_AGE_SECONDS = 600


def require_recent_aal2(ctx: AuthContext) -> None:
    """Require an AAL2 session verified within the last STEP_UP_MAX_AGE_SECONDS."""
    verified_at = ctx.session.assurance_verified_at
    if ctx.session.assurance_level != AssuranceLevel.AAL2 or verified_at is None:
        raise ApiError(403, "STEP_UP_REQUIRED", "Recent AAL2 authentication required")
    aware = verified_at.replace(tzinfo=verified_at.tzinfo or utcnow().tzinfo)
    if (utcnow() - aware).total_seconds() > STEP_UP_MAX_AGE_SECONDS:
        raise ApiError(403, "STEP_UP_REQUIRED", "Recent AAL2 authentication required")
