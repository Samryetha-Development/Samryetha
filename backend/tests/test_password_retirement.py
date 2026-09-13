"""密码链路退役：flag 开启后注册/登录/改密/找回/重置一律 410；紧急入口仅 admin+令牌可用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from samryetha.config import Settings
from samryetha.main import create_app
from samryetha.schema import users
from samryetha.security import hash_password


def make_app(tmp_path, **overrides):
    settings = Settings(
        _env_file=None,
        node_env="test",
        database_url=str(tmp_path / "retired.db"),
        upload_dir=str(tmp_path / "uploads"),
        **overrides,
    )
    app = create_app(settings)
    app.state.db.create_schema()
    return app


def make_admin(app, username="root", role="admin"):
    with app.state.db.request_conn() as conn:
        conn.execute(
            insert(users).values(
                username=username,
                display_name=username,
                email=f"{username}@example.com",
                password_hash=hash_password("old-password-123"),
                role=role,
                status="active",
            )
        )


@pytest.fixture
def retired(tmp_path):
    app = make_app(tmp_path, password_auth_disabled=True, emergency_login_token="emg-secret")
    make_admin(app)
    make_admin(app, username="pleb", role="student")
    with TestClient(app) as client:
        yield client


def test_password_endpoints_gone_when_retired(retired):
    assert retired.post("/api/auth/register", json={"username": "someone", "password": "password123"}).status_code == 410
    assert retired.post("/api/auth/login", json={"username": "x", "password": "y"}).status_code == 410
    assert retired.post("/api/auth/forgot-password", json={"username": "x", "recoveryEmail": "a@b.c"}).status_code == 410
    assert retired.post("/api/auth/reset-password", json={"token": "t", "newPassword": "bcd12345"}).status_code == 410
    # 已登录调改密同样 410（先经紧急入口拿会话）
    assert retired.post("/api/auth/emergency-login", json={"username": "root", "token": "emg-secret"}).status_code == 200
    assert retired.post("/api/auth/change-password", json={"currentPassword": "a", "newPassword": "bcd12345"}).status_code == 410
    config = retired.get("/api/auth/config").json()
    assert config["passwordAuthEnabled"] is False


def test_password_endpoints_alive_by_default(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/auth/config").json()["passwordAuthEnabled"] is True
        assert client.post("/api/auth/register", json={"username": "fresh", "password": "password123"}).status_code == 201


def test_emergency_login_admin_only_with_token(retired):
    assert retired.post("/api/auth/emergency-login", json={"username": "root", "token": "wrong"}).status_code == 403
    assert retired.post(
        "/api/auth/emergency-login", json={"username": "pleb", "token": "emg-secret"}
    ).status_code == 403
    assert retired.post("/api/auth/emergency-login", json={"username": "ghost", "token": "emg-secret"}).status_code == 403
    ok = retired.post("/api/auth/emergency-login", json={"username": "root", "token": "emg-secret"})
    assert ok.status_code == 200, ok.text
    assert "samryetha_session" in ok.cookies
    assert retired.get("/api/auth/me").json()["user"]["username"] == "root"


def test_emergency_login_disabled_without_token(tmp_path):
    app = make_app(tmp_path, password_auth_disabled=True)
    make_admin(app)
    with TestClient(app) as client:
        assert client.post("/api/auth/emergency-login", json={"username": "root", "token": "anything"}).status_code == 403
