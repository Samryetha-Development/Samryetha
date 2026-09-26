"""CSRF/Origin 守卫：只放行 APP_ORIGIN；已删除的独立 Tasks 站 origin 不再特殊放行。"""

from __future__ import annotations


def test_same_origin_allowed_foreign_rejected(client):
    # 同源（APP_ORIGIN）放行到鉴权层 → 未登录 401（说明没被 CSRF 拦）
    allowed = client.post(
        "/api/tasks",
        headers={"origin": "http://localhost:3000"},
        json={"title": "requires auth"},
    )
    assert allowed.status_code == 401

    # 旧独立 Tasks 站 origin（5300）：已随站点删除，不再放行
    removed = client.post(
        "/api/tasks",
        headers={"origin": "http://localhost:5300"},
        json={"title": "old tasks site"},
    )
    assert removed.status_code == 403
    assert removed.json()["error"]["message"] == "Cross-origin request rejected"

    evil = client.post(
        "/api/tasks",
        headers={"origin": "https://evil.example"},
        json={"title": "blocked before auth"},
    )
    assert evil.status_code == 403


def test_cors_preflight_only_for_app_origin(client):
    ok = client.options(
        "/api/tasks",
        headers={
            "origin": "http://localhost:3000",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert ok.headers["access-control-allow-credentials"] == "true"

    # 非白名单 origin 的预检不再回 Allow-Origin
    blocked = client.options(
        "/api/tasks",
        headers={
            "origin": "http://localhost:5300",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert "access-control-allow-origin" not in blocked.headers
