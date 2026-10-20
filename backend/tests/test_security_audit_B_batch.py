"""2026-10-18 安全审计 B 批回归测试（F05/F06/F10/F11）。

断言一律是"攻击被拒绝"的正向守卫：把对应修复回退后，用例必须失败（变红）。
F09 前端改动由 pnpm typecheck（CI）覆盖，逻辑见 PR 说明。

Tests here assert the fixed behaviour; reverting any fix must turn the matching test red.
F09's frontend change is covered by pnpm typecheck (CI); rationale in the PR body.
"""

from __future__ import annotations

import ssl
from types import SimpleNamespace

import pytest
from sqlalchemy import insert, select

from samryetha.adapters.mailer import SmtpMailer
from samryetha.adapters.storage import Storage
from samryetha.attachments.models import CreateAttachment
from samryetha.attachments.service import AttachmentService
from samryetha.auth import AuthService
from samryetha.core.config import Settings
from samryetha.core.db import now_ms
from samryetha.core.errors import APIError
from samryetha.core.schema import outbox_events, users


# ---------------------------------------------------------------- F05 回复写锁内复核板块


def test_f05_reply_rechecks_board_visibility_in_write_lock(api, monkeypatch):
    api.login_dev()
    assert api.c.post("/api/boards", json={"name": "B", "slug": "f05-board", "visibility": "public"}).status_code == 201
    did = api.c.post(
        "/api/discussions", json={"boardSlug": "f05-board", "title": "t", "bodyMarkdown": "b"}
    ).json()["id"]
    api.mkuser("nonmember")
    api.login("nonmember")

    from samryetha.core.ids import BoardID
    from samryetha.discussions.repository import BoardRecord, DiscussionRepository

    real = DiscussionRepository.get_board
    seen = {"n": 0}

    def fake(self, board_id):
        # 模拟：首次鉴权后、取写锁前，板块被改成 private。首次调用返回原 public，写锁内复核（第 2 次）返回 private。
        seen["n"] += 1
        if seen["n"] >= 2:
            return BoardRecord(
                id=BoardID(board_id), slug="f05-board", name="B", visibility="private", posting_policy="everyone"
            )
        return real(self, board_id)

    monkeypatch.setattr(DiscussionRepository, "get_board", fake)

    # 非成员回复：写锁内复核到 private → 403
    r = api.c.post(f"/api/discussions/{did}/replies", json={"bodyMarkdown": "x"})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------- F06 上传累计配额


def test_f06_attachment_quota_exceeded_rejected(db, tmp_path):
    with db.request_conn() as conn:
        uid = conn.execute(
            insert(users).values(
                username="quota-user",
                display_name="q",
                email="q@samryetha.local",
                password_hash="x",
                role="student",
                status="active",
                discriminator=6001,
                settings="{}",
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        ).inserted_primary_key[0]

    settings = Settings(_env_file=None, attachment_quota_bytes=10, attachment_quota_files=1)
    storage = Storage(str(tmp_path / "u"), "secret")
    actor = SimpleNamespace(id=uid, role="student", status="active")
    with db.request_conn() as conn:
        svc = AttachmentService(conn, storage=storage, settings=settings)
        # 第一次 presign（文件数=1，配额=1）成功
        svc.presign(actor, CreateAttachment(filename="a.txt", mime_type="text/plain", size_bytes=5))
        # 第二次 presign 超过文件数配额 → 413
        with pytest.raises(APIError) as exc:
            svc.presign(actor, CreateAttachment(filename="b.txt", mime_type="text/plain", size_bytes=5))
        assert exc.value.status == 413


# ---------------------------------------------------------------- F10 SMTP TLS 严格校验


def test_f10_smtp_tls_context_strict_by_default():
    strict = SmtpMailer("smtps://user:pass@localhost:465", "from@x.com")
    ctx = strict._tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True

    lax = SmtpMailer("smtps://user:pass@localhost:465", "from@x.com", verify_tls=False)
    lax_ctx = lax._tls_context()
    assert lax_ctx.verify_mode == ssl.CERT_NONE
    assert lax_ctx.check_hostname is False


# ---------------------------------------------------------------- F11 发信移出写事务


def test_f11_password_reset_email_moved_out_of_transaction(db):
    with db.request_conn() as conn:
        conn.execute(
            insert(users).values(
                username="mailuser",
                display_name="m",
                email="m@samryetha.local",
                password_hash="x",
                role="student",
                status="active",
                recovery_email="m@example.com",
                discriminator=6002,
                settings="{}",
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )

    class RecordingMailer:
        def __init__(self) -> None:
            self.calls: list = []

        def send(self, **kwargs) -> None:
            self.calls.append(kwargs)

    mailer = RecordingMailer()
    settings = Settings(_env_file=None)
    with db.request_conn() as conn:
        AuthService(conn, settings=settings, mailer=mailer).forgot_password("mailuser", "m@example.com")
    # 发信不在请求事务内同步发生（否则会占住写锁）
    assert mailer.calls == []
    # 而是落 outbox 事件，由后台 worker 在事务外发送
    with db.request_conn() as conn:
        event_type = conn.execute(
            select(outbox_events.c.event_type).where(outbox_events.c.event_type == "password-reset.requested")
        ).scalar_one_or_none()
        assert event_type == "password-reset.requested"
