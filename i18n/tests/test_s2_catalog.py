"""catalog 公开接口 + 管理员 upsert/delete 测试。"""

from __future__ import annotations


# ---------------------------------------------------------------- GET /api/catalog/{locale}

def test_catalog_empty(client):
    r = client.get("/api/catalog/zh-CN")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "zh-CN"
    assert body["entries"] == []
    assert body["total"] == 0


def test_catalog_invalid_locale_format(client):
    r = client.get("/api/catalog/not_valid_locale!!!")
    assert r.status_code == 400


def test_catalog_unsupported_locale(client):
    r = client.get("/api/catalog/ja-JP")
    assert r.status_code == 400


def test_catalog_returns_entries(api):
    token = api.login_as("admin1", role="admin")
    # 管理员写入条目
    r = api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    assert r.status_code == 200

    # 公开读取
    r = api.c.get("/api/catalog/zh-CN")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    entry = body["entries"][0]
    assert entry["key"] == "nav.home"
    assert entry["value"] == "首页"


def test_catalog_key_prefix_filter(api):
    token = api.login_as("admin2", role="admin")
    api.put("/api/catalog/en/nav.home", token=token, json={"value": "Home"})
    api.put("/api/catalog/en/nav.boards", token=token, json={"value": "Boards"})
    api.put("/api/catalog/en/auth.login", token=token, json={"value": "Log in"})

    r = api.c.get("/api/catalog/en?key_prefix=nav.")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    keys = {e["key"] for e in body["entries"]}
    assert keys == {"nav.home", "nav.boards"}


# ---------------------------------------------------------------- PUT /api/catalog/{locale}/{key}

def test_upsert_requires_admin(api):
    token = api.login_as("student1", role="student")
    r = api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    assert r.status_code == 403


def test_upsert_requires_login(api):
    r = api.put("/api/catalog/zh-CN/nav.home", json={"value": "首页"})
    assert r.status_code == 401


def test_upsert_invalid_key(api):
    token = api.login_as("admin3", role="admin")
    r = api.put("/api/catalog/zh-CN/bad key!!!", token=token, json={"value": "x"})
    assert r.status_code == 400


def test_upsert_empty_value(api):
    token = api.login_as("admin4", role="admin")
    r = api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "   "})
    assert r.status_code == 422


def test_upsert_creates_and_updates(api):
    token = api.login_as("admin5", role="admin")

    # 创建
    r = api.put("/api/catalog/en/common.save", token=token, json={"value": "Save", "description": "Save btn"})
    assert r.status_code == 200
    assert r.json()["value"] == "Save"

    # 更新
    r = api.put("/api/catalog/en/common.save", token=token, json={"value": "Save changes"})
    assert r.status_code == 200
    assert r.json()["value"] == "Save changes"

    # 确认只有一条
    r = api.c.get("/api/catalog/en")
    assert r.json()["total"] == 1


# ---------------------------------------------------------------- DELETE /api/catalog/{locale}/{key}

def test_delete_entry(api):
    token = api.login_as("admin6", role="admin")
    api.put("/api/catalog/zh-CN/to.delete", token=token, json={"value": "删"})

    r = api.delete("/api/catalog/zh-CN/to.delete", token=token)
    assert r.status_code == 204

    r = api.c.get("/api/catalog/zh-CN")
    assert r.json()["total"] == 0


def test_delete_nonexistent(api):
    token = api.login_as("admin7", role="admin")
    r = api.delete("/api/catalog/zh-CN/no.such.key", token=token)
    assert r.status_code == 404


# ---------------------------------------------------------------- GET /api/catalog/{locale}/translations  （SSR 端点）

def test_catalog_translations_empty(client):
    r = client.get("/api/catalog/zh-CN/translations")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "zh-CN"
    assert body["translations"] == {}


def test_catalog_translations_returns_flat_dict(api):
    token = api.login_as("admin8", role="admin")
    api.put("/api/catalog/zh-CN/nav.home", token=token, json={"value": "首页"})
    api.put("/api/catalog/zh-CN/nav.boards", token=token, json={"value": "版块"})

    r = api.c.get("/api/catalog/zh-CN/translations")
    assert r.status_code == 200
    body = r.json()
    assert body["locale"] == "zh-CN"
    assert body["translations"] == {"nav.home": "首页", "nav.boards": "版块"}


def test_catalog_translations_unsupported_locale(client):
    r = client.get("/api/catalog/ja-JP/translations")
    assert r.status_code == 400
