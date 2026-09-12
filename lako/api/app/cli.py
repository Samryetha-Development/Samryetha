import argparse
import asyncio

from sqlalchemy import select

from app.authentication.service import register
from app.authorization.service_seed import seed_defaults
from app.common.database import SessionFactory
from app.common.models import Role, user_roles


async def seed() -> None:
    async with SessionFactory() as db:
        await seed_defaults(db)
    print("Seeded roles, permissions, and Samryetha OAuth client.")


async def create_admin(username: str, email: str, password: str, display_name: str) -> None:
    async with SessionFactory() as db:
        await seed_defaults(db)
        user = await register(db, username, email, password, display_name)
        roles = (
            await db.execute(select(Role).where(Role.name.in_(["lako.admin", "samryetha-admins"])))
        ).scalars().all()
        for role in roles:
            await db.execute(user_roles.insert().values(user_id=user.id, role_id=role.id))
        await db.commit()
    print(f"Created administrator {username} ({user.id}).")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")
    admin = sub.add_parser("create-admin")
    admin.add_argument("--username", required=True)
    admin.add_argument("--email", required=True)
    admin.add_argument("--password", required=True)
    admin.add_argument("--display-name", default="Administrator")
    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(seed())
    else:
        asyncio.run(create_admin(args.username, args.email, args.password, args.display_name))


if __name__ == "__main__":
    main()
