"""文件服务文件夹（Stage 2）：迁移、权限、归属、可见性与删除策略。

File-service folders (stage 2): migration, authorisation, filing, visibility and the delete policy.

本文件的用例分三类 / Three kinds of cases live here:
1. **迁移与向前兼容**：新增表与新增列如何落到既有库上，历史数据为何自动视为"未分类"。
   Migration and forward compatibility: how the new table and column land on an existing database
   and why pre-existing rows are automatically treated as "unfiled".
2. **越权红线**：文件夹只是视图，绝不允许成为绕过可见性的旁路。
   The authorisation red line: a folder is only a view and must never become a bypass around
   visibility.
3. **删除策略**：删文件夹时资料如何处置（移到未分类，绝不删除）。
   The delete policy: what happens to resources when a folder goes (they move to unfiled and are
   never deleted).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from samryetha.core.db import now_ms
from samryetha.core.schema import file_resources, moderation_actions


def _category_id(api, slug: str = "study-syllabus") -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(api, title: str, *, visibility: str = "members", slug: str = "study-syllabus") -> dict:
    body = b"hello world"
    presign = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": len(body)},
    )
    assert presign.status_code == 200, presign.text
    ticket = presign.json()
    assert api.c.put(ticket["uploadUrl"], content=body).status_code == 204
    query = parse_qs(urlparse(ticket["uploadUrl"]).query)
    created = api.c.post(
        "/api/files/resources",
        json={
            "objectKey": ticket["objectKey"],
            "expires": query["expires"][0],
            "sig": query["sig"][0],
            "sizeBytes": len(body),
            "categoryId": _category_id(api, slug),
            "title": title,
            "visibility": visibility,
            "originalFilename": "notes.pdf",
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _seed_row(api, *, uploader_id: int, title: str, visibility: str = "private", folder_id: int | None = None) -> int:
    """直接落一行资料，用于构造"owner 不是管理员"的场景。

    拖拽/上传能力只给管理员（FILE_CREATE），所以普通用户其实传不上东西；但附件转入资料会
    把 owner 留给原附件上传者，因此非管理员 owner 是**真实存在**的，归属权限必须按它来验。
    Inserts a resource row directly so that a non-admin owner can be constructed. Uploading is
    admin-only (FILE_CREATE), so a plain user cannot publish anything; promoting an attachment does
    however leave ownership with the original uploader, which makes non-admin owners real, and the
    filing permission has to be checked against them.
    """
    with api.app.state.db.request_conn() as conn:
        result = conn.execute(file_resources.insert().values(
            category_id=_category_id(api), folder_id=folder_id, uploader_id=uploader_id,
            title=title, description_md="", tags="[]", object_key=f"seed/{title}-{now_ms()}",
            original_filename="seeded.pdf", mime_type="application/pdf", size_bytes=11,
            visibility=visibility, status="published", version=1, is_featured=0,
            download_count=0, favorite_count=0, rating_sum=0, rating_count=0,
            created_at=now_ms(), updated_at=now_ms()).returning(file_resources.c.id))
        return int(result.scalar_one())


def _user_id(api, username: str) -> int:
    from samryetha.core.schema import users

    with api.app.state.db.request_conn() as conn:
        return int(conn.execute(select(users.c.id).where(users.c.username == username)).scalar_one())


def _folders(api) -> list[dict]:
    response = api.c.get("/api/files/folders")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _find_folder(api, name: str) -> dict | None:
    for item in _folders(api):
        if item["name"] == name:
            return item
    return None


def _make_folder(api, name: str, parent_id: int | None = None) -> dict:
    payload: dict = {"name": name}
    if parent_id is not None:
        payload["parentId"] = parent_id
    response = api.c.post("/api/files/folders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _actions(api) -> list[tuple[str, str]]:
    with api.app.state.db.request_conn() as conn:
        return [(row[0], row[1]) for row in conn.execute(
            select(moderation_actions.c.action, moderation_actions.c.reason)).all()]


# ================================================================ 迁移 / migration


def test_folder_column_is_auto_addable_to_an_existing_database():
    """folder_id 必须满足 ensure_schema_drift 的自动补列条件，生成一条合法 DDL。

    这里没法真的把列"退回旧库"来跑一次补列：SQLite 拒绝 DROP 一个被外键定义引用的列
    （实测报 `unknown column "folder_id" in foreign key definition`）。所以改为直接钉死决定
    "能不能被自动补上"的三条性质（core/db.py:123 的判定条件）与渲染出的 DDL——
    这三条一破，存量库启动时就会直接抛 RuntimeError 拒绝启动，比晚一步发现要好得多。
    The column cannot literally be rolled back out of a database here to exercise the drift path:
    SQLite refuses to drop a column referenced by a foreign key definition (measured: `unknown
    column "folder_id" in foreign key definition`). So the three properties that decide whether it
    can be auto-added (the conditions in core/db.py:123) plus the rendered DDL are pinned instead.
    Break any of them and an existing database refuses to start with a RuntimeError, which is far
    better caught here than in production.
    """
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    from samryetha.core.db import _add_column_sql
    from samryetha.core.schema import file_resources

    column = file_resources.c.folder_id
    assert column.nullable is True, "a NOT NULL column could not be added to a table with rows"
    assert column.server_default is None, "the column needs no backfill, so it needs no default"
    assert column.primary_key is False
    # 未声明 unique 时 SQLAlchemy 给的是 None，而 ensure_schema_drift 判的是真值，故用 falsy 断言。
    # SQLAlchemy reports None when unique was never declared and ensure_schema_drift tests the value
    # for truthiness, so the assertion is on falsiness.
    assert not column.unique
    assert _add_column_sql(column, sqlite_dialect.dialect()) == "folder_id INTEGER"


def test_the_folder_index_is_restored_on_an_existing_database(db):
    """索引同样要走幂等补建：新增列往往配套新索引，而 create_all 对既有表是 no-op。"""
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS file_resources_folder_created_idx")
    with db.engine.connect() as conn:
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(file_resources)")}
    assert "file_resources_folder_created_idx" not in indexes

    db.ensure_schema_drift()

    with db.engine.connect() as conn:
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(file_resources)")}
    assert "file_resources_folder_created_idx" in indexes


def test_new_databases_get_the_folders_table(db):
    with db.engine.connect() as conn:
        tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "file_folders" in tables


def test_a_published_resource_starts_unfiled(api):
    """向前兼容的对外表现：没有任何归属的资料就是"未分类"，无需回填。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "未归类的资料")

    assert created["folderId"] is None
    unfiled = api.c.get("/api/files/resources?unfiled=true").json()
    assert [item["id"] for item in unfiled["items"]] == [created["id"]]
    assert api.c.get("/api/files/resources?folderId=1").json()["total"] == 0


# ================================================================ 权限 / authorisation


def test_folder_management_is_admin_only(api):
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("bob")
    assert api.c.post("/api/files/folders", json={"name": "bob 的文件夹"}).status_code == 403

    api.c.post("/api/auth/logout")
    api.login("alice")
    folder = _make_folder(api, "管理员建的文件夹")
    assert folder["parentId"] is None
    assert api.c.patch(f"/api/files/folders/{folder['id']}", json={"name": "改名后"}).status_code == 200

    api.c.post("/api/auth/logout")
    api.login("bob")
    assert api.c.patch(f"/api/files/folders/{folder['id']}", json={"name": "劫持"}).status_code == 403
    assert api.c.delete(f"/api/files/folders/{folder['id']}").status_code == 403


def test_folder_reads_are_open_but_writes_require_a_session(api):
    """读文件夹与读分类同样口径：匿名可读骨架，写一律要登录。

    匿名能拿到文件夹名与"对他可见"的计数，与 /api/files/categories、/api/files/config 一致；
    名字与过滤后的计数都不构成泄漏（见下面的可见性红线用例）。写操作则一律 401。
    Reading folders follows the same rule as reading categories: the skeleton is open to anyone and
    every write needs a session. An anonymous visitor gets folder names and viewer-filtered counts,
    exactly like /api/files/categories and /api/files/config, and neither is a leak (see the
    visibility red-line cases below). Writes answer 401.
    """
    assert api.c.get("/api/files/folders").status_code == 200
    assert api.c.post("/api/files/folders", json={"name": "匿名"}).status_code == 401
    assert api.c.patch("/api/files/folders/1", json={"name": "匿名"}).status_code == 401
    assert api.c.delete("/api/files/folders/1").status_code == 401


def test_owner_may_file_their_own_resource_but_not_someone_elses(api):
    """归属变更沿用既有 FILE_UPDATE 口径：管理员或该资料的 owner。

    这里刻意构造一个非管理员 owner（附件转入会留下这样的 owner），
    证明"能改这份资料"与"能改它的归属"用的是同一条权限。
    Filing goes through the existing FILE_UPDATE: an admin or the resource's owner. A non-admin
    owner is constructed deliberately (attachment promotion produces exactly this), proving that
    "may edit this resource" and "may re-file it" are the same permission.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.mkuser("carol")
    api.login("alice")
    folder = _make_folder(api, "公共文件夹")
    bob_id = _user_id(api, "bob")
    carol_id = _user_id(api, "carol")
    bobs = _seed_row(api, uploader_id=bob_id, title="bob 的资料", visibility="private")
    carols = _seed_row(api, uploader_id=carol_id, title="carol 的资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.put(f"/api/files/resources/{bobs}/folder", json={"folderId": folder["id"]}).status_code == 200
    # 别人的资料：403，且授权先于任何变更。
    # Someone else's resource: 403, and the check runs before any change.
    assert api.c.put(f"/api/files/resources/{carols}/folder", json={"folderId": folder["id"]}).status_code == 403
    # 不存在的资料是 404（不泄漏存在性），而不是 403。
    # A missing resource answers 404 rather than 403, so existence is not leaked.
    assert api.c.put("/api/files/resources/999999/folder", json={"folderId": folder["id"]}).status_code == 404


# ================================================================ 归属语义 / filing semantics


def test_filing_moves_a_resource_between_the_folder_and_unfiled(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "复习资料")
    created = _publish(api, "高等数学提纲")

    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 1
    assert api.c.put(f"/api/files/resources/{created['id']}/folder", json={"folderId": folder["id"]}).json()["folderId"] == folder["id"]
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 0
    assert api.c.get(f"/api/files/resources?folderId={folder['id']}").json()["total"] == 1
    # 不传 folderId = 显式移出到未分类（本端点的语义是"设置归属"而非"增量更新"）。
    # An omitted folderId means "move out into unfiled": this endpoint sets membership rather than
    # patching it.
    assert api.c.put(f"/api/files/resources/{created['id']}/folder", json={}).json()["folderId"] is None
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 1


def test_filing_a_missing_folder_is_a_404(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")
    assert api.c.put(f"/api/files/resources/{created['id']}/folder", json={"folderId": 999999}).status_code == 404
    # 拒绝之后归属保持不变：失败不能留下半截状态。
    # After the refusal the membership is unchanged: a failure must not leave a half-applied state.
    assert api.c.get(f"/api/files/resources/{created['id']}").json()["folderId"] is None


def test_folder_filters_combine_with_other_filters(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "限定范围")
    inside = _publish(api, "文件夹里的提纲")
    _publish(api, "文件夹外的提纲")
    api.c.put(f"/api/files/resources/{inside['id']}/folder", json={"folderId": folder["id"]})

    # 文件夹过滤与其他筛选是 AND：文件夹内 + 关键词命中 = 1 条。
    # Folder filtering ANDs with the other filters: inside the folder and matching the keyword is 1.
    assert api.c.get(f"/api/files/resources?folderId={folder['id']}&q=文件夹里").json()["total"] == 1
    assert api.c.get(f"/api/files/resources?folderId={folder['id']}&q=文件夹外").json()["total"] == 0
    # 不提供文件夹参数 = 全部（含已归类与未归类），与改动前行为一致。
    # Providing no folder parameter means everything, filed and unfiled alike, as before.
    assert api.c.get("/api/files/resources").json()["total"] == 2


# ================================================================ 可见性红线 / the red line


def test_folder_listing_never_widens_visibility(api):
    """核心红线：把资料放进文件夹，不能让原本不可见的资料变得可见。

    alice 的 private 资料放进文件夹 F 后，bob 看 F 必须看不到它，F 的计数对 bob 也必须是 0——
    计数如果按物理行数算，数字本身就会泄漏"这里藏了几份你看不到的资料"。
    The core red line: filing a resource must never make an invisible one visible. Once alice's
    private resource sits in folder F, bob must not see it in F, and F's count for bob must be zero;
    a physical count would leak through the number itself.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    folder = _make_folder(api, "私有归档")
    secret = _publish(api, "alice 的私有资料", visibility="private")
    shared = _publish(api, "alice 的公开资料", visibility="public")
    for item in (secret, shared):
        assert api.c.put(f"/api/files/resources/{item['id']}/folder", json={"folderId": folder["id"]}).status_code == 200
    api.c.post("/api/auth/logout")

    api.login("bob")
    listed = api.c.get(f"/api/files/resources?folderId={folder['id']}").json()
    assert [item["id"] for item in listed["items"]] == [shared["id"]]
    assert listed["total"] == 1
    # 直接从详情与下载两条路径再确认一次不可见。
    # The detail and download paths are checked as well.
    assert api.c.get(f"/api/files/resources/{secret['id']}").status_code == 404
    assert api.c.get(f"/api/files/resources/{secret['id']}/download").status_code == 404
    # 计数只算可见的那一份。
    # The count only includes the visible one.
    assert _find_folder(api, "私有归档")["resourceCount"] == 1
    # 未分类视图同样按可见性过滤，文件夹过滤器不是旁路。
    # The unfiled view is filtered by visibility too, so the folder filter is no bypass.
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 0

    api.c.post("/api/auth/logout")
    api.login("alice")
    assert _find_folder(api, "私有归档")["resourceCount"] == 2


def test_anonymous_visitors_see_no_private_folder_counts(api):
    """匿名能看到导航骨架，但计数里一份 member 资料都不算，更不算 private。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "内部资料")
    created = _publish(api, "成员可见资料", visibility="members")
    api.c.put(f"/api/files/resources/{created['id']}/folder", json={"folderId": folder["id"]})
    api.c.post("/api/auth/logout")

    listed = api.c.get("/api/files/folders")
    assert listed.status_code == 200
    visible = [item for item in listed.json()["items"] if item["name"] == "内部资料"]
    assert visible and visible[0]["resourceCount"] == 0
    # 列表接口本身也不会把 members 资料交给匿名访客。
    # The list endpoint does not hand a members-only resource to an anonymous visitor either.
    assert api.c.get(f"/api/files/resources?folderId={folder['id']}").json()["total"] == 0


# ================================================================ 删除策略 / delete policy


def test_deleting_a_folder_moves_resources_to_unfiled_and_deletes_none(api):
    """删除文件夹：资料移到未分类，一份都不删。

    与仓库既有约定同一底线——分类在仍持有资料时直接拒绝删除，可见"删容器连带删内容"在
    本项目是零容忍的。
    Deleting a folder moves its resources to unfiled and deletes none of them, the same bottom line
    the repository already draws for categories, which refuse to be deleted while they still hold
    resources.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "待删除")
    first = _publish(api, "资料一")
    second = _publish(api, "资料二")
    for item in (first, second):
        api.c.put(f"/api/files/resources/{item['id']}/folder", json={"folderId": folder["id"]})

    removed = api.c.delete(f"/api/files/folders/{folder['id']}")
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "detachedResourceCount": 2}

    assert _find_folder(api, "待删除") is None
    # 资料本身仍在，且回到未分类。
    # The resources themselves survive and are back in unfiled.
    assert api.c.get(f"/api/files/resources/{first['id']}").json()["folderId"] is None
    assert api.c.get(f"/api/files/resources/{second['id']}").json()["folderId"] is None
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 2


def test_deleting_a_folder_with_subfolders_is_refused(api):
    """子文件夹必须先处理：删父级顺手打散一整棵子树是用户看不出来的破坏。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    parent = _make_folder(api, "父级")
    _make_folder(api, "子级", parent_id=parent["id"])

    refused = api.c.delete(f"/api/files/folders/{parent['id']}")
    assert refused.status_code == 409, refused.text
    assert _find_folder(api, "父级") is not None
    assert _find_folder(api, "子级") is not None


def test_deleting_an_empty_folder_reports_zero_detached(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "空文件夹")
    assert api.c.delete(f"/api/files/folders/{folder['id']}").json()["detachedResourceCount"] == 0


# ================================================================ 校验与边界 / validation


def test_folder_names_are_unique_per_level_but_may_repeat_across_levels(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    top = _make_folder(api, "数学")
    # 同层重名（含大小写差异）直接拒绝：重名会让面包屑与移动目标都变得含糊。
    # A duplicate at the same level (case differences included) is refused outright, because it
    # would make both the breadcrumb and the move target ambiguous.
    assert api.c.post("/api/files/folders", json={"name": "数学"}).status_code == 409
    assert api.c.post("/api/files/folders", json={"name": "  数学  "}).status_code == 409
    child = _make_folder(api, "数学", parent_id=top["id"])
    assert child["parentId"] == top["id"]


def test_folder_nesting_has_a_ceiling(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    parent_id = None
    for depth in range(5):
        parent_id = _make_folder(api, f"第{depth + 1}层", parent_id=parent_id)["id"]
    too_deep = api.c.post("/api/files/folders", json={"name": "第6层", "parentId": parent_id})
    assert too_deep.status_code == 400, too_deep.text
    assert too_deep.json()["error"]["code"] == "BAD_REQUEST"


def test_a_folder_cannot_be_moved_into_itself_or_its_descendant(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    root = _make_folder(api, "根")
    child = _make_folder(api, "子", parent_id=root["id"])
    grandchild = _make_folder(api, "孙", parent_id=child["id"])

    assert api.c.patch(f"/api/files/folders/{root['id']}", json={"parentId": root["id"]}).status_code == 400
    assert api.c.patch(f"/api/files/folders/{root['id']}", json={"parentId": grandchild["id"]}).status_code == 400
    # 合法移动：孙子挂到根下，层数不超上限。
    # A legal move: the grandchild is re-parented onto the root, staying within the depth ceiling.
    moved = api.c.patch(f"/api/files/folders/{grandchild['id']}", json={"parentId": root["id"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["parentId"] == root["id"]
    # 显式 null = 移到顶层（与"未提供 parentId = 不动"靠 model_fields_set 区分）。
    # An explicit null means the top level, told apart from "parentId absent leaves it alone"
    # through model_fields_set.
    assert api.c.patch(f"/api/files/folders/{grandchild['id']}", json={"parentId": None}).json()["parentId"] is None
    assert api.c.patch(f"/api/files/folders/{child['id']}", json={"description": "子文件夹"}).json()["parentId"] == root["id"]


def test_folder_name_and_description_are_trimmed_and_capped(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = api.c.post("/api/files/folders", json={
        "name": "  两侧留白  ", "description": "  说明  ", "sortOrder": 7,
    }).json()
    assert folder["name"] == "两侧留白"
    assert folder["description"] == "说明"
    assert folder["sortOrder"] == 7
    # 空名（含全空白）拒绝；超长在请求模型层就被拒（与分类名 CategoryCreateBody 同上限 60）。
    # A blank name is refused; an over-long one is refused at the request-model layer, with the same
    # 60 character cap the category name carries.
    assert api.c.post("/api/files/folders", json={"name": "   "}).status_code == 400
    assert api.c.post("/api/files/folders", json={"name": "长" * 90}).status_code == 422
    assert api.c.post("/api/files/folders", json={"name": "长" * 60}).status_code == 201


def test_folder_listing_reports_child_and_resource_counts(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    parent = _make_folder(api, "父")
    _make_folder(api, "子一", parent_id=parent["id"])
    _make_folder(api, "子二", parent_id=parent["id"])
    created = _publish(api, "父级里的资料")
    api.c.put(f"/api/files/resources/{created['id']}/folder", json={"folderId": parent["id"]})

    listed = _find_folder(api, "父")
    assert listed["childCount"] == 2
    assert listed["resourceCount"] == 1
    # 子文件夹的计数只算自己体内的资料，不继承父级。
    # A subfolder counts only its own resources and does not inherit the parent's.
    assert _find_folder(api, "子一")["resourceCount"] == 0


# ================================================================ 审计 / audit trail


def test_folder_writes_leave_an_audit_trail(api):
    """文件夹与其归属的每一次写操作都要留痕，且 reason 里要能读出改了什么。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "留痕文件夹")
    created = _publish(api, "留痕资料")
    api.c.put(f"/api/files/resources/{created['id']}/folder", json={"folderId": folder["id"]})
    api.c.patch(f"/api/files/folders/{folder['id']}", json={"name": "改名留痕"})
    api.c.delete(f"/api/files/folders/{folder['id']}")

    recorded = {action: reason for action, reason in _actions(api)}
    assert "file.folder.create" in recorded
    assert "留痕文件夹" in recorded["file.folder.create"]
    assert "file.folder.update" in recorded
    assert "file.folder.delete" in recorded
    assert "detachedResources=1" in recorded["file.folder.delete"]
    assert recorded["file.resource.set_folder"] == f"from=None to={folder['id']}"


def test_folder_config_is_shipped_with_the_rest_of_the_constraints(api):
    """配置端点一次性下发文件夹，前端就不必再手抄一份结构、也就不可能与后端漂移。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    _make_folder(api, "配置里的文件夹")
    payload = api.c.get("/api/files/config").json()
    assert [item["name"] for item in payload["folders"]] == ["配置里的文件夹"]
    assert json.loads(json.dumps(payload))["folders"][0]["resourceCount"] == 0
