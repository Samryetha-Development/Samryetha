"""POST /api/admin/users/import: service-token 门禁、dry-run、幂等、校验。"""

import uuid

import pytest
from sqlalchemy import func, select

from app.common.config import get_settings
from app.common.database import SessionFactory
from app.common.models import Identity, IdentityType, Role, User, user_roles

TOKEN = "test-import-token"
HEADERS = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def import_token(monkeypatch):
    monkeypatch.setenv("ADMIN_IMPORT_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _user_count() -> int:
    async with SessionFactory() as db:
        return (await db.execute(select(func.count()).select_from(User))).scalar_one()


async def test_import_disabled_without_token(client, monkeypatch):
    monkeypatch.delenv("ADMIN_IMPORT_TOKEN", raising=False)
    get_settings.cache_clear()
    response = await client.post("/api/admin/users/import", json={"users": []})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_IMPORT_DISABLED"


async def test_import_rejects_bad_token(client):
    response = await client.post(
        "/api/admin/users/import", json={"users": []}, headers={"authorization": "Bearer wrong"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_IMPORT_DENIED"


async def test_import_dry_run_writes_nothing(client, registered):
    before = await _user_count()
    body = {
        "dry_run": True,
        "users": [
            {"username": "avocado", "email": "other@example.com"},
            {"username": "fresh", "email": "fresh@example.com", "admin": True, "mark_email_verified": True},
            {"username": "!!", "email": "bad@example.com"},
        ],
    }
    response = await client.post("/api/admin/users/import", json=body, headers=HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert [r["status"] for r in payload["results"]] == ["exists", "created", "invalid"]
    assert await _user_count() == before


async def test_import_creates_verified_admin_and_is_idempotent(client):
    first = await client.post(
        "/api/admin/users/import",
        json={
            "users": [
                {
                    "username": "migrated",
                    "email": "migrated@example.com",
                    "display_name": "Migrated",
                    "password": "a-very-long-password",
                    "mark_email_verified": True,
                    "admin": True,
                }
            ]
        },
        headers=HEADERS,
    )
    assert first.status_code == 200
    created = first.json()["results"][0]
    assert created["status"] == "created"
    user_id = created["lako_user_id"]
    user_uuid = uuid.UUID(user_id)

    async with SessionFactory() as db:
        email_row = (
            await db.execute(
                select(Identity).where(
                    Identity.user_id == user_uuid, Identity.type == IdentityType.EMAIL
                )
            )
        ).scalar_one()
        assert email_row.verified is True
        admin_role = (await db.execute(select(Role).where(Role.name == "samryetha-admins"))).scalar_one()
        link = (
            await db.execute(
                select(user_roles).where(user_roles.c.user_id == user_uuid, user_roles.c.role_id == admin_role.id)
            )
        ).first()
        assert link is not None

    # provided password works
    login = await client.post("/api/auth/login", json={"login": "migrated", "password": "a-very-long-password"})
    assert login.status_code == 200

    # rerun converges to exists with the same id
    second = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": "migrated", "email": "migrated@example.com"}]},
        headers=HEADERS,
    )
    assert second.json()["results"][0] == {"username": "migrated", "status": "exists", "lako_user_id": user_id}


async def test_import_rejects_ambiguous_identities(client, registered):
    await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": "second", "email": "second@example.com"}]},
        headers=HEADERS,
    )
    response = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": "avocado", "email": "second@example.com"}]},
        headers=HEADERS,
    )
    assert response.json()["results"][0]["status"] == "invalid"
    assert response.json()["results"][0]["error"] == "AMBIGUOUS_IDENTITIES"


async def test_import_rejects_weak_password_and_caps_batch(client):
    weak = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": "weakpw", "email": "weak@example.com", "password": "short"}]},
        headers=HEADERS,
    )
    assert weak.json()["results"][0]["error"] == "WEAK_PASSWORD"
    huge = await client.post(
        "/api/admin/users/import",
        json={"users": [{"username": f"u{i}", "email": f"u{i}@example.com"} for i in range(501)]},
        headers=HEADERS,
    )
    assert huge.status_code == 413
    assert huge.json()["error"]["code"] == "BATCH_TOO_LARGE"
