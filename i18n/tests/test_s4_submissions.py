"""翻译提交与审核流程测试。"""

from __future__ import annotations


# ---------------------------------------------------------------- POST /api/submissions

def test_create_submission_requires_login(client):
    r = client.post("/api/submissions", json={"locale": "zh-CN", "key": "nav.home", "value": "首页"})
    assert r.status_code == 401


def test_create_submission_success(api):
    token = api.login_as("user1")
    r = api.post(
        "/api/submissions",
        token=token,
        json={"locale": "zh-CN", "key": "nav.home", "value": "首页", "note": "来自用户建议"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["locale"] == "zh-CN"
    assert body["key"] == "nav.home"
    assert body["value"] == "首页"
    assert body["status"] == "pending"
    assert body["note"] == "来自用户建议"


def test_create_submission_invalid_locale(api):
    token = api.login_as("user2")
    r = api.post(
        "/api/submissions",
        token=token,
        json={"locale": "ja-JP", "key": "nav.home", "value": "ホーム"},
    )
    assert r.status_code == 400


def test_create_submission_invalid_key(api):
    token = api.login_as("user3")
    r = api.post(
        "/api/submissions",
        token=token,
        json={"locale": "zh-CN", "key": "bad key!", "value": "x"},
    )
    assert r.status_code == 400


def test_create_submission_empty_value(api):
    token = api.login_as("user4")
    r = api.post(
        "/api/submissions",
        token=token,
        json={"locale": "zh-CN", "key": "nav.home", "value": ""},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------- GET /api/submissions

def test_list_submissions_own_only(api):
    t1 = api.login_as("user_a")
    t2 = api.login_as("user_b")

    api.post("/api/submissions", token=t1, json={"locale": "zh-CN", "key": "k.a", "value": "A"})
    api.post("/api/submissions", token=t2, json={"locale": "zh-CN", "key": "k.b", "value": "B"})

    r = api.get("/api/submissions", token=t1)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["submissions"][0]["key"] == "k.a"


def test_admin_sees_all_submissions(api):
    t_user = api.login_as("user_c")
    t_admin = api.login_as("admin_list", role="admin")

    api.post("/api/submissions", token=t_user, json={"locale": "zh-CN", "key": "k.c", "value": "C"})
    api.post("/api/submissions", token=t_admin, json={"locale": "en", "key": "k.d", "value": "D"})

    r = api.get("/api/submissions", token=t_admin)
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_list_submissions_status_filter(api):
    t_user = api.login_as("user_e")
    t_admin = api.login_as("admin_filter", role="admin")

    sub_r = api.post("/api/submissions", token=t_user, json={"locale": "zh-CN", "key": "k.e", "value": "E"})
    sub_id = sub_r.json()["id"]

    # approve
    api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve"})

    r = api.get("/api/submissions?status=approved", token=t_user)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1

    r = api.get("/api/submissions?status=pending", token=t_user)
    assert r.json()["total"] == 0


# ---------------------------------------------------------------- POST /api/submissions/{id}/review

def test_review_approve_upserts_catalog(api):
    t_user = api.login_as("user_f")
    t_admin = api.login_as("admin_approve", role="admin")

    sub_r = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.approved", "value": "通过"}
    )
    sub_id = sub_r.json()["id"]

    r = api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve", "note": "LGTM"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    assert body["review_note"] == "LGTM"

    # catalog 中应有该条目
    r = api.c.get("/api/catalog/zh-CN")
    entries = {e["key"]: e["value"] for e in r.json()["entries"]}
    assert entries.get("test.approved") == "通过"


def test_review_reject_does_not_upsert_catalog(api):
    t_user = api.login_as("user_g")
    t_admin = api.login_as("admin_reject", role="admin")

    sub_r = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "en", "key": "test.rejected", "value": "Rejected val"}
    )
    sub_id = sub_r.json()["id"]

    r = api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "reject"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"

    r = api.c.get("/api/catalog/en")
    keys = {e["key"] for e in r.json()["entries"]}
    assert "test.rejected" not in keys


def test_review_requires_admin(api):
    t_user = api.login_as("user_h")

    sub_r = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.perm", "value": "x"}
    )
    sub_id = sub_r.json()["id"]

    r = api.post(f"/api/submissions/{sub_id}/review", token=t_user, json={"action": "approve"})
    assert r.status_code == 403


def test_review_not_found(api):
    t_admin = api.login_as("admin_nf", role="admin")
    r = api.post("/api/submissions/99999/review", token=t_admin, json={"action": "approve"})
    assert r.status_code == 404


def test_review_already_reviewed(api):
    t_user = api.login_as("user_i")
    t_admin = api.login_as("admin_dup", role="admin")

    sub_r = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.dup", "value": "x"}
    )
    sub_id = sub_r.json()["id"]

    api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve"})
    r = api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "reject"})
    assert r.status_code == 400


# ---------------------------------------------------------------- GET/POST /api/submissions/{id}/notes

def test_admin_can_add_and_list_submission_notes(api):
    user_token = api.login_as("note_author")
    admin_token = api.login_as("note_admin", role="admin")
    submission = api.post(
        "/api/submissions",
        token=user_token,
        json={"locale": "zh-CN", "key": "notes.demo", "value": "演示"},
    ).json()

    created = api.post(
        f"/api/submissions/{submission['id']}/notes",
        token=admin_token,
        json={"body": "  Please verify the terminology.  "},
    )
    assert created.status_code == 201
    assert created.json()["body"] == "Please verify the terminology."

    listed = api.get(f"/api/submissions/{submission['id']}/notes", token=admin_token)
    assert listed.status_code == 200
    assert [note["body"] for note in listed.json()["notes"]] == ["Please verify the terminology."]


def test_submitter_can_read_but_not_add_submission_notes(api):
    user_token = api.login_as("note_submitter")
    admin_token = api.login_as("note_reviewer", role="admin")
    submission = api.post(
        "/api/submissions",
        token=user_token,
        json={"locale": "zh-CN", "key": "notes.read", "value": "读取"},
    ).json()
    api.post(
        f"/api/submissions/{submission['id']}/notes",
        token=admin_token,
        json={"body": "Reviewer context"},
    )

    listed = api.get(f"/api/submissions/{submission['id']}/notes", token=user_token)
    assert listed.status_code == 200
    assert listed.json()["notes"][0]["author_name"] == "note_reviewer"

    denied = api.post(
        f"/api/submissions/{submission['id']}/notes",
        token=user_token,
        json={"body": "Not allowed"},
    )
    assert denied.status_code == 403


def test_submission_notes_reject_invalid_or_inaccessible_requests(api):
    owner_token = api.login_as("note_owner")
    stranger_token = api.login_as("note_stranger")
    admin_token = api.login_as("note_guard", role="admin")
    submission = api.post(
        "/api/submissions",
        token=owner_token,
        json={"locale": "zh-CN", "key": "notes.guard", "value": "权限"},
    ).json()

    assert api.get(f"/api/submissions/{submission['id']}/notes", token=stranger_token).status_code == 403
    assert api.post(
        f"/api/submissions/{submission['id']}/notes",
        token=admin_token,
        json={"body": "   "},
    ).status_code == 422
    assert api.get("/api/submissions/99999/notes", token=admin_token).status_code == 404
