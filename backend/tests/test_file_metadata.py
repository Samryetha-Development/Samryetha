"""文件服务·元数据编辑（Stage 3）：标签与简介的可编辑性、权限、校验与审计。

File-service metadata editing (stage 3): editability of tags and description, authorisation,
validation and the audit trail.

前置事实（本阶段零迁移的依据）：`file_resources.description_md` 与 `tags` 两列、以及
`PATCH /api/files/resources/{id}`（`ResourcePatchBody` 支持 `title` / `descriptionMarkdown` /
`tags` / `categoryId` / `visibility` / `status`）在本次改动之前**就已存在**，因此 Stage 3 不动表、
不写迁移，只补审计、前端编辑入口与用例。
Prior fact, and the reason this stage needs no migration: the `description_md` and `tags` columns and
the resource PATCH endpoint already existed, so stage 3 touches no table and writes no migration; it
adds the audit trail, the front-end editing entry point and the cases below.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from samryetha.core.db import now_ms
from samryetha.core.schema import file_resources, moderation_actions, users


def _category_id(api, slug: str = "study-syllabus") -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _publish(api, title: str, *, visibility: str = "members", tags: list[str] | None = None,
             description: str = "") -> dict:
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
            "categoryId": _category_id(api),
            "title": title,
            "visibility": visibility,
            "tags": tags or [],
            "descriptionMarkdown": description,
            "originalFilename": "notes.pdf",
            "mimeType": "application/pdf",
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _seed_row(api, *, uploader_id: int, title: str, visibility: str = "private",
              tags: list[str] | None = None, description: str = "") -> int:
    """直接落一行资料：拖拽/上传只给管理员（FILE_CREATE），而附件转入会留下非管理员 owner，
    所以"非管理员 owner 能不能编辑自己的元数据"只能用这种方式构造。
    Inserts a resource row directly: uploading is admin-only (FILE_CREATE) while attachment promotion
    does leave non-admin owners, so "may a non-admin owner edit their own metadata" can only be
    constructed this way.
    """
    import json as _json

    with api.app.state.db.request_conn() as conn:
        result = conn.execute(file_resources.insert().values(
            category_id=_category_id(api), uploader_id=uploader_id, title=title,
            description_md=description, tags=_json.dumps(tags or []), object_key=f"seed/{title}-{now_ms()}",
            original_filename="seeded.pdf", mime_type="application/pdf", size_bytes=11,
            visibility=visibility, status="published", version=1, is_featured=0,
            download_count=0, favorite_count=0, rating_sum=0, rating_count=0,
            created_at=now_ms(), updated_at=now_ms()).returning(file_resources.c.id))
        return int(result.scalar_one())


def _user_id(api, username: str) -> int:
    with api.app.state.db.request_conn() as conn:
        return int(conn.execute(select(users.c.id).where(users.c.username == username)).scalar_one())


def _actions(api) -> list[tuple[str, str]]:
    with api.app.state.db.request_conn() as conn:
        return [(row[0], row[1]) for row in conn.execute(
            select(moderation_actions.c.action, moderation_actions.c.reason)).all()]


def _action_rows(api) -> list[tuple[str, int, str]]:
    """审计表的原始三元组（动作 / 目标 id / 原因），按写入顺序。

    用例要能数"到底写了几条"，所以这里不能像 _actions() 那样让 dict() 把同名动作折叠掉。
    The raw audit triples (action / target id / reason) in insertion order. A case that has to count
    "how many rows were written" cannot go through _actions(), whose dict() collapses same-named
    actions.
    """
    with api.app.state.db.request_conn() as conn:
        return [(row[0], row[1], row[2] or "") for row in conn.execute(
            select(moderation_actions.c.action, moderation_actions.c.target_id,
                   moderation_actions.c.reason).order_by(moderation_actions.c.id)).all()]


# ================================================================ 管理员 / admin


def test_admin_can_edit_tags_and_description_of_any_resource(api):
    """管理员必须能编辑任意资料的标签与简介——这是本阶段的硬要求。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "高等数学提纲")

    patched = api.c.patch(f"/api/files/resources/{created['id']}", json={
        "tags": ["微积分", "期末", "微积分"],
        "descriptionMarkdown": "覆盖极限、导数与应用题。",
    })
    assert patched.status_code == 200, patched.text
    payload = patched.json()
    # 标签归一化：去重（并保持首次出现的顺序）。
    # Tags are normalised: de-duplicated, keeping first-seen order.
    assert payload["tags"] == ["微积分", "期末"]
    assert payload["descriptionMarkdown"] == "覆盖极限、导数与应用题。"

    # 读回确认真的落库了（不只是响应体里好看）。
    # Read back to confirm it really persisted rather than merely looking right in the response.
    reread = api.c.get(f"/api/files/resources/{created['id']}").json()
    assert reread["tags"] == ["微积分", "期末"]
    assert reread["descriptionMarkdown"] == "覆盖极限、导数与应用题。"


def test_admin_can_edit_another_persons_resource(api):
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    bobs = _seed_row(api, uploader_id=_user_id(api, "bob"), title="bob 的资料", visibility="members")

    patched = api.c.patch(f"/api/files/resources/{bobs}", json={"tags": ["由管理员整理"]})
    assert patched.status_code == 200, patched.text
    assert patched.json()["tags"] == ["由管理员整理"]


# ================================================================ 普通用户 / plain users


def test_owner_can_edit_their_own_resource_metadata(api):
    """沿用既有 FILE_UPDATE 口径：管理员 或 该资料的 owner。非管理员 owner 是真实存在的
    （附件转入会把 owner 留给原上传者），所以这条必须成立。"""
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    bobs = _seed_row(api, uploader_id=_user_id(api, "bob"), title="bob 的资料", visibility="members")
    api.c.post("/api/auth/logout")

    api.login("bob")
    patched = api.c.patch(f"/api/files/resources/{bobs}", json={
        "tags": ["bob 自己的标签"], "descriptionMarkdown": "bob 自己写的简介。"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["tags"] == ["bob 自己的标签"]


def test_a_plain_user_cannot_edit_someone_elses_metadata(api):
    """越权红线：不可见/不属于自己的资料，元数据接口一样改不动。"""
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.mkuser("carol")
    api.login("alice")
    carols = _seed_row(api, uploader_id=_user_id(api, "carol"), title="carol 的私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.login("bob")
    # 授权先于任何变更：403，且资料一个字段都没动。
    # Authorisation runs before any change: 403, and not a single field moved.
    denied = api.c.patch(f"/api/files/resources/{carols}", json={"tags": ["劫持"], "descriptionMarkdown": "劫持"})
    assert denied.status_code == 403, denied.text
    api.c.post("/api/auth/logout")
    api.login("carol")
    untouched = api.c.get(f"/api/files/resources/{carols}").json()
    assert untouched["tags"] == []
    assert untouched["descriptionMarkdown"] == ""


def test_metadata_editing_requires_a_session_and_a_real_resource(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")
    api.c.post("/api/auth/logout")

    # 未登录写：401。
    # Signed out: 401.
    assert api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": ["x"]}).status_code == 401

    api.login("alice")
    # 不存在的资料：404（不泄漏存在性，也不会因为"改不到"而回 403）。
    # A missing resource answers 404, so existence is not leaked through a different code.
    assert api.c.patch("/api/files/resources/999999", json={"tags": ["x"]}).status_code == 404


# ================================================================ 空值语义 / empty semantics


def test_omitted_fields_are_untouched_and_empty_values_clear(api):
    """空值语义：**不传**字段 = 不改；**显式传空** = 清空。两者不可混淆，否则"只想改简介"
    的人会把自己的标签静默清掉。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", tags=["原有标签"], description="原有简介。")

    # 只传简介：标签必须原样保留。
    # Only the description is sent, so the tags must survive untouched.
    only_description = api.c.patch(f"/api/files/resources/{created['id']}", json={"descriptionMarkdown": "新简介。"})
    assert only_description.status_code == 200, only_description.text
    assert only_description.json()["tags"] == ["原有标签"]
    assert only_description.json()["descriptionMarkdown"] == "新简介。"

    # 显式传空标签与空简介 = 清空。
    # An explicit empty list and empty string clear both fields.
    cleared = api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": [], "descriptionMarkdown": ""})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tags"] == []
    assert cleared.json()["descriptionMarkdown"] == ""


# ================================================================ 校验与边界 / validation


def test_tag_normalisation_bounds(api):
    """标签的数量/长度/去重/字符集都有明确口径，全部落在 normalize_tags 里。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")

    # 逗号/中文逗号/顿号/分号都算分隔符；大小写归一为小写；去重保序。
    # Commas, full-width commas, enumeration commas and semicolons all separate; case is lowered;
    # duplicates are dropped keeping first-seen order.
    patched = api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": "Math, MATH、math；物理"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["tags"] == ["math", "物理"]

    # 单个标签超长按 24 字符截断；超过 8 个只保留前 8 个。
    # A tag longer than 24 characters is truncated and only the first 8 tags are kept.
    many = [f"标签{i}" for i in range(12)] + ["超长" * 20]
    capped = api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": many})
    assert capped.status_code == 200, capped.text
    kept = capped.json()["tags"]
    assert len(kept) == 8
    assert all(len(tag) <= 24 for tag in kept), "every stored tag must respect the 24 character cap"

    # 纯空白/空串标签被丢弃（不产生空标签）。
    # Blank entries are dropped rather than stored as empty tags.
    blanks = api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": ["  ", "", "有效"]})
    assert blanks.json()["tags"] == ["有效"]


def test_description_length_ceiling_is_rejected_not_truncated(api):
    """简介超上限回 422，而不是被静默截断——截断会让用户以为整段都存进去了。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")
    too_long = api.c.patch(f"/api/files/resources/{created['id']}", json={"descriptionMarkdown": "长" * 20_001})
    assert too_long.status_code == 422, too_long.text
    assert api.c.get(f"/api/files/resources/{created['id']}").json()["descriptionMarkdown"] == ""
    # 恰好到上限应当被接受（边界值不属于超限）。
    # Exactly at the ceiling is accepted: a boundary value is not an overrun.
    assert api.c.patch(f"/api/files/resources/{created['id']}", json={"descriptionMarkdown": "长" * 20_000}).status_code == 200


def test_last_write_wins_and_is_documented(api):
    """并发口径：后写覆盖，没有版本号校验（`version` 列本期恒为 1、不参与乐观锁）。
    这条用例把该口径钉住，免得日后有人以为存在冲突检测。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")
    api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": ["第一次"]})
    api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": ["第二次"]})
    assert api.c.get(f"/api/files/resources/{created['id']}").json()["tags"] == ["第二次"]


# ================================================================ 审计 / audit trail


def test_metadata_edits_leave_an_audit_trail_with_old_and_new_values(api):
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", tags=["旧标签"], description="旧简介。")

    api.c.patch(f"/api/files/resources/{created['id']}", json={
        "tags": ["新标签"], "descriptionMarkdown": "新简介。"})

    recorded = dict(_actions(api))
    assert "file.update_metadata" in recorded, "a metadata edit must be recorded"
    reason = recorded["file.update_metadata"]
    assert "tags=['旧标签']->['新标签']" in reason, "the audit row must carry both sides of the change"
    # 简介只记长度：上限两万字符，把新旧全文塞进 reason 会把审计行撑成一份内容副本。
    # The description is recorded as a length only: it runs to 20,000 characters, and pasting both
    # versions into reason would turn the audit row into a copy of the content.
    assert "description=" in reason and "chars->" in reason


def test_a_patch_that_changes_nothing_leaves_no_audit_row(api):
    """没有变化就不写审计：否则每一次"打开编辑器再保存"都会在审计表里留下噪声，
    真正需要追溯的改动会被淹没。"""
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料", tags=["同标签"], description="同简介。")

    # 传与当前完全相同的值。
    # The very same values the resource already carries.
    same = api.c.patch(f"/api/files/resources/{created['id']}", json={
        "tags": ["同标签"], "descriptionMarkdown": "同简介。"})
    assert same.status_code == 200, same.text
    assert "file.update_metadata" not in dict(_actions(api)), "an unchanged patch must not be recorded"

    # 真的改了才写。
    # A real change is recorded.
    api.c.patch(f"/api/files/resources/{created['id']}", json={"tags": ["不同的标签"]})
    assert "file.update_metadata" in dict(_actions(api))


def test_metadata_audit_records_who_acted(api):
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    bobs = _seed_row(api, uploader_id=_user_id(api, "bob"), title="bob 的资料", visibility="members")
    api.c.post("/api/auth/logout")

    api.login("bob")
    api.c.patch(f"/api/files/resources/{bobs}", json={"tags": ["bob 改的"]})

    with api.app.state.db.request_conn() as conn:
        rows = conn.execute(select(moderation_actions.c.actor_user_id, moderation_actions.c.target_id)
                            .where(moderation_actions.c.action == "file.update_metadata")).all()
    assert (rows[0][0], rows[0][1]) == (_user_id(api, "bob"), bobs)


def test_changing_only_the_status_or_only_the_visibility_is_recorded(api):
    """回归锁定：只改状态、或只改可见性，都必须写审计。

    修之前会怎样：`visibility` / `status` 被写进 `values`，却没有进 `changed` 列表，而审计行只在
    `if changed:` 时写入——于是"把资料下架"这种最需要追溯的操作一条痕都不留（事后查不出是谁做的）。
    这两个字段与标签、简介同属 PATCH 的能力范围、同一条权限，没有理由区别对待。
    Regression lock: a status-only or visibility-only edit must be recorded. Before the fix these two
    fields were written into `values` without ever joining `changed`, and the audit row is written only
    under `if changed:` - so archiving a resource, the very operation that most needs tracing, left no
    trace at all. Both fields sit under the same endpoint and the same permission as tags and
    description, so there is no reason to treat them differently.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")

    before = _action_rows(api)
    archived = api.c.patch(f"/api/files/resources/{created['id']}", json={"status": "archived"})
    assert archived.status_code == 200, archived.text
    after_status = _action_rows(api)
    assert len(after_status) == len(before) + 1, "changing only the status must be recorded"
    action, target_id, reason = after_status[-1]
    assert (action, target_id) == ("file.update_metadata", created["id"])
    assert reason == "status='published'->'archived'", reason

    privatised = api.c.patch(f"/api/files/resources/{created['id']}", json={"visibility": "private"})
    assert privatised.status_code == 200, privatised.text
    after_visibility = _action_rows(api)
    assert len(after_visibility) == len(after_status) + 1, "changing only the visibility must be recorded"
    action, target_id, reason = after_visibility[-1]
    assert (action, target_id) == ("file.update_metadata", created["id"])
    assert reason == "visibility='members'->'private'", reason


def test_resending_the_same_status_and_visibility_writes_nothing(api):
    """行为级负对照（针对上一条）：把**相同的**状态与可见性再发一次，不得写审计。

    没有这一半，"只要带了 status/visibility 就记一条"这种恒真实现也能让上一条通过。
    Behavioural negative control for the case above: re-sending the *same* status and visibility must
    write nothing. Without this half, an implementation that records whenever the fields are present
    (regardless of whether they moved) would satisfy the previous case too.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "资料")

    first = api.c.patch(f"/api/files/resources/{created['id']}", json={"status": "archived", "visibility": "private"})
    assert first.status_code == 200, first.text
    recorded = len(_action_rows(api))

    same = api.c.patch(f"/api/files/resources/{created['id']}", json={"status": "archived", "visibility": "private"})
    assert same.status_code == 200, same.text
    assert len(_action_rows(api)) == recorded, "an unchanged status/visibility must not be recorded"


def test_deleting_a_resource_is_recorded(api):
    """回归锁定：删除资料必须写审计。

    修之前会怎样：`delete_resource` 是本文件里唯一没有 `insert_action` 的写操作，删除之后
    `moderation_actions` 一行都不加。软删除会同时挡掉详情页与下载，资料从界面上彻底消失，
    此后"这份资料是谁在什么时候删的"再也无从回答。
    Regression lock: deleting a resource must be recorded. Before the fix `delete_resource` was the only
    write in this file with no `insert_action` call, so a deletion added no audit row. A soft delete
    takes out both the detail page and the download and removes the resource from the interface
    entirely, after which "who deleted this, and when" can never be answered again.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    created = _publish(api, "待删资料")

    deleted = api.c.delete(f"/api/files/resources/{created['id']}")
    assert deleted.status_code == 200, deleted.text

    rows = [row for row in _action_rows(api) if row[0] == "file.resource.delete"]
    assert len(rows) == 1, "deleting must leave exactly one audit row"
    _, target_id, reason = rows[0]
    assert target_id == created["id"], "the audit row must point at the deleted resource"
    assert "待删资料" in reason, "the audit row must identify which resource went"


def test_a_refused_delete_changes_nothing(api):
    """行为级负对照（针对上一条）：越权删除被拒时必须什么副作用都没有。

    断言的是**可观察的状态**：越权者拿到 403，资料随后仍能被它的 owner 读到。
    把 delete_resource 里的鉴权整段去掉，这条立刻变红。

    诚实标注（不要把它读成比实际更强）：本仓库的请求事务在异常时回滚
    （core/db.py 的 `request_conn`），因此"审计写在鉴权**之前**"这类顺序错误**不会**在数据层留下
    痕迹——一个被拒的请求里写下的审计行会随事务一起回滚。可观察的判据只有"成功的删除恰好写一条"
    （上一条用例）。下面那句审计断言因此抓不住顺序错误，保留它只是为了把"被拒的删除没有留痕"
    这个事实写下来。
    Behavioural negative control for the case above: a refused delete must have no side effect. What is
    asserted is observable state - the unauthorised actor gets a 403 and the resource can still be read
    by its owner afterwards. Removing the authorisation check from delete_resource turns this red at
    once.

    Stated honestly, because it is weaker than it looks: this repository's request transaction rolls
    back on exception (request_conn in core/db.py), so an ordering mistake such as "the audit row is
    written *before* the authorisation check" leaves no trace in the data - anything written inside a
    refused request is rolled back with it. The only observable criterion is "a successful delete writes
    exactly one row" (the case above). The audit assertion below therefore cannot catch an ordering
    mistake; it is kept only to write the fact down.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    alices = _seed_row(api, uploader_id=_user_id(api, "alice"), title="alice 的私有资料", visibility="private")
    api.c.post("/api/auth/logout")

    api.login("bob")
    denied = api.c.delete(f"/api/files/resources/{alices}")
    assert denied.status_code == 403, denied.text

    api.login("alice")
    still_there = api.c.get(f"/api/files/resources/{alices}")
    assert still_there.status_code == 200, "a refused delete must leave the resource alone"
    assert still_there.json()["title"] == "alice 的私有资料"
    assert [row for row in _action_rows(api) if row[0] == "file.resource.delete"] == [], (
        "a refused delete must not be recorded")
