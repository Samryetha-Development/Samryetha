"""PR #85 regressions: immutable uploads, private rating access and URL encoding."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from test_files import _category_id, _publish


def _ticket(api, filename="notes.txt", body=b"safe content"):
    response = api.c.post("/api/files/resources/presign", json={
        "filename": filename, "mimeType": "text/plain", "sizeBytes": len(body),
    })
    assert response.status_code == 200, response.text
    return response.json()


def _create_body(api, ticket, body=b"safe content", filename="notes.txt"):
    return {
        "objectKey": ticket["objectKey"], "expires": ticket["expires"], "sig": ticket["sig"],
        "sizeBytes": len(body), "categoryId": _category_id(api, "study-syllabus"),
        "title": "Course notes", "visibility": "public", "originalFilename": filename,
        "mimeType": "text/plain",
    }


def test_published_upload_ticket_cannot_replace_bytes(api):
    api.mkuser("alice")
    api.login("alice")
    original = b"safe content"
    ticket = _ticket(api, body=original)
    assert api.c.put(ticket["uploadUrl"], content=original).status_code == 204
    resource = api.c.post("/api/files/resources", json=_create_body(api, ticket)).json()
    download = api.c.get(f"/api/files/resources/{resource['id']}/download").json()

    replay = api.c.put(ticket["uploadUrl"], content=b"evil content")
    assert replay.status_code == 409
    assert api.c.get(download["downloadUrl"]).content == original
    assert not list(Path(api.app.state.storage.root).rglob(".upload-*"))


def test_upload_completing_after_publication_cannot_replace_bytes(api):
    api.mkuser("alice")
    api.login("alice")
    original = b"safe content"
    ticket = _ticket(api, body=original)
    assert api.c.put(ticket["uploadUrl"], content=original).status_code == 204
    create_body = _create_body(api, ticket)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed_body():
            yield b"evil "
            started.set()
            await release.wait()
            yield b"content"

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver", cookies=api.c.cookies,
        ) as client:
            upload = asyncio.create_task(client.put(ticket["uploadUrl"], content=delayed_body()))
            try:
                await asyncio.wait_for(started.wait(), 5)
                created = await client.post("/api/files/resources", json=create_body)
                assert created.status_code == 201, created.text
            finally:
                release.set()
            assert (await upload).status_code == 409
            resource_id = created.json()["id"]
            download = (await client.get(f"/api/files/resources/{resource_id}/download")).json()
            assert (await client.get(download["downloadUrl"])).content == original

    asyncio.run(run())


def test_concurrent_uploads_complete_only_once(api):
    api.mkuser("alice")
    api.login("alice")
    bodies = (b"first content", b"other content")
    ticket = _ticket(api, body=bodies[0])

    async def run():
        ready = asyncio.Event()
        started = 0

        async def gated_body(body):
            nonlocal started
            started += 1
            if started == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            yield body

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver", cookies=api.c.cookies,
        ) as client:
            responses = await asyncio.gather(*(
                client.put(ticket["uploadUrl"], content=gated_body(body)) for body in bodies
            ))
        assert sorted(response.status_code for response in responses) == [204, 409]
        winner = next(body for body, response in zip(bodies, responses) if response.status_code == 204)
        assert Path(api.app.state.storage.path_for(ticket["objectKey"])).read_bytes() == winner
        assert not list(Path(api.app.state.storage.root).rglob(".upload-*"))

    asyncio.run(run())


def test_failed_upload_can_retry_with_same_ticket(api):
    api.mkuser("alice")
    api.login("alice")
    ticket = _ticket(api, body=b"abc")
    assert api.c.put(ticket["uploadUrl"], content=b"ab").status_code == 400
    assert api.c.put(ticket["uploadUrl"], content=b"abc").status_code == 204


@pytest.mark.parametrize("filename", [
    "notes#1.txt", "notes%20draft.txt", "notes%2Fdraft.txt", "notes%.txt", "课堂 笔记.txt",
])
def test_reserved_filename_round_trips_upload_and_download(api, filename):
    api.mkuser("alice")
    api.login("alice")
    body = b"file bytes"
    ticket = _ticket(api, filename=filename, body=body)
    create_body = _create_body(api, ticket, body, filename)

    async def run():
        # TestClient unquotes httpx's already-decoded URL.path again. ASGITransport
        # decodes once, matching Uvicorn and preserving literal percent sequences.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver", cookies=api.c.cookies,
        ) as client:
            uploaded = await client.put(ticket["uploadUrl"], content=body)
            assert uploaded.status_code == 204, uploaded.text
            created = await client.post("/api/files/resources", json=create_body)
            assert created.status_code == 201, created.text
            download = (await client.get(f"/api/files/resources/{created.json()['id']}/download")).json()
            await client.post("/api/auth/logout")
            served = await client.get(download["downloadUrl"])
            assert served.status_code == 200, served.text
            assert served.content == body

    asyncio.run(run())


def test_clear_rating_cannot_probe_never_public_resource(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    resource = _publish(api, "Private notes", visibility="private")
    resource_id = resource["id"]
    assert api.c.put(f"/api/files/resources/{resource_id}/rating", json={"score": 5}).status_code == 200
    api.c.post("/api/auth/logout")
    api.login("bob")
    assert api.c.delete(f"/api/files/resources/{resource_id}/rating").status_code == 404
    assert api.c.delete("/api/files/resources/999999/rating").status_code == 404


def test_clear_rating_rechecks_visibility_and_keeps_hidden_vote(api):
    api.mkuser("alice")
    api.mkuser("bob")
    api.login("alice")
    resource = _publish(api, "Shared notes", visibility="public")
    resource_id = resource["id"]
    api.c.post("/api/auth/logout")
    api.login("bob")
    assert api.c.put(f"/api/files/resources/{resource_id}/rating", json={"score": 4}).status_code == 200
    api.c.post("/api/auth/logout")
    api.login("alice")
    assert api.c.patch(f"/api/files/resources/{resource_id}", json={"visibility": "private"}).status_code == 200
    api.c.post("/api/auth/logout")
    api.login("bob")
    assert api.c.delete(f"/api/files/resources/{resource_id}/rating").status_code == 404
    api.c.post("/api/auth/logout")
    api.login("alice")
    detail = api.c.get(f"/api/files/resources/{resource_id}").json()
    assert detail["ratingCount"] == 1 and detail["ratingAvg"] == 4
