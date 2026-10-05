"""扫码登录邮箱确认码：只有真实且已验证的邮箱账号才要二次确认。

本地注册账号（username@samryetha.local 占位，或未验证邮箱）必须与今天完全一致地
一键批准，不能被新流程锁在门外。
"""

from __future__ import annotations

import re

from sqlalchemy import select, update

from samryetha.db import now_ms
from samryetha.schema import qr_login_confirmation_codes, users

CODE_RE = re.compile(r"\b(\d{6})\b")


def _register(client, username: str, password: str = "password123") -> int:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["userId"]


def _activate(
    client,
    username: str,
    *,
    email: str | None = None,
    verified: bool = True,
) -> None:
    """把待审账号改活，可选地换成真实邮箱 / 抹掉验证时间。"""
    values: dict = {"status": "active"}
    if email is not None:
        values["email"] = email
        values["email_domain"] = email.partition("@")[2]
    if verified:
        values["email_verified_at"] = now_ms()
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == username).values(**values))


def _start(client) -> dict:
    response = client.post("/api/auth/qr/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["qr_data_uri"].startswith("data:image/svg+xml;base64,")
    return body


def _login(client, username: str, password: str = "password123"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


# ---------------------------------------------------------------- 回归：占位邮箱


def test_unverified_placeholder_email_approves_without_code(api):
    """未验证 + 占位邮箱：保持今天的一键批准。"""
    _register(api.c, "localunverified")
    with api.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "localunverified").values(status="active"))
    _login(api.c, "localunverified")
    started = _start(api.c)

    info = api.c.get("/api/auth/qr/info", params={"ticket_id": started["ticket_id"]})
    assert info.status_code == 200
    assert info.json()["emailConfirmationRequired"] is False
    assert info.json()["emailHint"] is None

    assert api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"]}).status_code == 200
    exchanged = api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    )
    assert exchanged.status_code == 200, exchanged.text


def test_verified_placeholder_email_approves_without_code(api):
    """已验证但邮箱是 @samryetha.local 占位：同样不需要确认码。"""
    api.mkuser("localplaceholder")
    _login(api.c, "localplaceholder")
    started = _start(api.c)

    info = api.c.get("/api/auth/qr/info", params={"ticket_id": started["ticket_id"]})
    assert info.status_code == 200
    assert info.json()["emailConfirmationRequired"] is False

    assert api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"]}).status_code == 200
    exchanged = api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    )
    assert exchanged.status_code == 200, exchanged.text


# ---------------------------------------------------------------- 需确认账号


def test_verified_real_email_requires_code_and_hints_masked_address(api):
    _register(api.c, "realuser")
    _activate(api.c, "realuser", email="real.user@example.com")
    _login(api.c, "realuser")
    started = _start(api.c)

    info = api.c.get("/api/auth/qr/info", params={"ticket_id": started["ticket_id"]})
    assert info.status_code == 200, info.text
    body = info.json()
    assert body["emailConfirmationRequired"] is True
    assert body["emailHint"] == "r***@example.com"
    # 完整地址绝不从 qr/info 漏出
    assert "real.user@example.com" not in info.text

    # 没带码批准 → 403 EMAIL_CODE_REQUIRED（流程信号，非死路）
    missing = api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"]})
    assert missing.status_code == 403
    assert missing.json()["error"]["message"] == "EMAIL_CODE_REQUIRED"


def test_confirm_request_is_noop_for_placeholder_account(api):
    api.mkuser("plainsend")
    _login(api.c, "plainsend")
    started = _start(api.c)

    sent: list[str] = []
    api.app.state.mailer.send = lambda **kwargs: sent.append(kwargs["text"])
    response = api.c.post("/api/auth/qr/confirm/request", json={"ticket_id": started["ticket_id"]})
    assert response.status_code == 200, response.text
    assert response.json() == {"required": False}
    assert sent == []


def test_confirm_request_rejects_dead_ticket(api):
    api.mkuser("deadticket")
    _login(api.c, "deadticket")
    response = api.c.post("/api/auth/qr/confirm/request", json={"ticket_id": "nope"})
    assert response.status_code == 400


# ---------------------------------------------------------------- 发码 → 校验


def test_code_emailed_then_verified_single_use(api):
    _register(api.c, "codesuser")
    _activate(api.c, "codesuser", email="codes@example.com")
    _login(api.c, "codesuser")
    started = _start(api.c)

    sent: list[dict] = []
    api.app.state.mailer.send = lambda **kwargs: sent.append(kwargs)
    response = api.c.post("/api/auth/qr/confirm/request", json={"ticket_id": started["ticket_id"]})
    assert response.status_code == 200, response.text
    assert response.json() == {"required": True, "emailHint": "c***@example.com"}

    assert sent and sent[0]["to"] == "codes@example.com"
    assert "Confirm your Samryetha sign-in" == sent[0]["subject"]
    match = CODE_RE.search(sent[0]["text"])
    assert match, sent[0]["text"]
    code = match.group(1)
    # 正文与 HTML 都要带上码，且邮件里含过期说明
    assert code in sent[0]["html"]
    assert "10 minutes" in sent[0]["text"]

    # 库里只有哈希，绝不落 6 位明文
    with api.app.state.db.request_conn() as conn:
        rows = conn.execute(select(qr_login_confirmation_codes)).all()
    assert len(rows) == 1
    assert code not in rows[0].code_hash

    # 错码失败，且与"没有码"同一句通用错误
    wrong = "000000" if code != "000000" else "111111"
    bad = api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": wrong})
    assert bad.status_code == 400
    assert bad.json()["error"]["message"] == "This confirmation code is invalid or has expired"

    # 正码成功
    ok = api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": code})
    assert ok.status_code == 200, ok.text
    exchanged = api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    )
    assert exchanged.status_code == 200, exchanged.text

    # 单次使用：同一张票再换一次会话，票据已焚、码已消费
    assert api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    ).status_code == 400


def test_code_single_use_within_ticket_lifetime(api):
    _register(api.c, "onceuser")
    _activate(api.c, "onceuser", email="once@example.com")
    _login(api.c, "onceuser")
    started = _start(api.c)
    sent: list[dict] = []
    api.app.state.mailer.send = lambda **kwargs: sent.append(kwargs)
    api.c.post("/api/auth/qr/confirm/request", json={"ticket_id": started["ticket_id"]})
    code = CODE_RE.search(sent[0]["text"]).group(1)

    assert api.c.post(
        "/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": code}
    ).status_code == 200
    # 票据已 approved，再用同码 → 码已消费，通用 400
    reused = api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": code})
    assert reused.status_code == 400
    assert reused.json()["error"]["message"] == "This confirmation code is invalid or has expired"


def test_code_is_attempt_limited(api):
    _register(api.c, "limiteduser")
    _activate(api.c, "limiteduser", email="limited@example.com")
    _login(api.c, "limiteduser")
    started = _start(api.c)
    sent: list[dict] = []
    api.app.state.mailer.send = lambda **kwargs: sent.append(kwargs)
    api.c.post("/api/auth/qr/confirm/request", json={"ticket_id": started["ticket_id"]})
    code = CODE_RE.search(sent[0]["text"]).group(1)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(5):
        attempt = api.c.post(
            "/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": wrong}
        )
        assert attempt.status_code == 400
        assert attempt.json()["error"]["message"] == "This confirmation code is invalid or has expired"

    # 到达上限即作废：正确的码也换不出会话
    assert api.c.post(
        "/api/auth/qr/approve", json={"ticket_id": started["ticket_id"], "code": code}
    ).status_code == 400
    with api.app.state.db.request_conn() as conn:
        assert conn.execute(select(qr_login_confirmation_codes)).all() == []


# ---------------------------------------------------------------- 拒绝


def test_deny_still_works_without_code(api):
    _register(api.c, "denier")
    _activate(api.c, "denier", email="denier@example.com")
    _login(api.c, "denier")
    started = _start(api.c)

    assert api.c.post("/api/auth/qr/deny", json={"ticket_id": started["ticket_id"]}).status_code == 200
    waited = api.c.get("/api/auth/qr/wait", params={"ticket_id": started["ticket_id"]})
    assert "event: denied" in waited.text
    assert api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    ).status_code == 400
