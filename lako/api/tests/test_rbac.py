from sqlalchemy import select

from app.authentication.service import register
from app.authorization.service import authorize
from app.common.database import SessionFactory
from app.common.models import Role, user_roles


async def test_permission_assignment_allowed_and_denied():
    async with SessionFactory() as db:
        user = await register(db, "moderator", "mod@example.com", "correct horse battery", "Mod")
        assert not await authorize(db, user.id, "samryetha.user.ban")
        role = (await db.execute(select(Role).where(Role.name == "lako.admin"))).scalar_one()
        await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
        await db.commit()
        assert await authorize(db, user.id, "samryetha.user.ban")
