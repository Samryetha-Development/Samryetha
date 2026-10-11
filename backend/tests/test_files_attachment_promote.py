"""附件转入文件服务（管理员专属）测试。

覆盖 / Coverage:
1. 管理员转换成功：新资料可下载、字节与源附件逐字节一致、附件原下载链路不受影响。
   An admin promotes successfully: the resource downloads, its bytes match the source
   attachment byte for byte, and the attachment's own download path still works.
2. 鉴权：未登录 401 / 普通用户 403 / 不存在附件 404，且 403 不泄漏附件是否存在。
   Authorisation: 401 when signed out, 403 for a normal user, 404 for a missing attachment,
   and the 403 reveals nothing about whether the attachment exists.
3. 幂等：同一附件重复转换 409（软删资料后仍 409）。
   Idempotency: promoting the same attachment twice is a 409 (still a 409 after the resource
   is soft-deleted).
4. 可见性与可转换前提：不放大受众、非法取值 400、未挂帖附件不可转换。
   Visibility and eligibility: the audience is never widened, an unknown tier is a 400, and an
   unattached attachment cannot be promoted.
5. 字节不被回收/删除（与两个回收器 + 附件删除护栏的交互）。
   The bytes survive both reapers and the attachment-deletion guard.
"""

from __future__ import annotations

import os
import uuid

from samryetha.adapters.storage import OBJECT_KEY_RE
from samryetha.files import FileService

PROMOTE = "/api/files/resources/from-attachment"
BODY = b"promote-me-bytes"


# ---------------------------------------------------------------- helpers


def _category_id(api, slug: str = "study-syllabus") -> int:
    config = api.c.get("/api/files/config")
    assert config.status_code == 200, config.text
    for item in config.json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _board(api, slug: str, visibility: str = "public") -> None:
    created = api.c.post(
        "/api/boards",
        json={"name": slug.title(), "slug": slug, "visibility": visibility},
    )
    assert created.status_code == 201, created.text


def _upload_attachment(api, filename: str = "notes.txt", body: bytes = BODY) -> dict:
    """presign + 签名 PUT，返回 presign 结果（含 attachmentId / objectKey / uploadUrl）。"""
    presign = api.c.post(
        "/api/attachments/presign",
        json={"filename": filename, "mimeType": "text/plain", "sizeBytes": len(body)},
    )
    assert presign.status_code == 200, presign.text
    payload = presign.json()
    uploaded = api.c.put(payload["uploadUrl"], content=body)
    assert uploaded.status_code == 204, uploaded.text
    return payload


def _attach(api, board_slug: str, uploaded: dict, title: str = "Source thread") -> dict:
    """把已上传的附件挂到一条新讨论帖上，返回讨论帖详情。"""
    discussion = api.c.post(
        "/api/discussions",
        json={
            "boardSlug": board_slug,
            "title": title,
            "bodyMarkdown": "body",
            "attachmentIds": [uploaded["attachmentId"]],
        },
    )
    assert discussion.status_code == 201, discussion.text
    return discussion.json()


def _attached_source(api, *, slug: str = "promote-src", visibility: str = "public",
                     filename: str = "notes.txt", body: bytes = BODY) -> dict:
    """建板块 + 上传并挂帖，返回 {attachmentId, objectKey, discussionId}。"""
    _board(api, slug, visibility)
    uploaded = _upload_attachment(api, filename=filename, body=body)
    discussion = _attach(api, slug, uploaded)
    return {
        "attachmentId": uploaded["attachmentId"],
        "objectKey": uploaded["objectKey"],
        "discussionId": discussion["id"],
        "body": body,
        "filename": filename,
    }


def _promote(api, attachment_id: int, **overrides) -> object:
    payload = {"attachmentId": attachment_id, "categoryId": _category_id(api), "title": "转入的资料"}
    payload.update(overrides)
    return api.c.post(PROMOTE, json=payload)


def _storage_path(api, object_key: str) -> str:
    return api.app.state.storage.path_for(object_key)


# ---------------------------------------------------------------- 成功路径


def test_admin_promotes_attachment_and_bytes_match(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, filename="线性代数提纲.txt")

    created = _promote(api, source["attachmentId"], title=None)
    assert created.status_code == 201, created.text
    resource = created.json()

    # 标题缺省由附件文件名推导（去扩展名）。
    # The title defaults to the attachment filename without its extension.
    assert resource["title"] == "线性代数提纲"
    assert resource["originalFilename"] == "线性代数提纲.txt"
    assert resource["sizeBytes"] == len(source["body"])
    assert resource["visibility"] == "members"
    assert resource["status"] == "published"
    assert resource["uploader"]["username"] == "root"

    # 下载链路可用，且字节与源附件逐字节一致。
    # The download path works and the bytes match the source attachment byte for byte.
    ticket = api.c.get(f"/api/files/resources/{resource['id']}/download", params={"preview": True})
    assert ticket.status_code == 200, ticket.text
    served = api.c.get(ticket.json()["downloadUrl"])
    assert served.status_code == 200, served.text
    assert served.content == source["body"]

    # 零拷贝：资料与附件指向同一个 object_key，磁盘上只有一份字节。
    # Zero copy: the resource and the attachment share one object key, so there is one file.
    assert ticket.json()["objectKey"] == source["objectKey"]

    # 附件自身的下载链路不受影响（转换不能弄坏它）。
    # The attachment's own download path is unaffected (promotion must not break it).
    attachment = api.c.get(f"/api/attachments/{source['attachmentId']}")
    assert attachment.status_code == 200, attachment.text
    assert api.c.get(attachment.json()["downloadUrl"]).content == source["body"]

    # 新资料出现在列表里（前端"刷新进列表"依赖这一点）。
    # The new resource shows up in the list (the front end's refresh-into-list relies on it).
    listed = api.c.get("/api/files/resources").json()
    assert resource["id"] in [item["id"] for item in listed["items"]]


def test_admin_promote_keeps_description_tags_and_explicit_visibility(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-rich")

    created = _promote(
        api,
        source["attachmentId"],
        title="精选提纲",
        descriptionMarkdown="来源：论坛讨论帖",
        tags=["线代", "Math"],
        visibility="public",
    )
    assert created.status_code == 201, created.text
    resource = created.json()
    assert resource["title"] == "精选提纲"
    assert resource["descriptionMarkdown"] == "来源：论坛讨论帖"
    assert resource["tags"] == ["线代", "math"]
    assert resource["visibility"] == "public"


# ---------------------------------------------------------------- 鉴权


def test_signed_out_promote_is_unauthorized(api):
    api.mkuser("root", role="admin")
    api.login("root")
    _board(api, "promote-anon")
    uploaded = _upload_attachment(api)
    source = {"attachmentId": uploaded["attachmentId"]}
    api.c.post("/api/auth/logout")

    assert api.c.post(PROMOTE, json={"attachmentId": source["attachmentId"], "categoryId": 1}).status_code == 401


def test_normal_user_promote_is_forbidden_without_leaking_existence(api):
    api.mkuser("root", role="admin")
    api.mkuser("alice")
    api.login("root")
    source = _attached_source(api, slug="promote-forbidden")

    api.login("alice")
    # 已存在的附件与不存在的附件必须回同一个 403：否则状态码差异本身就是存在性泄漏。
    # requestId 每次请求都不同，因此只比对 code 与 message。
    # An existing attachment and a missing one must both be a plain 403, otherwise the status
    # difference alone leaks existence. requestId is per-request, so compare code and message.
    existing = api.c.post(PROMOTE, json={"attachmentId": source["attachmentId"], "categoryId": _category_id(api)})
    missing = api.c.post(PROMOTE, json={"attachmentId": 999999, "categoryId": _category_id(api)})
    assert existing.status_code == 403, existing.text
    assert missing.status_code == 403, missing.text
    assert existing.json()["error"]["code"] == missing.json()["error"]["code"]
    assert existing.json()["error"]["message"] == missing.json()["error"]["message"]

    # 反过来证明这不是"所有调用都 403"的假绿：管理员的同一个载荷能成功。
    # The converse proves this is not a false green where every call is a 403: the same payload
    # succeeds for an admin.
    api.login("root")
    assert api.c.post(
        PROMOTE, json={"attachmentId": source["attachmentId"], "categoryId": _category_id(api)}
    ).status_code == 201


def test_missing_attachment_is_not_found(api):
    api.mkuser("root", role="admin")
    api.login("root")
    assert _promote(api, 999999).status_code == 404


def test_unattached_attachment_cannot_be_promoted(api):
    api.mkuser("root", role="admin")
    api.login("root")
    uploaded = _upload_attachment(api)  # 已上传但未挂帖：仍是上传草稿
    assert _promote(api, uploaded["attachmentId"]).status_code == 404


def test_missing_category_is_not_found(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-badcat")
    assert _promote(api, source["attachmentId"], categoryId=999999).status_code == 404


# ---------------------------------------------------------------- 幂等


def test_repeat_promotion_is_rejected(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-idempotent")

    first = _promote(api, source["attachmentId"])
    assert first.status_code == 201, first.text
    second = _promote(api, source["attachmentId"])
    assert second.status_code == 409, second.text
    # 只有一条资料，且字节只有一份。
    # Exactly one resource exists and exactly one copy of the bytes.
    assert api.c.get("/api/files/resources").json()["total"] == 1


def test_repeat_promotion_after_soft_delete_is_still_rejected(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-softdel")
    resource = _promote(api, source["attachmentId"]).json()

    assert api.c.delete(f"/api/files/resources/{resource['id']}").status_code == 200
    # 资料是软删除，行仍在、唯一键仍被占用，因此仍是确定的 409（见设计说明 §5）。
    # The resource is soft-deleted, so the row and its unique key remain, hence the same
    # deterministic 409 (see design note section 5).
    assert _promote(api, source["attachmentId"]).status_code == 409


# ---------------------------------------------------------------- 可见性口径


def test_promotion_never_widens_the_audience(api):
    api.mkuser("root", role="admin")
    api.login("root")

    # 公开板块：缺省 members；显式 public 允许。
    # A public board: the default is members, and an explicit public is allowed.
    public_default = _attached_source(api, slug="widen-pub-a", filename="pub-a.txt")
    assert _promote(api, public_default["attachmentId"]).json()["visibility"] == "members"
    public_explicit = _attached_source(api, slug="widen-pub-b", filename="pub-b.txt")
    assert _promote(api, public_explicit["attachmentId"], visibility="public").json()["visibility"] == "public"

    # 非公开板块：缺省被夹紧到 private；显式 members/public 一律 400（不静默改写）。
    # 每个断言用一条独立附件：同一附件第二次转换必然是 409，会掩盖这里要验证的口径。
    # A non-public board: the default is clamped to private, while an explicit members/public
    # is a plain 400 rather than a silent rewrite. Each assertion uses its own attachment, since
    # a second promotion of the same attachment is always a 409 and would mask the rule at hand.
    private_default = _attached_source(api, slug="widen-prv-a", visibility="private", filename="prv-a.txt")
    assert _promote(api, private_default["attachmentId"]).json()["visibility"] == "private"

    private_members = _attached_source(api, slug="widen-prv-b", visibility="private", filename="prv-b.txt")
    assert _promote(api, private_members["attachmentId"], visibility="members").status_code == 400

    private_public = _attached_source(api, slug="widen-prv-c", visibility="private", filename="prv-c.txt")
    assert _promote(api, private_public["attachmentId"], visibility="public").status_code == 400

    # private 与上限相等，允许。
    # private equals the ceiling, so it is allowed.
    private_allowed = _attached_source(api, slug="widen-prv-d", visibility="private", filename="prv-d.txt")
    allowed = _promote(api, private_allowed["attachmentId"], visibility="private")
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["visibility"] == "private"


def test_unknown_visibility_is_rejected(api):
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-badvis")
    assert _promote(api, source["attachmentId"], visibility="secret").status_code == 400


# ---------------------------------------------------------------- 字节不被回收/删除


def test_attachment_reaper_keeps_promoted_object(api):
    """撤回源讨论帖会让附件变 orphaned——回收器绝不能因此删掉已认领的字节。"""
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-reaper")
    resource = _promote(api, source["attachmentId"]).json()

    # 删帖：discussions 仓储会把该帖附件置为 orphaned（回收候选）。
    # Deleting the thread marks its attachments orphaned, i.e. reaper candidates.
    assert api.c.delete(f"/api/discussions/{source['discussionId']}").status_code == 200
    assert api.app.state.reap_attachment_orphans(older_than_ms=-1) == 0

    path = _storage_path(api, source["objectKey"])
    assert os.path.isfile(path), "promoted bytes were reclaimed by the attachment reaper"
    ticket = api.c.get(f"/api/files/resources/{resource['id']}/download", params={"preview": True})
    assert api.c.get(ticket.json()["downloadUrl"]).content == source["body"]


def test_attachment_reaper_still_collects_unclaimed_orphans(api):
    """对照组：未认领的孤儿附件仍被回收——证明上一条不是"回收器整体失效"的假绿。"""
    api.mkuser("root", role="admin")
    api.login("root")
    _board(api, "promote-control")
    uploaded = _upload_attachment(api, filename="control.txt")
    api.c.post(
        "/api/discussions",
        json={
            "boardSlug": "promote-control",
            "title": "Control",
            "bodyMarkdown": "body",
            "attachmentIds": [uploaded["attachmentId"]],
        },
    )
    discussion = api.c.get("/api/discussions").json()["items"][0]
    assert api.c.delete(f"/api/discussions/{discussion['id']}").status_code == 200

    path = _storage_path(api, uploaded["objectKey"])
    assert os.path.isfile(path)
    assert api.app.state.reap_attachment_orphans(older_than_ms=-1) == 1
    assert not os.path.exists(path)


def test_file_reaper_keeps_promoted_object(api):
    """文件服务自己的孤儿对象回收器同样不得回收被认领的字节。

    这里把"为什么没被回收"钉死：`reap_orphan_objects` 的三重条件是「引用差集 + 24 小时窗口 +
    严格命名规范」，两种原因都会导致跳过，但结论完全不同。源附件的 object_key 也满足
    `OBJECT_KEY_RE`（下面显式断言），因此它被跳过只可能是因为它出现在 `claimed_keys()`
    的引用集合里——即"因被 file_resources 引用而跳过"，而不是"因命名不匹配而侥幸逃过"。
    This test pins down *why* nothing was collected. The reaper has three conditions (reference
    set + 24h window + strict naming), and either of two of them would cause a skip with very
    different meanings. The source attachment's object key also satisfies OBJECT_KEY_RE
    (asserted below), so the only remaining reason is that it appears in claimed_keys(): it was
    skipped because a file_resources row references it, not because its name fell outside the
    naming convention.
    """
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-filereaper")
    resource = _promote(api, source["attachmentId"]).json()

    # 命名规范确证：附件 objectKey 完全符合 files 侧回收器的严格命名要求。
    # Naming proof: the attachment object key fully satisfies the reaper's strict naming rule.
    assert OBJECT_KEY_RE.match(source["objectKey"]), "attachment key must match the storage naming contract"

    assert api.app.state.reap_file_orphans(older_than_ms=-1) == 0
    assert os.path.isfile(_storage_path(api, source["objectKey"]))
    ticket = api.c.get(f"/api/files/resources/{resource['id']}/download", params={"preview": True})
    assert api.c.get(ticket.json()["downloadUrl"]).content == source["body"]


def test_file_reaper_still_collects_unclaimed_objects(api):
    """对照组：未被任何表引用的对象仍被回收（防假绿）。"""
    api.mkuser("root", role="admin")
    api.login("root")
    storage = api.app.state.storage
    orphan_key = f"{uuid.uuid4()}/orphan.txt"
    assert OBJECT_KEY_RE.match(orphan_key), "fixture key must satisfy the storage naming contract"
    full = storage.path_for(orphan_key)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(b"orphan")

    assert api.app.state.reap_file_orphans(older_than_ms=-1) == 1
    assert not os.path.exists(full)


def test_promoted_attachment_cannot_be_deleted(api):
    """硬删附件会让资料字节消失——必须被拦下，而未被认领的附件仍可正常删除。"""
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-nodelete")
    resource = _promote(api, source["attachmentId"]).json()

    blocked = api.c.delete(f"/api/attachments/{source['attachmentId']}")
    assert blocked.status_code == 409, blocked.text
    assert os.path.isfile(_storage_path(api, source["objectKey"]))
    ticket = api.c.get(f"/api/files/resources/{resource['id']}/download", params={"preview": True})
    assert api.c.get(ticket.json()["downloadUrl"]).content == source["body"]

    # 对照组：未被资料认领的附件的删除路径没有被这次加严误伤。
    # Control: deleting an attachment that no resource claimed is unaffected by the new guard.
    _board(api, "promote-delcontrol")
    plain = _upload_attachment(api, filename="plain.txt")
    plain_discussion = _attach(api, "promote-delcontrol", plain)
    assert plain_discussion["attachments"][0]["id"] == plain["attachmentId"]
    assert api.c.delete(f"/api/attachments/{plain['attachmentId']}").status_code == 200


def test_promoted_object_key_is_claimed_by_both_tables(api):
    """认领口径确证：object_key 同时出现在 attachments 与 file_resources 中。"""
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="promote-claim")
    _promote(api, source["attachmentId"])

    with api.app.state.db.request_conn() as conn:
        claimed = FileService(conn).is_object_claimed(source["objectKey"])
        keys = FileService(conn)._repository.claimed_keys()  # noqa: SLF001 — 断言回收器的引用口径
    assert claimed is True
    assert source["objectKey"] in keys


# ---------------------------------------------------------------- 转入后 owner = 原上传者


def _promote_board(api, slug: str, visibility: str = "public") -> None:
    """建一个允许任何人发帖的板块。

    下面几个用例要让非管理员自己上传并挂帖，而板块缺省 posting_policy 是 members
    （非版成员发不了帖），所以显式开成 everyone。
    Creates a board anyone may post to. The cases below need a non-admin to upload and open
    their own thread, and the default posting policy is members (non-members cannot post), so
    the policy is opened explicitly.
    """
    created = api.c.post(
        "/api/boards",
        json={"name": slug.title(), "slug": slug, "visibility": visibility, "postingPolicy": "everyone"},
    )
    assert created.status_code == 201, created.text


def _mine_ids(api) -> list[int]:
    return [item["id"] for item in api.c.get("/api/files/mine").json()["items"]]


def test_promote_keeps_the_original_uploader_as_owner(api):
    """管理员转入「别人」的附件：资料 owner 必须是原上传者，而不是执行转入的管理员。

    这是本用例的核心：附件不按上传者过滤，所以非管理员也会成为资料的提交者，
    他必须在「我的上传」里看得到，并持有 file.update / file.delete。
    The admin promotes someone else's attachment: the resource must be owned by the original
    uploader, not by the admin who ran the promotion. This is the point of the case:
    attachments are not filtered by uploader, so a non-admin does become a submitter, and must
    see the resource in "my uploads" with file.update and file.delete.
    """
    api.mkuser("root", role="admin")
    api.mkuser("alice")
    api.login("root")
    _promote_board(api, "owner-keep")

    # alice（非管理员）自己上传并挂帖。
    # alice, a normal user, uploads and opens the thread herself.
    api.login("alice")
    uploaded = _upload_attachment(api, filename="alice-notes.txt", body=b"alice-bytes")
    _attach(api, "owner-keep", uploaded, title="Alice thread")

    # root 执行转入，但 owner 应当仍是 alice。
    # root runs the promotion, but the owner must still be alice.
    api.login("root")
    created = _promote(api, uploaded["attachmentId"], title="Al 的提纲")
    assert created.status_code == 201, created.text
    resource = created.json()
    assert resource["uploader"]["username"] == "alice", "promotion must keep the original uploader as owner"

    # alice 看得到自己的那份资料，且 can 标记与真实写入路径都允许她改。
    # alice sees her resource, and both the can flags and the real write path allow her to edit.
    api.login("alice")
    assert resource["id"] in _mine_ids(api)
    detail = api.c.get(f"/api/files/resources/{resource['id']}").json()
    assert detail["can"]["update"] is True
    assert detail["can"]["delete"] is True
    patched = api.c.patch(f"/api/files/resources/{resource['id']}", json={"title": "alice 改过的标题"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "alice 改过的标题"

    # 反向确证：owner 不是 root，所以这份资料不进 root 的「我的上传」。
    # The converse: the owner is not root, so the resource is absent from root's "my uploads".
    api.login("root")
    assert resource["id"] not in _mine_ids(api)


def test_promoted_uploader_can_delete_own_resource(api):
    """原上传者对被转入的资料同样持有删除权（与编辑权分开验证，删除会改变可见状态）。"""
    api.mkuser("root", role="admin")
    api.mkuser("erin")
    api.login("root")
    _promote_board(api, "owner-delete")

    api.login("erin")
    uploaded = _upload_attachment(api, filename="erin-notes.txt", body=b"erin-bytes")
    _attach(api, "owner-delete", uploaded, title="Erin thread")

    api.login("root")
    resource = _promote(api, uploaded["attachmentId"], title="Erin 的提纲").json()

    api.login("erin")
    assert api.c.delete(f"/api/files/resources/{resource['id']}").status_code == 200
    assert resource["id"] not in _mine_ids(api)


def test_promote_falls_back_to_admin_when_uploader_is_banned(api):
    """边缘口径：原上传者已封禁时 owner 落回执行转入的管理员。

    封禁账号登录不了，把资料指派给他会让资料变成没人能管的孤儿；管理员本就对所有资料
    持有写权限，落回管理员是唯一不会产出孤儿资料的选项。
    Edge rule: when the original uploader is banned, ownership falls back to the acting admin.
    A banned account cannot sign in, so assigning the resource to it would strand it with
    nobody able to manage it; admins already hold write access to every resource, so falling
    back to the acting admin is the only option that never produces an unmanageable resource.
    """
    api.mkuser("root", role="admin")
    api.mkuser("bob")
    api.login("root")
    _promote_board(api, "owner-banned")

    api.login("bob")
    uploaded = _upload_attachment(api, filename="bob-notes.txt", body=b"bob-bytes")
    _attach(api, "owner-banned", uploaded, title="Bob thread")

    # 走真实的封禁端点（而不是只改状态位），与线上路径一致。
    # Use the real ban endpoint rather than flipping the status column, matching production.
    api.login("root")
    banned = api.c.post("/api/moderation/bans", json={"username": "bob", "reason": "promotion test"})
    assert banned.status_code == 200, banned.text

    created = _promote(api, uploaded["attachmentId"], title="Bob 的提纲")
    assert created.status_code == 201, created.text
    resource = created.json()
    assert resource["uploader"]["username"] == "root"
    assert resource["id"] in _mine_ids(api)


def test_promote_falls_back_to_admin_when_uploader_is_deleted(api):
    """边缘口径：原上传者已注销（软删）时同样落回执行转入的管理员。"""
    from sqlalchemy import update

    from samryetha.core.db import now_ms
    from samryetha.core.schema import users

    api.mkuser("root", role="admin")
    api.mkuser("carol")
    api.login("root")
    _promote_board(api, "owner-deleted")

    api.login("carol")
    uploaded = _upload_attachment(api, filename="carol-notes.txt", body=b"carol-bytes")
    _attach(api, "owner-deleted", uploaded, title="Carol thread")

    # 软删账号：deleted_at 置位，与真实注销路径一致（行仍在，附件的外键仍成立）。
    # Soft-delete the account: deleted_at is set as the real deletion path does (the row
    # remains, so the attachment's foreign key still resolves).
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "carol").values(deleted_at=now_ms()))

    api.login("root")
    created = _promote(api, uploaded["attachmentId"], title="Carol 的提纲")
    assert created.status_code == 201, created.text
    resource = created.json()
    assert resource["uploader"]["username"] == "root"
    assert resource["id"] in _mine_ids(api)


def test_promotion_is_audited_with_initiator_and_owner(api):
    """审计留痕：一次转入既记「谁发起」（actor）也记「owner 是谁」（reason），两者可不同。"""
    api.mkuser("root", role="admin")
    api.mkuser("dave")
    api.login("root")
    _promote_board(api, "audit-promote")

    api.login("dave")
    uploaded = _upload_attachment(api, filename="dave-notes.txt", body=b"dave-bytes")
    _attach(api, "audit-promote", uploaded, title="Dave thread")

    api.login("root")
    created = _promote(api, uploaded["attachmentId"], title="Dave 的提纲")
    assert created.status_code == 201, created.text
    resource = created.json()

    actions = api.c.get("/api/moderation/actions").json()["items"]
    entries = [item for item in actions if item["action"] == "file.promote_from_attachment"]
    assert len(entries) == 1, "promotion must write exactly one audit entry"
    entry = entries[0]
    # 发起人是管理员 root，owner 是原上传者 dave：两条信息都在，缺一不可。
    # The initiator is the admin root and the owner is the uploader dave; both facts are
    # recorded, and neither alone is enough.
    assert entry["actor"]["username"] == "root"
    assert entry["targetType"] == "file_resource"
    assert entry["targetId"] == resource["id"]
    dave_id = resource["uploader"]["id"]
    assert dave_id != entry["actor"]["id"], "fixture must use a non-admin uploader"
    assert f"owner={dave_id}" in entry["reason"]
    assert f"uploader={dave_id}" in entry["reason"]
    assert "uploaderEligible=1" in entry["reason"]


def test_failed_promotion_writes_no_audit_entry(api):
    """对照组：转入失败（重复转入 409）不得留下审计行，证明审计不是无条件写入。"""
    api.mkuser("root", role="admin")
    api.login("root")
    source = _attached_source(api, slug="audit-negative")

    assert _promote(api, source["attachmentId"]).status_code == 201
    assert _promote(api, source["attachmentId"]).status_code == 409

    actions = api.c.get("/api/moderation/actions").json()["items"]
    assert len([item for item in actions if item["action"] == "file.promote_from_attachment"]) == 1
