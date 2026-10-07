"""Model latency must not block unrelated writes or overwrite newer edits."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from test_automod import am, automod_app, _board, _post
from samryetha import automod
from samryetha.automod.providers import LLMVerdict
from samryetha.core.db import now_ms
from samryetha.core.schema import bans, discussions, direct_messages, moderation_queue, replies, users
from samryetha.auth.security import SESSION_COOKIE, create_session


def _setup(am):
    slug = _board(am)
    am.mkuser("slowwriter")
    am.mkuser("otherwriter")
    am.login("slowwriter")
    did = _post(am, slug, "Original body.")["id"]
    reply = am.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "Original reply."})
    assert reply.status_code == 201
    with am.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "otherwriter")).scalar_one()
        token = create_session(conn, uid)[0]
    return slug, did, reply.json()["id"], token


def _operation(kind, slug, did, rid):
    return {
        "post": ("POST", "/api/discussions", {"boardSlug": slug, "title": "Slow", "bodyMarkdown": "slow body"}),
        "reply": ("POST", f"/api/discussions/{did}/replies", {"bodyMarkdown": "slow reply"}),
        "edit_post": ("PATCH", f"/api/discussions/{did}", {"bodyMarkdown": "slow edit"}),
        "edit_reply": ("PATCH", f"/api/replies/{rid}", {"bodyMarkdown": "slow edit"}),
        "message": ("POST", "/api/messages", {"username": "otherwriter", "body": "slow message"}),
        "profile": ("PATCH", "/api/me/profile", {"bio": "slow profile"}),
    }[kind]


def _blocked_provider(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class Provider:
        def classify(self, text, *, context="post", recheck=False):
            if "slow " in text:
                entered.set()
                assert release.wait(10), "test failed to release the model"
            return LLMVerdict(risk=0, category="none", reason="test allow")

    monkeypatch.setattr("samryetha.automod.service._provider_for", lambda settings: Provider())
    return entered, release


@pytest.mark.parametrize("kind", ["post", "reply", "edit_post", "edit_reply", "message", "profile"])
def test_model_wait_allows_another_account_to_write(am, monkeypatch, kind):
    slug, did, rid, token = _setup(am)
    entered, release = _blocked_provider(monkeypatch)
    method, url, data = _operation(kind, slug, did, rid)
    result = {}
    completed = threading.Event()
    slow = threading.Thread(target=lambda: result.update(slow=am.c.request(method, url, json=data)))
    slow.start()
    other = TestClient(am.app, raise_server_exceptions=False)
    other.cookies.set(SESSION_COOKIE, token)

    def write():
        result["other"] = other.post("/api/drafts", json={"title": "Independent draft"})
        completed.set()

    writer = threading.Thread(target=write)
    try:
        assert entered.wait(3)
        writer.start()
        assert completed.wait(2), "another writer was blocked while the provider was waiting"
        assert result["other"].status_code == 201
    finally:
        release.set()
        slow.join(10)
        if writer.ident is not None:
            writer.join(10)
        other.close()
    assert result["slow"].status_code == (201 if method == "POST" else 200), result["slow"].text


@pytest.mark.parametrize("kind", ["edit_post", "edit_reply", "profile"])
def test_slow_edit_cannot_overwrite_a_newer_version(am, monkeypatch, kind):
    slug, did, rid, _ = _setup(am)
    entered, release = _blocked_provider(monkeypatch)
    method, url, data = _operation(kind, slug, did, rid)
    result = {}
    slow = threading.Thread(target=lambda: result.update(slow=am.c.request(method, url, json=data)))
    slow.start()
    try:
        assert entered.wait(3)
        field = "bio" if kind == "profile" else "bodyMarkdown"
        winner = am.c.patch(url, json={field: "Newer accepted version"})
        assert winner.status_code == 200, winner.text
    finally:
        release.set()
        slow.join(10)
    assert result["slow"].status_code == 409, result["slow"].text
    with am.app.state.db.request_conn() as conn:
        if kind == "profile":
            actual = conn.execute(select(users.c.bio).where(users.c.username == "slowwriter")).scalar_one()
        elif kind == "edit_post":
            actual = conn.execute(select(discussions.c.body_md).where(discussions.c.id == did)).scalar_one()
        else:
            actual = conn.execute(select(replies.c.body_md).where(replies.c.id == rid)).scalar_one()
    assert actual == "Newer accepted version"


def test_legacy_recheck_batch_does_not_hold_a_write_lock(am, monkeypatch):
    _, did, _, token = _setup(am)
    other_did = _post(am, "general", "slow legacy content")["id"]
    with am.app.state.db.request_conn() as conn:
        uid = conn.execute(select(discussions.c.author_id).where(discussions.c.id == did)).scalar_one()
        for content_id in (did, other_did):
            conn.execute(update(discussions).where(discussions.c.id == content_id).values(moderation_status="pending"))
            conn.execute(
                moderation_queue.insert().values(
                    content_type="discussion",
                    content_id=content_id,
                    author_id=uid,
                    excerpt="legacy",
                    decision="review",
                    score=50,
                    signals="[]",
                    review_state="pending",
                    created_at=now_ms(),
                    hold_until=1,
                )
            )
    entered, release = _blocked_provider(monkeypatch)
    from samryetha.automod.worker import finalize_once

    result = {}
    slow = threading.Thread(target=lambda: result.update(finalized=finalize_once(am.app.state.db, am.settings)))
    slow.start()
    try:
        assert entered.wait(3)
        with TestClient(am.app) as other:
            other.cookies.set(SESSION_COOKIE, token)
            response = other.post("/api/drafts", json={"title": "Independent draft"})
            assert response.status_code == 201
    finally:
        release.set()
        slow.join(10)
    assert len(result["finalized"]) == 2


def test_draft_changed_during_review_is_preserved(am, monkeypatch):
    slug, _, _, _ = _setup(am)
    saved = am.c.post("/api/drafts", json={"title": "Draft", "bodyMarkdown": "slow draft"})
    assert saved.status_code == 201
    draft_id = saved.json()["id"]
    entered, release = _blocked_provider(monkeypatch)
    result = {}
    slow = threading.Thread(
        target=lambda: result.update(
            published=am.c.post(
                "/api/discussions",
                json={
                    "boardSlug": slug,
                    "title": "Draft",
                    "bodyMarkdown": "slow draft",
                    "draftId": draft_id,
                },
            )
        )
    )
    slow.start()
    try:
        assert entered.wait(3)
        newer = am.c.put(f"/api/drafts/{draft_id}", json={"title": "Newer", "bodyMarkdown": "Keep this"})
        assert newer.status_code == 200
    finally:
        release.set()
        slow.join(10)
    assert result["published"].status_code == 409
    assert am.c.get(f"/api/drafts/{draft_id}").json()["bodyMarkdown"] == "Keep this"
    with am.app.state.db.request_conn() as conn:
        assert not conn.execute(select(discussions.c.id).where(discussions.c.title == "Draft")).first()


@pytest.mark.parametrize("kind", ["post", "reply", "edit_post", "edit_reply", "message", "profile"])
def test_account_banned_during_model_wait_cannot_write(am, monkeypatch, kind):
    slug, did, rid, _ = _setup(am)
    entered, release = _blocked_provider(monkeypatch)
    method, url, data = _operation(kind, slug, did, rid)
    result = {}
    with am.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "slowwriter")).scalar_one()
        counts = [
            conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in (discussions, replies, direct_messages, moderation_queue)
        ]
    slow = threading.Thread(target=lambda: result.update(slow=am.c.request(method, url, json=data)))
    slow.start()
    try:
        assert entered.wait(3)
        with am.app.state.db.request_conn() as conn:
            conn.execute(update(users).where(users.c.id == uid).values(status="banned"))
    finally:
        release.set()
        slow.join(10)
    assert result["slow"].status_code == 409, result["slow"].text
    with am.app.state.db.request_conn() as conn:
        assert counts == [
            conn.execute(select(func.count()).select_from(t)).scalar_one()
            for t in (discussions, replies, direct_messages, moderation_queue)
        ]
        assert (
            conn.execute(select(discussions.c.body_md).where(discussions.c.id == did)).scalar_one() == "Original body."
        )
        assert conn.execute(select(replies.c.body_md).where(replies.c.id == rid)).scalar_one() == "Original reply."
        assert conn.execute(select(users.c.bio).where(users.c.id == uid)).scalar_one() != "slow profile"


def test_expired_ban_maintenance_does_not_hold_model_write_lock(am, monkeypatch):
    slug, did, rid, token = _setup(am)
    with am.app.state.db.request_conn() as conn:
        uid = conn.execute(select(users.c.id).where(users.c.username == "slowwriter")).scalar_one()
        conn.execute(update(users).where(users.c.id == uid).values(status="banned"))
        conn.execute(bans.insert().values(user_id=uid, banned_by_user_id=uid, banned_until=1, created_at=1))
    # A surviving session triggers lazy unban in the authentication dependency.
    _assert_existing_model_wait_allows_writer(am, monkeypatch, slug, did, rid, token)


def _assert_existing_model_wait_allows_writer(am, monkeypatch, slug, did, rid, token):
    entered, release = _blocked_provider(monkeypatch)
    result = {}
    slow = threading.Thread(
        target=lambda: result.update(
            slow=am.c.post(
                "/api/discussions",
                json={
                    "boardSlug": slug,
                    "title": "Slow",
                    "bodyMarkdown": "slow body",
                },
            )
        )
    )
    slow.start()
    try:
        assert entered.wait(3)
        with TestClient(am.app) as other:
            other.cookies.set(SESSION_COOKIE, token)
            response = other.post("/api/drafts", json={"title": "Independent draft"})
            assert response.status_code == 201
    finally:
        release.set()
        slow.join(10)
    assert result["slow"].status_code == 201, result["slow"].text
