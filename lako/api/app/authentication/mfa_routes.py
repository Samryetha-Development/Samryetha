from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.email_codes import (
    PURPOSE_LOGIN,
    issue_code,
    render_code_email,
)
from app.authentication.email_service import deliverable_email
from app.authentication.mfa_service import (
    active_totp,
    begin_totp_setup,
    confirm_totp_setup,
    replace_recovery_codes,
    verify_second_factor,
)
from app.authentication.routes import masked_email
from app.authentication.service import audit
from app.common.client_ip import client_ip
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.mailer import MAIL_SEND_ERRORS
from app.common.models import AssuranceLevel, Credential, CredentialType, utcnow
from app.common.ratelimit import check as check_rate_limit
from app.security.csrf import require_csrf
from app.security.stepup import require_recent_aal2
from app.sessions.dependencies import AuthContext, require_auth

router = APIRouter(prefix="/api/account/mfa", tags=["multi-factor authentication"])


class CodeInput(BaseModel):
    code: str


@router.get("")
async def mfa_status(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> dict:
    enabled = await active_totp(db, ctx.user.id) is not None
    # Recovery codes also back passkey-only accounts (issued on first passkey),
    # so report the count regardless of TOTP.
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
        or 0
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


@router.post("/step-up/code/request")
async def request_step_up_code(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    """Mail a step-up code.

    This is what lets a password or passkey account satisfy ``require_recent_aal2``
    without an authenticator app and without recovery codes — before this
    endpoint existed, an account with no TOTP and no recovery codes could not
    pass any sensitive-operation guard at all. The address must be verified:
    a code is never sent anywhere the account has not already proven control of.
    """
    require_csrf(request)
    await check_rate_limit(f"step-up-email-code:{client_ip(request)}", 5)
    email = await deliverable_email(db, ctx.user.id)
    if email is None:
        raise ApiError(409, "NO_VERIFIED_EMAIL", "Add and verify an email address first")
    code = await issue_code(db, user_id=ctx.user.id, purpose=PURPOSE_LOGIN)
    text, html = render_code_email(
        heading="Confirm it's you",
        intro="Enter this code to continue with a security change on your Samryetha account.",
        code=code,
        outro="The code is valid for 10 minutes. If this wasn't you, change your password.",
    )
    try:
        await request.app.state.mailer.send(
            to=email.identifier, subject="Your Samryetha security code", text=text, html=html
        )
    except MAIL_SEND_ERRORS:
        raise ApiError(503, "MAIL_UNAVAILABLE", "Email service is temporarily unavailable")
    await audit(
        db,
        "auth.email_code_requested",
        actor_user_id=ctx.user.id,
        target_user_id=ctx.user.id,
        session_id=ctx.session.id,
        metadata_json={"scope": "step-up"},
    )
    await db.commit()
    return {"ok": True, "email": masked_email(email.identifier)}


@router.post("/step-up/email-code")
async def email_code_step_up(
    body: CodeInput, request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    """Redeem a mailed step-up code for AAL2."""
    require_csrf(request)
    await check_rate_limit(f"step-up-email-code-confirm:{client_ip(request)}", 20)
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
