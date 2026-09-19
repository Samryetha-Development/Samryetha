"""GET /api/source 公开多语言对照接口测试。"""

from __future__ import annotations


def test_source_empty(client):
    r = client.get("/api/source")
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["total"] == 0


def test_source_returns_all_locales(api):
    token = api.login_as("admin_src", role="admin")
    api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    api.put("/api/catalog/en/nav.home", token=token, json={"value": "Home"})

    r = api.c.get("/api/source")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    locales = {e["locale"] for e in body["entries"]}
    assert locales == {"zh-CN", "en"}


def test_source_locale_filter(api):
    token = api.login_as("admin_src2", role="admin")
    api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    api.put("/api/catalog/en/nav.home", token=token, json={"value": "Home"})

    r = api.c.get("/api/source?locale=zh-CN")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["entries"][0]["locale"] == "zh-CN"


def test_source_key_prefix_filter(api):
    token = api.login_as("admin_src3", role="admin")
    api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    api.put("/api/catalog/zh-CN/auth.login", token=token, json={"value": "登录"})

    r = api.c.get("/api/source?key_prefix=nav.")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["entries"][0]["key"] == "nav.home"
