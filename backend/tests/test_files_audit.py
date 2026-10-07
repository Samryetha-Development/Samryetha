"""文件服务安全与边界自检（第二阶段）。

File-service security and edge-case audit (phase two).

本文件的用例分两类 / Two kinds of cases live here:
1. **回归锁定**：每条对应一个本轮自检发现并修掉的真实缺陷，注释里写明"修之前会怎样"。
   Regression locks: each one corresponds to a real defect found and fixed in this audit, with
   the pre-fix behaviour spelled out in the comment.
2. **持续保证**：越权、指纹、路径穿越、CSRF、软删除、边界与归一化等本应成立的性质，
   写成用例防止将来被改坏。
   Standing guarantees: authorisation, fingerprinting, path traversal, CSRF, soft delete,
   boundaries and normalisation, pinned so a future change cannot quietly break them.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse

from samryetha import files_service
from samryetha.storage import OBJECT_KEY_RE


def _category_id(api, slug: str) -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _presign(api, filename: str = "notes.pdf", size: int = 11):
    response = api.c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": "application/pdf", "sizeBytes": size},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _publish(
    api,
    title: str,
    *,
    body: bytes = b"hello world",
    visibility: str = "members",
    filename: str = "notes.pdf",
    tags: list[str] | None = None,
    slug: str = "study-syllabus",
) -> dict:
    presign = _presign(api, filename, len(body))
    assert api.c.put(presign["uploadUrl"], content=body).status_code == 204
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": len(body),
            "categoryId": _category_id(api, slug),
            "title": title,
            "visibility": visibility,
            "tags": tags or [],
            "originalFilename": filename,
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    # 详情 DTO 刻意不下发 objectKey（客户端不需要它），但本文件多条用例要拿它做磁盘断言，
    # 所以在测试本地挂一份，避免为此改动对外契约。
    # The detail DTO deliberately omits objectKey (the client has no use for it), yet several
    # cases here need it for disk assertions, so it is attached locally in the test rather than
    # widening the public contract for testing convenience.
    payload["objectKey"] = presign["objectKey"]
    return payload


# ================================================================ 越权 / authorisation


def test_other_user_cannot_read_or_mutate_private_resource(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    created = _publish(api, "alice 的私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.login("bob")
    # 读：404（不泄漏存在性）；改/删：403（授权先于资源变更）。
    # Read: 404 so existence is not leaked. Mutate: 403, because authorisation is checked
    # before any change.
    assert api.c.get(f"/api/files/resources/{created['id']}").status_code == 404
    assert api.c.patch(f"/api/files/resources/{created['id']}", json={"title": "劫持"}).status_code == 403
    assert api.c.delete(f"/api/files/resources/{created['id']}").status_code == 403
    # 申请下载票据同样必须被拒。
    # Requesting a download ticket must be refused as well.
    assert api.c.get(f"/api/files/resources/{created['id']}/download").status_code == 404


def test_favorites_and_mine_never_leak_other_users_rows(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    _publish(api, "alice 的公开资料", visibility="public")
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.get("/api/files/favorites").json()["total"] == 0
    assert api.c.get("/api/files/mine").json()["total"] == 0


def test_favorites_stops_returning_a_resource_that_became_private(api):
    """回归：修之前 /favorites 不过滤可见性，收藏会被当成绕过授权的后门。

    Regression: before the fix the favourites endpoint applied no visibility predicate, so a
    favourite acted as a back door around authorisation.
    """
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    created = _publish(api, "先公开后转私有", visibility="public")
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.put(f"/api/files/resources/{created['id']}/favorite").status_code == 200
    assert api.c.get("/api/files/favorites").json()["total"] == 1

    api.c.post("/api/auth/logout")
    api.login("alice")
    assert api.c.patch(
        f"/api/files/resources/{created['id']}", json={"visibility": "private"}
    ).status_code == 200

    api.c.post("/api/auth/logout")
    api.login("bob")
    # 授权每次请求重新判定：bob 的收藏列表必须立刻不再包含这条。
    # Authorisation is re-evaluated per request: bob's favourites must stop listing it at once.
    assert api.c.get("/api/files/favorites").json()["total"] == 0


def test_rating_requires_visibility(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    created = _publish(api, "私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.put(f"/api/files/resources/{created['id']}/rating", json={"score": 5}).status_code == 404


def test_moderator_role_is_not_enough_for_category_management(api):
    api.mkuser("mod1", role="moderator")
    api.login("mod1")
    # 分类管理是 admin 能力，moderator 不够——避免权限体系悄悄放宽。
    # Category management is an admin ability; a moderator is not enough, so the permission
    # model cannot quietly widen.
    assert api.c.post("/api/files/categories", json={"slug": "x", "name": "X"}).status_code == 403


# ================================================================ 指纹 / 路径穿越 / CSRF


def test_object_keys_are_random_and_well_formed(api):
    api.mkuser("alice")
    api.login("alice")
    first = _presign(api)["objectKey"]
    second = _presign(api)["objectKey"]
    # uuid4 前缀 + 规范化文件名：既不可枚举，也无法从一个键推出另一个。
    # A uuid4 prefix plus a sanitised filename: neither enumerable nor predictable from another key.
    assert OBJECT_KEY_RE.match(first)
    assert first != second
    assert first.split("/")[0] != second.split("/")[0]


def test_object_key_path_traversal_is_rejected(api):
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api)
    traversal = "00000000-0000-0000-0000-000000000000/../../server.env"
    # 查询串沿用真实签名，确保被拒的原因是对象键形状而不是签名无效。
    # The real query string is reused so the rejection is provably about the key shape, not a bad signature.
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    url = (
        f"/api/files/upload/1/{traversal}"
        f"?size={query['size'][0]}&expires={query['expires'][0]}&sig={query['sig'][0]}"
    )
    # 无论被路径规范化挡在路由层还是被具体校验拦下，关键性质是"绝不落盘成功"。
    # Whether path normalisation stops it before routing or a specific check rejects it, the
    # property that matters is that it never writes anything.
    assert api.c.put(url, content=b"abc").status_code >= 400


def test_storage_refuses_to_resolve_a_traversing_object_key(api):
    """直接对存储层断言：这是穿越防护的最后一道、也是真正生效的那道。

    Assert directly against the storage layer: this is the last and genuinely effective line of
    traversal defence. HTTP-level tests can be short-circuited by path normalisation before the
    guard even runs, so the guard itself needs its own assertion.
    """
    storage = api.app.state.storage
    for candidate in [
        "00000000-0000-0000-0000-000000000000/../../server.env",
        "../outside.txt",
        "/absolute.txt",
    ]:
        raised = False
        try:
            storage.path_for(candidate)
        except Exception:
            raised = True
        assert raised, f"{candidate!r} was not refused by path_for"


def test_forged_upload_signature_is_rejected(api):
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api)
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    object_key = presign["objectKey"]
    url = f"/api/files/upload/1/{object_key}?size=11&expires={query['expires'][0]}&sig=deadbeef"
    # 签名不匹配一律 400：实现刻意不区分"签名错/过期/形状错"，免得给探测者反馈。
    # A signature mismatch is always 400: the implementation deliberately does not distinguish
    # "wrong signature" from "expired" or "malformed", so a prober learns nothing.
    assert api.c.put(url, content=b"hello world").status_code == 400


def test_expired_upload_ticket_is_rejected(api):
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api)
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": "1",
            "sig": query["sig"][0],
            "sizeBytes": 11,
            "categoryId": _category_id(api, "study-syllabus"),
            "title": "过期票据",
        },
    )
    assert created.status_code == 400


def test_cross_origin_write_is_rejected(api):
    api.mkuser("alice")
    api.login("alice")
    # CSRF：带站外 Origin 的非安全方法必须在 Guards 中间件被短路。
    # CSRF: a non-safe method carrying a foreign Origin must be short-circuited by the guard.
    response = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": 11},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_upload_ticket_cannot_back_two_resources(api):
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api)
    assert api.c.put(presign["uploadUrl"], content=b"hello world").status_code == 204
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    body = {
        "objectKey": presign["objectKey"],
        "expires": query["expires"][0],
        "sig": query["sig"][0],
        "sizeBytes": 11,
        "categoryId": _category_id(api, "study-syllabus"),
        "title": "第一条",
    }
    assert api.c.post("/api/files/resources", json=body).status_code == 201
    # 同一个 objectKey 再建第二条会被唯一约束挡住：对象键一次性，不能拿一份文件冒充两份资料。
    # A second resource on the same object key is stopped by the unique constraint: an object key
    # is single-use and one file must not masquerade as two resources.
    duplicate = api.c.post("/api/files/resources", json={**body, "title": "第二条"})
    assert duplicate.status_code == 409


def test_create_without_uploading_bytes_is_rejected(api):
    """回归：修之前只验签名，用户可以只 presign、不传字节就创建条目。

    Regression: before the fix only the signature was verified, so a user could obtain a
    presign and create a row without ever uploading the bytes.
    """
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api, size=11)
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": 11,
            "categoryId": _category_id(api, "study-syllabus"),
            "title": "从未上传",
        },
    )
    # 交上来的字节压根不存在 → 必须拒绝，否则列表里会出现永远下载不到的资料。
    # The bytes were never uploaded, so this must be refused; otherwise the listing would show
    # a resource nobody can ever download.
    assert created.status_code == 400
    assert api.c.get("/api/files/resources").json()["total"] == 0


def test_size_mismatch_against_disk_is_rejected(api):
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api, size=11)
    api.c.put(presign["uploadUrl"], content=b"hello world")
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    # 签名是给 size=11 签的，这里如实申报 11 但落盘文件被人为改小，创建仍必须拒绝。
    # The signature was issued for size=11 and 11 is declared truthfully, yet the file on disk is
    # truncated by hand; creation must still be refused.
    storage = api.app.state.storage
    with open(storage.path_for(presign["objectKey"]), "wb") as handle:
        handle.write(b"short")
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": 11,
            "categoryId": _category_id(api, "study-syllabus"),
            "title": "体积对不上",
        },
    )
    assert created.status_code == 400


# ================================================================ 软删除一致性


def test_soft_deleted_resource_is_gone_from_every_path(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "将被删除", visibility="public")
    rid = created["id"]
    assert api.c.put(f"/api/files/resources/{rid}/favorite").status_code == 200
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 4}).status_code == 200

    assert api.c.delete(f"/api/files/resources/{rid}").status_code == 200

    # 软删之后每条路径都必须一致：详情/下载/收藏/评分全部按"不存在"处理。
    # After a soft delete every path must agree: detail, download, favourite and rating all
    # behave as if the resource does not exist.
    assert api.c.get(f"/api/files/resources/{rid}").status_code == 404
    assert api.c.get(f"/api/files/resources/{rid}/download").status_code == 404
    assert api.c.put(f"/api/files/resources/{rid}/favorite").status_code == 404
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 1}).status_code == 404
    assert api.c.delete(f"/api/files/resources/{rid}").status_code == 404
    assert api.c.get("/api/files/resources").json()["total"] == 0
    assert api.c.get("/api/files/favorites").json()["total"] == 0
    assert api.c.get("/api/files/mine").json()["total"] == 0


def test_builtin_category_can_be_renamed_but_not_deleted(api):
    api.mkuser("root", role="admin")
    api.login("root")
    system_id = _category_id(api, "freshman-guide")
    assert api.c.patch(f"/api/files/categories/{system_id}", json={"name": "新生指南"}).status_code == 200
    assert api.c.delete(f"/api/files/categories/{system_id}").status_code == 409


# ================================================================ 边界与归一化


def test_like_wildcards_are_escaped_in_search(api):
    api.mkuser("alice")
    api.login("alice")
    _publish(api, "线性代数", visibility="public")
    _publish(api, "英语", visibility="public")

    # 通配符必须被当成字面量：否则一个 % 就能把全库都捞出来。
    # Wildcards must be treated literally, otherwise a single % would surface the whole library.
    for term in ["%", "_", "%%", "__", "a%b", "a_b", "\\", "%\\%"]:
        result = api.c.get("/api/files/resources", params={"q": term}).json()
        assert result["total"] == 0, f"term={term!r} matched {result['total']} rows"

    # 正常关键词仍然要能命中，证明上一条不是"所有搜索都返回空"的假绿。
    # A normal keyword must still match, proving the previous check is not a false green where
    # every search simply returns nothing.
    assert api.c.get("/api/files/resources", params={"q": "线性"}).json()["total"] == 1


def test_tag_normalisation_handles_extremes(api):
    api.mkuser("alice")
    api.login("alice")
    long_tag = "x" * 60
    tags = ["  Math  ", "math", "MATH", long_tag, "", "   ", "a,b", "c，d", "e、f", "g;h"]
    created = _publish(api, "标签极端值", visibility="public", tags=tags)
    result = created["tags"]
    # 小写、去重、去空、限长 24、限量 8；中英文分隔符都当作分隔符处理。
    # Lower-cased, de-duplicated, empties dropped, clamped to 24 chars and 8 entries, with both
    # ASCII and CJK separators treated as separators.
    assert result.count("math") == 1
    assert all(len(tag) <= files_service.MAX_TAG_LENGTH for tag in result)
    assert len(result) <= files_service.MAX_TAGS
    assert "" not in result
    assert "x" * 24 in result
    assert "a" in result and "b" in result


def test_pagination_beyond_the_last_page_is_empty_not_an_error(api):
    api.mkuser("alice")
    api.login("alice")
    _publish(api, "唯一一条", visibility="public")
    page = api.c.get("/api/files/resources", params={"page": 99, "pageSize": 20}).json()
    assert page["items"] == []
    assert page["total"] == 1
    assert page["page"] == 99


def test_size_boundaries(api):
    api.mkuser("alice")
    api.login("alice")
    # 0 字节与超上限都必须在 presign 就被拒（体积下限/上限各有 422）。
    # Zero bytes and an over-limit size must both be refused at presign time (422 for each bound).
    assert api.c.post(
        "/api/files/resources/presign",
        json={"filename": "a.pdf", "mimeType": "application/pdf", "sizeBytes": 0},
    ).status_code == 422
    assert api.c.post(
        "/api/files/resources/presign",
        json={"filename": "a.pdf", "mimeType": "application/pdf", "sizeBytes": 50 * 1024 * 1024 + 1},
    ).status_code == 422


def test_declared_mime_type_never_controls_served_content_type(api):
    api.mkuser("alice")
    api.login("alice")
    # 声明成 text/html 也必须按扩展名回源为 text/plain：否则可被内联渲染成存储型 XSS。
    # Declaring text/html must still be served as text/plain derived from the extension, otherwise
    # it could be rendered inline as stored XSS.
    body = b"<script>alert(1)</script>"
    presign = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.txt", "mimeType": "text/html", "sizeBytes": len(body)},
    ).json()
    assert api.c.put(presign["uploadUrl"], content=body).status_code == 204
    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": len(body),
            "categoryId": _category_id(api, "study-syllabus"),
            "title": "伪装的 HTML",
            "visibility": "public",
            "originalFilename": "notes.txt",
            "mimeType": "text/html",
        },
    )
    assert created.status_code == 201, created.text
    resource_id = created.json()["id"]

    ticket = api.c.get(f"/api/files/resources/{resource_id}/download").json()
    served = api.c.get(ticket["downloadUrl"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/plain")
    assert served.headers.get("x-content-type-options", "").lower() == "nosniff"


# ================================================================ 孤儿对象回收


def test_orphan_sweep_reclaims_uploads_that_never_became_resources(api):
    """回归：修之前 presign 后不上传完就放弃的字节会永远留在磁盘上。

    Regression: before the fix, bytes abandoned after a presign stayed on disk forever.
    """
    api.mkuser("alice")
    api.login("alice")
    storage = api.app.state.storage

    # 认领过的对象：绝不能删。
    # A claimed object must never be removed.
    claimed = _publish(api, "已认领", visibility="public")
    claimed_path = storage.path_for(claimed["objectKey"])
    assert os.path.exists(claimed_path)

    # 传了字节但从未创建资料：应当被回收。
    # Bytes uploaded but never turned into a resource: these should be reclaimed.
    orphan = _presign(api, filename="abandoned.pdf", size=3)
    assert api.c.put(orphan["uploadUrl"], content=b"abc").status_code == 204
    orphan_path = storage.path_for(orphan["objectKey"])
    assert os.path.exists(orphan_path)

    assert api.app.state.reap_file_orphans(0) == 1
    assert not os.path.exists(orphan_path)
    assert os.path.exists(claimed_path)


def test_orphan_sweep_respects_the_retention_window(api):
    api.mkuser("alice")
    api.login("alice")
    storage = api.app.state.storage
    orphan = _presign(api, filename="fresh.pdf", size=3)
    assert api.c.put(orphan["uploadUrl"], content=b"abc").status_code == 204
    path = storage.path_for(orphan["objectKey"])

    # 保留窗口内的对象不能被删：否则会误删"正在上传、还没提交元数据"的字节。
    # Objects inside the retention window must survive, otherwise bytes still being uploaded
    # (metadata not submitted yet) would be deleted underneath the user.
    assert api.app.state.reap_file_orphans(24 * 3600 * 1000) == 0
    assert os.path.exists(path)


def test_orphan_sweep_never_touches_attachment_objects(api):
    """附件与资料共用同一个上传根目录，回收器只认数据库引用，不认目录。

    Attachments and resources share one upload root, and the sweep works from database
    references rather than directory layout, so an attachment's bytes are never at risk.
    """
    api.mkuser("alice")
    api.login("alice")
    storage = api.app.state.storage
    presign = api.c.post(
        "/api/attachments/presign",
        json={"filename": "attach.pdf", "mimeType": "application/pdf", "sizeBytes": 3},
    )
    assert presign.status_code == 200, presign.text
    upload_url = presign.json()["uploadUrl"]
    assert api.c.put(upload_url, content=b"abc").status_code == 204

    from sqlalchemy import select

    from samryetha.schema import attachments

    with api.app.state.db.request_conn() as conn:
        object_key = conn.execute(select(attachments.c.object_key)).scalar()
    assert object_key
    path = storage.path_for(object_key)
    assert os.path.exists(path)

    assert api.app.state.reap_file_orphans(0) == 0
    assert os.path.exists(path)
