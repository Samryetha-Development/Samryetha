"""审计修复回归测试（S1/H1-H4/M1/M3-M5/M7-M8/M11-M14/L8/L12 + drift）。"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from samryetha import auth as auth_service
from samryetha import oidc as oidc_mod
from samryetha.attachments import reap_orphans
from samryetha.config import Settings
from samryetha.db import now_ms
from samryetha.errors import ApiError
from samryetha.outbox import emit_event
from samryetha.outbox_worker import OutboxDispatcher, poll_once
from samryetha.schema import attachments, outbox_events, users


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def _mk_active(conn, username: str, role: str = "student") -> int:
    uid = auth_service.register(conn, username, "password123")
    conn.execute(
        update(users).where(users.c.id == uid).values(status="active", email_verified_at=now_ms(), role=role)
    )
    return uid


# ---------------------------------------------------------------- S1 出租回收


def test_outbox_reclaims_stale_processing(db):
    dispatcher = OutboxDispatcher()
    seen: list = []
    dispatcher.on("job", lambda conn, payload: seen.append(payload) or [])
    with db.request_conn() as conn:
        emit_event(conn, "job", payload={"n": 1})
        row_id = conn.execute(select(outbox_events.c.id)).scalar_one()
        # 模拟 worker 在 claim 后崩溃：processing 已超时 6 分钟
        conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id == row_id)
            .values(status="processing", processing_at=now_ms() - 6 * 60 * 1000, available_at=now_ms() - 1000)
        )
    poll_once(db, dispatcher)
    assert seen == [{"n": 1}]
    with db.request_conn() as conn:
        row = conn.execute(select(outbox_events).where(outbox_events.c.id == row_id)).first()
        assert row.status == "done"


def test_outbox_keeps_fresh_processing(db):
    dispatcher = OutboxDispatcher()
    dispatcher.on("job", lambda conn, payload: [])
    with db.request_conn() as conn:
        emit_event(conn, "job", payload={})
        row_id = conn.execute(select(outbox_events.c.id)).scalar_one()
        conn.execute(
            update(outbox_events)
            .where(outbox_events.c.id == row_id)
            .values(status="processing", processing_at=now_ms(), available_at=now_ms() - 1000)
        )
    # 刚 claim 的行不被回收、不被重复消费
    assert poll_once(db, dispatcher) == []
    with db.request_conn() as conn:
        row = conn.execute(select(outbox_events).where(outbox_events.c.id == row_id)).first()
        assert row.status == "processing"


def test_schema_drift_adds_processing_at(db):
    with db.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE outbox_events DROP COLUMN processing_at")
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(outbox_events)")}
        assert "processing_at" not in cols
    db.ensure_schema_drift()
    with db.engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(outbox_events)")}
        assert "processing_at" in cols
    db.ensure_schema_drift()  # no-op


# ---------------------------------------------------------------- H2 admin 双向同步


def _finish(db, uid: int, groups: set[str]):
    settings = _settings()
    with db.request_conn() as conn:
        return oidc_mod._finish_login(conn, uid, settings, {"groups": list(groups)}, ip=None, user_agent=None)


def test_oidc_admin_promotion(db):
    with db.request_conn() as conn:
        uid = _mk_active(conn, "promotee")
    assert _finish(db, uid, {"samryetha-admins"})["user"]["role"] == "admin"


def test_oidc_admin_demotion_when_others_exist(db):
    with db.request_conn() as conn:
        uid = _mk_active(conn, "demotee", role="admin")
        _mk_active(conn, "otheradmin", role="admin")
    assert _finish(db, uid, {"samryetha-users"})["user"]["role"] == "student"


def test_oidc_last_admin_keeps_role(db):
    with db.request_conn() as conn:
        uid = _mk_active(conn, "soleadmin", role="admin")
    assert _finish(db, uid, {"samryetha-users"})["user"]["role"] == "admin"


# ---------------------------------------------------------------- H1 认领时序


def test_claim_unknown_user_takes_dummy_path(db, monkeypatch):
    import samryetha.oidc as oidc_module

    calls: list = []
    monkeypatch.setattr(oidc_module, "verify_against_dummy", lambda pw: calls.append(pw) or False)
    settings = _settings()
    with db.request_conn() as conn:
        ticket = oidc_mod.begin_claim(conn, issuer="iss", subject="sub", email=None, display_name="X")
    with db.request_conn() as conn:
        with pytest.raises(ApiError) as exc:
            oidc_mod.claim_account(
                conn, ticket=ticket, username="ghost", password="whatever", settings=settings, ip=None, user_agent=None
            )
    assert exc.value.code == "INVALID_CREDENTIALS"
    assert calls == ["whatever"]


# ---------------------------------------------------------------- M1 并发 IntegrityError


def test_register_concurrent_conflict_is_409(db, monkeypatch):
    import samryetha.auth as auth_module

    def boom(*args, **kwargs):
        raise IntegrityError("INSERT INTO users", {}, Exception("UNIQUE constraint failed: users.username"))

    monkeypatch.setattr(auth_module, "register_user_row", boom)
    with db.request_conn() as conn:
        with pytest.raises(ApiError) as exc:
            auth_module.register(conn, "racer", "password123")
    assert exc.value.status == 409
    assert exc.value.message == "That username is already taken"


def test_claim_concurrent_bind_is_409(db):
    from unittest.mock import patch

    settings = _settings()
    with db.request_conn() as conn:
        uid = _mk_active(conn, "claimvictim")
        ticket = oidc_mod.begin_claim(conn, issuer="iss", subject="sub9", email=None, display_name="V")
    with db.request_conn() as conn:
        real_execute = conn.execute

        def flaky(stmt, *args, **kwargs):
            text = str(stmt)
            if text.startswith("INSERT INTO oidc_identities"):
                raise IntegrityError(text, {}, Exception("UNIQUE constraint failed"))
            return real_execute(stmt, *args, **kwargs)

        with patch.object(conn, "execute", side_effect=flaky):
            with pytest.raises(ApiError) as exc:
                oidc_mod.claim_account(
                    conn, ticket=ticket, username="claimvictim", password="password123",
                    settings=settings, ip=None, user_agent=None,
                )
    assert exc.value.status == 409


# ---------------------------------------------------------------- M3 SMTP 穿透


def test_forgot_password_smtp_failure_still_200(api):
    api.mkuser("mailuser")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "mailuser").values(recovery_email="m@example.com"))

    class BoomMailer:
        def send(self, **kwargs):
            raise ConnectionError("smtp down")

    api.app.state.mailer = BoomMailer()
    r = api.c.post("/api/auth/forgot-password", json={"username": "mailuser", "recoveryEmail": "m@example.com"})
    assert r.status_code == 200, r.text
    # 令牌已落库
    from samryetha.schema import password_reset_tokens

    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(password_reset_tokens)).first() is not None
    # 不存在的账号同样 200（统一口径）
    r2 = api.c.post("/api/auth/forgot-password", json={"username": "nobody", "recoveryEmail": "n@example.com"})
    assert r2.status_code == 200


# ---------------------------------------------------------------- H3 封禁用户上传


def test_upload_rejects_malformed_content_length(api):
    api.login_dev()
    pres = api.c.post(
        "/api/attachments/presign",
        json={"filename": "c.txt", "mimeType": "text/plain", "sizeBytes": 4},
    ).json()
    r = api.c.put(pres["uploadUrl"], content=b"test", headers={"content-length": "not-a-number"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["message"] == "Invalid Content-Length"


def test_banned_uploader_put_is_403(api):
    api.mkuser("uploader")
    api.login("uploader")
    pres = api.c.post(
        "/api/attachments/presign",
        json={"filename": "note.txt", "mimeType": "text/plain", "sizeBytes": 4},
    )
    assert pres.status_code == 200
    upload_url = pres.json()["uploadUrl"]
    # 封禁后再持有效签名 PUT → 403
    api.login_dev()
    assert api.c.post("/api/moderation/bans", json={"username": "uploader", "reason": "spam"}).status_code == 200
    r = api.c.put(upload_url, content=b"test")
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------- H4/M7 回收


def test_reap_uploaded_orphans_with_longer_grace(api):
    api.login_dev()
    up = api.c.post(
        "/api/attachments/presign",
        json={"filename": "stale.txt", "mimeType": "text/plain", "sizeBytes": 4},
    ).json()
    assert api.c.put(up["uploadUrl"], content=b"test").status_code == 204
    att_id = up["attachmentId"]
    # 默认宽限内（7 天）不回收
    with api.app.state.db.request_conn() as conn:
        assert reap_orphans(conn, api.app.state.storage) == 0
    # 回到 8 天前 → 回收
    with api.app.state.db.request_conn() as conn:
        conn.execute(
            update(attachments).where(attachments.c.id == att_id).values(created_at=now_ms() - 8 * 24 * 3600 * 1000)
        )
    with api.app.state.db.request_conn() as conn:
        assert reap_orphans(conn, api.app.state.storage) == 1
    assert api.c.get(f"/api/attachments/{att_id}").status_code == 404


def test_reap_skips_bad_key_and_continues(db, tmp_path):
    from samryetha.storage import Storage

    storage = Storage(str(tmp_path / "u"), "secret")
    with db.request_conn() as conn:
        conn.execute(
            insert(users).values(
                username="reaper", email="r@x.local", display_name="r", bio="",
                password_hash="x", role="student", status="active",
                settings="{}", created_at=now_ms(), updated_at=now_ms(),
            )
        )
        uid = conn.execute(select(users.c.id).where(users.c.username == "reaper")).scalar_one()
        old = now_ms() - 2 * 24 * 3600 * 1000
        conn.execute(
            insert(attachments).values(
                uploader_id=uid, object_key="../evil-escape", original_filename="e.txt",
                mime_type="text/plain", size_bytes=1, state="pending", created_at=old,
            )
        )
        conn.execute(
            insert(attachments).values(
                uploader_id=uid, object_key="good-key", original_filename="g.txt",
                mime_type="text/plain", size_bytes=1, state="pending", created_at=old,
            )
        )
    with db.request_conn() as conn:
        assert reap_orphans(conn, storage) == 1
        remaining = [r.object_key for r in conn.execute(select(attachments)).all()]
        assert remaining == ["../evil-escape"]


# ---------------------------------------------------------------- M4 restore 逆操作


def test_restore_discussion_reattaches_orphans(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "R", "slug": "restore-b"}).status_code == 201
    up = api.c.post(
        "/api/attachments/presign",
        json={"filename": "f.txt", "mimeType": "text/plain", "sizeBytes": 4},
    ).json()
    assert api.c.put(up["uploadUrl"], content=b"test").status_code == 204
    disc = api.c.post(
        "/api/discussions",
        json={"boardSlug": "restore-b", "title": "t", "bodyMarkdown": "b", "attachmentIds": [up["attachmentId"]]},
    ).json()
    did = disc["id"]
    assert api.c.delete(f"/api/discussions/{did}").status_code == 200
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(attachments.c.state).where(attachments.c.id == up["attachmentId"])).scalar_one() == "orphaned"
    assert api.c.post("/api/moderation/restore", json={"targetType": "discussion", "targetId": did}).status_code == 200
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(attachments.c.state).where(attachments.c.id == up["attachmentId"])).scalar_one() == "attached"
    assert api.c.get(f"/api/discussions/{did}").status_code == 200


def test_restore_reply_bumps_count_once(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "R2", "slug": "restore-r"}).status_code == 201
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "restore-r", "title": "t", "bodyMarkdown": "b"}
    ).json()["id"]
    rid = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "hi"}).json()["id"]
    assert api.c.delete(f"/api/replies/{rid}").status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 0
    assert api.c.post("/api/moderation/restore", json={"targetType": "reply", "targetId": rid}).status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 1
    # 未软删的回复再 restore：不重复加计数
    assert api.c.post("/api/moderation/restore", json={"targetType": "reply", "targetId": rid}).status_code == 200
    assert api.c.get(f"/api/discussions/{did}").json()["replyCount"] == 1


# ---------------------------------------------------------------- M8 路径判定


def test_abspath_rejects_escape(tmp_path):
    from samryetha.storage import Storage

    storage = Storage(str(tmp_path / "root"), "s")
    ok = storage.path_for("12345678-1234-1234-1234-123456789012/file.png")
    assert ok.startswith(str(tmp_path / "root"))
    for bad in ("../evil", "a/../../evil", "/absolute"):
        try:
            storage.path_for(bad)
        except (PermissionError, ValueError):
            continue
        raise AssertionError(f"escape not rejected: {bad!r}")
    # 同前缀兄弟目录绕过（字符串前缀比较的经典漏洞）必须拦
    sibling = tmp_path / "root-evil"
    sibling.mkdir()
    try:
        storage.path_for("../root-evil/x")
    except (PermissionError, ValueError):
        pass
    else:
        raise AssertionError("sibling-prefix escape not rejected")


# ---------------------------------------------------------------- M11 附件探针


def test_attachment_probe_is_404_for_strangers_but_visible_to_admin(api):
    api.mkuser("owner1")
    api.mkuser("stranger")
    api.login("owner1")
    up = api.c.post(
        "/api/attachments/presign",
        json={"filename": "p.png", "mimeType": "image/png", "sizeBytes": 5},
    ).json()
    aid = up["attachmentId"]
    api.login("stranger")
    assert api.c.get(f"/api/attachments/{aid}").status_code == 404
    assert api.c.delete(f"/api/attachments/{aid}").status_code == 404
    # 全局管理员可读写他人附件
    api.login_dev()
    assert api.c.get(f"/api/attachments/{aid}").status_code == 200
    assert api.c.delete(f"/api/attachments/{aid}").status_code == 200


# ---------------------------------------------------------------- M12 seq/gap


def test_sse_seq_increases_and_gap_on_full():
    from samryetha.routers.realtime import _enqueue_notification_frame, _next_sse_seq

    a = _next_sse_seq()
    b = _next_sse_seq()
    assert b == a + 1

    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    _enqueue_notification_frame(q, {"userId": 7})
    first = json.loads(q.get_nowait().split("data: ", 1)[1])
    assert first["userId"] == 7 and isinstance(first["seq"], int)
    # 队列满时第二帧触发 gap：最旧被丢，落 gap 控制帧
    _enqueue_notification_frame(q, {"userId": 7})
    _enqueue_notification_frame(q, {"userId": 7})
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    assert frames, "gap frame must be enqueued instead of silent drop"
    last = frames[-1]
    assert last.startswith("event: gap\n")
    gap_data = json.loads(last.split("data: ", 1)[1])
    assert gap_data["seq"] > first["seq"]


# ---------------------------------------------------------------- M13 举报批量


def test_list_reports_batches_targets(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "Mod", "slug": "mod-b"}).status_code == 201
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "mod-b", "title": "reported", "bodyMarkdown": "x"}
    ).json()["id"]
    rid = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "reply!"}).json()["id"]
    api.mkuser("reporter")
    api.login("reporter")
    assert api.c.post("/api/moderation/reports", json={"reportableType": "discussion", "reportableId": did, "reason": "s"}).status_code == 201
    assert api.c.post("/api/moderation/reports", json={"reportableType": "reply", "reportableId": rid, "reason": "s"}).status_code == 201
    me_id = api.c.get("/api/auth/me").json()["user"]["id"]
    assert api.c.post("/api/moderation/reports", json={"reportableType": "user", "reportableId": me_id, "reason": "s"}).status_code == 201
    api.login_dev()
    items = api.c.get("/api/moderation/reports").json()["items"]
    assert len(items) == 3
    by_type = {i["reportableType"]: i for i in items}
    assert by_type["discussion"]["target"]["title"] == "reported"
    assert by_type["reply"]["target"]["discussionId"] == did
    assert by_type["user"]["target"]["username"] == "reporter"


# ---------------------------------------------------------------- M5/M14 分页


def test_discussion_pagination_across_pin_boundary(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "Pg", "slug": "pg-b"}).status_code == 201
    ids = []
    for i in range(4):
        ids.append(
            api.c.post(
                "/api/discussions", json={"boardSlug": "pg-b", "title": f"p{i}", "bodyMarkdown": "b"}
            ).json()["id"]
        )
    # 置顶最旧的一篇，制造置顶/非置顶边界
    assert api.c.post(f"/api/discussions/{ids[0]}/pin").status_code == 200
    seen: list = []
    cursor = None
    for _ in range(3):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = api.c.get("/api/discussions", params=params).json()
        seen.extend(t["id"] for t in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids)
    assert len(set(seen)) == 4


def test_malformed_discussion_cursor_is_400(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "C", "slug": "cur-b"}).status_code == 201
    assert api.c.get("/api/discussions", params={"cursor": "garbage!!"}).status_code == 400
    assert api.c.get("/api/discussions", params={"cursor": "1_2_3_4"}).status_code == 400
    # 旧式两段游标仍兼容
    assert api.c.get("/api/discussions", params={"cursor": "9999999999999_999999"}).status_code == 200


def test_malformed_reply_cursor_is_400(api):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "RC", "slug": "rcur-b"}).status_code == 201
    r = api.c.get("/api/users/dev/replies", params={"cursor": "nope"})
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------- L8 组合


def test_profile_rename_drops_spoofed_sync_marker(api):
    api.mkuser("oidcuser")
    with api.app.state.db.request_conn() as conn:
        conn.execute(
            update(users).where(users.c.username == "oidcuser").values(settings=json.dumps({"display_name_source": "oidc"}))
        )
    api.login("oidcuser")
    r = api.c.patch(
        "/api/me/profile",
        json={"displayName": "Local", "settings": {"display_name_source": "oidc", "theme": "dark"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["displayName"] == "Local"
    assert r.json()["user"]["settings"].get("display_name_source") is None
    assert r.json()["user"]["settings"].get("theme") == "dark"


# ---------------------------------------------------------------- L12 迁移通道锁定


def test_claim_new_migration_channel(db):
    settings = _settings()
    with db.request_conn() as conn:
        ticket = oidc_mod.begin_claim(
            conn, issuer="https://idp.example/", subject="mig-1", email="m@example.com", display_name="Mig"
        )
    with db.request_conn() as conn:
        result = oidc_mod.claim_create_account(conn, ticket=ticket, settings=settings, ip=None, user_agent=None)
    assert result["user"]["username"]
    assert result["token"]
