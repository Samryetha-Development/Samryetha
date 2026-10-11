"""Private schedules, immutable commitments, durable atomic publication and races."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select, update

from samryetha.core.db import now_ms
from samryetha.core.schema import attachments, boards, discussion_drafts, discussions, outbox_events, scheduled_posts, users
from samryetha.scheduled_posts.worker import ScheduledPostWorker


def setup_board(api):
    api.login_dev()
    result = api.c.post("/api/boards", json={"name": "Scheduled", "slug": "scheduled", "postingPolicy": "everyone"})
    assert result.status_code == 201, result.text


def schedule(api, **changes):
    response = api.c.post("/api/scheduled-posts", json={
        "boardSlug": "scheduled", "bodyMarkdown": "**A future post**", "bodyFormat": "markdown",
        "scheduledAt": now_ms() + 3600_000, **changes,
    })
    assert response.status_code == 201, response.text
    return response.json()


def count(api, table):
    with api.app.state.db.request_conn() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def due(api, post):
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(scheduled_posts).where(scheduled_posts.c.id == post["id"]).values(scheduled_at=now_ms() - 1))


def test_schedule_is_private_and_cancel_returns_intact_draft(api):
    setup_board(api)
    post = schedule(api, title="Secret future post")
    assert post["canModify"] is True
    assert post["status"] == "pending"
    assert "authorId" not in post
    assert count(api, discussions) == count(api, outbox_events) == 0
    assert api.c.get("/api/discussions").json()["items"] == []
    assert api.c.get("/api/me/posts").json()["items"] == []
    assert api.c.get("/api/drafts").json()["items"] == []
    assert api.c.get("/api/scheduled-posts").json()["items"] == [post]
    stamp = now_ms() + 7200_000
    changed = api.c.patch(f"/api/scheduled-posts/{post['id']}", json={"scheduledAt": stamp})
    assert changed.status_code == 200
    assert changed.json()["scheduledAt"] == stamp
    result = api.c.delete(f"/api/scheduled-posts/{post['id']}")
    assert result.status_code == 200
    draft = api.c.get(f"/api/drafts/{result.json()['draftId']}").json()
    assert draft["bodyMarkdown"] == "**A future post**"
    assert draft["title"] == "Secret future post"
    assert api.c.get("/api/scheduled-posts").json()["items"] == []
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    assert count(api, discussions) == 0
    assert api.c.delete(f"/api/scheduled-posts/{post['id']}").status_code == 409


@pytest.mark.parametrize("can_modify", [True, False])
def test_no_draft_attachment_or_immediate_publish_bypass(api, can_modify):
    setup_board(api)
    upload = api.c.post("/api/attachments/presign", json={"filename": "future.txt", "mimeType": "text/plain", "sizeBytes": 4}).json()
    assert api.c.put(upload["uploadUrl"], content=b"test").status_code == 204
    post = schedule(api, canModify=can_modify, attachmentIds=[upload["attachmentId"]])
    draft_id = post["draftId"]
    path = f"/api/drafts/{draft_id}"
    assert api.c.get(path).status_code == 409
    assert api.c.put(path, json={"bodyMarkdown": "changed"}).status_code == 409
    assert api.c.delete(path).status_code == 409
    assert api.c.delete(f"/api/attachments/{upload['attachmentId']}").status_code == 409
    assert api.c.post("/api/discussions", json={"boardSlug": "scheduled", "bodyMarkdown": "early", "draftId": draft_id, "attachmentIds": [upload["attachmentId"]]}).status_code == 409
    assert api.c.post("/api/discussions", json={"boardSlug": "scheduled", "bodyMarkdown": "early", "attachmentIds": [upload["attachmentId"]]}).status_code == 422
    with api.app.state.db.request_conn() as conn:
        from samryetha.attachments import AttachmentService
        assert AttachmentService(conn, storage=api.app.state.storage).reap_orphans(older_than_ms=-1, uploaded_older_than_ms=-1) == 0
    if not can_modify:
        path = f"/api/scheduled-posts/{post['id']}"
        assert api.c.patch(path, json={"scheduledAt": now_ms() + 7200_000, "canModify": True}).status_code == 403
        assert api.c.delete(path).status_code == 403
        assert api.c.get(path).json()["canModify"] is False
    due(api, post)
    assert api.app.state.scheduled_post_worker.publish_once() == 1
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    assert count(api, discussion_drafts) == 0
    published = api.c.get("/api/me/posts").json()["items"]
    assert len(published) == 1
    detail = api.c.get(f"/api/discussions/{published[0]['id']}").json()
    assert detail["bodyHtml"] == "<p><strong>A future post</strong></p>\n"
    assert detail["attachments"][0]["id"] == upload["attachmentId"]
    assert api.c.get(detail["attachments"][0]["downloadUrl"]).content == b"test"
    record = api.c.get(f"/api/scheduled-posts/{post['id']}").json()
    assert record["status"] == "published" and record["discussionId"] == detail["id"]
    assert api.c.delete(f"/api/scheduled-posts/{post['id']}").status_code == 409


def test_other_users_admins_and_guests_cannot_read_or_manage_schedules(api):
    setup_board(api)
    api.mkuser("writer")
    api.login("writer")
    post = schedule(api)
    api.login_dev()
    path = f"/api/scheduled-posts/{post['id']}"
    assert api.c.get("/api/scheduled-posts").json()["items"] == []
    assert api.c.get(path).status_code == 404
    assert api.c.patch(path, json={"scheduledAt": now_ms() + 7200_000}).status_code == 404
    assert api.c.delete(path).status_code == 404
    api.c.post("/api/auth/logout")
    for url in ["/api/scheduled-posts", path, "/api/me/posts"]:
        assert api.c.get(url).status_code == 401
    assert api.c.post("/api/scheduled-posts", json={"boardSlug": "scheduled", "bodyMarkdown": "test", "scheduledAt": now_ms() + 3600_000}).status_code == 401
    assert api.c.patch(path, json={"scheduledAt": now_ms() + 3600_000}).status_code == 401
    assert api.c.delete(path).status_code == 401


@pytest.mark.parametrize("changes", [
    {"scheduledAt": 1}, {"scheduledAt": -1}, {"scheduledAt": True}, {"scheduledAt": "123"},
    {"scheduledAt": now_ms() + 367 * 86400_000}, {"bodyMarkdown": "   "}, {"canModify": "false"},
    {"bodyFormat": "html"}, {"attachmentIds": [9999]},
    {"poll": {"question": "", "options": ["First", "Second"]}},
    {"poll": {"question": "Which?", "options": ["Same", "same"]}},
])
def test_invalid_schedule_is_rejected_atomically(api, changes):
    setup_board(api)
    response = api.c.post("/api/scheduled-posts", json={"boardSlug": "scheduled", "bodyMarkdown": "text", "scheduledAt": now_ms() + 3600_000, **changes})
    assert response.status_code == 422, response.text
    assert count(api, scheduled_posts) == count(api, discussion_drafts) == 0


@pytest.mark.parametrize("allow_multiple", [False, True])
def test_scheduled_poll_survives_cancellation_and_publication(api, allow_multiple):
    setup_board(api)
    poll = {"question": "Which?", "allowMultiple": allow_multiple, "options": ["First", "Second"]}
    post = schedule(api, poll=poll)
    response = api.c.delete(f"/api/scheduled-posts/{post['id']}")
    assert response.status_code == 200
    draft_id = response.json()["draftId"]
    draft = api.c.get(f"/api/drafts/{draft_id}").json()
    assert draft["poll"] == poll
    post = schedule(api, draftId=draft_id, poll=draft["poll"], canModify=False)
    due(api, post)
    assert api.app.state.scheduled_post_worker.publish_once() == 1
    published = api.c.get("/api/me/posts").json()["items"]
    detail = api.c.get(f"/api/discussions/{published[0]['id']}").json()
    assert detail["poll"]["question"] == poll["question"]
    assert detail["poll"]["allowMultiple"] is allow_multiple
    assert [option["label"] for option in detail["poll"]["options"]] == poll["options"]
    assert detail["poll"]["totalVoters"] == 0


def test_schedule_draft_preserves_original_on_failure_and_can_cancel_reschedule(api):
    setup_board(api)
    draft = api.c.post("/api/drafts", json={"bodyMarkdown": "original"}).json()
    assert api.c.post("/api/scheduled-posts", json={"draftId": draft["id"], "boardSlug": "missing", "bodyMarkdown": "replacement", "scheduledAt": now_ms() + 3600_000}).status_code == 404
    assert api.c.get(f"/api/drafts/{draft['id']}").json()["bodyMarkdown"] == "original"
    post = schedule(api, draftId=draft["id"], bodyMarkdown="replacement")
    assert count(api, discussion_drafts) == 1
    due(api, post)
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(boards).where(boards.c.slug == "scheduled").values(deleted_at=now_ms()))
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    assert api.c.get(f"/api/scheduled-posts/{post['id']}").json()["status"] == "failed"
    assert count(api, discussion_drafts) == 1
    assert count(api, discussions) == count(api, outbox_events) == 0
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(boards).where(boards.c.slug == "scheduled").values(deleted_at=None))
    assert api.c.patch(f"/api/scheduled-posts/{post['id']}", json={"scheduledAt": now_ms() + 3600_000}).status_code == 200
    due(api, post)
    assert api.app.state.scheduled_post_worker.publish_once() == 1


@pytest.mark.parametrize("change", ["account", "permission"])
def test_publication_rechecks_current_author_permissions(api, change):
    setup_board(api)
    api.mkuser("writer")
    api.login("writer")
    post = schedule(api, canModify=False)
    with api.app.state.db.request_conn() as conn:
        if change == "account":
            conn.execute(update(users).where(users.c.username == "writer").values(status="banned"))
        else:
            conn.execute(update(boards).where(boards.c.slug == "scheduled").values(posting_policy="members"))
    due(api, post)
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    assert count(api, discussions) == count(api, outbox_events) == 0
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(scheduled_posts.c.status)).scalar_one() == "failed"
        assert conn.execute(select(scheduled_posts.c.draft_id)).scalar_one() == post["draftId"]


def test_failed_publication_rolls_back_post_attachments_and_events(api, monkeypatch):
    setup_board(api)
    upload = api.c.post("/api/attachments/presign", json={"filename": "retry.txt", "mimeType": "text/plain", "sizeBytes": 4}).json()
    api.c.put(upload["uploadUrl"], content=b"test")
    post = schedule(api, attachmentIds=[upload["attachmentId"]])
    from samryetha.events.content_events import ContentEventService
    original = ContentEventService.publish_content

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("forced failure after event insertion")

    monkeypatch.setattr(ContentEventService, "publish_content", fail)
    due(api, post)
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    assert count(api, discussions) == count(api, outbox_events) == 0
    assert count(api, discussion_drafts) == 1
    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(attachments)).mappings().one()
        assert row["discussion_id"] is None and row["state"] == "uploaded"
    assert api.c.delete(f"/api/scheduled-posts/{post['id']}").status_code == 200
    assert api.c.get(f"/api/drafts/{post['draftId']}").json()["attachments"][0]["id"] == upload["attachmentId"]


def test_restarted_and_concurrent_workers_publish_exactly_once(api):
    setup_board(api)
    post = schedule(api)
    assert api.app.state.scheduled_post_worker.publish_once() == 0
    due(api, post)
    workers = [ScheduledPostWorker(api.app.state.db, api.settings, api.app.state.storage) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.publish_once(), workers))
    assert sum(results) == 1
    assert count(api, discussions) == count(api, outbox_events) == 1


@pytest.mark.parametrize("operation", ["cancel", "reschedule"])
def test_pending_worker_respects_concurrent_cancellation_or_time_change(api, monkeypatch, operation):
    setup_board(api)
    post = schedule(api)
    due(api, post)
    from samryetha.scheduled_posts.repository import ScheduledPostRepository
    original = ScheduledPostRepository.claim
    ready, proceed = Event(), Event()

    def claim(self, *args):
        ready.set()
        assert proceed.wait(10)
        return original(self, *args)

    monkeypatch.setattr(ScheduledPostRepository, "claim", claim)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(api.app.state.scheduled_post_worker.publish_once)
        assert ready.wait(10)
        try:
            path = f"/api/scheduled-posts/{post['id']}"
            response = api.c.delete(path) if operation == "cancel" else api.c.patch(path, json={"scheduledAt": now_ms() + 3600_000})
            assert response.status_code == 200, response.text
        finally:
            proceed.set()
        assert job.result(timeout=10) == 0
    assert count(api, discussions) == count(api, outbox_events) == 0


def test_pagination_and_private_own_posts_feed(api):
    setup_board(api)
    posts = [schedule(api, title=f"Post {i}") for i in range(3)]
    first = api.c.get("/api/scheduled-posts?limit=2").json()
    assert [row["id"] for row in first["items"]] == [posts[2]["id"], posts[1]["id"]]
    second = api.c.get(f"/api/scheduled-posts?cursor={first['nextCursor']}").json()
    assert [row["id"] for row in second["items"]] == [posts[0]["id"]]
    assert second["nextCursor"] is None
    for cursor in ["bad", "0", "-1", "999999999999999999999999", "1" * 5000, ""]:
        assert api.c.get(f"/api/scheduled-posts?cursor={cursor}").status_code == 400
    api.c.post("/api/discussions", json={"boardSlug": "scheduled", "bodyMarkdown": "Dev post"})
    api.mkuser("writer")
    api.login("writer")
    assert api.c.get("/api/me/posts").json()["items"] == []
    own = api.c.post("/api/discussions", json={"boardSlug": "scheduled", "bodyMarkdown": "Writer post"}).json()
    assert [row["id"] for row in api.c.get("/api/me/posts").json()["items"]] == [own["id"]]
