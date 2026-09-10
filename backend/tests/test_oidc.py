from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from sqlalchemy import select, update

from samryetha.config import Settings
from samryetha.db import now_ms
from samryetha.errors import ApiError
from samryetha.main import create_app
from samryetha.oidc import OidcClient, consume_login, safe_return_to
from samryetha.schema import oidc_identities, users


class FakeOidcClient:
    def __init__(self, claims: dict) -> None:
        self.claims = claims
        self.exchange_args: tuple[str, str, str] | None = None

    def authorization_url(self, state: str, nonce: str, challenge: str) -> str:
        return "https://auth.samryetha.test/authorize?" + parse_url_params(
            {"state": state, "nonce": nonce, "code_challenge": challenge}
        )

    def exchange_and_validate(self, code: str, verifier: str, nonce: str) -> dict:
        self.exchange_args = (code, verifier, nonce)
        return self.claims

    def end_session_url(self) -> str:
        return "https://auth.samryetha.test/logout"


def parse_url_params(values: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return urlencode(values)


@pytest.fixture
def oidc_client(tmp_path):
    settings = Settings(
        _env_file=None,
        node_env="test",
        database_url=str(tmp_path / "oidc.db"),
        upload_dir=str(tmp_path / "uploads"),
        app_origin="https://samryetha.test",
        oidc_issuer="https://auth.samryetha.test/application/o/samryetha/",
        oidc_client_id="forum",
        oidc_client_secret="secret",
        oidc_redirect_uri="https://samryetha.test/api/auth/callback",
        oidc_post_logout_redirect_uri="https://samryetha.test/",
        oidc_allowed_groups="samryetha-users,samryetha-admins",
    )
    app = create_app(settings)
    fake = FakeOidcClient(
        {
            "iss": settings.oidc_issuer,
            "sub": "subject-123",
            "preferred_username": "Alice",
            "name": "Alice Example",
            "email": "alice@example.edu.cn",
            "email_verified": True,
            "groups": ["samryetha-users"],
        }
    )
    app.state.oidc = fake
    with TestClient(app, base_url="https://samryetha.test") as client:
        yield client, fake


def begin(client: TestClient, return_to: str = "/") -> tuple[str, str]:
    response = client.get("/api/auth/login", params={"returnTo": return_to}, follow_redirects=False)
    assert response.status_code == 302, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["state"][0], query["nonce"][0]


def test_oidc_config_and_pkce_login_creates_local_session(oidc_client):
    client, fake = oidc_client
    assert client.get("/api/auth/config").json() == {"oidcEnabled": True}
    state, nonce = begin(client, "/settings")
    response = client.get(
        "/api/auth/callback",
        params={"code": "one-time-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    assert response.headers["location"] == "https://samryetha.test/settings"
    assert "samryetha_session" in response.cookies
    assert fake.exchange_args is not None
    assert fake.exchange_args[0] == "one-time-code"
    assert fake.exchange_args[2] == nonce

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    with client.app.state.db.request_conn() as conn:
        identity = conn.execute(select(oidc_identities)).one()
        assert identity.issuer == client.app.state.settings.oidc_issuer
        assert identity.subject == "subject-123"


def test_oidc_reuses_identity_and_maps_admin_group(oidc_client):
    client, fake = oidc_client
    state, _ = begin(client)
    assert client.get("/api/auth/callback", params={"code": "first", "state": state}, follow_redirects=False).status_code == 302
    first_id = client.get("/api/auth/me").json()["user"]["id"]
    client.post("/api/auth/logout")
    fake.claims["preferred_username"] = "RenamedAtProvider"
    fake.claims["groups"] = ["samryetha-admins"]
    state, _ = begin(client)
    assert client.get("/api/auth/callback", params={"code": "second", "state": state}, follow_redirects=False).status_code == 302
    user = client.get("/api/auth/me").json()["user"]
    assert user["id"] == first_id
    assert user["username"] == "alice"
    assert user["role"] == "admin"


def test_oidc_links_only_a_verified_matching_email(oidc_client):
    client, fake = oidc_client
    registered = client.post("/api/auth/register", json={"username": "legacy", "password": "password123"})
    legacy_id = registered.json()["userId"]
    with client.app.state.db.request_conn() as conn:
        conn.execute(
            update(users)
            .where(users.c.id == legacy_id)
            .values(email="alice@example.edu.cn", email_verified_at=now_ms(), status="active")
        )
    state, _ = begin(client)
    assert client.get("/api/auth/callback", params={"code": "link", "state": state}, follow_redirects=False).status_code == 302
    assert client.get("/api/auth/me").json()["user"]["id"] == legacy_id
    with client.app.state.db.request_conn() as conn:
        assert conn.execute(select(oidc_identities.c.user_id)).scalar_one() == legacy_id


def test_oidc_rejects_missing_allowed_group(oidc_client):
    client, fake = oidc_client
    fake.claims["groups"] = ["unrelated"]
    state, _ = begin(client)
    response = client.get("/api/auth/callback", params={"code": "denied", "state": state}, follow_redirects=False)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_oidc_state_is_one_time_and_return_path_is_local(oidc_client):
    client, _ = oidc_client
    state, _ = begin(client, "https://evil.example/")
    with client.app.state.db.request_conn() as conn:
        transaction = consume_login(conn, state)
    assert transaction["return_to"] == "/"
    with pytest.raises(ApiError):
        with client.app.state.db.request_conn() as conn:
            consume_login(conn, state)
    assert safe_return_to("//evil.example") == "/"
    assert safe_return_to("/safe?next=1") == "/safe?next=1"


def test_oidc_does_not_link_an_unverified_local_email(oidc_client):
    client, _ = oidc_client
    registered = client.post("/api/auth/register", json={"username": "unverified", "password": "password123"})
    legacy_id = registered.json()["userId"]
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.id == legacy_id).values(email="alice@example.edu.cn"))
    state, _ = begin(client)
    assert client.get("/api/auth/callback", params={"code": "new", "state": state}, follow_redirects=False).status_code == 302
    assert client.get("/api/auth/me").json()["user"]["id"] != legacy_id


def test_oidc_client_verifies_signature_audience_issuer_expiry_and_nonce(monkeypatch):
    settings = Settings(
        _env_file=None,
        oidc_issuer="https://auth.samryetha.test/application/o/forum/",
        oidc_client_id="forum",
        oidc_client_secret="secret",
        oidc_redirect_uri="https://samryetha.test/api/auth/callback",
    )
    key = RSAKey.generate_key(auto_kid=True)
    claims = {
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_client_id,
        "sub": "subject-1",
        "nonce": "expected-nonce",
        "exp": int(time.time()) + 60,
    }
    encoded = jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key, algorithms=["RS256"])
    client = OidcClient(settings)
    client._discovery = {
        "issuer": settings.oidc_issuer,
        "authorization_endpoint": "https://auth.samryetha.test/authorize",
        "token_endpoint": "https://auth.samryetha.test/token",
        "jwks_uri": "https://auth.samryetha.test/jwks",
    }
    client._discovery_at = time.monotonic()
    client._jwks = {"keys": [key.as_dict()]}
    client._jwks_at = time.monotonic()

    def token_response(*args, **kwargs):
        return httpx.Response(200, json={"id_token": encoded}, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr(httpx, "post", token_response)
    verified = client.exchange_and_validate("code", "verifier", "expected-nonce")
    assert verified["sub"] == "subject-1"
    with pytest.raises(ApiError):
        client.exchange_and_validate("code", "verifier", "wrong-nonce")
