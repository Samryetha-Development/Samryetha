from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.mfa_service import (
    active_totp,
    begin_totp_setup,
    confirm_totp_setup,
    replace_recovery_codes,
    verify_second_factor,
)
from app.authentication.service import audit
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import AssuranceLevel, Credential, CredentialType, utcnow
from app.security.csrf import require_csrf
from app.sessions.dependencies import AuthContext, require_auth

router = APIRouter(prefix="/api/account/mfa", tags=["multi-factor authentication"])
STEP_UP_MAX_AGE_SECONDS = 600


class CodeInput(BaseModel):
    code: str


def require_recent_aal2(ctx: AuthContext) -> None:
    verified_at = ctx.session.assurance_verified_at
    if ctx.session.assurance_level != AssuranceLevel.AAL2 or verified_at is None:
        raise ApiError(403, "STEP_UP_REQUIRED", "Recent AAL2 authentication required")
    aware = verified_at.replace(tzinfo=verified_at.tzinfo or utcnow().tzinfo)
    if (utcnow() - aware).total_seconds() > STEP_UP_MAX_AGE_SECONDS:
        raise ApiError(403, "STEP_UP_REQUIRED", "Recent AAL2 authentication required")


@router.get("")
async def mfa_status(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    enabled = await active_totp(db, ctx.user.id) is not None
    remaining = (
        await db.scalar(
            select(func.count())
            .select_from(Credential)
            .where(
                Credential.user_id == ctx.user.id,
                Credential.type == CredentialType.RECOVERY_CODE,
                Credential.last_used_at.is_(None),
            )
        )
        if enabled
        else 0
    )
    return {
        "totp_enabled": enabled,
        "recovery_codes_remaining": remaining,
        "assurance_level": ctx.session.assurance_level.value,
    }


@router.post("/totp/setup")
async def setup_totp(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    return await begin_totp_setup(db, ctx.user)


@router.post("/totp/confirm")
async def confirm_totp(
    body: CodeInput, request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    codes = await confirm_totp_setup(db, ctx.user, ctx.session, body.code)
    return {"enabled": True, "recovery_codes": codes, "assurance_level": "AAL2"}


@router.post("/step-up")
async def step_up(
    body: CodeInput, request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    method = await verify_second_factor(db, ctx.user.id, body.code)
    ctx.session.assurance_level = AssuranceLevel.AAL2
    ctx.session.assurance_verified_at = utcnow()
    ctx.session.authentication_method = f"PASSWORD_{method}"
    await audit(
        db,
        "authentication.step_up",
        actor_user_id=ctx.user.id,
        target_user_id=ctx.user.id,
        session_id=ctx.session.id,
        metadata_json={"method": method},
    )
    await db.commit()
    return {"assurance_level": "AAL2", "method": method}


@router.post("/recovery-codes/regenerate")
async def regenerate_codes(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    require_recent_aal2(ctx)
    codes = await replace_recovery_codes(db, ctx.user.id)
    await audit(
        db,
        "mfa.recovery_codes.regenerated",
        actor_user_id=ctx.user.id,
        target_user_id=ctx.user.id,
        session_id=ctx.session.id,
    )
    await db.commit()
    return {"recovery_codes": codes}


@router.post("/totp/disable")
async def disable_totp(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    require_recent_aal2(ctx)
    await db.execute(
        delete(Credential).where(
            Credential.user_id == ctx.user.id, Credential.type.in_([CredentialType.TOTP, CredentialType.RECOVERY_CODE])
        )
    )
    ctx.session.assurance_level = AssuranceLevel.AAL1
    ctx.session.assurance_verified_at = utcnow()
    await audit(
        db, "mfa.totp.disabled", actor_user_id=ctx.user.id, target_user_id=ctx.user.id, session_id=ctx.session.id
    )
    await db.commit()
    return {"enabled": False}
