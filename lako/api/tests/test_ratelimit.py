"""Rate limiting on abuse-sensitive endpoints."""

import pytest

from app.common import ratelimit
from app.common.errors import ApiError


async def test_login_rate_limited(client):
    for _ in range(30):
        response = await client.post("/api/auth/login", json={"login": "nobody", "password": "wrong"})
        assert response.status_code == 401
    limited = await client.post("/api/auth/login", json={"login": "nobody", "password": "wrong"})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


async def test_reset_request_rate_limited(client):
    for _ in range(5):
        response = await client.post("/api/auth/password/reset/request", json={"login": "nobody"})
        assert response.json() == {"ok": True}
    limited = await client.post("/api/auth/password/reset/request", json={"login": "nobody"})
    assert limited.status_code == 429


async def test_limiter_resets():
    await ratelimit.check("probe", 1)
    with pytest.raises(ApiError) as exc_info:
        await ratelimit.check("probe", 1)
    assert exc_info.value.status_code == 429
    await ratelimit.reset_all()
    await ratelimit.check("probe", 1)
