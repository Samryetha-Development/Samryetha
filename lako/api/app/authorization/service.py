from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import Permission, Role, role_permissions, user_roles
from app.sessions.dependencies import AuthContext, require_auth


async def authorize(db: AsyncSession, user_id, permission: str) -> bool:
    query = (
        select(Permission.id)
        .join(role_permissions)
        .join(Role)
        .join(user_roles)
        .where(user_roles.c.user_id == user_id, Permission.name == permission)
    )
    return (await db.execute(query)).first() is not None


def require_permission(permission: str):
    async def dependency(ctx: AuthContext = Depends(require_auth), db: AsyncSession = Depends(get_db)) -> AuthContext:
        if not await authorize(db, ctx.user.id, permission):
            raise ApiError(403, "FORBIDDEN", "Permission denied")
        return ctx

    return dependency
