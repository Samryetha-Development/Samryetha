"""Account-specific report hiding and atomic administrator decisions."""
import pytest
from sqlalchemy import func, inspect, select, update
from samryetha.core.schema import bans, users
from samryetha.discussions.service import DiscussionService


def setup_content(api, visibility="public", attachment=False):
    api.login_dev()
    assert api.c.post("/api/boards", json={"slug": "reports", "name": "Reports", "visibility": visibility, "postingPolicy": "everyone"}).status_code == 201
    for name in ("reportauthor", "reportreader", "reportother"):
        api.mkuser(name)
        if visibility != "public":
            api.login(name)
            assert api.c.post("/api/boards/reports/join").status_code == 200
    api.login("reportauthor")
    upload = None
    if attachment:
        upload = api.c.post("/api/attachments/presign", json={"filename": "report.txt", "mimeType": "text/plain", "sizeBytes": 4}).json()
        assert api.c.put(upload["uploadUrl"], content=b"test").status_code == 204
    post = api.c.post("/api/discussions", json={"boardSlug": "reports", "title": "Reported searchable post", "bodyMarkdown": "Reported post body", "attachmentIds": [upload["attachmentId"]] if upload else []})
    assert post.status_code == 201, post.text
    did = post.json()["id"]
    download = post.json()["attachments"][0]["downloadUrl"] if upload else None
    api.login("reportreader")
    assert api.c.post(f"/api/discussions/{did}/save").status_code == 200
    assert api.c.post(f"/api/discussions/{did}/follow").status_code == 200
    api.login("reportauthor")
    reply = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "Reported comment body"})
    assert reply.status_code == 201, reply.text
    api.app.state.flush_outbox()
    api.login("reportreader")
    return did, reply.json()["id"], download


def report(api, target_type, target_id, reason="spam"):
    return api.c.post("/api/moderation/reports", json={"reportableType": target_type, "reportableId": target_id, "reason": reason})


def review(api, report_id, action):
    return api.c.post(f"/api/moderation/reports/{report_id}/review", json={"action": action})


def ids(response):
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_post_report_hides_all_reader_surfaces_and_survives_dismissal(api):
    did, rid, download = setup_content(api, attachment=True)
    assert api.c.get("/api/notifications/unread-count").json()["unreadCount"] == 1
    first = report(api, "discussion", did)
    assert first.status_code == 201, first.text
    duplicate = report(api, "discussion", did)
    assert duplicate.status_code == 201 and duplicate.json()["id"] == first.json()["id"]
    api.login_dev()
    assert review(api, first.json()["id"], "dismiss").status_code == 200
    api.login("reportreader")
    for path in (f"/api/discussions/{did}", f"/api/discussions/{did}/replies", download):
        assert api.c.get(path).status_code == 404
    for path in ("/api/discussions", "/api/discussions?feed=followed", "/api/discussions?board=reports", "/api/users/reportauthor/posts", "/api/users/reportreader/saved", "/api/users/reportauthor/replies"):
        assert ids(api.c.get(path)) == []
    search = api.c.get("/api/search?q=Reported").json()
    assert search["items"] == [] and search["total"] == 0
    assert api.c.get("/api/notifications").json()["items"] == []
    assert api.c.get("/api/notifications/unread-count").json()["unreadCount"] == 0
    api.login("reportother")
    assert api.c.get(f"/api/discussions/{did}").status_code == 200
    assert ids(api.c.get(f"/api/discussions/{did}/replies")) == [rid]
    assert api.c.get(download).status_code == 200
    api.c.cookies.clear()
    assert api.c.get(f"/api/discussions/{did}").status_code == 200


def test_reply_report_hides_only_that_comment_and_notifications(api):
    did, rid, _ = setup_content(api)
    api.login("reportauthor")
    child = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "Visible child", "parentReplyId": rid}).json()["id"]
    api.login("reportreader")
    assert report(api, "reply", rid).status_code == 201
    assert api.c.get(f"/api/discussions/{did}").status_code == 200
    assert ids(api.c.get(f"/api/discussions/{did}/replies")) == [child]
    assert ids(api.c.get("/api/users/reportauthor/replies")) == [child]
    assert api.c.get("/api/notifications").json()["items"] == []
    api.login("reportother")
    assert ids(api.c.get(f"/api/discussions/{did}/replies")) == [rid, child]


@pytest.mark.parametrize("target_type", ["discussion", "reply"])
def test_delete_closes_related_reports_and_cannot_decrement_twice(api, target_type):
    did, rid, _ = setup_content(api)
    target_id = did if target_type == "discussion" else rid
    first = report(api, target_type, target_id).json()
    api.login("reportother")
    second = report(api, target_type, target_id).json()
    api.login_dev()
    target = api.c.get("/api/moderation/reports?pendingOnly=true").json()["items"][0]["target"]
    assert target["bodyMarkdown"] == ("Reported post body" if target_type == "discussion" else "Reported comment body")
    assert target["author"]["username"] == "reportauthor"
    result = review(api, first["id"], "delete")
    assert result.status_code == 200, result.text
    assert result.json()["target"]["isDeleted"] is True
    assert ids(api.c.get("/api/moderation/reports?pendingOnly=true")) == []
    assert review(api, first["id"], "delete").status_code == 409
    assert review(api, second["id"], "delete").status_code == 409
    api.login("reportauthor")
    if target_type == "discussion":
        assert api.c.get(f"/api/discussions/{did}").status_code == 404
    else:
        assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 0
        assert api.c.get(f"/api/discussions/{did}/replies").json()["items"][0]["isDeleted"] is True
    api.login_dev()
    log = api.c.get("/api/moderation/actions").json()["items"]
    assert len([a for a in log if a["action"] == "report.delete"]) == 1


@pytest.mark.parametrize("target_type", ["discussion", "reply"])
def test_ban_targets_content_author_and_revokes_sessions(api, target_type):
    did, rid, _ = setup_content(api)
    rep = report(api, target_type, did if target_type == "discussion" else rid).json()
    api.login_dev()
    assert review(api, rep["id"], "ban").status_code == 200
    assert api.c.post("/api/auth/login", json={"username": "reportauthor", "password": "password123"}).status_code == 403
    assert review(api, rep["id"], "ban").status_code == 409
    with api.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "reportauthor")).scalar_one()
        assert conn.execute(select(func.count()).select_from(bans).where(bans.c.user_id == uid)).scalar_one() == 1


def test_failed_admin_ban_keeps_report_pending(api):
    did, _, _ = setup_content(api)
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "reportauthor").values(role="admin"))
    rep = report(api, "discussion", did).json()
    api.login_dev()
    assert review(api, rep["id"], "ban").status_code == 409
    assert ids(api.c.get("/api/moderation/reports?pendingOnly=true")) == [rep["id"]]
    assert api.c.get("/api/moderation/actions").json()["items"] == []


def test_failed_delete_rolls_back_content_report_and_audit(api, monkeypatch):
    did, _, _ = setup_content(api)
    rep = report(api, "discussion", did).json()
    original = DiscussionService.delete
    def failing_delete(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("simulated downstream failure")
    monkeypatch.setattr(DiscussionService, "delete", failing_delete)
    api.login_dev()
    with pytest.raises(RuntimeError, match="simulated downstream failure"):
        review(api, rep["id"], "delete")
    assert api.c.get(f"/api/discussions/{did}").status_code == 200
    assert ids(api.c.get("/api/moderation/reports?pendingOnly=true")) == [rep["id"]]
    assert api.c.get("/api/moderation/actions").json()["items"] == []


def test_validation_and_authorization(api):
    did, rid, _ = setup_content(api, visibility="members")
    api.mkuser("outsider")
    api.login("outsider")
    for kind, target in (("discussion", did), ("reply", rid), ("discussion", 999999), ("reply", 999999)):
        assert report(api, kind, target).status_code == 404
    assert report(api, "discussion", did, " ").status_code == 400
    assert report(api, "discussion", did, "x" * 2001).status_code == 422
    api.login("reportreader")
    rep = report(api, "discussion", did).json()
    assert review(api, rep["id"], "delete").status_code == 403
    assert api.c.get("/api/moderation/reports").status_code == 403
    api.c.cookies.clear()
    assert report(api, "discussion", did).status_code == 401
    assert review(api, rep["id"], "delete").status_code == 401


def test_pending_queue_includes_in_progress_and_paginates(api):
    did, rid, _ = setup_content(api)
    first = report(api, "discussion", did).json()
    api.login("reportother")
    second = report(api, "reply", rid).json()
    api.login_dev()
    assert api.c.patch(f"/api/moderation/reports/{first['id']}", json={"status": "in_progress"}).status_code == 200
    page = api.c.get("/api/moderation/reports?pendingOnly=true&limit=1").json()
    assert [r["id"] for r in page["items"]] == [second["id"]]
    assert page["nextCursor"] == second["id"]
    assert ids(api.c.get(f"/api/moderation/reports?pendingOnly=true&cursor={page['nextCursor']}&limit=1")) == [first["id"]]
    assert review(api, first["id"], "dismiss").status_code == 200
    assert ids(api.c.get("/api/moderation/reports?pendingOnly=true")) == [second["id"]]


def test_report_filter_precedes_feed_pagination(api):
    did, _, _ = setup_content(api)
    api.login("reportauthor")
    newer = api.c.post("/api/discussions", json={"boardSlug": "reports", "title": "Newer", "bodyMarkdown": "Newer"}).json()["id"]
    api.login("reportreader")
    assert report(api, "discussion", newer).status_code == 201
    page = api.c.get("/api/discussions?limit=1").json()
    assert [r["id"] for r in page["items"]] == [did]
    assert page["nextCursor"] is None


def test_existing_database_gains_reporter_target_index(db):
    with db.request_conn() as conn:
        conn.exec_driver_sql("DROP INDEX reports_reporter_target_idx")
    db.ensure_schema_drift()
    db.ensure_schema_drift()
    assert "reports_reporter_target_idx" in {i["name"] for i in inspect(db.engine).get_indexes("reports")}
