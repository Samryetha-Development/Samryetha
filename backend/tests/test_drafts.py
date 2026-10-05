"""Private drafts, attachment retention, and atomic conversion to discussions."""

from sqlalchemy import func, select, update

from samryetha.schema import discussion_drafts, discussions, outbox_events, users
from samryetha.attachments import reap_orphans


def _board(api):
    api.login_dev()
    response = api.c.post("/api/boards", json={"name": "Draft board", "slug": "draft-board"})
    assert response.status_code == 201, response.text


def _upload(api, filename="notes.txt", complete=True):
    response = api.c.post("/api/attachments/presign", json={
        "filename": filename, "mimeType": "text/plain", "sizeBytes": 4,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    if complete:
        assert api.c.put(result["uploadUrl"], content=b"test").status_code == 204
    return result["attachmentId"]


def _save(api, **changes):
    payload = {"title": "A draft", "bodyMarkdown": "**unfinished**", "bodyFormat": "markdown", **changes}
    response = api.c.post("/api/drafts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _count(api, table):
    with api.app.state.db.request_conn() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _reap(api):
    with api.app.state.db.request_conn() as conn:
        return reap_orphans(conn, api.app.state.storage, older_than_ms=-1, uploaded_older_than_ms=-1)


def test_draft_crud_preserves_partial_text_without_publishing(api):
    _board(api)
    event_count = _count(api, outbox_events)
    draft = _save(api, title="  Only a title  ", bodyMarkdown="", boardSlug=None, bodyFormat="text")
    assert draft["title"] == "  Only a title  "
    assert draft["bodyMarkdown"] == ""
    assert draft["boardSlug"] is None
    assert draft["attachments"] == []
    assert _count(api, discussions) == 0
    assert _count(api, outbox_events) == event_count

    payload = {"title": "", "bodyMarkdown": "  text\n\n  ", "bodyFormat": "markdown", "boardSlug": "draft-board"}
    updated = api.c.put(f"/api/drafts/{draft['id']}", json=payload)
    assert updated.status_code == 200, updated.text
    got = api.c.get(f"/api/drafts/{draft['id']}").json()
    for key, value in payload.items():
        assert got[key] == value
    assert got["createdAt"] == draft["createdAt"]
    assert got["updatedAt"] >= draft["updatedAt"]
    assert _count(api, discussion_drafts) == 1
    items = api.c.get("/api/drafts").json()["items"]
    assert len(items) == 1
    assert items[0]["preview"] == "text"
    assert "bodyMarkdown" not in items[0]
    assert api.c.get("/api/discussions").json()["items"] == []
    assert _count(api, outbox_events) == event_count
    assert api.c.delete(f"/api/drafts/{draft['id']}").status_code == 200
    assert api.c.get(f"/api/drafts/{draft['id']}").status_code == 404


def test_drafts_are_account_private_even_from_admins(api):
    api.mkuser("writer")
    api.login("writer")
    draft = _save(api)
    api.login_dev()
    assert api.c.get("/api/drafts").json()["items"] == []
    path = f"/api/drafts/{draft['id']}"
    assert api.c.get(path).status_code == 404
    assert api.c.put(path, json={"title": "overwrite"}).status_code == 404
    assert api.c.delete(path).status_code == 404
    assert api.c.post("/api/discussions", json={
        "draftId": draft["id"], "boardSlug": "unknown", "bodyMarkdown": "publish another user's draft",
    }).status_code == 404
    api.login("writer")
    assert api.c.get(path).json()["title"] == "A draft"
    api.c.post("/api/auth/logout")
    assert api.c.get("/api/drafts").status_code == 401
    assert api.c.get(path).status_code == 401
    assert api.c.post("/api/drafts", json={}).status_code == 401
    assert api.c.put(path, json={}).status_code == 401
    assert api.c.delete(path).status_code == 401


def test_inactive_users_cannot_access_drafts(api):
    api.mkuser("pendingwriter")
    api.login("pendingwriter")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "pendingwriter").values(status="pending"))
    assert api.c.get("/api/drafts").status_code == 403
    assert api.c.post("/api/drafts", json={}).status_code == 403


def test_draft_validation_and_private_board_permissions(api):
    _board(api)
    assert api.c.post("/api/drafts", json={"title": "x" * 101}).status_code == 422
    assert api.c.post("/api/drafts", json={"bodyMarkdown": "x" * 40001}).status_code == 422
    assert api.c.post("/api/drafts", json={"bodyFormat": "html"}).status_code == 422
    assert api.c.post("/api/drafts", json={"attachmentIds": list(range(1, 12))}).status_code == 422
    assert api.c.post("/api/drafts", json={"boardSlug": "missing"}).status_code == 404
    assert api.c.post("/api/boards", json={"name": "Private", "slug": "private-draft", "visibility": "private"}).status_code == 201
    api.mkuser("outsider")
    api.login("outsider")
    assert api.c.post("/api/drafts", json={"boardSlug": "private-draft"}).status_code == 403
    assert _count(api, discussion_drafts) == 0


def test_draft_attachments_survive_cleanup_then_publish_atomically(api):
    _board(api)
    attachment_id = _upload(api)
    draft = _save(api, boardSlug="draft-board", attachmentIds=[attachment_id])
    assert draft["attachments"][0]["id"] == attachment_id
    assert api.c.get("/api/drafts").json()["items"][0]["attachmentCount"] == 1
    assert _reap(api) == 0
    restored = api.c.get(f"/api/drafts/{draft['id']}").json()
    assert api.c.get(restored["attachments"][0]["downloadUrl"]).content == b"test"
    payload = {"draftId": draft["id"], "boardSlug": "draft-board", "bodyMarkdown": "Final text", "attachmentIds": [attachment_id]}
    published = api.c.post("/api/discussions", json=payload)
    assert published.status_code == 201, published.text
    assert published.json()["bodyMarkdown"] == "Final text"
    assert published.json()["attachments"][0]["id"] == attachment_id
    assert api.c.get("/api/drafts").json()["items"] == []
    assert api.c.get(f"/api/attachments/{attachment_id}").json()["state"] == "attached"
    # A repeat submission cannot create a second discussion from this draft.
    assert api.c.post("/api/discussions", json=payload).status_code == 404
    assert _count(api, discussions) == 1


def test_failed_publish_rolls_back_and_keeps_draft_and_files(api):
    _board(api)
    attachment_id = _upload(api)
    draft = _save(api, boardSlug="draft-board", attachmentIds=[attachment_id])
    event_count = _count(api, outbox_events)
    response = api.c.post("/api/discussions", json={
        "draftId": draft["id"], "boardSlug": "draft-board", "bodyMarkdown": "body",
        "attachmentIds": [attachment_id, 999999],
    })
    assert response.status_code == 422, response.text
    assert _count(api, discussions) == 0
    assert _count(api, outbox_events) == event_count
    assert api.c.get(f"/api/drafts/{draft['id']}").json()["attachments"][0]["id"] == attachment_id
    assert api.c.get(f"/api/attachments/{attachment_id}").json()["state"] == "uploaded"
    assert _reap(api) == 0


def test_cannot_steal_attachments_from_another_draft_or_user(api):
    _board(api)
    attachment_id = _upload(api)
    draft = _save(api, attachmentIds=[attachment_id])
    assert api.c.post("/api/drafts", json={"attachmentIds": [attachment_id]}).status_code == 422
    other = _save(api)
    assert api.c.put(f"/api/drafts/{other['id']}", json={"attachmentIds": [attachment_id]}).status_code == 422
    for extra in ({}, {"draftId": other["id"]}):
        assert api.c.post("/api/discussions", json={
            "boardSlug": "draft-board", "bodyMarkdown": "body", "attachmentIds": [attachment_id], **extra,
        }).status_code == 422
    pending_id = _upload(api, filename="pending.txt", complete=False)
    assert api.c.post("/api/drafts", json={"attachmentIds": [pending_id]}).status_code == 422
    api.mkuser("anotherwriter")
    api.login("anotherwriter")
    assert api.c.post("/api/drafts", json={"attachmentIds": [attachment_id]}).status_code == 422
    api.login_dev()
    assert api.c.get(f"/api/drafts/{draft['id']}").json()["attachments"][0]["id"] == attachment_id


def test_removed_and_deleted_draft_attachments_become_reapable(api):
    api.login_dev()
    attachment_id = _upload(api)
    draft = _save(api, attachmentIds=[attachment_id])
    assert api.c.put(f"/api/drafts/{draft['id']}", json={"title": "Keep text", "attachmentIds": []}).status_code == 200
    assert _reap(api) == 1
    new_id = _upload(api)
    assert api.c.put(f"/api/drafts/{draft['id']}", json={"attachmentIds": [new_id]}).status_code == 200
    assert api.c.delete(f"/api/drafts/{draft['id']}").status_code == 200
    assert _reap(api) == 1


def test_deleting_attachment_removes_draft_reference(api):
    api.login_dev()
    attachment_id = _upload(api)
    draft = _save(api, attachmentIds=[attachment_id])
    assert api.c.delete(f"/api/attachments/{attachment_id}").status_code == 200
    assert api.c.get(f"/api/drafts/{draft['id']}").json()["attachments"] == []


def test_list_pagination_is_owner_scoped(api):
    api.login_dev()
    ids = [_save(api)["id"] for _ in range(3)]
    first = api.c.get("/api/drafts?limit=2").json()
    assert [item["id"] for item in first["items"]] == ids[:0:-1]
    second = api.c.get(f"/api/drafts?limit=2&cursor={first['nextCursor']}").json()
    assert [item["id"] for item in second["items"]] == ids[:1]
    assert second["nextCursor"] is None
    assert api.c.get("/api/drafts?cursor=bad").status_code == 422


def test_existing_database_gets_draft_tables_without_losing_content(api):
    _board(api)
    posted = api.c.post("/api/discussions", json={"boardSlug": "draft-board", "bodyMarkdown": "Existing post"}).json()
    with api.app.state.db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE draft_attachments")
        conn.exec_driver_sql("DROP TABLE discussion_drafts")
    api.app.state.db.create_schema()
    api.app.state.db.create_schema()
    assert api.c.get(f"/api/discussions/{posted['id']}").json()["bodyMarkdown"] == "Existing post"
    assert _save(api)["id"] > 0
