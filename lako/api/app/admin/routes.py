"""Admin bulk provisioning for forum migration.

POST /api/admin/users/import creates Lako accounts for existing forum users
(idempotent: re-running converges flags and reports `exists`, or `extended`
when a missing username/email identity had to be attached). Auth is a
long random service token in `Authorization: Bearer <ADMIN_IMPORT_TOKEN>`;
the endpoint fail-closes with 403 when the token is unset. Intended to be
reachable only from the migration operator / internal network, never public.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_import_token
from app.authentication.service import USERNAME_PATTERN, audit, register
from app.common.client_ip import client_ip
from app.common.database import get_db
from app.common.errors import ApiError
from app.common.models import Identity, IdentityType, Role, user_roles
from app.common.ratelimit import check as check_rate_limit
from app.security.core import normalize_email, normalize_username

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_IMPORT_BATCH = 500


class ImportUser(BaseModel):
    model_config = ConfigDict(extra="ignore")
    username: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=1, max_length=320)
    display_name: str | None = Field(default=None, max_length=120)
    # Optional initial password (>=12 chars). Absent = unguessable random value;
    # the user then sets a real password via the invite/reset email flow.
    password: str | None = Field(default=None, max_length=256)
    # Only set for addresses known-good at the source (e.g. verified recovery
    # emails). Placeholder addresses must stay unverified.
    mark_email_verified: bool = False
    # Assigns the samryetha-admins role (drives the forum admin mapping).
    admin: bool = False


class ImportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    users: list[ImportUser] = Field(default_factory=list)
    dry_run: bool = False


async def _owner_ids(db: AsyncSession, username_norm: str, email_norm: str) -> tuple[object | None, object | None]:
    """Return (username owner id, email owner id) for the normalized identifiers."""
    rows = (
        (await db.execute(select(Identity).where(Identity.normalized_identifier.in_([username_norm, email_norm]))))
        .scalars()
        .all()
    )
    username_owner = next(
        (row.user_id for row in rows if row.type == IdentityType.USERNAME and row.normalized_identifier == username_norm),
        None,
    )
    email_owner = next(
        (row.user_id for row in rows if row.type == IdentityType.EMAIL and row.normalized_identifier == email_norm),
        None,
    )
    return username_owner, email_owner


async def _converge_flags(db: AsyncSession, user_id: object, email_norm: str, *, admin: bool, verified: bool) -> None:
    """Idempotently converge verified/admin flags (used for create/exists/extended paths).

    One-way only: flags are added, never removed. Re-running an import can
    grant `verified`/admin but will never revoke them, so repeats are safe.
    """
    if verified:
        email_row = (
            await db.execute(
                select(Identity).where(
                    Identity.user_id == user_id,
                    Identity.type == IdentityType.EMAIL,
                    Identity.normalized_identifier == email_norm,
                )
            )
        ).scalar_one_or_none()
        if email_row is not None:
            email_row.verified = True
    if admin:
        role = (await db.execute(select(Role).where(Role.name == "samryetha-admins"))).scalar_one_or_none()
        if role is not None:
            exists = (
                await db.execute(
                    select(user_roles).where(user_roles.c.user_id == user_id, user_roles.c.role_id == role.id)
                )
            ).first()
            if exists is None:
                await db.execute(user_roles.insert().values(user_id=user_id, role_id=role.id))


async def _extend_missing_identity(
    db: AsyncSession, owner: object, *, username: str, email: str, need_username: bool, need_email: bool
) -> str | None:
    """Create whichever of the username/email identities is missing.

    New email identities start unverified (callers converge `verified` via
    flags afterwards); new username identities are verified like at register.
    Returns an error code when the missing identifier collides, else None.
    """
    if need_username:
        username_norm = normalize_username(username)
        clash = (
            await db.execute(select(Identity).where(Identity.normalized_identifier == username_norm))
        ).scalar_one_or_none()
        if clash is not None:
            return "AMBIGUOUS_IDENTITIES"
        db.add(
            Identity(
                user_id=owner,
                type=IdentityType.USERNAME,
                identifier=username,
                normalized_identifier=username_norm,
                verified=True,
            )
        )
    if need_email:
        email_norm = normalize_email(email)
        clash = (
            await db.execute(select(Identity).where(Identity.normalized_identifier == email_norm))
        ).scalar_one_or_none()
        if clash is not None:
            return "AMBIGUOUS_IDENTITIES"
        db.add(
            Identity(
                user_id=owner,
                type=IdentityType.EMAIL,
                identifier=email,
                normalized_identifier=email_norm,
                verified=False,
            )
        )
    await db.flush()
    return None


async def _import_one(db: AsyncSession, item: ImportUser, *, dry_run: bool) -> dict:
    username = item.username.strip()
    email = item.email.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        return {"username": item.username, "status": "invalid", "error": "INVALID_USERNAME"}
    email_norm = normalize_email(email)
    if "@" not in email_norm:
        return {"username": username, "status": "invalid", "error": "INVALID_EMAIL"}
    if item.password is not None and len(item.password) < 12:
        return {"username": username, "status": "invalid", "error": "WEAK_PASSWORD"}
    username_owner, email_owner = await _owner_ids(db, normalize_username(username), email_norm)
    if username_owner is not None and email_owner is not None and username_owner != email_owner:
        return {"username": username, "status": "invalid", "error": "AMBIGUOUS_IDENTITIES"}
    owner = username_owner if username_owner is not None else email_owner
    if owner is not None:
        if username_owner is not None and email_owner is not None:
            if not dry_run:
                await _converge_flags(db, owner, email_norm, admin=item.admin, verified=item.mark_email_verified)
            return {"username": username, "status": "exists", "lako_user_id": str(owner)}
        # The owner holds one identifier but lacks the other: attach the
        # missing identity instead of silently reporting `exists`, so a
        # re-run with fuller rows converges the account (`extended`).
        if dry_run:
            return {"username": username, "status": "extended", "lako_user_id": str(owner)}
        error = await _extend_missing_identity(
            db,
            owner,
            username=username,
            email=email,
            need_username=username_owner is None,
            need_email=email_owner is None,
        )
        if error is not None:
            return {"username": username, "status": "invalid", "error": error}
        await _converge_flags(db, owner, email_norm, admin=item.admin, verified=item.mark_email_verified)
        return {"username": username, "status": "extended", "lako_user_id": str(owner)}
    if dry_run:
        return {"username": username, "status": "created", "lako_user_id": None}
    password = item.password if item.password is not None else secrets.token_urlsafe(32)
    user = await register(db, username, email, password, item.display_name or username)
    await _converge_flags(db, user.id, email_norm, admin=item.admin, verified=item.mark_email_verified)
    await audit(db, "admin.user_imported", target_user_id=user.id, metadata_json={"dry_run": False})
    return {"username": username, "status": "created", "lako_user_id": str(user.id)}


@router.post("/users/import")
async def import_users(body: ImportBody, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Bulk-provision accounts row by row; re-runs converge.

    Each created user commits immediately inside `register`, so an
    interrupted batch keeps its prefix — re-running converges flags and
    extends missing identities (`exists`/`extended`) instead of duplicating.
    Flag convergence for pre-existing owners plus the batch audit commit once
    at the end.
    """
    require_import_token(request)
    host = client_ip(request) or "unknown"
    await check_rate_limit(f"admin-import:{host}", 120)
    if len(body.users) > MAX_IMPORT_BATCH:
        raise ApiError(413, "BATCH_TOO_LARGE", f"At most {MAX_IMPORT_BATCH} users per request")
    results = [await _import_one(db, item, dry_run=body.dry_run) for item in body.users]
    counts = {"created": 0, "exists": 0, "extended": 0, "invalid": 0}
    for result in results:
        counts[result["status"]] += 1
    if not body.dry_run:
        ip = client_ip(request)
        await audit(db, "admin.users_imported", ip=ip, metadata_json={"counts": counts, "dry_run": False})
        await db.commit()
    return {"dry_run": body.dry_run, "total": len(body.users), **counts, "results": results}
