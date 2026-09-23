"""翻译提交与审核流程测试。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from i18n_svc.routers import submissions as submissions_router


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


def test_review_approve_updates_existing_catalog(api):
    """已有 catalog 条目时 approve 必须 update（upsert），不得 500，且只保留一条。"""
    t_user = api.login_as("user_upd")
    t_admin = api.login_as("admin_upd", role="admin")

    # 管理员预置旧值
    r = api.put("/api/catalog/zh-CN/test.upsert", token=t_admin, json={"value": "旧值"})
    assert r.status_code == 200

    sub_r = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.upsert", "value": "新值"},
    )
    sub_id = sub_r.json()["id"]

    # 新实现走 insert → IntegrityError(uq_catalog_locale_key) → update 路径
    r = api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = api.c.get("/api/catalog/zh-CN")
    entries = [e for e in r.json()["entries"] if e["key"] == "test.upsert"]
    assert len(entries) == 1
    assert entries[0]["value"] == "新值"


def test_review_approve_second_submission_same_key(api):
    """两个不同 submission 同 locale/key 先后 approve（并发 double-insert 的串行化版本）。

    第二个 approve 必须转为 update 并返回 200，而不是 500。
    """
    t_u1 = api.login_as("user_race1")
    t_u2 = api.login_as("user_race2")
    t_admin = api.login_as("admin_race", role="admin")

    s1 = api.post(
        "/api/submissions", token=t_u1,
        json={"locale": "en", "key": "test.race", "value": "v1"},
    ).json()["id"]
    s2 = api.post(
        "/api/submissions", token=t_u2,
        json={"locale": "en", "key": "test.race", "value": "v2"},
    ).json()["id"]

    r1 = api.post(f"/api/submissions/{s1}/review", token=t_admin, json={"action": "approve"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "approved"

    r2 = api.post(f"/api/submissions/{s2}/review", token=t_admin, json={"action": "approve"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "approved"

    r = api.c.get("/api/catalog/en")
    entries = [e for e in r.json()["entries"] if e["key"] == "test.race"]
    assert len(entries) == 1
    assert entries[0]["value"] == "v2"


def test_catalog_unique_violation_detection_only_matches_locale_key():
    """只有 (locale, key) 唯一约束冲突才算"已存在"；其他完整性错误必须判 False。"""
    dup = IntegrityError(
        "INSERT INTO catalog_entries ...",
        {},
        Exception("UNIQUE constraint failed: catalog_entries.locale, catalog_entries.key"),
    )
    not_null = IntegrityError(
        "INSERT INTO catalog_entries ...",
        {},
        Exception("NOT NULL constraint failed: catalog_entries.value"),
    )
    other_table = IntegrityError(
        "INSERT INTO submissions ...",
        {},
        Exception("UNIQUE constraint failed: submissions.locale, submissions.key"),
    )
    assert submissions_router._is_catalog_unique_violation(dup) is True
    assert submissions_router._is_catalog_unique_violation(not_null) is False
    assert submissions_router._is_catalog_unique_violation(other_table) is False


def test_review_approve_conflict_uses_savepoint_update_fallback(api):
    """预置同 (locale,key) 的 catalog 行 → insert 冲突必须回退 update 且只留一行。"""
    t_user = api.login_as("user_savepoint")
    t_admin = api.login_as("admin_savepoint", role="admin")

    api.put("/api/catalog/zh-CN/test.savepoint", token=t_admin, json={"value": "旧"})

    sub_id = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.savepoint", "value": "新"},
    ).json()["id"]

    r = api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    entries = [
        e for e in api.c.get("/api/catalog/zh-CN").json()["entries"]
        if e["key"] == "test.savepoint"
    ]
    assert len(entries) == 1
    assert entries[0]["value"] == "新"


def test_review_approve_non_unique_integrity_error_is_not_swallowed(api, monkeypatch):
    """非 (locale,key) 唯一冲突的 IntegrityError 必须上抛，且外层事务回滚（保持 pending）。"""
    t_user = api.login_as("user_nonuniq")
    t_admin = api.login_as("admin_nonuniq", role="admin")

    sub_id = api.post(
        "/api/submissions", token=t_user,
        json={"locale": "zh-CN", "key": "test.nonuniq", "value": "v"},
    ).json()["id"]

    # 预置 catalog 行，使 insert 触发真实的 uq_catalog_locale_key 冲突
    api.put("/api/catalog/zh-CN/test.nonuniq", token=t_admin, json={"value": "old"})

    # 模拟"该冲突不是 (locale,key) 唯一冲突"：实现必须上抛而非静默 update
    monkeypatch.setattr(
        submissions_router, "_is_catalog_unique_violation", lambda exc: False
    )

    with pytest.raises(IntegrityError):
        api.post(f"/api/submissions/{sub_id}/review", token=t_admin, json={"action": "approve"})

    # 外层事务回滚：submission 仍为 pending
    pending = api.get("/api/submissions?status=pending", token=t_admin).json()["submissions"]
    assert any(s["id"] == sub_id for s in pending)
