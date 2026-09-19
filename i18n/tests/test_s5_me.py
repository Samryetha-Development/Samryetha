"""GET /api/me 与 SPA fallback 边界测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_me_anonymous(client) -> None:
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"user": None}


def test_me_logged_in(client, api) -> None:
    token = api.login_as("alice", role="admin")
    r = api.get("/api/me", token=token)
    assert r.status_code == 200
    data = r.json()["user"]
    assert data["username"] == "alice"
    assert data["role"] == "admin"
    assert data["status"] == "active"
    assert "email" in data


def test_spa_fallback_never_swallows_api(tmp_path) -> None:
    """未匹配的 /api/* 必须 404，绝不能返回 SPA index.html（否则客户端会把
    不存在的接口当成 200 页面，导致 undefined/永久 loading）。"""
    from i18n_svc.config import Settings
    from i18n_svc.main import create_app

    d = tmp_path / "dist"
    d.mkdir()
    (d / "index.html").write_text("<html>site-index</html>", encoding="utf-8")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=str(tmp_path / "x.db"),
            auth_db_url=str(tmp_path / "x.db"),
            supported_locales="en",
            site_dir=str(d),
        )
    )
    app.state.db.create_schema()
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        r = c.get("/api/users/me")  # i18n 无此端点 → 404，不命中 SPA fallback
        assert r.status_code == 404
        r = c.get("/some/deep/spa")
        assert r.status_code == 200
        assert "site-index" in r.text
        assert c.get("/api/me").status_code == 200  # 真端点正常