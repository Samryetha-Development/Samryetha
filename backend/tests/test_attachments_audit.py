"""附件审计回归：挂载校验 / 白名单 / 体积 / 删除清理 / 孤儿回收 / 配置下发。"""

from __future__ import annotations


def _mkboard(api, slug: str = "b1") -> None:
    r = api.c.post("/api/boards", json={"name": "B", "slug": slug})
    assert r.status_code in (200, 201), r.text


def _presign(api, filename: str = "a.png", mime: str = "image/png", size: int = 5) -> dict:
    r = api.c.post(
        "/api/attachments/presign",
        json={"filename": filename, "mimeType": mime, "sizeBytes": size},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_presign_rejects_extensionless(api):
    api.login_dev()
    r = api.c.post(
        "/api/attachments/presign",
        json={"filename": "noext", "mimeType": "application/octet-stream", "sizeBytes": 5},
    )
    assert r.status_code == 400


def test_attach_requires_upload(api):
    api.login_dev()
    _mkboard(api)
    p = _presign(api)
    r = api.c.post(
        "/api/discussions",
        json={"boardSlug": "b1", "title": "t12345", "bodyMarkdown": "hi", "attachmentIds": [p["attachmentId"]]},
    )
    assert r.status_code == 422, r.text


def test_attach_happy_path_and_reattach_rejected(api):
    api.login_dev()
    _mkboard(api)
    p = _presign(api)
    up = api.c.put(p["uploadUrl"], content=b"hello", headers={"content-type": "image/png"})
    assert up.status_code == 204, up.text
    d1 = api.c.post(
        "/api/discussions",
        json={"boardSlug": "b1", "title": "first post", "bodyMarkdown": "a", "attachmentIds": [p["attachmentId"]]},
    )
    assert d1.status_code == 201, d1.text
    atts = d1.json()["attachments"]
    assert len(atts) == 1 and atts[0]["state"] == "attached"
    # 同一附件挂第二帖 → 422，且第一帖不受影响
    d2 = api.c.post(
        "/api/discussions",
        json={"boardSlug": "b1", "title": "second post", "bodyMarkdown": "b", "attachmentIds": [p["attachmentId"]]},
    )
    assert d2.status_code == 422, d2.text
    again = api.c.get(f"/api/discussions/{d1.json()['id']}").json()["attachments"]
    assert len(again) == 1


def test_attach_bogus_id_rejected(api):
    api.login_dev()
    _mkboard(api)
    r = api.c.post(
        "/api/discussions",
        json={"boardSlug": "b1", "title": "bogus attach", "bodyMarkdown": "x", "attachmentIds": [99999]},
    )
    assert r.status_code == 422, r.text


def test_upload_beyond_declared_size_rejected(api):
    api.login_dev()
    p = _presign(api, size=3)
    up = api.c.put(p["uploadUrl"], content=b"x" * 100, headers={"content-type": "image/png"})
    assert up.status_code == 400, up.text


def test_delete_discussion_cleans_attachments(api):
    api.login_dev()
    _mkboard(api)
    p = _presign(api)
    api.c.put(p["uploadUrl"], content=b"hello", headers={"content-type": "image/png"})
    d = api.c.post(
        "/api/discussions",
        json={"boardSlug": "b1", "title": "to be deleted", "bodyMarkdown": "bye", "attachmentIds": [p["attachmentId"]]},
    )
    assert d.status_code == 201, d.text
    did = d.json()["id"]
    dl = d.json()["attachments"][0]["downloadUrl"]
    assert api.c.get(dl).status_code == 200
    assert api.c.delete(f"/api/discussions/{did}").status_code == 200
    assert api.c.get(dl).status_code == 404


def test_reap_orphans(api):
    api.login_dev()
    p = _presign(api)
    n = api.app.state.reap_attachment_orphans(older_than_ms=-1)
    assert n >= 1
    assert api.c.get(f"/api/attachments/{p['attachmentId']}").status_code == 404


def test_attachments_config(api):
    r = api.c.get("/api/attachments/config")
    assert r.status_code == 200, r.text
    body = r.json()
    assert ".png" in body["allowedExtensions"]
    assert body["maxUploadBytes"] == 50 * 1024 * 1024
