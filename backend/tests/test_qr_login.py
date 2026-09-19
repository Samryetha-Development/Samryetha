"""扫码登录：发起→确认页上下文→批准→SSE→兑换；拒绝/过期/冒用一律失败。"""

from __future__ import annotations

from sqlalchemy import select, update

from samryetha.schema import qr_login_tickets, users


def _register_active(client, username: str, password: str = "old-password-123") -> int:
    user_id = client.post("/api/auth/register", json={"username": username, "password": password}).json()["userId"]
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(status="active"))
    return user_id


def _start(client) -> dict:
    response = client.post("/api/auth/qr/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["qr_data_uri"].startswith("data:image/svg+xml;base64,")
    assert body["approve_url"].endswith("/qr/approve?t=" + body["ticket_id"])
    return body


def test_qr_full_flow_approve_exchange_single_use(api):
    phone_id = _register_active(api.c, "phoneuser")
    api.c.post("/api/auth/login", json={"username": "phoneuser", "password": "old-password-123"})
    started = _start(api.c)

    info = api.c.get("/api/auth/qr/info", params={"ticket_id": started["ticket_id"]})
    assert info.status_code == 200
    assert info.json()["ip"] is not None

    assert api.c.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"]}).status_code == 200

    waited = api.c.get("/api/auth/qr/wait", params={"ticket_id": started["ticket_id"]})
    assert waited.status_code == 200
    assert "event: approved" in waited.text

    exchanged = api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": "wrong-secret"}
    )
    assert exchanged.status_code == 400
    ok = api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    )
    assert ok.status_code == 200, ok.text
    assert "samryetha_session" in ok.cookies
    assert api.c.get("/api/auth/me").json()["user"]["id"] == phone_id
    # 单次有效：用过即焚
    assert api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": started["secret"]}
    ).status_code == 400


def test_qr_exchange_needs_secret_and_approval(api):
    _register_active(api.c, "owner")
    started = _start(api.c)
    # 未批准不可兑换
    assert api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": "x"}
    ).status_code == 400
    # 批准要登录
    logged_out = api.c
    logged_out.post("/api/auth/logout")
    assert logged_out.post("/api/auth/qr/approve", json={"ticket_id": started["ticket_id"]}).status_code == 401


def test_qr_deny_and_expiry(api):
    _register_active(api.c, "decider")
    api.c.post("/api/auth/login", json={"username": "decider", "password": "old-password-123"})
    started = _start(api.c)
    assert api.c.post("/api/auth/qr/deny", json={"ticket_id": started["ticket_id"]}).status_code == 200
    waited = api.c.get("/api/auth/qr/wait", params={"ticket_id": started["ticket_id"]})
    assert "event: denied" in waited.text
    assert api.c.post(
        "/api/auth/qr/exchange", json={"ticket_id": started["ticket_id"], "secret": "x"}
    ).status_code == 400

    started2 = _start(api.c)
    with api.c.app.state.db.request_conn() as conn:
        from samryetha.db import now_ms
        from samryetha.security import hash_token as _hash_token

        conn.execute(
            update(qr_login_tickets)
            .where(qr_login_tickets.c.ticket_id_hash == _hash_token(started2["ticket_id"]))
            .values(expires_at=now_ms() - 1)
        )
    waited2 = api.c.get("/api/auth/qr/wait", params={"ticket_id": started2["ticket_id"]})
    assert "event: expired" in waited2.text
    assert api.c.get("/api/auth/qr/info", params={"ticket_id": "nope"}).status_code == 400
