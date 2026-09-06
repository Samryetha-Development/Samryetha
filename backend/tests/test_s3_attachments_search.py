"""S3 附件（presign/直传/直下/删除）+ 搜索。"""

from __future__ import annotations

import os

import pytest


def test_attachment_full_flow(api, tmp_path):
    api.login_dev()
    pres = api.c.post(
        "/api/attachments/presign",
        json={"filename": "photo.png", "mimeType": "image/png", "sizeBytes": 11},
    )
    assert pres.status_code == 200, pres.text
    p = pres.json()
    assert p["uploadMethod"] == "PUT"
    assert p["uploadHeaders"] == {"content-type": "image/png"}
    assert "expires=" in p["uploadUrl"] and "sig=" in p["uploadUrl"]
    attachment_id = p["attachmentId"]

    # 签名直传
    up = api.c.put(p["uploadUrl"], content=b"hello world", headers={"content-type": "image/png"})
    assert up.status_code == 204, up.text

    # 元数据
    meta = api.c.get(f"/api/attachments/{attachment_id}")
    assert meta.status_code == 200
    m = meta.json()
    assert m["objectKey"] == p["objectKey"]
    assert m["mimeType"] == "image/png"
    assert m["sizeBytes"] == 11
    assert "serve/" in m["downloadUrl"]

    # 带签 GET 回内容 + 头
    srv = api.c.get(m["downloadUrl"])
    assert srv.status_code == 200
    assert srv.content == b"hello world"
    assert srv.headers["x-content-type-options"] == "nosniff"
    assert "inline; filename=" in srv.headers["content-disposition"]

    # 改签无效 → 403
    from urllib.parse import parse_qs, urlparse

    url = m["downloadUrl"]
    qs = parse_qs(urlparse(url).query)
    bad = url.replace(qs["sig"][0], "0" * 64)
    assert api.c.get(bad).status_code == 403

    # 删除
    assert api.c.delete(f"/api/attachments/{attachment_id}").json() == {"ok": True}
    assert api.c.get(f"/api/attachments/{attachment_id}").status_code == 404


def test_presign_rejects_unsupported_mime(api):
    # 安全修复：presign 只收白名单 Content-Type（镜像 attachments/routes.ts），白名单外 → 400
    api.login_dev()
    res = api.c.post(
        "/api/attachments/presign",
        json={"filename": "evil.exe", "mimeType": "application/octet-stream", "sizeBytes": 10},
    )
    assert res.status_code == 400


def test_attachments_require_login(api):
    # 未登录 presign → 401
    res = api.c.post(
        "/api/attachments/presign",
        json={"filename": "a.png", "mimeType": "image/png", "sizeBytes": 5},
    )
    assert res.status_code == 401


def _board(api, slug="attach"):
    # dev(admin) 建板块，供附件挂载测试用
    r = api.c.post("/api/boards", json={"name": slug, "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def test_presign_rejects_extensionless_filename(api):
    # 无扩展名绕过白名单 → 400（F5）
    api.login_dev()
    res = api.c.post(
        "/api/attachments/presign",
        json={"filename": "noext", "mimeType": "application/octet-stream", "sizeBytes": 10},
    )
    assert res.status_code == 400


def test_attach_requires_upload(api):
    # 只 presign 不 PUT 就挂载 → 422（F3 attach-without-PUT）
    api.login_dev()
    _board(api)
    pres = api.c.post(
        "/api/attachments/presign",
        json={"filename": "a.png", "mimeType": "image/png", "sizeBytes": 11},
    )
    assert pres.status_code == 200, pres.text
    aid = pres.json()["attachmentId"]
    res = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attach", "title": "valid title", "bodyMarkdown": "body", "attachmentIds": [aid]},
    )
    assert res.status_code == 422, res.text


def test_attach_rejects_bogus_id(api):
    # 不存在的 attachmentId 不能静默忽略 → 422（F4）
    api.login_dev()
    _board(api)
    res = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attach", "title": "valid title", "bodyMarkdown": "body", "attachmentIds": [99999]},
    )
    assert res.status_code == 422, res.text


def test_attach_rejects_reattach(api):
    # 同一附件跨帖挂载 → 第二次 422（F4 跨帖搬运）
    api.login_dev()
    _board(api)
    pres = api.c.post(
        "/api/attachments/presign",
        json={"filename": "a.png", "mimeType": "image/png", "sizeBytes": 11},
    )
    p = pres.json()
    up = api.c.put(p["uploadUrl"], content=b"hello world", headers={"content-type": "image/png"})
    assert up.status_code == 204, up.text
    d1 = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attach", "title": "first post", "bodyMarkdown": "body", "attachmentIds": [p["attachmentId"]]},
    )
    assert d1.status_code == 201, d1.text
    d2 = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attach", "title": "second post", "bodyMarkdown": "body", "attachmentIds": [p["attachmentId"]]},
    )
    assert d2.status_code == 422, d2.text


def test_search_visibility(api):
    api.login_dev()
    api.c.post("/api/boards", json={"name": "Public", "slug": "public-board"})
    api.c.post(
        "/api/discussions",
        json={"boardSlug": "public-board", "title": "量子力学入门", "bodyMarkdown": "测不准原理很玄"},
    )
    # 私密板块的帖子对非成员不可见
    api.c.post("/api/boards", json={"name": "Secret", "slug": "secret", "visibility": "members"})
    api.c.post(
        "/api/discussions",
        json={"boardSlug": "secret", "title": "量子私货", "bodyMarkdown": "内部消息"},
    )

    api.mkuser("stu1")
    api.login("stu1")
    res = api.c.get("/api/search", params={"q": "量子"})
    assert res.status_code == 200
    body = res.json()
    titles = [t["title"] for t in body["items"]]
    assert "量子力学入门" in titles
    assert "量子私货" not in titles

    # 管理员能看到全部
    api.login_dev()
    res2 = api.c.get("/api/search", params={"q": "量子"})
    assert len(res2.json()["items"]) == 2

    # 限定 board（仅存在板块生效）
    res3 = api.c.get("/api/search", params={"q": "量子", "board": "secret"})
    assert [t["title"] for t in res3.json()["items"]] == ["量子私货"]
    # 不存在的板块被忽略
    res4 = api.c.get("/api/search", params={"q": "量子", "board": "no-such"})
    assert len(res4.json()["items"]) == 2
