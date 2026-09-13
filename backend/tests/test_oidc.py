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


def claim_ticket_from(response) -> str:
    location = response.headers["location"]
    assert "/claim?ticket=" in location, location
    return parse_qs(urlparse(location).query)["ticket"][0]


def activate_user(client: TestClient, user_id: int) -> None:
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(status="active"))


def test_oidc_config_and_pkce_login_creates_local_session(oidc_client):
    client, fake = oidc_client
    config = client.get("/api/auth/config").json()
    assert config["oidcEnabled"] is True
    assert config["passwordAuthEnabled"] is True
    state, nonce = begin(client, "/settings")
    response = client.get(
        "/api/auth/callback",
        params={"code": "one-time-code", "state": state},
        follow_redirects=False,
    )
    # 无映射、无可信邮箱：不再静默建空号，转认领页
    assert response.status_code == 302, response.text
    assert "samryetha_session" not in response.cookies
    assert fake.exchange_args is not None
    assert fake.exchange_args[0] == "one-time-code"
    assert fake.exchange_args[2] == nonce
    ticket = claim_ticket_from(response)
    info = client.get("/api/auth/claim", params={"ticket": ticket})
    assert info.status_code == 200
    assert info.json()["email"] == "alice@example.edu.cn"
    created = client.post("/api/auth/claim/new", json={"ticket": ticket})
    assert created.status_code == 200, created.text
    assert "samryetha_session" in created.cookies
    me = client.get("/api/auth/me").json()["user"]
    assert me["username"] == "alice"
    with client.app.state.db.request_conn() as conn:
        identity = conn.execute(select(oidc_identities)).one()
        assert identity.issuer == client.app.state.settings.oidc_issuer
        assert identity.subject == "subject-123"


def test_oidc_reuses_identity_and_maps_admin_group(oidc_client):
    client, fake = oidc_client
    state, _ = begin(client)
    ticket = claim_ticket_from(
        client.get("/api/auth/callback", params={"code": "first", "state": state}, follow_redirects=False)
    )
    assert client.post("/api/auth/claim/new", json={"ticket": ticket}).status_code == 200
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
    client, fake = oidc_client
    registered = client.post("/api/auth/register", json={"username": "unverified", "password": "password123"})
    legacy_id = registered.json()["userId"]
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.id == legacy_id).values(email="alice@example.edu.cn"))
    state, _ = begin(client)
    response = client.get("/api/auth/callback", params={"code": "new", "state": state}, follow_redirects=False)
    assert response.status_code == 302
    # 未验证邮箱不再静默建号：转认领，且不建立任何会话
    claim_ticket_from(response)
    assert "samryetha_session" not in response.cookies
    assert client.get("/api/auth/me").status_code == 401


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


def test_claim_links_existing_account_with_password_proof(oidc_client):
    client, fake = oidc_client
    fake.claims["email_verified"] = False
    registered = client.post("/api/auth/register", json={"username": "olduser", "password": "old-password-123"})
    activate_user(client, registered.json()["userId"])
    state, _ = begin(client)
    ticket = claim_ticket_from(
        client.get("/api/auth/callback", params={"code": "claim", "state": state}, follow_redirects=False)
    )
    info = client.get("/api/auth/claim", params={"ticket": ticket})
    assert info.status_code == 200
    assert info.json()["email"] == "alice@example.edu.cn"
    linked = client.post("/api/auth/claim", json={"ticket": ticket, "username": "olduser", "password": "old-password-123"})
    assert linked.status_code == 200, linked.text
    assert "samryetha_session" in linked.cookies
    me = client.get("/api/auth/me").json()["user"]
    assert me["username"] == "olduser"
    # 绑定成功即作废旧密码：密码登录不再可用
    assert client.post("/api/auth/login", json={"username": "olduser", "password": "old-password-123"}).status_code == 401
    with client.app.state.db.request_conn() as conn:
        identity = conn.execute(select(oidc_identities)).one()
        assert identity.user_id == me["id"]
        assert identity.subject == "subject-123"
    # 票据一次性：用过即失效
    assert client.get("/api/auth/claim", params={"ticket": ticket}).status_code == 400


def test_claim_info_rejects_bad_ticket(oidc_client):
    client, _ = oidc_client
    assert client.get("/api/auth/claim", params={"ticket": "nope"}).status_code == 400
    assert client.get("/api/auth/claim").status_code == 400
    assert client.post("/api/auth/claim/new", json={"ticket": "nope"}).status_code == 400


def test_claim_wrong_password_locks_ticket(oidc_client):
    client, fake = oidc_client
    fake.claims["email_verified"] = False
    registered = client.post("/api/auth/register", json={"username": "lockme", "password": "old-password-123"})
    activate_user(client, registered.json()["userId"])
    state, _ = begin(client)
    ticket = claim_ticket_from(
        client.get("/api/auth/callback", params={"code": "lock", "state": state}, follow_redirects=False)
    )
    bad = {"ticket": ticket, "username": "lockme", "password": "wrong-password"}
    for _ in range(5):
        assert client.post("/api/auth/claim", json=bad).status_code == 401
    # 5 次错后票据作废：即使密码对了也只是无效链接
    good = {"ticket": ticket, "username": "lockme", "password": "old-password-123"}
    assert client.post("/api/auth/claim", json=good).status_code == 400


def test_display_name_sync_follows_provider_until_local_edit(oidc_client):
    import json as _json

    client, fake = oidc_client
    state, _ = begin(client)
    ticket = claim_ticket_from(
        client.get("/api/auth/callback", params={"code": "sync", "state": state}, follow_redirects=False)
    )
    assert client.post("/api/auth/claim/new", json={"ticket": ticket}).status_code == 200
    user = client.get("/api/auth/me").json()["user"]
    assert user["displayName"] == "Alice Example"

    def settings_of():
        with client.app.state.db.request_conn() as conn:
            row = conn.execute(select(users.c.settings).where(users.c.id == user["id"])).first()
            return _json.loads(row.settings or "{}")

    assert settings_of().get("display_name_source") == "oidc"
    # 本地改名后不再跟随
    assert client.patch("/api/me/profile", json={"displayName": "Local Name"}).status_code == 200
    assert "display_name_source" not in settings_of()
    fake.claims["name"] = "Renamed At Provider"
    state, _ = begin(client)
    client.get("/api/auth/callback", params={"code": "sync2", "state": state}, follow_redirects=False)
    assert client.get("/api/auth/me").json()["user"]["displayName"] == "Local Name"
    # 未改过名的账号跟随 IdP 改名
    client.post("/api/auth/logout")
    fake.claims["sub"] = "subject-fresh"
    fake.claims["email"] = "fresh@example.edu.cn"
    state, _ = begin(client)
    ticket = claim_ticket_from(
        client.get("/api/auth/callback", params={"code": "sync3", "state": state}, follow_redirects=False)
    )
    assert client.post("/api/auth/claim/new", json={"ticket": ticket}).status_code == 200
    assert client.get("/api/auth/me").json()["user"]["displayName"] == "Renamed At Provider"
    fake.claims["name"] = "Renamed Again"
    state, _ = begin(client)
    client.get("/api/auth/callback", params={"code": "sync4", "state": state}, follow_redirects=False)
    assert client.get("/api/auth/me").json()["user"]["displayName"] == "Renamed Again"
