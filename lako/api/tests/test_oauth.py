import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import jwt
from sqlalchemy import select

from app.common.database import SessionFactory
from app.common.models import AuthorizationCode, OAuthClient, OAuthRedirectURI, utcnow


def verifier_and_challenge():
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(challenge=None, **changes):
    base = {
        "response_type": "code",
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:4000/auth/callback",
        "scope": "openid profile email",
        "state": "fixed-state",
        "nonce": "fixed-nonce",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    base.update(changes)
    return base


async def get_code(client, challenge):
    response = await client.get("/oauth/authorize", params=authorize_params(challenge))
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"] == ["fixed-state"]
    return query["code"][0]


async def test_discovery(client):
    body = (await client.get("/.well-known/openid-configuration")).json()
    assert body["issuer"] == "http://localhost:3000"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert (await client.get("/.well-known/jwks.json")).json()["keys"][0]["alg"] == "RS256"


async def test_authorize_redirects_to_login_without_session(client):
    _, challenge = verifier_and_challenge()
    response = await client.get("/oauth/authorize", params=authorize_params(challenge))
    assert response.status_code == 302 and response.headers["location"].startswith("/login?return_to=")


async def test_complete_pkce_flow_and_userinfo(client, logged_in):
    verifier, challenge = verifier_and_challenge()
    code = await get_code(client, challenge)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:4000/auth/callback",
        "code_verifier": verifier,
    }
    response = await client.post("/oauth/token", data=form)
    assert response.status_code == 200
    tokens = response.json()
    assert tokens["token_type"] == "Bearer"
    claims = jwt.decode(tokens["id_token"], options={"verify_signature": False})
    assert claims["aud"] == "samryetha" and claims["nonce"] == "fixed-nonce"
    assert claims["acr"] == "urn:lako:aal:1" and claims["amr"] == ["password"]
    assert isinstance(claims["auth_time"], int)
    info = await client.get("/oauth/userinfo", headers={"authorization": f"Bearer {tokens['access_token']}"})
    assert info.json()["name"] == "Avocado" and info.json()["email"] == "avo@example.com"
    replay = await client.post("/oauth/token", data=form)
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


async def test_invalid_client_redirect_and_missing_pkce(client, logged_in):
    _, challenge = verifier_and_challenge()
    bad = await client.get("/oauth/authorize", params=authorize_params(challenge, client_id="unknown"))
    assert bad.status_code == 400
    missing = await client.get("/oauth/authorize", params=authorize_params(None))
    assert parse_qs(urlparse(missing.headers["location"]).query)["state"] == ["fixed-state"]


async def test_exact_redirect_uri_and_wrong_exchange_bindings(client, logged_in):
    verifier, challenge = verifier_and_challenge()
    bad = await client.get(
        "/oauth/authorize", params=authorize_params(challenge, redirect_uri="http://localhost:4000/auth/callback/evil")
    )
    assert bad.status_code == 400
    code = await get_code(client, challenge)
    base = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:3000/auth/callback",
        "code_verifier": verifier,
    }
    assert (await client.post("/oauth/token", data=base)).json()["error"] == "invalid_grant"


async def test_invalid_verifier_does_not_consume_code(client, logged_in):
    verifier, challenge = verifier_and_challenge()
    code = await get_code(client, challenge)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:4000/auth/callback",
        "code_verifier": "wrong" * 12,
    }
    assert (await client.post("/oauth/token", data=form)).json()["error"] == "invalid_grant"
    form["code_verifier"] = verifier
    assert (await client.post("/oauth/token", data=form)).status_code == 200


async def test_expired_code(client, logged_in):
    verifier, challenge = verifier_and_challenge()
    code = await get_code(client, challenge)
    async with SessionFactory() as db:
        record = (await db.execute(select(AuthorizationCode))).scalar_one()
        record.expires_at = utcnow()
        await db.commit()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "samryetha",
        "redirect_uri": "http://localhost:4000/auth/callback",
        "code_verifier": verifier,
    }
    assert (await client.post("/oauth/token", data=form)).json()["error"] == "invalid_grant"


async def test_wrong_client_cannot_exchange_code(client, logged_in):
    verifier, challenge = verifier_and_challenge()
    code = await get_code(client, challenge)
    async with SessionFactory() as db:
        evil = OAuthClient(client_id="other-client", name="Other", is_public=True)
        db.add(evil)
        await db.flush()
        db.add(OAuthRedirectURI(oauth_client_id=evil.id, uri="http://other.local/callback"))
        await db.commit()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "other-client",
        "redirect_uri": "http://other.local/callback",
        "code_verifier": verifier,
    }
    assert (await client.post("/oauth/token", data=form)).json()["error"] == "invalid_grant"
