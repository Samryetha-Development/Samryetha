"""测试 fixtures。

每个测试用例使用独立的临时 SQLite（i18n DB + auth DB 合一，方便测试中直接写入 users/sessions）。
auth DB schema（users + sessions）在 conftest 中手工建，不依赖主站代码。
"""

from __future__ import annotations

import hashlib
import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert, text

os.environ.setdefault("NODE_ENV", "test")

from i18n_svc.config import Settings
from i18n_svc.main import create_app


# ---------------------------------------------------------------- helpers

def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- auth schema DDL（复刻主站最小集）

_AUTH_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'student',
    status TEXT NOT NULL DEFAULT 'active',
    deleted_at INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    expires_at INTEGER NOT NULL,
    ip TEXT,
    user_agent TEXT,
    created_at INTEGER,
    last_seen_at INTEGER
);
"""


class AuthHelper:
    """测试便捷层：在 auth DB 中注入 user + session。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        # 建 auth 表
        with self._engine.begin() as conn:
            for stmt in _AUTH_DDL.strip().split(";"):
                s = stmt.strip()
                if s:
                    conn.exec_driver_sql(s)

    def add_user(
        self,
        username: str,
        role: str = "student",
        status: str = "active",
    ) -> int:
        now = _now_ms()
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO users (username, display_name, email, role, status) "
                    "VALUES (:u, :dn, :e, :r, :s)"
                ),
                {
                    "u": username,
                    "dn": username,
                    "e": f"{username}@test.example.edu.cn",
                    "r": role,
                    "s": status,
                },
            )
            return result.lastrowid

    def add_session(self, user_id: int, token: str) -> None:
        """注入一个有效 session（30 天过期）。"""
        now = _now_ms()
        expires = now + 30 * 24 * 3600 * 1000
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO sessions (token_hash, user_id, expires_at, created_at, last_seen_at) "
                    "VALUES (:th, :uid, :exp, :now, :now)"
                ),
                {"th": _hash_token(token), "uid": user_id, "exp": expires, "now": now},
            )

    def close(self):
        self._engine.dispose()


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def auth(tmp_path):
    """返回 AuthHelper，已建好 users/sessions 表。"""
    db_path = str(tmp_path / "test.db")
    helper = AuthHelper(db_path)
    yield helper
    helper.close()


@pytest.fixture
def client(tmp_path, auth):
    """TestClient，i18n DB 与 auth DB 合用同一个 SQLite 文件（简化测试）。"""
    db_path = str(tmp_path / "test.db")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=db_path,
            auth_db_url=db_path,
            supported_locales="zh-CN,en",
        )
    )
    # 建 i18n 自身的表
    app.state.db.create_schema()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api(client, auth):
    """高层 API 辅助，持有 client + auth 两个 fixture。"""
    return ApiHelper(client, auth)


class ApiHelper:
    def __init__(self, client: TestClient, auth: AuthHelper):
        self.c = client
        self.auth = auth
        self._tokens: dict[str, str] = {}  # username → raw token

    def login_as(self, username: str, role: str = "student") -> str:
        """在 auth DB 注入 user+session，返回 raw token（用于 cookie 注入）。"""
        import secrets

        token = secrets.token_urlsafe(32)
        uid = self.auth.add_user(username, role=role)
        self.auth.add_session(uid, token)
        self._tokens[username] = token
        return token

    def get(self, path: str, token: str | None = None, **kw):
        cookies = {"samryetha_session": token} if token else {}
        return self.c.get(path, cookies=cookies, **kw)

    def post(self, path: str, token: str | None = None, **kw):
        cookies = {"samryetha_session": token} if token else {}
        return self.c.post(path, cookies=cookies, **kw)

    def put(self, path: str, token: str | None = None, **kw):
        cookies = {"samryetha_session": token} if token else {}
        return self.c.put(path, cookies=cookies, **kw)

    def delete(self, path: str, token: str | None = None, **kw):
        cookies = {"samryetha_session": token} if token else {}
        return self.c.delete(path, cookies=cookies, **kw)
