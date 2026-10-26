"""根视图语义：上传即归档 + 根视图只列"不属于任何文件夹"的资料。

Root-view semantics: filing at upload time, and a root view that lists only unfiled resources.

背景 / Background
----------------
管理员报障："把文件移入文件夹以后也没有显示（文件还在外面显示），应该是移入文件夹，只在文件夹
里面显示的。" 前端默认视图当时是"全部"——不带任何文件夹过滤，于是已归档与未归档的资料混在一起。
本次把默认视图改成根目录（请求参数 unfiled=true），并让上传可以直接指定目标文件夹。

The administrator reported that a file moved into a folder stayed visible outside it, and that a
filed resource should appear only inside its folder. The frontend's default view used to be
"everything", which sent no folder filter and therefore mixed filed and unfiled resources together.
The default is now the root view (the request carries unfiled=true), and an upload may name its
destination folder directly.

本文件锁的是后端这一半的契约 / This file pins the backend half of that contract:
1. 上传时可以一次写定归属，且归属必须真实存在（否则整条请求失败，不留悬空 folder_id）。
   An upload may set its folder in one write, and that folder has to exist (otherwise the whole
   request fails and no dangling folder_id is left behind).
2. `unfiled=true` 是根视图的取数口径：它必须排除任何已归档资料，这正是"移入后仍在外面显示"
   这条症状的反面。
   `unfiled=true` is the root view's query: it has to exclude every filed resource, which is exactly
   the inverse of the reported symptom.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def _category_id(api, slug: str = "study-syllabus") -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _presign_and_post(api, title: str, *, folder_id: int | None, include_folder: bool = True):
    """上传一份资料；folder_id 为 None 且 include_folder 为真时显式发 null。"""
    body = b"hello world"
    presign = api.c.post(
        "/api/files/resources/presign",
        json={"filename": "notes.pdf", "mimeType": "application/pdf", "sizeBytes": len(body)},
    )
    assert presign.status_code == 200, presign.text
    ticket = presign.json()
    assert api.c.put(ticket["uploadUrl"], content=body).status_code == 204
    query = parse_qs(urlparse(ticket["uploadUrl"]).query)
    payload = {
        "objectKey": ticket["objectKey"],
        "expires": query["expires"][0],
        "sig": query["sig"][0],
        "sizeBytes": len(body),
        "categoryId": _category_id(api),
        "title": title,
        "visibility": "members",
        "originalFilename": "notes.pdf",
        "mimeType": "application/pdf",
    }
    if include_folder:
        payload["folderId"] = folder_id
    return api.c.post("/api/files/resources", json=payload)


def _make_folder(api, name: str, parent_id: int | None = None) -> dict:
    payload = {"name": name} if parent_id is None else {"name": name, "parentId": parent_id}
    response = api.c.post("/api/files/folders", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ================================================================ 上传即归档 / filing at upload


def test_upload_can_target_a_folder_in_one_write(api):
    """上传时直接指定文件夹：资料一次落位，且只出现在该文件夹里。

    这是"在文件夹里点上传 = 归入该文件夹"的后端契约；先传根目录再移动会留下一个
    "短暂出现在根视图"的中间态，也就重新制造了管理员报告的那种错觉。
    Uploading straight into a folder places the resource in one write and it shows up only there.
    This is the backend contract behind "uploading while inside a folder files it there": uploading
    to the root and moving afterwards would leave a transient root-view appearance, recreating the
    very illusion the administrator reported.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "物理")

    created = _presign_and_post(api, "归档的资料", folder_id=folder["id"])
    assert created.status_code == 201, created.text
    assert created.json()["folderId"] == folder["id"]

    # 根视图（unfiled=true）必须看不到它，文件夹视图必须只有它。
    # The root view must not show it and the folder view must hold exactly it.
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 0
    inside = api.c.get(f"/api/files/resources?folderId={folder['id']}").json()
    assert [item["id"] for item in inside["items"]] == [created.json()["id"]]


def test_upload_without_a_folder_id_still_lands_at_the_root(api):
    """不传 folderId（或显式传 null）依旧落在根目录：新字段不能改变既有调用方的行为。

    An omitted folderId (or an explicit null) still lands at the root: the new field must not change
    what existing callers get.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    _make_folder(api, "物理")

    omitted = _presign_and_post(api, "省略字段", folder_id=None, include_folder=False)
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["folderId"] is None

    explicit_null = _presign_and_post(api, "显式 null", folder_id=None)
    assert explicit_null.status_code == 201, explicit_null.text
    assert explicit_null.json()["folderId"] is None

    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 2


def test_upload_into_a_missing_folder_fails_and_writes_nothing(api):
    """目标文件夹不存在时整条请求失败（404），库里的资料数不变——绝不留下悬空的 folder_id。

    A missing destination folder fails the whole request (404) and the resource count does not move:
    a dangling folder_id must never be written. The insert therefore happens only after the folder
    has been resolved.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    before = api.c.get("/api/files/resources?unfiled=true").json()["total"]

    response = _presign_and_post(api, "指到不存在的文件夹", folder_id=999999)
    assert response.status_code == 404, response.text

    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == before
    assert api.c.get("/api/files/resources").json()["total"] == before


def test_upload_folder_id_must_be_a_positive_id(api):
    """folderId 的形状由模型约束（ge=1）：0 与负数在请求校验阶段就被拒，不会走到服务层。

    The shape of folderId is a model constraint (ge=1): 0 and negative values are rejected during
    request validation and never reach the service layer.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    for bad in (0, -3):
        response = _presign_and_post(api, f"非法 folderId {bad}", folder_id=bad)
        assert response.status_code == 422, f"{bad}: {response.text}"


# ================================================================ 根视图取数 / the root query


def test_the_root_view_excludes_a_resource_after_it_is_filed(api):
    """本任务的核心症状锁：一份资料移到文件夹之后，根视图必须看不到它。

    这是"移入文件夹后仍在外面显示"的反面断言。它同时锁住后端两个口径必须一致：
    列表（unfiled=true）与文件夹视图互斥，一份资料只能出现在一边。
    The regression lock for this task's core symptom: once a resource is moved into a folder the
    root view must not show it. It is the inverse of "still visible outside after moving", and it
    also pins the two queries as mutually exclusive: a resource shows up on exactly one side.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "物理")
    created = _presign_and_post(api, "待归档", folder_id=None)
    assert created.status_code == 201, created.text
    resource_id = created.json()["id"]

    # 归档前：根视图有它，文件夹视图没有。
    # Before filing: the root view has it, the folder view does not.
    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 1
    assert api.c.get(f"/api/files/resources?folderId={folder['id']}").json()["total"] == 0

    moved = api.c.put(f"/api/files/resources/{resource_id}/folder", json={"folderId": folder["id"]})
    assert moved.status_code == 200, moved.text

    # 归档后：根视图必须干净，文件夹视图必须恰好有它。
    # After filing: the root view must be clean and the folder view must hold exactly it.
    root = api.c.get("/api/files/resources?unfiled=true").json()
    assert root["total"] == 0, root
    assert [item["id"] for item in root["items"]] == []
    inside = api.c.get(f"/api/files/resources?folderId={folder['id']}").json()
    assert [item["id"] for item in inside["items"]] == [resource_id]


def test_the_cross_folder_view_still_lists_both_sides(api):
    """"全部资料（含文件夹内）"是显式选项：不传任何文件夹参数时两份资料都在。

    这是"保留跨文件夹视图"的契约面——它必须与根视图区分开，否则移除默认过滤后用户会以为
    资料消失了。
    "All resources (including inside folders)" is an explicit option: with no folder parameter both
    resources are listed. This is the contract of keeping the cross-folder view: it has to stay
    distinguishable from the root view, or dropping the default filter would look like data loss.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    folder = _make_folder(api, "物理")
    filed = _presign_and_post(api, "已归档", folder_id=folder["id"])
    unfiled = _presign_and_post(api, "未归档", folder_id=None)
    assert filed.status_code == 201 and unfiled.status_code == 201

    everything = api.c.get("/api/files/resources").json()
    assert everything["total"] == 2
    assert {item["id"] for item in everything["items"]} == {filed.json()["id"], unfiled.json()["id"]}

    assert api.c.get("/api/files/resources?unfiled=true").json()["total"] == 1
