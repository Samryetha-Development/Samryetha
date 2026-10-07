"""Signed attachment URLs must honor subsequent moderation decisions."""

import pytest
from sqlalchemy import select

from test_automod import am, automod_app, _board
from samryetha import automod
from samryetha.automod.providers import LLMVerdict
from samryetha.schema import attachments, moderation_queue


def _upload(am):
    response = am.c.post(
        "/api/attachments/presign",
        json={
            "filename": "retained.txt",
            "mimeType": "text/plain",
            "sizeBytes": 12,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert am.c.put(data["uploadUrl"], content=b"AUDIT_SECRET").status_code == 204
    metadata = am.c.get(f"/api/attachments/{data['attachmentId']}").json()
    return data["attachmentId"], metadata["downloadUrl"]


def _post(am, slug, aid, body):
    response = am.c.post(
        "/api/discussions",
        json={
            "boardSlug": slug,
            "title": "Attachment review",
            "bodyMarkdown": body,
            "attachmentIds": [aid],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _queue_id(am, did):
    with am.app.state.db.request_conn() as conn:
        return conn.execute(
            select(moderation_queue.c.id).where(
                moderation_queue.c.content_type == "discussion",
                moderation_queue.c.content_id == did,
                moderation_queue.c.superseded_at.is_(None),
            )
        ).scalar_one()


@pytest.mark.parametrize("initial", ["machine_rejected", "pending"])
def test_held_attachment_urls_require_parent_read_access_and_preserve_files(am, monkeypatch, initial):
    slug = _board(am)
    am.mkuser("attachmentwriter")
    am.mkuser("attachmentreader")
    am.login("attachmentwriter")
    aid, old_url = _upload(am)
    if initial == "pending":

        class ReviewProvider:
            def classify(self, text, *, context="post", recheck=False):
                return LLMVerdict(risk=60, category="spam", reason="test review")

        monkeypatch.setattr("samryetha.automod.service._provider_for", lambda settings: ReviewProvider())
    body = "A review sample" if initial == "pending" else "傻逼"
    created = _post(am, slug, aid, body)
    did = created["id"]
    assert created["moderationStatus"] == ("pending" if initial == "pending" else "rejected")
    owner_metadata = am.c.get(f"/api/attachments/{aid}")
    assert owner_metadata.status_code == (200 if initial == "pending" else 404)
    assert am.c.get(old_url).status_code == (200 if initial == "pending" else 404)
    if initial == "machine_rejected":
        assert created["attachments"] == []
        assert am.c.delete(f"/api/attachments/{aid}").status_code == 404
    am.c.cookies.clear()
    assert am.c.get(old_url).status_code == 404
    am.login("attachmentreader")
    assert am.c.get(old_url).status_code == 404
    am.login_dev()
    metadata = am.c.get(f"/api/attachments/{aid}")
    assert metadata.status_code == 200
    admin_url = metadata.json()["downloadUrl"]
    download = am.c.get(admin_url)
    assert download.status_code == 200 and download.content == b"AUDIT_SECRET"
    assert download.headers["cache-control"] == "private, no-store"
    am.c.cookies.clear()
    assert am.c.get(admin_url).status_code == 404
    # Attached retained files must not become eligible for orphan cleanup.
    assert am.app.state.reap_attachment_orphans(older_than_ms=-1) == 0
    with am.app.state.db.request_conn() as conn:
        assert conn.execute(select(attachments.c.state).where(attachments.c.id == aid)).scalar_one() == "attached"
    am.login_dev()
    queue_id = _queue_id(am, did)
    assert am.c.post(f"/api/admin/moderation/queue/{queue_id}/approve", json={"note": "Allow"}).status_code == 200
    am.c.cookies.clear()
    assert am.c.get(old_url).status_code == 200


def test_manual_rejection_revokes_existing_signed_urls(am):
    slug = _board(am)
    am.mkuser("attachmentwriter")
    am.login("attachmentwriter")
    aid, old_url = _upload(am)
    created = _post(am, slug, aid, "Normal content")
    # A normal approved attachment keeps the original bearer URL behavior.
    am.c.cookies.clear()
    assert am.c.get(old_url).status_code == 200
    # Manual moderation may act on a queued historical/flagged approved post.
    from samryetha.automod import apply_review_state, CONTENT_DISCUSSION

    with am.app.state.db.request_conn() as conn:
        apply_review_state(conn, content_type=CONTENT_DISCUSSION, content_id=created["id"], status="rejected")
    assert am.c.get(old_url).status_code == 404
    am.login("attachmentwriter")
    assert am.c.get(f"/api/attachments/{aid}").status_code == 404
    assert am.c.get(f"/api/discussions/{created['id']}").status_code == 404
    am.login_dev()
    assert am.c.get(old_url).content == b"AUDIT_SECRET"
