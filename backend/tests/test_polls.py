"""Poll publication, atomic ballots, aggregate accuracy, and post visibility."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from samryetha.core.schema import discussion_drafts, discussion_polls, discussions, poll_options, poll_votes, users

POLL = {"question": "Which day?", "allowMultiple": False, "options": ["Saturday", "Sunday", "Either"]}


def setup_poll(api, multiple=False, visibility="public"):
    api.login_dev()
    result = api.c.post("/api/boards", json={"name": "Polls", "slug": "polls", "visibility": visibility})
    assert result.status_code == 201, result.text
    result = api.c.post("/api/discussions", json={"boardSlug": "polls", "bodyMarkdown": "Pick a day", "poll": {**POLL, "allowMultiple": multiple}})
    assert result.status_code == 201, result.text
    return result.json()


def ballot(api, discussion, *indexes):
    return api.c.put(f"/api/discussions/{discussion['id']}/poll/vote", json={"optionIds": [discussion["poll"]["options"][i]["id"] for i in indexes]})


def test_single_choice_repeat_change_and_public_statistics(api):
    d = setup_poll(api)
    assert d["poll"]["totalVoters"] == d["poll"]["totalVotes"] == 0
    assert d["poll"]["viewerOptionIds"] == [] and d["poll"]["canVote"]
    assert ballot(api, d, 0, 1).status_code == 422
    first = ballot(api, d, 0)
    assert first.status_code == 200, first.text
    assert first.json()["totalVoters"] == first.json()["totalVotes"] == 1
    assert ballot(api, d, 0).json()["totalVotes"] == 1
    changed = ballot(api, d, 2).json()
    assert [o["voteCount"] for o in changed["options"]] == [0, 0, 1]
    assert changed["totalVoters"] == 1
    api.mkuser("voter")
    api.login("voter")
    assert ballot(api, d, 2).json()["totalVoters"] == 2
    api.c.post("/api/auth/logout")
    guest = api.c.get(f"/api/discussions/{d['id']}").json()["poll"]
    assert guest["totalVotes"] == guest["totalVoters"] == 2
    assert guest["viewerOptionIds"] == [] and guest["canVote"] is False
    assert ballot(api, d, 1).status_code == 401
    assert set(guest) == {"question", "allowMultiple", "options", "totalVoters", "totalVotes", "viewerOptionIds", "canVote"}


def test_multiple_choice_unique_voters_and_replacement(api):
    d = setup_poll(api, multiple=True)
    first = ballot(api, d, 0, 1)
    assert first.status_code == 200, first.text
    assert first.json()["totalVoters"] == 1 and first.json()["totalVotes"] == 2
    api.mkuser("multi")
    api.login("multi")
    second = ballot(api, d, 1, 2).json()
    assert second["totalVoters"] == 2 and second["totalVotes"] == 4
    assert [o["voteCount"] for o in second["options"]] == [1, 2, 1]
    changed = ballot(api, d, 0).json()
    assert changed["totalVoters"] == 2 and changed["totalVotes"] == 3
    assert [o["voteCount"] for o in changed["options"]] == [2, 1, 0]


@pytest.mark.parametrize("poll", [
    {**POLL, "question": " "}, {**POLL, "question": "x" * 201},
    {**POLL, "options": ["Only one"]}, {**POLL, "options": ["x"] * 11},
    {**POLL, "options": ["x", " "]}, {**POLL, "options": ["x", "x" * 101]},
    {**POLL, "options": [" Saturday ", "saturday"]}, {**POLL, "allowMultiple": "false"},
])
def test_invalid_poll_never_publishes(api, poll):
    setup_poll(api)
    r = api.c.post("/api/discussions", json={"boardSlug": "polls", "bodyMarkdown": "body", "poll": poll})
    assert r.status_code == 422, r.text
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(func.count()).select_from(discussions)).scalar_one() == 1


def test_invalid_ballot_keeps_existing_vote(api):
    d = setup_poll(api)
    other = api.c.post("/api/discussions", json={"boardSlug": "polls", "bodyMarkdown": "other", "poll": POLL}).json()
    chosen = d["poll"]["options"][0]["id"]
    assert ballot(api, d, 0).status_code == 200
    for ids in ([], [chosen, chosen], [chosen, 999999], [other["poll"]["options"][0]["id"]], [True], ["1"]):
        r = api.c.put(f"/api/discussions/{d['id']}/poll/vote", json={"optionIds": ids})
        assert r.status_code == 422, r.text
    poll = api.c.get(f"/api/discussions/{d['id']}").json()["poll"]
    assert poll["viewerOptionIds"] == [chosen] and poll["totalVoters"] == poll["totalVotes"] == 1
    no_poll = api.c.post("/api/discussions", json={"boardSlug": "polls", "bodyMarkdown": "no poll"}).json()
    assert no_poll["poll"] is None
    assert api.c.put(f"/api/discussions/{no_poll['id']}/poll/vote", json={"optionIds": [chosen]}).status_code == 404


def test_edit_before_voting_then_freeze_configuration(api):
    d = setup_poll(api)
    path = f"/api/discussions/{d['id']}"
    changed_poll = {**POLL, "question": "When?", "allowMultiple": True}
    changed = api.c.patch(path, json={"poll": changed_poll})
    assert changed.status_code == 200, changed.text
    assert ballot(api, d, 0).status_code == 422  # Never reinterpret a stale option ID.
    d = changed.json()
    assert ballot(api, d, 0, 1).status_code == 200
    for patch in ({"poll": None}, {"poll": POLL}, {"title": "Must roll back", "poll": {**changed_poll, "question": "New?"}}):
        r = api.c.patch(path, json=patch)
        assert r.status_code == 409, r.text
    unchanged = api.c.get(path).json()
    assert unchanged["title"] != "Must roll back"
    saved = api.c.patch(path, json={"bodyMarkdown": "Edited body", "poll": changed_poll})
    assert saved.status_code == 200, saved.text
    assert saved.json()["poll"] == unchanged["poll"]
    assert api.c.patch(path, json={"bodyMarkdown": "Another edit"}).json()["poll"] == unchanged["poll"]
    api.mkuser("outsider")
    api.login("outsider")
    assert api.c.patch(path, json={"poll": None}).status_code == 403


def test_add_and_remove_poll_on_existing_post(api):
    d = setup_poll(api)
    path = f"/api/discussions/{d['id']}"
    assert api.c.patch(path, json={"poll": None}).json()["poll"] is None
    assert api.c.patch(path, json={"poll": POLL}).json()["poll"]["question"] == POLL["question"]


@pytest.mark.parametrize("visibility", ["private", "members"])
def test_board_permissions_apply_to_ballots(api, visibility):
    d = setup_poll(api, visibility=visibility)
    api.mkuser("outside")
    api.login("outside")
    assert api.c.get(f"/api/discussions/{d['id']}").status_code == 403
    assert ballot(api, d, 0).status_code == 403
    api.login_dev()
    assert api.c.get(f"/api/discussions/{d['id']}").json()["poll"]["totalVoters"] == 0


def test_locked_deleted_reported_and_inactive_cannot_vote(api):
    d = setup_poll(api)
    path = f"/api/discussions/{d['id']}"
    assert api.c.post(path + "/lock").status_code == 200
    assert ballot(api, d, 0).status_code == 403  # Administrators also respect closure.
    assert api.c.get(path).json()["poll"]["canVote"] is False
    assert api.c.post(path + "/lock").status_code == 200
    assert ballot(api, d, 0).status_code == 200
    api.mkuser("reporter")
    api.login("reporter")
    report = api.c.post("/api/moderation/reports", json={"reportableType": "discussion", "reportableId": d["id"], "reason": "Hide this"})
    assert report.status_code == 201, report.text
    assert ballot(api, d, 1).status_code == 404
    api.login_dev()
    for status in ("pending", "banned", "deactivated"):
        with api.app.state.db.request_conn() as conn:
            conn.execute(update(users).where(users.c.username == "dev").values(status=status))
        assert ballot(api, d, 1).status_code in (401, 403)
        with api.app.state.db.request_conn() as conn:
            conn.execute(update(users).where(users.c.username == "dev").values(status="active"))
        api.login_dev()
    assert api.c.delete(path).status_code == 200
    assert ballot(api, d, 1).status_code == 404


def test_draft_preserves_partial_poll_and_atomic_publish(api):
    setup_poll(api)
    partial = {"question": "", "allowMultiple": True, "options": ["Saturday", ""]}
    draft = api.c.post("/api/drafts", json={"poll": partial}).json()
    assert draft["poll"] == partial
    path = f"/api/drafts/{draft['id']}"
    assert api.c.get(path).json()["poll"] == partial
    updated = api.c.put(path, json={"poll": POLL})
    assert updated.status_code == 200, updated.text
    assert updated.json()["poll"] == POLL
    command = {"draftId": draft["id"], "boardSlug": "polls", "bodyMarkdown": "Final", "poll": POLL}
    assert api.c.post("/api/discussions", json={**command, "attachmentIds": [999999]}).status_code == 422
    assert api.c.get(path).json()["poll"] == POLL
    published = api.c.post("/api/discussions", json=command)
    assert published.status_code == 201, published.text
    assert published.json()["poll"]["question"] == POLL["question"]
    assert api.c.get(path).status_code == 404


def test_same_account_concurrent_ballots_do_not_double_count(api):
    d = setup_poll(api, multiple=True)
    cookies = dict(api.c.cookies)
    ids = [o["id"] for o in d["poll"]["options"]]
    def submit(choices):
        with TestClient(api.app) as client:
            client.cookies.update(cookies)
            return client.put(f"/api/discussions/{d['id']}/poll/vote", json={"optionIds": choices})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, ([ids[0], ids[1]], [ids[2]])))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    poll = api.c.get(f"/api/discussions/{d['id']}").json()["poll"]
    assert poll["totalVoters"] == 1 and poll["viewerOptionIds"] in ([ids[0], ids[1]], [ids[2]])
    assert poll["totalVotes"] == len(poll["viewerOptionIds"])


def test_existing_database_upgrade_preserves_drafts(tmp_path):
    from samryetha.core.db import Database
    db = Database(str(tmp_path / "legacy.db"))
    with db.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE discussion_drafts (id INTEGER PRIMARY KEY, author_id INTEGER NOT NULL, board_slug TEXT, title TEXT NOT NULL DEFAULT '', body_md TEXT NOT NULL DEFAULT '', body_format TEXT NOT NULL DEFAULT 'text', created_at BIGINT, updated_at BIGINT)")
        conn.exec_driver_sql("INSERT INTO discussion_drafts (id, author_id, title) VALUES (1, 1, 'Keep me')")
    for _ in range(2):
        db.create_schema()
        db.ensure_schema_drift()
    with db.request_conn() as conn:
        row = conn.execute(select(discussion_drafts)).mappings().one()
        assert row["title"] == "Keep me" and row["poll_json"] is None
        for table in (discussion_polls, poll_options, poll_votes):
            assert conn.execute(select(func.count()).select_from(table)).scalar_one() == 0
    db.close()
