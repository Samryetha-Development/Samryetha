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


def test_presign_rejects_extensionless_filename(api):
    api.login_dev()
    res = api.c.post(
        "/api/attachments/presign",
        json={"filename": "no-extension", "mimeType": "application/octet-stream", "sizeBytes": 10},
    )
    assert res.status_code == 400


def test_discussion_only_claims_uploaded_attachments(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "Attachments", "slug": "attachments"}).status_code == 201
    pending = api.c.post(
        "/api/attachments/presign",
        json={"filename": "note.txt", "mimeType": "text/plain", "sizeBytes": 4},
    ).json()["attachmentId"]
    missing_upload = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attachments", "title": "Missing upload", "bodyMarkdown": "body", "attachmentIds": [pending]},
    )
    assert missing_upload.status_code == 422

    uploaded = api.c.post(
        "/api/attachments/presign",
        json={"filename": "ready.txt", "mimeType": "text/plain", "sizeBytes": 4},
    ).json()
    assert api.c.put(uploaded["uploadUrl"], content=b"test").status_code == 204
    first = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attachments", "title": "First discussion", "bodyMarkdown": "body", "attachmentIds": [uploaded["attachmentId"]]},
    )
    assert first.status_code == 201
    second = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attachments", "title": "Second discussion", "bodyMarkdown": "body", "attachmentIds": [uploaded["attachmentId"]]},
    )
    assert second.status_code == 422
    bogus = api.c.post(
        "/api/discussions",
        json={"boardSlug": "attachments", "title": "Bogus attachment", "bodyMarkdown": "body", "attachmentIds": [999999]},
    )
    assert bogus.status_code == 422


def test_attachments_require_login(api):
    # 未登录 presign → 401
    res = api.c.post(
        "/api/attachments/presign",
        json={"filename": "a.png", "mimeType": "image/png", "sizeBytes": 5},
    )
    assert res.status_code == 401


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
