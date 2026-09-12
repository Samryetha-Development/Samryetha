import os
from pathlib import Path

import httpx
import pytest_asyncio

TEST_DB = Path(__file__).parent / "test-lako.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["COOKIE_SECURE"] = "false"
os.environ["OIDC_ISSUER"] = "http://localhost:3000"

from app.authorization.service_seed import seed_defaults
from app.common.database import SessionFactory, engine
from app.common.models import Base
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with SessionFactory() as db:
        await seed_defaults(db)
    yield
    await engine.dispose()
    TEST_DB.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:3000", follow_redirects=False
    ) as value:
        yield value


@pytest_asyncio.fixture
async def registered(client: httpx.AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "avocado",
            "email": "avo@example.com",
            "password": "correct horse battery",
            "display_name": "Avocado",
        },
    )
    assert response.status_code == 200 or response.status_code == 201
    return response.json()


@pytest_asyncio.fixture
async def logged_in(client: httpx.AsyncClient, registered):
    response = await client.post(
        "/api/auth/login",
        json={"login": "avocado", "password": "correct horse battery"},
        headers={"user-agent": "Mozilla/5.0 Chrome Windows"},
    )
    assert response.status_code == 200
    return response.json()
