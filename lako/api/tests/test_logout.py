"""OIDC RP-initiated logout: discovery advertises it; end-session revokes safely."""

from __future__ import annotations

from app.common.config import get_settings


async def test_discovery_advertises_end_session(client):
    document = (await client.get("/.well-known/openid-configuration")).json()
    assert document["end_session_endpoint"] == f"{get_settings().oidc_issuer}/oauth/end-session"


async def test_end_session_revokes_session_and_redirects_home(client, logged_in):
    assert (await client.get("/api/auth/me")).status_code == 200
    response = await client.get("/oauth/end-session")
    assert response.status_code == 302
    assert response.headers["location"] == get_settings().app_origin
    # The session is revoked and the cookie cleared.
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_end_session_rejects_untrusted_redirect(client, logged_in):
    response = await client.get(
        "/oauth/end-session", params={"post_logout_redirect_uri": "https://evil.example/steal"}
    )
    assert response.status_code == 302
    assert response.headers["location"] == get_settings().app_origin


async def test_end_session_allows_trusted_origin_and_echoes_state(client, logged_in):
    response = await client.get(
        "/oauth/end-session",
        params={"post_logout_redirect_uri": "http://localhost:4000/done?x=1", "state": "abc"},
    )
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:4000/done?x=1&state=abc"


async def test_end_session_without_session_is_harmless(client):
    response = await client.get("/oauth/end-session")
    assert response.status_code == 302
    assert response.headers["location"] == get_settings().app_origin
