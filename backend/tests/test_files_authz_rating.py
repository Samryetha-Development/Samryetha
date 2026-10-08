"""上传鉴权收口与星级评分的回归用例（2026-10-08）。

Regression cases for the upload-authorization tightening and star ratings (2026-10-08).

背景 / Background:
管理员在实测中发现"任何登录用户都能上传资料"，要求把上传/新建资料的能力收口为管理员，
同时明确"星级评分功能要留"。收口上传时有一个必须处理的耦合：评分与收藏此前借用
``FILE_CREATE`` 做校验，若只把 ``FILE_CREATE`` 收紧，普通用户会连评分和收藏一起失去。
因此本轮把两种能力拆开（``FILE_CREATE`` 仅管理员、``FILE_INTERACT`` 为 active 用户），
本文件同时锁定这两半。

The administrator found that any signed-in user could upload and asked for uploading and creating
resources to be restricted to admins, while making clear that star ratings must stay. Tightening
``FILE_CREATE`` has a coupling that has to be handled: rating and favouriting used to borrow
``FILE_CREATE`` for their check, so narrowing it alone would have taken rating and favouriting away
from ordinary users as well. The two abilities are therefore split (``FILE_CREATE`` for admins,
``FILE_INTERACT`` for any active user) and this file locks down both halves.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sqlalchemy import select, update

from samryetha.authz import Abilities, AuthorizationService
from samryetha.core.deps import CurrentUser
from samryetha.core.schema import users


def _category_id(api, slug: str) -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _presign(api, filename: str = "notes.txt", size: int = 7, mime: str = "text/plain"):
    return api.c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": mime, "sizeBytes": size},
    )


def _publish(api, title: str, *, filename: str = "notes.txt", body: bytes = b"payload", visibility: str = "public"):
    """完整走一遍上传链路（须由管理员会话调用）。
    Run the whole upload chain (must be called from an admin session).
    """
    presign = _presign(api, filename, len(body))
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
            "categoryId": _category_id(api, "study-syllabus"),
            "title": title,
            "visibility": visibility,
            "originalFilename": filename,
            "mimeType": "text/plain",
        },
    )
    return ticket, created


def _valid_create_body(api, title: str = "冒名发布") -> dict:
    """构造一份**校验层面合法**的建资料请求体：这样被拒的原因只能是鉴权，不会与 422 混淆。
    Build a request body that is valid as far as validation goes, so a rejection can only be about
    authorization and cannot be confused with a 422.
    """
    return {
        "objectKey": "00000000-0000-0000-0000-000000000000/notes.txt",
        "expires": "1",
        "sig": "deadbeef",
        "sizeBytes": 7,
        "categoryId": _category_id(api, "study-syllabus"),
        "title": title,
        "originalFilename": "notes.txt",
        "mimeType": "text/plain",
    }


def _user_id(api, username: str) -> int:
    with api.app.state.db.request_conn() as conn:
        return int(conn.execute(select(users.c.id).where(users.c.username == username)).scalar_one())


def _set_role(api, username: str, role: str) -> None:
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == username).values(role=role))


def _actor(role: str, *, status: str = "active", user_id: int = 7) -> CurrentUser:
    return CurrentUser(user_id, "probe", "probe", "probe@example.com", role, status)


# ================================================================ 授权矩阵 / ability matrix


def test_authz_matrix_keeps_upload_and_interaction_apart(api):
    """能力矩阵直接断言：上传是管理员专属，互动对所有 active 用户开放。

    这条是"拆成两个能力"的最小锁定：只要有人把 ``FILE_CREATE`` 放回 ``is_active``，
    或者把 ``FILE_INTERACT`` 收紧成管理员，这里立刻失败。

    Assert the ability matrix directly: uploading is admin-only while interactions stay open to
    every active user. This is the smallest lock on the split: if anyone relaxes ``FILE_CREATE``
    back to ``is_active`` or narrows ``FILE_INTERACT`` to admins, this fails immediately.
    """
    with api.app.state.db.request_conn() as conn:
        authz = AuthorizationService(conn)
        admin = _actor("admin")
        member = _actor("student")
        banned = _actor("student", status="banned")

        assert authz.can(None, Abilities.FILE_CREATE, None) is False
        assert authz.can(admin, Abilities.FILE_CREATE, None) is True
        assert authz.can(member, Abilities.FILE_CREATE, None) is False
        assert authz.can(banned, Abilities.FILE_CREATE, None) is False

        assert authz.can(None, Abilities.FILE_INTERACT, None) is False
        assert authz.can(admin, Abilities.FILE_INTERACT, None) is True
        assert authz.can(member, Abilities.FILE_INTERACT, None) is True
        assert authz.can(banned, Abilities.FILE_INTERACT, None) is False

        # 上传收口之后"上传者"必然是管理员，但 update/delete 的"上传者本人"分支仍然存在，
        # 用鸭子类型资源直接锁定它，避免这条分支失去覆盖。
        # After the tightening an uploader is always an admin, yet the "uploader themselves" branch
        # of update/delete still exists; it is locked down here with a duck-typed resource so the
        # branch keeps its coverage.
        owned = {"type": "file_resource", "id": 1, "uploaderId": member.id, "visibility": "public"}
        assert authz.can(member, Abilities.FILE_UPDATE, owned) is True
        assert authz.can(_actor("student", user_id=member.id + 1), Abilities.FILE_UPDATE, owned) is False
        assert authz.can(member, Abilities.FILE_DELETE, owned) is True
        assert authz.can(_actor("student", user_id=member.id + 1), Abilities.FILE_DELETE, owned) is False


# ================================================================ 上传鉴权收口 / upload gate


def test_plain_active_user_is_refused_at_presign(api):
    """普通 active 用户请求上传票据必须被拒，且响应体不泄漏任何票据信息。

    修之前：``FILE_CREATE`` 等于 ``is_active``，这条请求返回 200 并附完整 objectKey 与
    上传签名，也就是任何登录用户都能往资料库投放文件。

    A plain active user asking for an upload ticket must be refused, with a body that leaks no
    ticket material. Before the fix ``FILE_CREATE`` was ``is_active``, so this request returned 200
    together with a full object key and upload signature: any signed-in user could publish.
    """
    api.mkuser("root", role="admin")
    api.mkuser("bob")
    api.login("bob")

    refused = _presign(api, "invade.txt", 7)
    assert refused.status_code == 403, refused.text
    assert "objectKey" not in refused.text
    assert "uploadUrl" not in refused.text
    assert "sig" not in refused.text


def test_plain_active_user_is_refused_at_create(api):
    """普通用户提交完全合法的建资料请求体仍必须被拒（鉴权先于资源校验）。
    A plain user submitting a fully valid create body must still be refused: authorization comes
    before anything the payload says.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    _, created = _publish(api, "管理员发布的资料")
    assert created.status_code == 201, created.text
    api.c.post("/api/auth/logout")

    api.login("bob")
    denied = api.c.post("/api/files/resources", json=_valid_create_body(api))
    assert denied.status_code == 403, denied.text
    assert "objectKey" not in denied.text

    # 资料总数没有变化：被拒的请求确实没有落下任何一行。
    # The resource count is unchanged, so the refused request really wrote nothing.
    assert api.c.get("/api/files/resources").json()["total"] == 1


def test_plain_user_cannot_write_upload_bytes_even_with_a_well_formed_url(api):
    """上传字节这一步同样按管理员能力把关，而不只是建资料那一步。

    修之前：非管理员带着形状合法的上传地址会先撞到签名校验（400）；现在鉴权先于签名判定，
    直接 403。用 403 而不是 400 正是"能力检查真的挡住了"的判据。

    Writing the bytes is gated on the admin ability too, not just the metadata step. Before the fix
    a non-admin with a well-formed upload URL hit the signature check first (400); authorization now
    runs before the signature, so it is a 403. Getting 403 rather than 400 is exactly the evidence
    that the ability check is what stopped it.
    """
    api.mkuser("bob")
    bob = _user_id(api, "bob")
    api.login("bob")

    refused = api.c.put(
        f"/api/files/upload/{bob}/00000000-0000-0000-0000-000000000000/notes.txt"
        "?size=7&expires=1&sig=deadbeef",
        content=b"payload",
    )
    assert refused.status_code == 403, refused.text


def test_demoted_admin_cannot_keep_uploading_with_an_old_ticket(api):
    """管理员被降权后，此前取得的上传票据也必须失效。

    否则"只有管理员能上传"会留下一个时间窗：降权前的票据仍能写入字节。身份与角色每次请求
    重新读取，所以降权立刻生效。

    A ticket issued while an admin must stop working once that account is demoted, otherwise
    "only admins upload" would leave a window in which a pre-demotion ticket still writes bytes.
    Identity and role are re-read on every request, so the demotion takes effect at once.
    """
    api.mkuser("root", role="admin")
    api.login("root")
    ticket = _presign(api, "before-demotion.txt", 7).json()

    _set_role(api, "root", "student")

    refused = api.c.put(ticket["uploadUrl"], content=b"payload")
    assert refused.status_code == 403, refused.text


def test_admin_upload_flow_still_works_end_to_end(api):
    """管理员的上传全流程必须完好：收口不能把正常功能一起挡掉。
    The admin upload chain must remain intact: tightening must not break the normal path.
    """
    api.mkuser("root", role="admin")
    api.login("root")

    ticket, created = _publish(api, "管理员资料", filename="guide.txt", body=b"admin-payload")
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert created.json()["uploader"]["username"] == "root"

    served = api.c.get(api.c.get(f"/api/files/resources/{rid}/download").json()["downloadUrl"])
    assert served.status_code == 200
    # 落盘与回源逐字节一致：收口不能影响文件内容。
    # Bytes on disk and bytes served match exactly: the tightening must not disturb content.
    assert served.content == b"admin-payload"
    assert ticket["objectKey"]


def test_signed_out_visitor_is_refused_everywhere(api):
    """未登录：上传与互动两条线都必须按未认证拒绝（401），而不是 403 或 500。
    Signed out: both the upload and the interaction lines must be refused as unauthenticated (401),
    not 403 and not 500.
    """
    api.mkuser("alice", role="admin")
    api.login("alice")
    _, created = _publish(api, "公开资料")
    rid = created.json()["id"]
    api.c.post("/api/auth/logout")

    assert _presign(api).status_code == 401
    assert api.c.post("/api/files/resources", json=_valid_create_body(api)).status_code == 401
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 5}).status_code == 401
    assert api.c.delete(f"/api/files/resources/{rid}/rating").status_code == 401
    assert api.c.put(f"/api/files/resources/{rid}/favorite").status_code == 401
    assert api.c.delete(f"/api/files/resources/{rid}/favorite").status_code == 401


# ================================================================ 评分与收藏必须留下 / interactions stay


def test_plain_user_can_still_rate_and_favourite(api):
    """收口的核心风险：普通用户必须仍然能评分与收藏。

    这条是"拆能力"的必要反面——只把 ``FILE_CREATE`` 收紧的最省事做法会让这里变成 403，
    也就是管理员说的"评分功能没了"。

    The central risk of the tightening: ordinary users must still be able to rate and favourite.
    This is the necessary converse of the ability split; the laziest way of narrowing
    ``FILE_CREATE`` alone would turn these into 403s, which is exactly the "rating is gone" the
    administrator warned about.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.login("alice")
    _, created = _publish(api, "可评分的资料")
    rid = created.json()["id"]
    api.c.post("/api/auth/logout")

    api.login("bob")
    rated = api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 4})
    assert rated.status_code == 200, rated.text
    assert rated.json() == {"resourceId": rid, "myRating": 4, "ratingAvg": 4.0, "ratingCount": 1}

    favourited = api.c.put(f"/api/files/resources/{rid}/favorite")
    assert favourited.status_code == 200, favourited.text
    assert favourited.json()["isFavorited"] is True
    assert favourited.json()["favoriteCount"] == 1

    detail = api.c.get(f"/api/files/resources/{rid}").json()
    assert detail["myRating"] == 4
    assert detail["ratingAvg"] == 4.0
    assert detail["ratingCount"] == 1
    assert detail["isFavorited"] is True
    assert api.c.get("/api/files/favorites").json()["total"] == 1


def test_rating_and_favourite_stay_consistent_across_users(api):
    """多人评分/收藏的聚合、改分覆盖、撤销与排序：收口不得改动这些既有语义。
    Aggregation, overwriting, clearing and sorting across several users: the tightening must not
    change any of that existing behaviour.
    """
    api.mkuser("alice", role="admin")
    api.mkuser("bob")
    api.mkuser("carol")
    api.login("alice")
    _, first = _publish(api, "第一条")
    _, second = _publish(api, "第二条")
    rid = first.json()["id"]
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 5}).json()["ratingAvg"] == 5.0
    api.c.post("/api/auth/logout")

    api.login("carol")
    carol = api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 3}).json()
    assert carol["ratingCount"] == 2
    assert carol["ratingAvg"] == 4.0
    # 改分是覆盖式：人数不变、均分按新分数重算。
    # Overwriting a score keeps the count and recomputes the average.
    carol = api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 1}).json()
    assert carol["ratingCount"] == 2
    assert carol["ratingAvg"] == 3.0

    # 撤销后均分回落到剩余那一票。
    # Clearing drops the average back to the remaining vote.
    cleared = api.c.delete(f"/api/files/resources/{rid}/rating")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"resourceId": rid, "myRating": None, "ratingAvg": 5.0, "ratingCount": 1}

    # 排序与列表里的 myRating 都要跟着走。
    # Sorting and the list's myRating must follow along.
    ordered = api.c.get("/api/files/resources", params={"sort": "rating"}).json()
    assert ordered["sort"] == "rating"
    assert ordered["items"][0]["id"] == rid
    assert ordered["items"][0]["ratingAvg"] == 5.0
    assert ordered["items"][-1]["id"] == second.json()["id"]
    assert ordered["items"][0]["myRating"] is None

    api.c.post("/api/auth/logout")
    api.login("bob")
    by_bob = api.c.get("/api/files/resources", params={"sort": "rating"}).json()
    assert by_bob["items"][0]["myRating"] == 5
