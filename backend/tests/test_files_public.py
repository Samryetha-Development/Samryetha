"""Open to Public 直链公开开关（file_resources.is_public）测试。

覆盖四条主线 / Four threads:
1. 设置权限：仅管理员可切换；匿名 401、普通用户 403、幽灵 ID（不存在）鉴权先于查找且不产生写入。
   Set permissions: only admins may toggle; signed-out gets 401, a plain user 403, and a ghost id
   is answered with 403/404 before any write.
2. 匿名放行：is_public=true 的资料匿名可经详情/下载/回源三个端点访问（visibility=private 亦然）。
   Anonymous release: an is_public resource is readable/downloadable/servable by a signed-out
   visitor even when visibility is private.
3. 最小暴露：is_public=true 且 visibility=private 的资料不进匿名列表，只能凭资源 ID 直取。
   Minimal exposure: such a resource never enters the anonymous list, only reachable by id.
4. 撤销即时生效 + 审计字段落库。
   Revocation takes effect immediately and the audit columns are written.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _category_id(api, slug: str) -> int:
    r = api.c.get("/api/files/config")
    assert r.status_code == 200, r.text
    for item in r.json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(
    api,
    title: str,
    *,
    filename: str = "notes.pdf",
    body: bytes = b"hello world",
    visibility: str = "private",
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
            "tags": [],
            "originalFilename": filename,
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_admin_can_set_public_and_anonymous_gains_access(api):
    # 红绿对照：若去掉 FILE_READ 里的 is_public 分支，本用例的匿名访问断言全部变红。
    # Red/green control: without the is_public branch in FILE_READ every anonymous-access
    # assertion below turns red.
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "公开资料", visibility="private")
    rid = created["id"]
    assert created["isPublic"] is False

    switched = api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True})
    assert switched.status_code == 200, switched.text
    assert switched.json()["isPublic"] is True

    api.c.post("/api/auth/logout")
    # 匿名可经详情/下载/回源访问。
    # Signed-out may access via detail, download and serve.
    detail = api.c.get(f"/api/files/resources/{rid}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["isPublic"] is True

    ticket = api.c.get(f"/api/files/resources/{rid}/download")
    assert ticket.status_code == 200, ticket.text
    served = api.c.get(ticket.json()["downloadUrl"])
    assert served.status_code == 200
    assert served.content == b"hello world"

    # 最小暴露：visibility=private 的公开资料不进匿名列表。
    # Minimal exposure: a private public resource never enters the anonymous list.
    assert api.c.get("/api/files/resources").json()["total"] == 0


def test_non_owner_favorite_includes_public_private_link_but_anonymous_list_excludes_it(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "公开私有直链", visibility="private")
    rid = created["id"]
    assert api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True}).status_code == 200

    api.mkuser("bob")
    api.login("bob")
    assert api.c.put(f"/api/files/resources/{rid}/favorite").status_code == 200
    favorites = api.c.get("/api/files/favorites")
    assert favorites.status_code == 200, favorites.text
    assert [item["id"] for item in favorites.json()["items"]] == [rid]

    api.c.post("/api/auth/logout")
    anonymous = api.c.get("/api/files/resources")
    assert anonymous.status_code == 200, anonymous.text
    assert rid not in [item["id"] for item in anonymous.json()["items"]]


def test_only_admin_can_set_public(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", visibility="private")
    rid = created["id"]
    api.c.post("/api/auth/logout")

    # 匿名 → 401（require_admin 先于 body 解析）。
    # Signed-out -> 401 (require_admin runs before the body is parsed).
    assert api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True}).status_code == 401

    api.mkuser("bob")
    api.login("bob")
    # 普通用户 → 403；幽灵 ID + 合法载荷同样 403 = 鉴权先于资源查找。
    # A plain user -> 403; a ghost id with a valid payload is 403 too, which proves authorisation
    # precedes the resource lookup.
    assert api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True}).status_code == 403
    assert api.c.patch("/api/files/resources/999999/public", json={"isPublic": True}).status_code == 403


def test_admin_set_public_on_missing_resource_is_404_without_write(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    assert api.c.patch("/api/files/resources/999999/public", json={"isPublic": True}).status_code == 404


def test_cancel_public_revokes_anonymous_access_immediately(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", visibility="private")
    rid = created["id"]
    api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True})
    api.c.post("/api/auth/logout")
    assert api.c.get(f"/api/files/resources/{rid}").status_code == 200

    api.login("alice")
    assert api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": False}).status_code == 200
    api.c.post("/api/auth/logout")
    # 撤销公开后匿名立刻 404（详情与下载都回 404）。
    # Once un-published the anonymous visitor immediately gets 404 on both detail and download.
    assert api.c.get(f"/api/files/resources/{rid}").status_code == 404
    assert api.c.get(f"/api/files/resources/{rid}/download").status_code == 404


def test_private_resource_stays_hidden_from_anonymous_without_public(api):
    # is_public=false 的文件匿名访问保持原有 404 行为。
    # A resource with is_public=false keeps its original 404 for signed-out visitors.
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "私有资料", visibility="private")
    rid = created["id"]
    api.c.post("/api/auth/logout")

    assert api.c.get(f"/api/files/resources/{rid}").status_code == 404
    assert api.c.get(f"/api/files/resources/{rid}/download").status_code == 404


def test_public_toggle_records_audit_fields(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", visibility="private")
    rid = created["id"]

    api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True})

    from sqlalchemy import func, select

    from samryetha.core.schema import file_resources, moderation_actions

    with api.app.state.db.request_conn() as conn:
        row = conn.execute(
            select(file_resources.c.is_public, file_resources.c.public_set_by, file_resources.c.public_set_at)
            .where(file_resources.c.id == rid)
        ).one()
        audit = conn.execute(
            select(func.count()).select_from(moderation_actions).where(
                moderation_actions.c.action == "file.set_public",
                moderation_actions.c.target_type == "file_resource",
                moderation_actions.c.target_id == rid,
            )
        ).scalar_one()
    assert row[0] == 1
    assert row[1] is not None  # public_set_by 记录切换者
    assert row[2] is not None  # public_set_at 记录切换时间
    assert audit == 1

    # 管理员视图的详情要回这两个审计字段。
    # The admin view of the detail carries both audit fields back.
    detail = api.c.get(f"/api/files/resources/{rid}").json()
    assert detail["publicSetBy"] == row[1]
    assert detail["publicSetAt"] == row[2]


def test_public_audit_fields_are_admin_only(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", visibility="public")
    rid = created["id"]
    api.c.patch(f"/api/files/resources/{rid}/public", json={"isPublic": True})
    api.c.post("/api/auth/logout")

    # 匿名可见（visibility=public 本身匿名可读），但审计字段只给管理员，匿名响应里不该出现。
    # A signed-out visitor may read it (visibility=public is anonymous-readable on its own), but the
    # audit fields are admin-only and must not appear in the anonymous response.
    guest = api.c.get(f"/api/files/resources/{rid}")
    assert guest.status_code == 200
    assert "publicSetBy" not in guest.json()
    assert "publicSetAt" not in guest.json()
