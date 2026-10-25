"""文件服务批量操作：能力分层、逐条结果、审计与删除语义。

File-service batch operations: the ability tiers, per-row results, auditing and delete semantics.

本文件把"只有管理员可以批量管理，普通用户只能批量下载"这条需求拆成可判红绿的断言，
每条断言都对应一个真实 HTTP 请求，而不是读代码里的 if。
This file turns the requirement "only admins may batch manage while ordinary users may only batch
download" into assertions that can genuinely go red: every assertion corresponds to a real HTTP
request rather than to an `if` read in the source.

三类用例 / Three kinds of cases:
1. **能力分层**：批量下载对 active 账户开放；批量移动/复制/删除/重命名/编辑仅管理员，
   普通用户拿到真实的 403、匿名拿到 401。
   Ability tiers: batch download is open to any active account; batch move/copy/delete/rename/edit
   are admin-only, with ordinary users getting a real 403 and anonymous callers a 401.
2. **逐条结果**：部分失败时必须能说清哪一条失败及原因，不能让整批看起来成功。
   Per-row results: a partial failure has to name the failed row and its reason; the batch must
   never look uniformly successful.
3. **语义与留痕**：移动/复制/删除/重命名/编辑各自的数据后果，以及 moderation_actions 审计。
   Semantics and trails: the data effect of move/copy/delete/rename/edit plus the audit rows.
"""

from __future__ import annotations

import io
import zipfile
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from samryetha.core.db import now_ms
from samryetha.core.schema import file_resources, moderation_actions


def _category_id(api, slug: str = "study-syllabus") -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(api, title: str, *, visibility: str = "members", filename: str = "notes.pdf") -> dict:
    body = b"hello world"
    presign = api.c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": "application/pdf", "sizeBytes": len(body)},
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
            "categoryId": _category_id(api),
            "title": title,
            "visibility": visibility,
            "originalFilename": filename,
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _make_folder(api, name: str) -> dict:
    response = api.c.post("/api/files/folders", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _user_id(api, username: str) -> int:
    from samryetha.core.schema import users

    with api.app.state.db.request_conn() as conn:
        return int(conn.execute(select(users.c.id).where(users.c.username == username)).scalar_one())


def _actions(api) -> list[tuple[str, str]]:
    with api.app.state.db.request_conn() as conn:
        return [(row[0], row[1]) for row in conn.execute(
            select(moderation_actions.c.action, moderation_actions.c.reason)).all()]


def _detail(api, resource_id: int) -> dict:
    response = api.c.get(f"/api/files/resources/{resource_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _admin(api) -> dict:
    """建一个管理员并登录；返回 api 里当前会话身份是管理员。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    return api


def _plain_user(api) -> None:
    """建一个普通 active 用户并登录（批量下载必须对它可用）。"""
    api.mkuser("bob")
    api.login("bob")


# ================================================================ 批量下载 / batch download


def test_an_admin_can_batch_download_the_selected_resources(api):
    _admin(api)
    first = _publish(api, "资料甲")
    second = _publish(api, "资料乙")
    response = api.c.post("/api/files/batch/download", json={"ids": [first["id"], second["id"]]})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    # 两个同名文件都要在包里：第二个必须改成带序号的名字，否则压缩包会静默少一份。
    # Both same-named files must be present: the second has to gain a numeric suffix, or the archive
    # would silently lose one entry.
    assert sorted(names) == ["notes (2).pdf", "notes.pdf"], names


def test_batch_download_counts_each_retrieved_resource(api):
    _admin(api)
    first = _publish(api, "计数甲")
    before = _detail(api, first["id"])["downloadCount"]
    assert api.c.post("/api/files/batch/download", json={"ids": [first["id"]]}).status_code == 200
    assert _detail(api, first["id"])["downloadCount"] == before + 1


def test_a_plain_user_may_batch_download(api):
    """需求的核心一条：普通用户必须能批量下载，不能因为不是管理员而被挡。"""
    _admin(api)
    shared = _publish(api, "成员可见资料", visibility="members")
    _plain_user(api)
    response = api.c.post("/api/files/batch/download", json={"ids": [shared["id"]]})
    assert response.status_code == 200, response.text
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == ["notes.pdf"]


def test_batch_download_is_refused_when_any_selected_resource_is_unreadable(api):
    """一条读不到就整批 403：绝不"悄悄少放几份"。"""
    _admin(api)
    shared = _publish(api, "可见资料", visibility="members")
    hidden = _publish(api, "私有资料", visibility="private")
    _plain_user(api)
    response = api.c.post("/api/files/batch/download", json={"ids": [shared["id"], hidden["id"]]})
    assert response.status_code == 403, response.text


def test_anonymous_callers_cannot_batch_download(api):
    _admin(api)
    created = _publish(api, "公开资料", visibility="public")
    api.c.cookies.clear()
    assert api.c.post("/api/files/batch/download", json={"ids": [created["id"]]}).status_code == 401


# ================================================================ 批量移动 / batch move


def test_an_admin_can_batch_move_resources_into_a_folder(api):
    _admin(api)
    folder = _make_folder(api, "学期资料")
    first = _publish(api, "移动甲")
    second = _publish(api, "移动乙")
    response = api.c.post("/api/files/batch/move", json={"ids": [first["id"], second["id"]], "folderId": folder["id"]})
    assert response.status_code == 200, response.text
    assert response.json() == {"requested": 2, "succeeded": 2, "failed": 0,
                               "results": [{"id": first["id"], "ok": True}, {"id": second["id"], "ok": True}]}
    assert _detail(api, first["id"])["folderId"] == folder["id"]
    assert _detail(api, second["id"])["folderId"] == folder["id"]
    assert any(action == "file.resource.set_folder" and "batch" in reason for action, reason in _actions(api))


def test_batch_move_back_to_unfiled_needs_no_folder_id(api):
    _admin(api)
    folder = _make_folder(api, "临时归档")
    created = _publish(api, "归档资料")
    api.c.post("/api/files/batch/move", json={"ids": [created["id"]], "folderId": folder["id"]})
    response = api.c.post("/api/files/batch/move", json={"ids": [created["id"]]})
    assert response.status_code == 200, response.text
    assert _detail(api, created["id"])["folderId"] is None


def test_a_plain_user_is_refused_batch_move(api):
    """需求的核心一条：普通用户的管理类批量操作必须被真实拒绝（403）。"""
    _admin(api)
    folder = _make_folder(api, "管理员专属")
    created = _publish(api, "普通用户的资料")
    _plain_user(api)
    response = api.c.post("/api/files/batch/move", json={"ids": [created["id"]], "folderId": folder["id"]})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "FORBIDDEN"


# ================================================================ 批量删除 / batch delete


def test_an_admin_can_batch_delete_and_the_rows_are_soft_deleted(api):
    _admin(api)
    first = _publish(api, "待删甲")
    second = _publish(api, "待删乙")
    response = api.c.post("/api/files/batch/delete", json={"ids": [first["id"], second["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 2
    # 软删：详情变 404，但库里仍有行且记了删除人——批量删除不是物理抹除。
    # Soft delete: the detail answers 404 while the row survives with a deleted_by, because a batch
    # delete is not a physical wipe.
    assert api.c.get(f"/api/files/resources/{first['id']}").status_code == 404
    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(file_resources.c.deleted_at, file_resources.c.deleted_by)
                           .where(file_resources.c.id == first["id"])).first()
    assert row is not None and row[0] is not None and row[1] == _user_id(api, "alice")
    assert any(action == "file.resource.delete" and "batch" in reason for action, reason in _actions(api))


def test_a_plain_user_is_refused_batch_delete(api):
    _admin(api)
    created = _publish(api, "受保护资料")
    _plain_user(api)
    assert api.c.post("/api/files/batch/delete", json={"ids": [created["id"]]}).status_code == 403
    # 拒绝之后资料仍然可见：被拒的请求绝不能留下半截状态。
    # After the refusal the resource is still there: a rejected request must leave no half state.
    assert api.c.get(f"/api/files/resources/{created['id']}").status_code == 200


# ================================================================ 批量重命名 / batch rename


def test_batch_rename_supports_prefix_suffix_and_replace(api):
    _admin(api)
    created = _publish(api, "线性代数")
    assert api.c.post("/api/files/batch/rename", json={"ids": [created["id"]], "mode": "prefix", "prefix": "期末-"}
                      ).status_code == 200
    assert _detail(api, created["id"])["title"] == "期末-线性代数"
    assert api.c.post("/api/files/batch/rename", json={"ids": [created["id"]], "mode": "suffix", "suffix": "-修订"}
                      ).status_code == 200
    assert _detail(api, created["id"])["title"] == "期末-线性代数-修订"
    assert api.c.post("/api/files/batch/rename",
                      json={"ids": [created["id"]], "mode": "replace", "find": "期末", "replace": "期中"}
                      ).status_code == 200
    assert _detail(api, created["id"])["title"] == "期中-线性代数-修订"
    assert any(action == "file.update_metadata" and "batch rename" in reason for action, reason in _actions(api))


def test_batch_rename_without_a_needle_is_a_bad_request(api):
    """参数错误整批 400：不能一半标题被改、一半被拒。"""
    _admin(api)
    created = _publish(api, "原题")
    response = api.c.post("/api/files/batch/rename", json={"ids": [created["id"]], "mode": "replace", "replace": "x"})
    assert response.status_code == 400, response.text
    assert _detail(api, created["id"])["title"] == "原题"


def test_a_plain_user_is_refused_batch_rename(api):
    _admin(api)
    created = _publish(api, "不许改名")
    _plain_user(api)
    assert api.c.post("/api/files/batch/rename",
                      json={"ids": [created["id"]], "mode": "prefix", "prefix": "x"}
                      ).status_code == 403
    assert _detail(api, created["id"])["title"] == "不许改名"


# ================================================================ 批量编辑 / batch edit


def test_batch_edit_changes_only_the_supplied_fields(api):
    _admin(api)
    created = _publish(api, "批量编辑对象", visibility="members")
    response = api.c.post("/api/files/batch/edit",
                          json={"ids": [created["id"]], "visibility": "public", "status": "archived"})
    assert response.status_code == 200, response.text
    detail = _detail(api, created["id"])
    assert detail["visibility"] == "public"
    assert detail["status"] == "archived"
    # 未提供的字段一律不动：分类保持原值。
    # A field not supplied is left untouched, so the category keeps its original value.
    assert detail["category"]["slug"] == "study-syllabus"


def test_batch_edit_rejects_an_unknown_visibility(api):
    _admin(api)
    created = _publish(api, "非法可见性")
    response = api.c.post("/api/files/batch/edit", json={"ids": [created["id"]], "visibility": "everyone"})
    assert response.status_code == 400, response.text
    assert _detail(api, created["id"])["visibility"] == "members"


def test_a_plain_user_is_refused_batch_edit(api):
    _admin(api)
    created = _publish(api, "不许批量编辑")
    _plain_user(api)
    assert api.c.post("/api/files/batch/edit", json={"ids": [created["id"]], "visibility": "public"}
                      ).status_code == 403


# ================================================================ 批量复制 / batch copy


def test_an_admin_can_batch_copy_and_the_copy_is_an_independent_row(api):
    _admin(api)
    folder = _make_folder(api, "副本目标")
    created = _publish(api, "原始资料")
    response = api.c.post("/api/files/batch/copy", json={"ids": [created["id"]], "folderId": folder["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["succeeded"] == 1, response.json()
    listing = api.c.get(f"/api/files/resources?folderId={folder['id']}").json()
    assert listing["total"] == 1
    copy_row = listing["items"][0]
    assert copy_row["id"] != created["id"]
    assert copy_row["title"] == "原始资料（副本）"
    # 副本的计数从零开始，不继承原件的下载/收藏/评分。
    # The copy starts its counters at zero rather than inheriting the original's.
    assert copy_row["downloadCount"] == 0 and copy_row["favoriteCount"] == 0 and copy_row["ratingCount"] == 0
    assert any(action == "file.resource.copy" for action, _reason in _actions(api))


def test_a_plain_user_is_refused_batch_copy(api):
    _admin(api)
    created = _publish(api, "不许复制")
    _plain_user(api)
    assert api.c.post("/api/files/batch/copy", json={"ids": [created["id"]]}).status_code == 403


# ================================================================ 选择集与逐条结果 / selection and results


def test_a_repeated_id_is_executed_once(api):
    """重复 id 只做一次：否则批量删除会给同一行留两条"删了它"的假审计。"""
    _admin(api)
    created = _publish(api, "重复选中")
    response = api.c.post("/api/files/batch/delete", json={"ids": [created["id"], created["id"]]})
    assert response.status_code == 200, response.text
    assert response.json()["requested"] == 1
    matching = [reason for action, reason in _actions(api)
                if action == "file.resource.delete" and str(created["id"]) and "batch" in reason]
    assert len(matching) == 1


def test_a_missing_id_becomes_a_per_row_failure_not_a_batch_failure(api):
    """部分失败要逐条说清：成功的那条照做，失败的那条给 id 与原因。"""
    _admin(api)
    created = _publish(api, "存在的那条")
    response = api.c.post("/api/files/batch/delete", json={"ids": [created["id"], 999999]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested"] == 2 and body["succeeded"] == 1 and body["failed"] == 1
    failed = [item for item in body["results"] if not item["ok"]]
    assert failed == [{"id": 999999, "ok": False, "error": "Resource not found"}]


def test_an_empty_selection_is_a_validation_error(api):
    _admin(api)
    assert api.c.post("/api/files/batch/delete", json={"ids": []}).status_code == 422
    assert api.c.post("/api/files/batch/download", json={"ids": []}).status_code == 422


def test_anonymous_callers_cannot_use_any_batch_endpoint(api):
    _admin(api)
    created = _publish(api, "匿名目标", visibility="public")
    api.c.cookies.clear()
    for path in ("move", "copy", "delete", "rename", "edit"):
        response = api.c.post(f"/api/files/batch/{path}", json={"ids": [created["id"]], "mode": "prefix", "prefix": "x"})
        assert response.status_code in (401, 403), (path, response.status_code, response.text)
    # 匿名整轮没改动任何数据。
    # The anonymous round changed no data at all.
    assert _detail(api, created["id"])["title"] == "匿名目标"


def test_the_batch_selection_cap_matches_the_listing_page_size(api):
    """上限与单页上限对齐：界面能勾到的数量，后端必须收得下。"""
    _admin(api)
    assert api.c.post("/api/files/batch/delete", json={"ids": list(range(1, 52))}).status_code == 422
    created = _publish(api, "上限内的那一条")
    assert api.c.post("/api/files/batch/delete", json={"ids": [created["id"]] + list(range(5000, 5049))}
                      ).status_code == 200


def test_batch_rows_still_feed_the_audit_table(api):
    """每一条批量写都必须留痕：批量动作一次动 N 条，没留痕就再也查不出是谁动的。"""
    _admin(api)
    created = _publish(api, "审计对象")
    api.c.post("/api/files/batch/rename", json={"ids": [created["id"]], "mode": "suffix", "suffix": "-v2"})
    api.c.post("/api/files/batch/delete", json={"ids": [created["id"]]})
    with api.app.state.db.request_conn() as conn:
        rows = conn.execute(select(moderation_actions.c.action, moderation_actions.c.target_id,
                                   moderation_actions.c.created_at)
                            .where(moderation_actions.c.target_id == created["id"])).all()
    actions = {row[0] for row in rows}
    assert {"file.update_metadata", "file.resource.delete"} <= actions
    assert all(row[2] <= now_ms() for row in rows)
