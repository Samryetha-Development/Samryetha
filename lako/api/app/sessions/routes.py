import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import audit
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import Device, Session, utcnow
from app.security.csrf import CSRF_COOKIE, require_csrf
from app.sessions.dependencies import SESSION_COOKIE, AuthContext, require_auth

router = APIRouter(tags=["sessions"])


@router.post("/api/sessions/logout")
async def logout(
    request: Request, response: Response, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    ctx.session.revoked_at = utcnow()
    await audit(
        db,
        "session.revoked",
        actor_user_id=ctx.user.id,
        target_user_id=ctx.user.id,
        session_id=ctx.session.id,
        device_id=ctx.device.id,
    )
    await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@router.post("/api/sessions/logout-others")
async def logout_others(
    request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    result = await db.execute(
        update(Session)
        .where(Session.user_id == ctx.user.id, Session.id != ctx.session.id, Session.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await audit(
        db,
        "session.revoked",
        actor_user_id=ctx.user.id,
        target_user_id=ctx.user.id,
        session_id=ctx.session.id,
        metadata_json={"scope": "others"},
    )
    await db.commit()
    return {"revoked": result.rowcount}


@router.get("/api/sessions")
async def list_sessions(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.user_id == ctx.user.id, Session.revoked_at.is_(None))
                .order_by(Session.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(item.id),
            "device_id": str(item.device_id),
            "current": item.id == ctx.session.id,
            "last_seen_at": item.last_seen_at,
            "expires_at": item.expires_at,
            "ip": item.ip,
        }
        for item in sessions
    ]


@router.get("/api/devices")
async def list_devices(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> list[dict]:
    devices = (
        (await db.execute(select(Device).where(Device.user_id == ctx.user.id).order_by(Device.last_seen_at.desc())))
        .scalars()
        .all()
    )
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "last_seen_at": item.last_seen_at,
            "trusted": item.trusted,
            "revoked_at": item.revoked_at,
            "current": item.id == ctx.device.id,
            "user_agent": item.last_user_agent,
        }
        for item in devices
    ]


@router.post("/api/devices/{device_id}/revoke")
async def revoke_device(
    device_id: uuid.UUID, request: Request, ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)
) -> dict:
    require_csrf(request)
    device = await db.get(Device, device_id)
    if not device or device.user_id != ctx.user.id:
        raise ApiError(404, "DEVICE_NOT_FOUND", "Device not found")
    device.revoked_at = utcnow()
    await db.execute(
        update(Session).where(Session.device_id == device.id, Session.revoked_at.is_(None)).values(revoked_at=utcnow())
    )
    await audit(db, "device.revoked", actor_user_id=ctx.user.id, target_user_id=ctx.user.id, device_id=device.id)
    await db.commit()
    return {"ok": True}
