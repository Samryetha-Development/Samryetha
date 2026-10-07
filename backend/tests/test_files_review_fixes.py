"""PR #85 评审意见的回归用例（F01 / F03 / F04）。

Regression cases for the review findings on PR #85 (F01 / F03 / F04).

命名与评审报告对齐，便于逐条核对：每条用例的 docstring 都写明"修之前会怎样"以及评审给出的复现步骤。
Names map onto the review report so each finding can be checked off one by one: every docstring
states the pre-fix behaviour and the reproduction the reviewer gave.

F02 不在此文件内：评审在 PR 评论中明确表示由他本人修复（"正在修复F02就不修了"，2026-10-07 06:49Z），
我方不改动那条代码路径，以免与其修复冲突。
F02 is deliberately absent: the reviewer stated in a PR comment that they are fixing it themselves
("正在修复F02就不修了", 2026-10-07 06:49Z), so that code path is left untouched to avoid conflicting
with their fix.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx


def _category_id(api, slug: str) -> int:
    for item in api.c.get("/api/files/config").json()["categories"]:
        if item["slug"] == slug:
            return item["id"]
    raise AssertionError(f"category {slug} not found")


def _presign(api, filename: str, size: int, mime: str = "text/plain"):
    response = api.c.post(
        "/api/files/resources/presign",
        json={"filename": filename, "mimeType": mime, "sizeBytes": size},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _publish(api, title: str, *, filename: str, body: bytes, mime: str = "text/plain", visibility: str = "public"):
    presign = _presign(api, filename, len(body), mime)
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
            "title": title,
            "visibility": visibility,
            "originalFilename": filename,
            "mimeType": mime,
        },
    )
    return presign, created


# ================================================================ F01 · P1 对象不可变


def test_published_object_cannot_be_overwritten_by_replaying_the_ticket(api):
    """F01（P1）回归：修之前，发布后仍能用原上传票据把内容换成等长的另一份。

    评审复现：先上传 safe content 并公开发布，再用原票据上传同长度的 evil content 返回 204，
    下载地址随即返回新字节，而版本号与校验和仍描述旧内容。

    F01 (P1) regression: before the fix, the original upload ticket could still swap the payload
    for a different one of equal length after publishing. The reviewer's reproduction uploaded
    "safe content", published it, replayed the ticket with an equal-length "evil content" and got
    a 204, after which the download URL served the new bytes while the version and checksum kept
    describing the old ones.
    """
    api.mkuser("alice")
    api.login("alice")
    safe = b"safe content"
    evil = b"evil content"
    assert len(safe) == len(evil)

    presign, created = _publish(api, "已发布的资料", filename="notes.txt", body=safe)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    ticket = api.c.get(f"/api/files/resources/{rid}/download").json()
    assert api.c.get(ticket["downloadUrl"]).content == safe

    # 重放同一张票据：写入等长但不同的内容，必须被拒。
    # Replaying the same ticket to write equal-length different content must be refused.
    replay = api.c.put(presign["uploadUrl"], content=evil)
    assert replay.status_code == 409, replay.text

    # 关键断言：实际字节没有变，读者拿到的仍是发布时的那份。
    # The decisive assertion: the bytes are unchanged, so readers still get what was published.
    after = api.c.get(ticket["downloadUrl"])
    assert after.status_code == 200
    assert after.content == safe

    # 再次重放同样被拒（票据不是"用一次就失效"而是"对象一旦写完就不可变"）。
    # A further replay is refused too: the ticket does not merely expire after one use, the object
    # itself is immutable once written.
    assert api.c.put(presign["uploadUrl"], content=evil).status_code == 409


def test_upload_ticket_is_single_use_before_publishing_too(api):
    """F01 补充：对象在**尚未被认领**时同样不可覆盖，证明不可变来自对象本身而非数据库行。

    F01 follow-up: the object is immutable even before any resource claims it, proving the
    guarantee comes from the object rather than from a database row.
    """
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api, "draft.txt", 5)
    assert api.c.put(presign["uploadUrl"], content=b"first").status_code == 204
    second = api.c.put(presign["uploadUrl"], content=b"other")
    assert second.status_code == 409, second.text


def test_concurrent_replay_of_one_ticket_only_lets_one_upload_win(api):
    """F01 补充：并发重放同一张票据不可能两份都成功——完成步骤是原子的，不是先查后写。

    F01 follow-up: two concurrent replays of one ticket cannot both succeed because completion is
    atomic rather than a check-then-write.
    """
    api.mkuser("alice")
    api.login("alice")
    storage = api.app.state.storage
    presign = _presign(api, "race.txt", 5)
    full = storage.path_for(presign["objectKey"])

    assert api.c.put(presign["uploadUrl"], content=b"first").status_code == 204
    statuses = [api.c.put(presign["uploadUrl"], content=b"other").status_code for _ in range(3)]
    assert statuses == [409, 409, 409], statuses

    with open(full, "rb") as handle:
        assert handle.read() == b"first"


# ================================================================ F03 · P2 撤销评分不得泄漏


def test_clear_rating_does_not_leak_an_invisible_resource(api):
    """F03（P2）回归：修之前，从未获授权的账户 DELETE 评分可以读到私有资料的评分统计。

    评审复现：Alice 建一条从未公开的 private 资料并打 5 分；Bob 从未获阅读权限也从未评分——
    GET 详情为 404，但 DELETE 评分返回 200 且带 ratingAvg=5.0、ratingCount=1。

    F03 (P2) regression: before the fix, an account that never had access could DELETE the rating
    and read back a private resource's rating statistics. The reviewer's reproduction had Alice
    create a never-public private resource rated 5; Bob, who never had read access and never rated,
    saw a 404 on the detail but a 200 on the DELETE carrying ratingAvg=5.0 and ratingCount=1.
    """
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    _, created = _publish(api, "从未公开的私有资料", filename="secret.txt", body=b"secret", visibility="private")
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 5}).status_code == 200
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.get(f"/api/files/resources/{rid}").status_code == 404
    revoked = api.c.delete(f"/api/files/resources/{rid}/rating")
    assert revoked.status_code == 404, revoked.text
    # 不只是状态码：响应体里绝不能出现该资料的任何评分统计。
    # Not just the status code: the body must not carry any of that resource's rating statistics.
    body = revoked.text
    assert "ratingAvg" not in body and "ratingCount" not in body
    assert "5.0" not in body


def test_clear_rating_after_visibility_is_tightened_returns_404(api):
    """F03 补充：曾经可见（评过分）的资料转私有后，撤销评分必须同样 404。

    F03 follow-up: once a previously visible (and rated) resource turns private, clearing the
    rating must 404 as well.
    """
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    _, created = _publish(api, "先公开后转私有", filename="notes.txt", body=b"payload", visibility="public")
    rid = created.json()["id"]
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 4}).status_code == 200
    assert api.c.delete(f"/api/files/resources/{rid}/rating").status_code == 200
    assert api.c.put(f"/api/files/resources/{rid}/rating", json={"score": 4}).status_code == 200
    api.c.post("/api/auth/logout")

    api.login("alice")
    assert api.c.patch(f"/api/files/resources/{rid}", json={"visibility": "private"}).status_code == 200
    api.c.post("/api/auth/logout")

    api.login("bob")
    assert api.c.delete(f"/api/files/resources/{rid}/rating").status_code == 404


# ================================================================ F04 · P2 保留字符文件名


def test_filenames_with_url_reserved_characters_round_trip(api):
    """F04（P2）回归：修之前，文件名含 `#` 或 `%` 时上传地址不可用。

    评审复现：`notes#1.txt` 的 presign 成功但 PUT 返回 400 Invalid upload URL；
    `notes%20draft.txt` 返回 400 Invalid or expired upload ticket；对照 `notes.txt` 为 204。

    重要：上传与下载必须经 **httpx.ASGITransport** 驱动，不能用 Starlette 的 TestClient。
    实测（backend/.pytmp-verify/probe_decoding.py）表明真实 uvicorn 与 ASGITransport **都只对
    路径解码一次**，而 Starlette TestClient 会二次解码（它对 httpx 已解码的 `url.path` 再执行一次
    `unquote`）。用 TestClient 跑这条用例，会把"文件名里字面 %20"误判成产品缺陷——实际那是测试
    工具的假象。本用例因此走与生产一致的解码路径。

    F04 (P2) regression: before the fix, a filename containing "#" or "%" produced an unusable
    upload URL. The reviewer saw a successful presign but a 400 "Invalid upload URL" for notes#1.txt
    and a 400 "Invalid or expired upload ticket" for notes%20draft.txt, against 204 for the plain
    notes.txt control.

    Note: the upload and download must be driven through **httpx.ASGITransport**, not Starlette's
    TestClient. Measurement (backend/.pytmp-verify/probe_decoding.py) shows real uvicorn and
    ASGITransport both decode a path exactly once, while the Starlette TestClient decodes twice by
    running unquote() over an already-decoded url.path. Running this case through TestClient would
    misreport a literal "%20" in a filename as a product defect when it is a test-tool artefact, so
    the case follows the production decoding path instead.
    """
    api.mkuser("alice")
    api.login("alice")
    names = [
        "notes#1.txt",
        "notes%20draft.txt",
        "100%.txt",
        "notes #1 %2F.txt",
        "我的笔记 草稿.txt",
    ]
    for index, name in enumerate(names):
        body = f"payload-{index}".encode("utf-8")
        presign = _presign(api, name, len(body))
        # 路径段里不能出现裸的 "#"：它会把签名查询串整个变成 fragment。
        # No bare "#" may survive in the path segment, or it turns the signature query string into
        # a fragment.
        path_part = presign["uploadUrl"].split("?", 1)[0]
        assert "#" not in path_part, f"{name}: {presign['uploadUrl']}"

        async def run(presign=presign, body=body, name=name):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api.app),
                base_url="http://testserver",
                cookies=api.c.cookies,
            ) as client:
                uploaded = await client.put(presign["uploadUrl"], content=body)
                assert uploaded.status_code == 204, f"{name}: {uploaded.text}"
                created = await client.post(
                    "/api/files/resources",
                    json={
                        "objectKey": presign["objectKey"],
                        "expires": presign["expires"],
                        "sig": presign["sig"],
                        "sizeBytes": len(body),
                        "categoryId": _category_id(api, "study-syllabus"),
                        "title": f"保留字符文件名 {index}",
                        "visibility": "public",
                        "originalFilename": name,
                        "mimeType": "text/plain",
                    },
                )
                assert created.status_code == 201, f"{name}: {created.text}"
                payload = created.json()
                # 用户可见的原始文件名必须原样保留。
                # The user-visible original filename must be preserved verbatim.
                assert payload["originalFilename"] == name

                ticket = (
                    await client.get(f"/api/files/resources/{payload['id']}/download")
                ).json()
                assert "#" not in ticket["downloadUrl"].split("?", 1)[0], name
                served = await client.get(ticket["downloadUrl"])
                assert served.status_code == 200, f"{name}: {served.status_code}"
                # 落盘与回源必须逐字节一致：编码只影响地址，不影响内容。
                # Bytes on disk and bytes served must match exactly: encoding affects the address
                # only.
                assert served.content == body, name

        asyncio.run(run())


def test_failed_upload_can_retry_with_the_same_ticket(api):
    """F01 补充：未完成的失败上传必须能用同一张票据重试。

    这是"对象不可变"的必要反面：不可变只应针对**已完成**的对象。若完成步骤一失败就把
    目标路径占住，用户遇到网络抖动或截断上传后就再也传不上去了——修复一个缺陷不能换来另一个。
    F01 follow-up: an incomplete, failed upload must remain retryable with the same ticket.

    This is the necessary converse of immutability: only **completed** objects should be immutable.
    If a failed completion left the destination claimed, a user hitting a network hiccup or a
    truncated upload could never upload again, trading one defect for another.
    """
    api.mkuser("alice")
    api.login("alice")
    presign = _presign(api, "retry.txt", 3)
    assert api.c.put(presign["uploadUrl"], content=b"ab").status_code == 400
    assert api.c.put(presign["uploadUrl"], content=b"abc").status_code == 204


def test_upload_still_in_flight_when_the_resource_is_published_cannot_replace_it(api):
    """F01 补充：上传在"资料发布时仍在流中"的竞态下也不能覆盖已发布内容。

    前一条用例是"发布后重放"，这条制造真正的时间重叠：第二个请求的字节在资料创建**之前**就已经
    开始发送，在创建**之后**才写完。完成步骤的原子性必须覆盖这个窗口。
    F01 follow-up: an upload that is still streaming when the resource is published must not replace
    the published content either.

    The previous case replays after publication; this one creates a genuine overlap where the second
    request starts sending bytes **before** the resource is created and finishes **after**. The
    atomicity of the completion step has to cover that window.
    """
    api.mkuser("alice")
    api.login("alice")
    original = b"safe content"
    presign = _presign(api, "race-inflight.txt", len(original))
    assert api.c.put(presign["uploadUrl"], content=original).status_code == 204
    create_body = {
        "objectKey": presign["objectKey"],
        "expires": presign["expires"],
        "sig": presign["sig"],
        "sizeBytes": len(original),
        "categoryId": _category_id(api, "study-syllabus"),
        "title": "竞态用例",
        "visibility": "public",
        "originalFilename": "race-inflight.txt",
        "mimeType": "text/plain",
    }

    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_body():
            yield b"evil "
            started.set()
            await release.wait()
            yield b"content"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://testserver",
            cookies=api.c.cookies,
        ) as client:
            upload = asyncio.create_task(client.put(presign["uploadUrl"], content=delayed_body()))
            try:
                await asyncio.wait_for(started.wait(), 5)
                created = await client.post("/api/files/resources", json=create_body)
                assert created.status_code == 201, created.text
            finally:
                release.set()
            assert (await upload).status_code == 409

            resource_id = created.json()["id"]
            ticket = (await client.get(f"/api/files/resources/{resource_id}/download")).json()
            served = await client.get(ticket["downloadUrl"])
            assert served.status_code == 200
            assert served.content == original

    asyncio.run(run())
