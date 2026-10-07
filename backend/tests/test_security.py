"""argon2id + 会话 基座测试。"""

from __future__ import annotations

from samryetha.db import now_ms
from samryetha.schema import users
from samryetha.auth.security import (
    create_session,
    delete_session,
    get_session_user,
    hash_password,
    hash_token,
    verify_password,
)
from sqlalchemy import insert


def _insert_user(conn) -> int:
    _now = now_ms()
    res = conn.execute(
        insert(users).values(
            username="alice",
            email="alice@example.edu.cn",
            display_name="Alice",
            bio="",
            password_hash=hash_password("hunter22"),
            role="student",
            status="active",
            settings="{}",
            created_at=_now,
            updated_at=_now,
        )
    )
    return res.inserted_primary_key[0]


def test_password_roundtrip():
    h = hash_password("s3cret!")
    assert h.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password("s3cret!", h)
    assert not verify_password("wrong", h)
    assert not verify_password("s3cret!", "not-a-hash")


def test_token_hash_stability():
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


def test_session_create_and_read(db):
    with db.request_conn() as conn:
        uid = _insert_user(conn)
        token, _expires = create_session(conn, uid, {"ip": "127.0.0.1"})
        # 在同一事务里能读到
        row = get_session_user(conn, token)
        assert row is not None
        assert row["id"] == uid
        assert row["username"] == "alice"
        # 无效 token
        assert get_session_user(conn, "bogus") is None


def test_session_read_cross_request(db):
    with db.request_conn() as conn:
        uid = _insert_user(conn)
        token, _ = create_session(conn, uid)
    # 新事务（已提交）仍可读 → 落库正确
    with db.request_conn() as conn:
        row = get_session_user(conn, token)
        assert row is not None and row["id"] == uid
        delete_session(conn, token)
    with db.request_conn() as conn:
        assert get_session_user(conn, token) is None
