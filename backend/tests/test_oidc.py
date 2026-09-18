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
from samryetha.oidc import OidcClient, consume_login, resolve_return_to, safe_return_to
from samryetha.schema import oidc_identities, users


class FakeOidcClient:
    def __init__(self, claims: dict) -> None:
        self.claims = claims
        self.exchange_args: tuple[str, str, str] | None = None

    def authorization_params(self, state: str, nonce: str, challenge: str, *, embedded: bool = False) -> dict:
        params = {"state": state, "nonce": nonce, "code_challenge": challenge}
        if embedded:
            params["display"] = "popup"
        return params

    def authorization_url(self, state: str, nonce: str, challenge: str, *, embedded: bool = False) -> str:
        return "https://auth.samryetha.test/authorize?" + parse_url_params(
            self.authorization_params(state, nonce, challenge, embedded=embedded)
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
    settings = client.app.state.settings
    assert safe_return_to("//evil.example", settings) == "/"
    assert safe_return_to("/safe?next=1", settings) == "/safe?next=1"


def test_safe_return_to_allows_only_whitelisted_origins():
    """站外 returnTo 只放行 SIGNIN_RETURN_ORIGINS 里精确匹配的 origin。"""
    settings = Settings(
        app_origin="https://samryetha.com",
        # 第二项故意带尾斜杠，验证归一化
        signin_return_origins="https://i18n.samryetha.com, https://feedback.samryetha.com/",
    )

    # 站内路径照旧放行
    assert safe_return_to("/login/done", settings) == "/login/done"
    assert safe_return_to("/", settings) == "/"

    # 白名单 origin 放行（尾部斜杠差异不影响）
    assert safe_return_to("https://i18n.samryetha.com", settings) == "https://i18n.samryetha.com"
    assert safe_return_to("https://i18n.samryetha.com/submit?a=1", settings) == "https://i18n.samryetha.com/submit?a=1"
    assert safe_return_to("https://feedback.samryetha.com/", settings) == "https://feedback.samryetha.com/"

    # 非白名单 / 绕过手法一律回落
    assert safe_return_to("https://evil.com", settings) == "/"
    assert safe_return_to("//evil.com", settings) == "/"
    assert safe_return_to("/\\evil.com", settings) == "/"
    assert safe_return_to("https://i18n.samryetha.com.evil.com", settings) == "/"  # 不是后缀匹配
    assert safe_return_to("http://i18n.samryetha.com", settings) == "/"  # scheme 必须一致
    assert safe_return_to("https://i18n.samryetha.com:8443", settings) == "/"  # port 必须一致
    assert safe_return_to("javascript:alert(1)", settings) == "/"
    assert safe_return_to("https://i18n.samryetha.com%0d%0aSet-Cookie:x", settings) == "/"
    assert safe_return_to("https://i18n.samryetha.com\r\nSet-Cookie: x", settings) == "/"
    assert safe_return_to(None, settings) == "/"


def test_resolve_return_to_never_double_prefixes_app_origin():
    """站外绝对 URL 不能再去拼 app_origin，否则得到畸形地址。"""
    settings = Settings(
        app_origin="https://samryetha.com",
        signin_return_origins="https://i18n.samryetha.com",
    )
    assert resolve_return_to(settings, "/settings") == "https://samryetha.com/settings"
    assert resolve_return_to(settings, "https://i18n.samryetha.com") == "https://i18n.samryetha.com"


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


# ---------------------------------------------------------------- 嵌入流（start / complete）
#
# 与重定向流共用 begin_login / _complete_login，只是不再靠 302 推进。


def embedded_start(client: TestClient, return_to: str = "/") -> str:
    response = client.post("/api/auth/oidc/start", json={"returnTo": return_to})
    assert response.status_code == 200, response.text
    return response.json()["params"]["state"]


def test_config_reports_redirect_mode_by_default(oidc_client):
    client, _ = oidc_client
    assert client.get("/api/auth/config").json()["oidcMode"] == "redirect"


def test_config_only_honours_json_when_oidc_is_enabled(oidc_client):
    client, _ = oidc_client
    client.app.state.settings.oidc_mode = "json"
    assert client.get("/api/auth/config").json()["oidcMode"] == "json"

    # 写错的值得回退到 redirect，而不是半新半旧地跑。
    client.app.state.settings.oidc_mode = "bogus"
    assert client.get("/api/auth/config").json()["oidcMode"] == "redirect"


def test_oidc_start_returns_params_and_scopes_transaction_cookie(oidc_client):
    client, _ = oidc_client
    response = client.post("/api/auth/oidc/start", json={"returnTo": "/settings"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    params = response.json()["params"]
    assert params["state"] and params["nonce"] and params["code_challenge"]
    # 不跳转——这就是嵌入流与重定向流的全部差别。
    assert "location" not in {k.lower() for k in response.headers}

    cookie = next(
        value for value in response.headers.get_list("set-cookie") if value.startswith("samryetha_oidc_state=")
    )
    # 必须同时覆盖 /api/auth/callback（重定向流）与 /api/auth/oidc/complete（嵌入流）。
    assert "Path=/api/auth;" in cookie, cookie


def test_oidc_complete_creates_session_and_reaches_claim(oidc_client):
    client, fake = oidc_client
    state = embedded_start(client, "/settings")
    response = client.post("/api/auth/oidc/complete", json={"code": "embedded-code", "state": state})
    assert response.status_code == 200, response.text
    body = response.json()
    # 无映射、无可信邮箱：与重定向流一样转认领，不静默建空号。
    assert body["status"] == "claim_required"
    assert body["claimUrl"].endswith("/claim?ticket=" + body["ticket"])
    assert "samryetha_session" not in response.cookies
    assert fake.exchange_args is not None and fake.exchange_args[0] == "embedded-code"

    created = client.post("/api/auth/claim/new", json={"ticket": body["ticket"]})
    assert created.status_code == 200, created.text
    assert "samryetha_session" in created.cookies
    assert client.get("/api/auth/me").json()["user"]["username"] == "alice"


def test_oidc_complete_rejects_mismatched_or_missing_state(oidc_client):
    client, _ = oidc_client
    embedded_start(client)
    mismatch = client.post("/api/auth/oidc/complete", json={"code": "c", "state": "not-the-cookie-state"})
    assert mismatch.status_code == 400
    missing = client.post("/api/auth/oidc/complete", json={"code": "c"})
    assert missing.status_code == 400
    no_code = client.post("/api/auth/oidc/complete", json={"state": "whatever"})
    assert no_code.status_code == 400
