"""S1 认证与账户、关注 — 镜像 auth/users/follows 行为。"""

from __future__ import annotations

from sqlalchemy import select, update

from samryetha.db import now_ms
from samryetha.schema import moderation_actions, password_reset_tokens, sessions, users


def _activate(client, username: str) -> None:
    db = client.app.state.db
    with db.request_conn() as conn:
        conn.execute(
            update(users)
            .where(users.c.username == username)
            .values(status="active", email_verified_at=now_ms())
        )


def _mkuser(client, username: str, password: str = "password123"):
    res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert res.status_code == 201, res.text
    _activate(client, username.strip().lower().lstrip("@"))
    return res


def _login(client, username: str, password: str):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_register_returns_pending(client):
    res = client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    assert res.status_code == 201
    body = res.json()
    assert body["message"] == "pending"
    assert isinstance(body["userId"], int)
    # pending 不能登录
    r = _login(client, "alice", "password123")
    assert r.status_code == 403
    assert r.json()["error"]["message"] == "Your account is awaiting admin approval"


def test_register_duplicate_409(client):
    _mkuser(client, "bob")
    res = client.post("/api/auth/register", json={"username": "bob", "password": "password123"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"
    assert res.json()["error"]["message"] == "That username is already taken"


def test_register_username_upper_normalized(client):
    _mkuser(client, "Carol")
    # normalize → carol
    r = _login(client, "carol", "password123")
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "carol"


def test_login_sets_cookie_and_me(client):
    _mkuser(client, "alice")
    res = _login(client, "alice", "password123")
    assert res.status_code == 200
    body = res.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["handle"].startswith("alice#")
    assert body["user"]["status"] == "active"
    assert body["user"]["emailVerified"] is True
    assert isinstance(body["sessionExpiresAt"], int)
    assert "samryetha_session" in res.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]


def test_login_wrong_password_and_missing_user(client):
    _mkuser(client, "alice")
    r = _login(client, "alice", "wrongpass")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    # 不存在账号：同样 401（不暴露存在性）
    r2 = _login(client, "ghost", "whatever1")
    assert r2.status_code == 401


def test_logout_clears_session(client):
    _mkuser(client, "alice")
    _login(client, "alice", "password123")
    res = client.post("/api/auth/logout")
    assert res.status_code == 204
    me = client.get("/api/auth/me")
    assert me.status_code == 401
    assert me.json()["error"]["code"] == "AUTH_REQUIRED"


def test_change_password_logs_out_everywhere(client):
    _mkuser(client, "alice")
    _login(client, "alice", "password123")
    res = client.post(
        "/api/auth/change-password",
        json={"currentPassword": "password123", "newPassword": "newpass456"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    # 旧会话已全部作废
    assert client.get("/api/auth/me").status_code == 401
    # 旧密码失效
    assert _login(client, "alice", "password123").status_code == 401
    # 新密码可登录
    assert _login(client, "alice", "newpass456").status_code == 200


def test_password_reset_is_hashed_one_time_and_invalidates_sessions(client):
    _mkuser(client, "alice")
    _login(client, "alice", "password123")
    with client.app.state.db.request_conn() as conn:
        conn.execute(update(users).where(users.c.username == "alice").values(recovery_email="alice@example.com"))
    sent: list[str] = []
    client.app.state.mailer.send = lambda **kwargs: sent.append(kwargs["text"])
    assert client.post("/api/auth/forgot-password", json={"username": "alice", "recoveryEmail": "alice@example.com"}).status_code == 200
    with client.app.state.db.request_conn() as conn:
        row = conn.execute(select(password_reset_tokens)).first()
        assert row is not None
        assert "alice" not in row.token_hash
    assert sent
    token = sent[0].rsplit("?token=", 1)[1]
    assert client.post("/api/auth/reset-password", json={"token": token, "newPassword": "newpass456"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert _login(client, "alice", "newpass456").status_code == 200
    again = client.post("/api/auth/reset-password", json={"token": token, "newPassword": "otherpass789"})
    assert again.status_code == 400
    assert again.json()["error"]["message"] == "Reset link is invalid or expired"


def test_forgot_password_does_not_enumerate_accounts(client):
    r = client.post("/api/auth/forgot-password", json={"username": "ghost", "recoveryEmail": "ghost@example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_profile_patch(client):
    _mkuser(client, "alice")
    _login(client, "alice", "password123")
    res = client.patch("/api/me/profile", json={"displayName": "Alice L", "bio": "hi there"})
    assert res.status_code == 200
    u = res.json()["user"]
    assert u["displayName"] == "Alice L"
    assert u["bio"] == "hi there"

    # 空对象 → 422 No fields to update
    r2 = client.patch("/api/me/profile", json={})
    assert r2.status_code == 422
    assert r2.json()["error"]["details"][0]["message"] == "No fields to update"


def test_profile_username_conflict_409(client):
    _mkuser(client, "alice")
    _mkuser(client, "bob")
    _login(client, "alice", "password123")
    res = client.patch("/api/me/profile", json={"username": "bob"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "CONFLICT"


def test_public_profile_and_follows(client):
    _mkuser(client, "alice")
    _mkuser(client, "bob")
    _login(client, "alice", "password123")

    # 匿名看 profile
    guest = client.post("/api/auth/logout")
    assert guest.status_code == 204
    prof = client.get("/api/users/bob")
    assert prof.status_code == 200
    p = prof.json()
    assert p["username"] == "bob"
    assert p["stats"] == {"discussions": 0, "replies": 0, "followers": 0, "following": 0}
    assert p["isFollowing"] is False

    # alice 关注 bob
    _login(client, "alice", "password123")
    f = client.post("/api/users/bob/follow")
    assert f.status_code == 200
    assert f.json() == {"following": True}
    prof2 = client.get("/api/users/bob")
    assert prof2.json()["stats"]["followers"] == 1
    assert prof2.json()["isFollowing"] is True

    # 幂等关注
    f2 = client.post("/api/users/bob/follow")
    assert f2.status_code == 200
    assert client.get("/api/users/bob").json()["stats"]["followers"] == 1

    # 取关
    u = client.delete("/api/users/bob/follow")
    assert u.status_code == 200
    assert u.json() == {"following": False}
    assert client.get("/api/users/bob").json()["stats"]["followers"] == 0


def test_cannot_follow_self(client):
    _mkuser(client, "alice")
    _login(client, "alice", "password123")
    res = client.post("/api/users/alice/follow")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_user_profile_404(client):
    res = client.get("/api/users/nobody")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
