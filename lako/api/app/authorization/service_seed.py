from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.models import OAuthClient, OAuthRedirectURI, Permission, Role

PERMISSIONS = [
    "lako.account.read",
    "lako.account.manage",
    "lako.device.read",
    "lako.device.revoke",
    "samryetha.post.read",
    "samryetha.post.create",
    "samryetha.post.delete",
    "samryetha.user.ban",
]


async def seed_defaults(db: AsyncSession) -> None:
    permissions: dict[str, Permission] = {}
    for name in PERMISSIONS:
        item = (await db.execute(select(Permission).where(Permission.name == name))).scalar_one_or_none()
        if not item:
            item = Permission(name=name)
            db.add(item)
        permissions[name] = item
    await db.flush()
    admin = (await db.execute(select(Role).where(Role.name == "lako.admin"))).scalar_one_or_none()
    if not admin:
        admin = Role(name="lako.admin")
        db.add(admin)
    admin.permissions = list(permissions.values())
    client = (await db.execute(select(OAuthClient).where(OAuthClient.client_id == "samryetha"))).scalar_one_or_none()
    if not client:
        client = OAuthClient(client_id="samryetha", name="Samryetha", is_public=True, auto_consent=True)
        db.add(client)
        await db.flush()
    for uri in ["http://localhost:3000/auth/callback", "http://localhost:4000/auth/callback"]:
        exists = (
            await db.execute(
                select(OAuthRedirectURI).where(
                    OAuthRedirectURI.oauth_client_id == client.id, OAuthRedirectURI.uri == uri
                )
            )
        ).scalar_one_or_none()
        if not exists:
            db.add(OAuthRedirectURI(oauth_client_id=client.id, uri=uri))
    await db.commit()
