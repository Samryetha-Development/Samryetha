"""文件服务（/api/files）测试。

覆盖三条主线 / Three main threads:
1. 可见性：public / members / private 三档 + 待审状态对访客、他人、上传者、管理员的差异。
   Visibility: the public / members / private tiers plus how pending state differs for a
   guest, another user, the uploader and an admin.
2. 上传链路：presign -> signed PUT -> 创建，含签名与体积绑定被篡改时必须拒绝。
   The upload chain: presign -> signed PUT -> create, including rejection when the signed
   size binding is tampered with.
3. 互动与计数：收藏/评分的幂等性、均分、下载去重计数与明细全量记录。
   Interaction and counters: favourite/rating idempotency, averages, and download
   counting with de-duplication while the log still records every hit.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from samryetha.files import service as files_service
from samryetha.files import FileService


def _categories(api) -> list[dict]:
    r = api.c.get("/api/files/config")
    assert r.status_code == 200, r.text
    return r.json()["categories"]


def _category_id(api, slug: str) -> int:
    for item in _categories(api):
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(
    api,
    title: str,
    *,
    filename: str = "notes.pdf",
    body: bytes = b"hello world",
    visibility: str = "members",
    tags: list[str] | None = None,
    slug: str = "study-syllabus",
) -> dict:
    """走完整上传链路发布一条资料，返回创建后的详情。
    Publish a resource through the full upload chain and return the created detail.
    """
    c = api.c
    r = c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": "application/pdf", "sizeBytes": len(body)},
    )
    assert r.status_code == 200, r.text
    presign = r.json()

    upload = c.put(presign["uploadUrl"], content=body)
    assert upload.status_code == 204, upload.text

    query = parse_qs(urlparse(presign["uploadUrl"]).query)
    created = c.post(
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
    return created.json()


# ---------------------------------------------------------------- 配置与种子数据


def test_config_seeds_categories_and_constraints(api):
    data = api.c.get("/api/files/config").json()
    slugs = {item["slug"] for item in data["categories"]}
    # 内建分类必须齐备，否则导航骨架是空的。
    # Every built-in category must be present, otherwise the navigation skeleton is empty.
    assert {"freshman-guide", "exam-outline", "study-syllabus", "past-papers", "other-materials"} <= slugs
    assert data["maxUploadBytes"] > 0
    assert ".pdf" in data["allowedExtensions"]
    assert data["maxTags"] == files_service.MAX_TAGS


def test_seed_categories_are_idempotent(api):
    with api.app.state.db.request_conn() as conn:
        # 再跑一次种子必须新增 0 行。
        # Running the seed again must create zero rows.
        assert FileService(conn).ensure_seed_categories() == 0


# ---------------------------------------------------------------- 可见性


def test_guest_sees_only_public_resources(api):
    api.mkuser("alice")
    api.login("alice")
    _publish(api, "公开资料", visibility="public")
    _publish(api, "登录可见资料", visibility="members")
    _publish(api, "私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    listing = api.c.get("/api/files/resources").json()
    titles = {item["title"] for item in listing["items"]}
    # 三档可见性 + 总数都要一致，避免分页总数泄漏隐藏资源的存在。
    # Both the items and the total must agree, so the paging total cannot leak hidden rows.
    assert titles == {"公开资料"}
    assert listing["total"] == 1


def test_members_visibility_visible_to_signed_in_user(api):
    api.mkuser("alice")
    api.login("alice")
    _publish(api, "登录可见资料", visibility="members")
    api.c.post("/api/auth/logout")

    api.mkuser("bob")
    api.login("bob")
    titles = {item["title"] for item in api.c.get("/api/files/resources").json()["items"]}
    assert "登录可见资料" in titles


def test_private_resource_hidden_from_others_but_visible_to_uploader(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.mkuser("bob")
    api.login("bob")
    assert api.c.get(f"/api/files/resources/{created['id']}").status_code == 404

    api.c.post("/api/auth/logout")
    api.login("alice")
    assert api.c.get(f"/api/files/resources/{created['id']}").status_code == 200


def test_published_public_resource_has_no_review_state(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    assert "moderationStatus" not in created
    api.c.post("/api/auth/logout")
    assert api.c.get("/api/files/resources").json()["total"] == 1
    assert api.c.get(f"/api/files/resources/{created['id']}").status_code == 200


def test_unknown_resource_returns_404(api):
    assert api.c.get("/api/files/resources/999999").status_code == 404


# ---------------------------------------------------------------- 上传链路


def test_upload_flow_creates_downloadable_resource(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "高数期末提纲", visibility="public", tags=["Math", " math ", "期末"])
    # 标签归一化：小写、去重。
    # Tags are normalised: lower-cased and de-duplicated.
    assert created["tags"] == ["math", "期末"]
    assert created["downloadCount"] == 0
    assert created["can"]["update"] is True

    download = api.c.get(f"/api/files/resources/{created['id']}/download")
    assert download.status_code == 200, download.text
    served = api.c.get(download.json()["downloadUrl"])
    assert served.status_code == 200
    assert served.content == b"hello world"


def test_upload_rejects_size_mismatch(api):
    api.mkuser("alice")
    api.login("alice")
    c = api.c
    presign = c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": 5},
    ).json()
    upload = c.put(presign["uploadUrl"], content=b"far more than five bytes")
    assert upload.status_code == 400


def test_upload_signature_bound_to_uploader(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    presign = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": 3},
    ).json()
    api.c.post("/api/auth/logout")

    api.login("bob")
    # bob 拿到 alice 上传地址也不能用：签名里的上传者与签名会话不一致。
    # bob cannot reuse alice's upload URL: the uploader inside the signature does not match
    # the signing session.
    assert api.c.put(presign["uploadUrl"], content=b"abc").status_code == 403


def test_create_rejects_tampered_size(api):
    api.mkuser("alice")
    api.login("alice")
    c = api.c
    presign = c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": 3},
    ).json()
    c.put(presign["uploadUrl"], content=b"abc")
    query = parse_qs(urlparse(presign["uploadUrl"]).query)

    tampered = c.post(
        "/api/files/resources",
        json={
            "objectKey": presign["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": 4096,
            "categoryId": _category_id(api, "study-syllabus"),
            "title": "篡改体积",
        },
    )
    assert tampered.status_code == 400


def test_upload_rejects_unsupported_extension(api):
    api.mkuser("alice")
    api.login("alice")
    r = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "payload.exe", "mimeType": "application/octet-stream", "sizeBytes": 10},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------- 权限


def test_only_uploader_or_admin_can_update_and_delete(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.mkuser("root", role="admin")

    api.login("alice")
    created = _publish(api, "alice 的资料", visibility="public")
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.patch(f"/api/files/resources/{created['id']}", json={"title": "改名"}).status_code == 403
    assert api.c.delete(f"/api/files/resources/{created['id']}").status_code == 403
    api.c.post("/api/auth/logout")

    api.login("root")
    assert api.c.patch(f"/api/files/resources/{created['id']}", json={"title": "管理员改名"}).status_code == 200


def test_category_management_requires_admin(api):
    api.mkuser("alice")
    api.mkuser("root", role="admin")

    api.login("alice")
    assert api.c.post("/api/files/categories", json={"slug": "x", "name": "X"}).status_code == 403
    api.c.post("/api/auth/logout")

    api.login("root")
    created = api.c.post(
        "/api/files/categories", json={"slug": "lab-reports", "name": "实验报告", "kind": "other"}
    )
    assert created.status_code == 201, created.text
    assert api.c.delete(f"/api/files/categories/{created.json()['id']}").status_code == 200


def test_builtin_category_cannot_be_deleted(api):
    api.mkuser("root", role="admin")
    api.login("root")
    system_id = _category_id(api, "freshman-guide")
    assert api.c.delete(f"/api/files/categories/{system_id}").status_code == 409


def test_category_with_resources_cannot_be_deleted(api):
    api.mkuser("root", role="admin")
    api.login("root")
    created = api.c.post("/api/files/categories", json={"slug": "temp-cat", "name": "临时"}).json()
    _publish(api, "占用中的资料", slug="temp-cat")
    assert api.c.delete(f"/api/files/categories/{created['id']}").status_code == 409


# ---------------------------------------------------------------- 收藏与评分


def test_favorite_is_idempotent_and_counted(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]

    first = api.c.put(f"/api/files/resources/{rid}/favorite")
    assert first.status_code == 200
    assert first.json()["favoriteCount"] == 1
    # 重复收藏不得把计数推高（幂等）。
    # Favouriting twice must not push the counter up (idempotent).
    again = api.c.put(f"/api/files/resources/{rid}/favorite")
    assert again.json()["favoriteCount"] == 1

    assert api.c.get("/api/files/favorites").json()["total"] == 1
    removed = api.c.delete(f"/api/files/resources/{rid}/favorite")
    assert removed.json()["favoriteCount"] == 0
    # 再删一次同样幂等。
    # Deleting twice stays idempotent too.
    assert api.c.delete(f"/api/files/resources/{rid}/favorite").json()["favoriteCount"] == 0


def test_rating_average_and_update_and_clear(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.mkuser("carol")

    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 5}).json()["ratingAvg"] == 5.0
    api.c.post("/api/auth/logout")

    api.login("bob")
    bob = api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 3}).json()
    assert bob["ratingCount"] == 2
    assert bob["ratingAvg"] == 4.0
    # 同一人改分是覆盖式，不新增票数。
    # Changing one's own score overwrites it and adds no new vote.
    bob = api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 1}).json()
    assert bob["ratingCount"] == 2
    assert bob["ratingAvg"] == 3.0
    api.c.post("/api/auth/logout")

    api.login("carol")
    cleared = api.c.delete(f"/api/files/resources/{rid}/rating").json()
    # carol 从未评分：撤销应当是无副作用的空操作。
    # carol never rated: clearing must be a side-effect-free no-op.
    assert cleared["ratingCount"] == 2

    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 6}).status_code == 422


def test_rating_rejected_out_of_range(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    assert api.c.put(f"/api/files/resources/{created['id']}/rating", json={"score": 0}).status_code == 422


# ---------------------------------------------------------------- 下载计数


def test_download_count_is_deduplicated_per_user(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]

    for _ in range(3):
        assert api.c.get(f"/api/files/resources/{rid}/download").status_code == 200
    detail = api.c.get(f"/api/files/resources/{rid}").json()
    # 计数只算首次，但明细必须留 3 条记录。
    # The counter only counts the first hit while the log must keep all three records.
    assert detail["downloadCount"] == 1

    from sqlalchemy import func, select

    from samryetha.core.schema import file_downloads

    with api.app.state.db.request_conn() as conn:
        total = conn.execute(
            select(func.count()).select_from(file_downloads).where(file_downloads.c.resource_id == rid)
        ).scalar()
    assert total == 3


def test_deleted_resource_disappears_and_cannot_be_downloaded(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]
    assert api.c.delete(f"/api/files/resources/{rid}").status_code == 200
    assert api.c.get(f"/api/files/resources/{rid}").status_code == 404
    assert api.c.get(f"/api/files/resources/{rid}/download").status_code == 404
    assert api.c.get("/api/files/resources").json()["total"] == 0


# ---------------------------------------------------------------- 检索与排序


def test_search_filter_and_sort(api):
    api.mkuser("alice")
    api.login("alice")
    _publish(api, "线性代数提纲", visibility="public", tags=["math"], slug="exam-outline")
    _publish(api, "英语四级词汇", visibility="public", tags=["english"], slug="study-syllabus")

    by_query = api.c.get("/api/files/resources", params={"q": "线性"}).json()
    assert by_query["total"] == 1
    assert by_query["items"][0]["title"] == "线性代数提纲"

    by_tag = api.c.get("/api/files/resources", params={"tag": "english"}).json()
    assert by_tag["total"] == 1

    by_category = api.c.get("/api/files/resources", params={"category": "exam-outline"}).json()
    assert by_category["total"] == 1

    # LIKE 通配符必须被转义：否则单个 % 会匹配到全部资料。
    # LIKE wildcards must be escaped, otherwise a lone % would match everything.
    assert api.c.get("/api/files/resources", params={"q": "%"}).json()["total"] == 0


def test_list_is_paginated(api):
    api.mkuser("alice")
    api.login("alice")
    for index in range(3):
        _publish(api, f"资料 {index}", visibility="public")
    first = api.c.get("/api/files/resources", params={"page": 1, "pageSize": 2}).json()
    assert first["total"] == 3
    assert len(first["items"]) == 2
    second = api.c.get("/api/files/resources", params={"page": 2, "pageSize": 2}).json()
    assert len(second["items"]) == 1
    # 固定分页边界：pageSize 超上限必须被夹紧而不是报错。
    # Fixed paging boundary: an over-large pageSize is clamped rather than rejected.
    assert api.c.get("/api/files/resources", params={"pageSize": 999}).json()["pageSize"] == files_service.MAX_PAGE_SIZE


def test_sort_by_favorites_surfaces_most_saved(api):
    api.mkuser("alice")
    api.login("alice")
    plain = _publish(api, "无人收藏", visibility="public")
    popular = _publish(api, "被收藏的", visibility="public")
    assert api.c.put(f"/api/files/resources/{popular['id']}/favorite").status_code == 200

    ordered = api.c.get("/api/files/resources", params={"sort": "favorites"}).json()
    assert ordered["sort"] == "favorites"
    # 收藏数排序：收藏最多的一律排在最前，且下载量为 0 也不影响它。
    # Sorting by saves puts the most saved first, regardless of a zero download count.
    assert ordered["items"][0]["id"] == popular["id"]
    assert ordered["items"][-1]["id"] == plain["id"]


def test_preview_does_not_count_as_a_download(api):
    api.mkuser("alice")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]

    assert api.c.get(f"/api/files/resources/{rid}/download", params={"preview": "true"}).status_code == 200
    assert api.c.get(f"/api/files/resources/{rid}/download", params={"preview": "true"}).status_code == 200
    # 预览不算下载：下载量这个核心排序信号不能被"看一眼"污染。
    # A preview is not a download: the core sorting signal must not be polluted by glances.
    assert api.c.get(f"/api/files/resources/{rid}").json()["downloadCount"] == 0
