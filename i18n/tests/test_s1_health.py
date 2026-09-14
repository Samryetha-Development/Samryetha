"""健康检查与基础错误包络测试。"""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_not_found_returns_404(client):
    r = client.get("/api/no-such-endpoint")
    assert r.status_code == 404


def test_auth_required_without_cookie(client):
    r = client.get("/api/submissions")
    assert r.status_code == 401
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "AUTH_REQUIRED"
