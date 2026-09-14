from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.config import get_settings
from app.common.models import OAuthClient, OAuthRedirectURI, Permission, Role
from app.security.core import token_hash

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
    admin = (
        await db.execute(select(Role).options(selectinload(Role.permissions)).where(Role.name == "lako.admin"))
    ).scalar_one_or_none()
    if not admin:
        admin = Role(name="lako.admin", permissions=list(permissions.values()))
        db.add(admin)
    else:
        admin.permissions = list(permissions.values())
    for name in ["samryetha-users", "samryetha-admins"]:
        role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
        if not role:
            db.add(Role(name=name))
    settings = get_settings()
    client = (
        await db.execute(select(OAuthClient).where(OAuthClient.client_id == settings.samryetha_client_id))
    ).scalar_one_or_none()
    if not client:
        client = OAuthClient(client_id=settings.samryetha_client_id, name="Samryetha", auto_consent=True)
        db.add(client)
        await db.flush()
    client.is_public = not bool(settings.samryetha_client_secret)
    client.client_secret_hash = token_hash(settings.samryetha_client_secret) if settings.samryetha_client_secret else None
    for uri in settings.samryetha_redirect_uri_list:
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
