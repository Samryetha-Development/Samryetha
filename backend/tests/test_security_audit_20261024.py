"""Regression tests for the 2026-10-24 security audit findings.

Covers:
- F12: orphaned attachments still count against the uploader quota; the owner can delete them.
- F13: concurrent presigns cannot overshoot the quota.
- F14: the body-size guard does not depend on the declared Content-Type.
- F15: OIDC login commits its one-time transaction before a slow authorization URL call.
- F16: converter output symlinks are rejected, not copied from the host.
- F18: delivered password-reset payloads no longer retain the bearer token.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from samryetha.adapters.docpreview import ConversionFailed, convert_once
from samryetha.core.config import Settings
from samryetha.core.schema import attachments, outbox_events
from samryetha.main import create_app


def _upload(client: TestClient, filename: str, body: bytes = b"data") -> dict[str, object]:
    response = client.post("/api/attachments/presign", json={
        "filename": filename, "mimeType": "text/plain", "sizeBytes": len(body),
    })
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert client.put(ticket["uploadUrl"], content=body).status_code == 204
    return ticket


def test_f12_orphaned_attachments_count_against_quota_and_owner_can_cleanup(api):
    api.settings.attachment_quota_files = 5
    api.settings.attachment_quota_bytes = 1_000_000
    api.login_dev()
    board = api.c.post("/api/boards", json={
        "name": "Quota", "slug": "quota-board", "visibility": "public", "postingPolicy": "everyone",
    })
    assert board.status_code == 201, board.text
    api.mkuser("diskuser")
    api.login("diskuser")

    ids = [int(_upload(api.c, f"first-{index}.txt")["attachmentId"]) for index in range(5)]
    created = api.c.post("/api/discussions", json={
        "boardSlug": "quota-board", "title": "temporary", "bodyMarkdown": "x", "attachmentIds": ids,
    })
    assert created.status_code == 201, created.text
    discussion_id = created.json()["id"]
    deleted = api.c.request("DELETE", f"/api/discussions/{discussion_id}", json={"reason": "test"})
    assert deleted.status_code == 200, deleted.text

    with api.app.state.db.request_conn() as conn:
        states = conn.execute(select(attachments.c.state)).scalars().all()
    assert states == ["orphaned"] * 5

    # F12: the orphaned bytes still occupy disk, so they must not free a fresh quota.
    rejected = api.c.post("/api/attachments/presign", json={
        "filename": "second-0.txt", "mimeType": "text/plain", "sizeBytes": 4,
    })
    assert rejected.status_code == 413, rejected.text

    # The owner may explicitly clean up the orphaned rows; this removes the bytes too.
    for attachment_id in ids:
        removed = api.c.delete(f"/api/attachments/{attachment_id}")
        assert removed.status_code == 200, removed.text
    accepted = api.c.post("/api/attachments/presign", json={
        "filename": "second-1.txt", "mimeType": "text/plain", "sizeBytes": 4,
    })
    assert accepted.status_code == 200, accepted.text


def test_f13_concurrent_presigns_cannot_overshoot_quota(api):
    api.settings.attachment_quota_files = 5
    api.settings.attachment_quota_bytes = 1_000_000
    api.mkuser("raceuser")
    api.login("raceuser")

    def presign(index: int) -> int:
        return api.c.post("/api/attachments/presign", json={
            "filename": f"race-{index}.txt", "mimeType": "text/plain", "sizeBytes": 1,
        }).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(presign, range(6)))

    assert Counter(codes) == Counter({200: 5, 413: 1}), codes
    with api.app.state.db.request_conn() as conn:
        count = conn.execute(select(attachments.c.id)).all()
    assert len(count) == 5


def test_f14_body_limit_is_content_type_independent(api):
    body = b'{"username":"u","password":"' + b"x" * (2 * 1024 * 1024) + b'"}'
    cases = ("application/json", "text/plain", "application/json-patch+json", "application/vnd.api+json", None)
    for content_type in cases:
        headers = {"Content-Type": content_type} if content_type else {}
        response = api.c.post("/api/auth/register", content=body, headers=headers)
        assert response.status_code == 413, (content_type, response.status_code, response.text[:120])


def test_f14_signed_upload_still_accepts_large_body(api):
    """The body cap must not block the streaming upload endpoints it deliberately exempts."""
    api.mkuser("uploader")
    api.login("uploader")
    body = b"x" * (2 * 1024 * 1024)
    ticket = _upload(api.c, "large.txt", body)
    assert ticket["attachmentId"]


def test_f15_oidc_login_commits_before_slow_authorization_url(tmp_path):
    settings = Settings(
        _env_file=None,
        node_env="test",
        database_url=str(tmp_path / "oidc.db"),
        upload_dir=str(tmp_path / "uploads"),
        preview_cache_dir=str(tmp_path / "preview-cache"),
        preview_converter="off",
        oidc_issuer="https://idp.example/",
        oidc_client_id="forum",
        oidc_redirect_uri="https://forum.example/api/auth/callback",
    )
    app = create_app(settings)
    app.state.db.create_schema()

    class SlowOidc:
        def authorization_url(self, *_args: object, **_kwargs: object) -> str:
            time.sleep(2)
            return "https://idp.example/authorize"

        def authorization_params(self, *_args: object, **_kwargs: object) -> dict[str, str]:
            return {}

    result: dict[str, int] = {}
    app.state.oidc = SlowOidc()
    with TestClient(app, follow_redirects=False) as client:
        def begin() -> None:
            result["login"] = client.get("/api/auth/login").status_code

        thread = threading.Thread(target=begin)
        thread.start()
        time.sleep(0.2)
        started = time.monotonic()
        registered = client.post("/api/auth/register", json={
            "username": "duringoidc", "password": "password123",
        })
        elapsed = time.monotonic() - started
        thread.join(timeout=5)
        assert registered.status_code == 201, registered.text
        assert elapsed < 1.5, f"write waited {elapsed:.2f}s behind OIDC login"
        assert result.get("login") == 302
        assert not thread.is_alive()


def test_f16_converter_output_symlink_is_rejected(tmp_path):
    class SymlinkConverter:
        name = "symlink"

        def available(self) -> bool:
            return True

        def convert(self, source: Path, out_dir: Path) -> Path:
            produced = out_dir / "document.pdf"
            produced.symlink_to("/etc/passwd")
            return produced

    source = tmp_path / "hostile.docx"
    source.write_bytes(b"not a real docx")
    destination = tmp_path / "cache" / "result.pdf"
    destination.parent.mkdir()
    with pytest.raises(ConversionFailed):
        convert_once(SymlinkConverter(), source, destination)
    assert not destination.exists()


def test_f16_converter_output_fifo_is_rejected_without_blocking(tmp_path):
    class FifoConverter:
        name = "fifo"

        def available(self) -> bool:
            return True

        def convert(self, source: Path, out_dir: Path) -> Path:
            produced = out_dir / "document.pdf"
            os.mkfifo(produced)
            return produced

    source = tmp_path / "hostile.docx"
    source.write_bytes(b"not a real docx")
    destination = tmp_path / "cache" / "result.pdf"
    destination.parent.mkdir()
    with pytest.raises(ConversionFailed):
        convert_once(FifoConverter(), source, destination)
    assert not destination.exists()


def test_f18_password_reset_payload_is_cleared_after_delivery(api):
    api.mkuser("mailuser")
    api.login("mailuser")
    profile = api.c.patch("/api/me/profile", json={
        "recoveryEmail": "owner@example.test", "currentPassword": "password123",
    })
    assert profile.status_code == 200, profile.text
    assert api.c.post("/api/auth/forgot-password", json={
        "username": "mailuser", "recoveryEmail": "owner@example.test",
    }).status_code == 200

    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(outbox_events.c.payload, outbox_events.c.status).where(
            outbox_events.c.event_type == "password-reset.requested"
        )).one()
    assert row.status == "pending"
    assert re.search(r"token=[A-Za-z0-9_-]+", row.payload)

    api.app.state.flush_outbox()
    with api.app.state.db.request_conn() as conn:
        row = conn.execute(select(outbox_events.c.payload, outbox_events.c.status).where(
            outbox_events.c.event_type == "password-reset.requested"
        )).one()
    assert row.status == "done"
    assert row.payload == "{}"
    assert "token=" not in row.payload
