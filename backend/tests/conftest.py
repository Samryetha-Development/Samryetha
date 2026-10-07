from __future__ import annotations

import os

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel

# 测试进程统一跑在 NODE_ENV=test：auth 限流走放宽档（镜像 TS auth/routes.ts 的 test 分支）
os.environ.setdefault("NODE_ENV", "test")

from samryetha.core.config import Settings
from samryetha.core.db import Database
from samryetha.core.errors import bad_request, not_found
from samryetha.main import create_app

pytest_plugins = []

# --- Demo 路由：供错误包络/验证形状测试。定义在模块级（conftest 函数内动态建类在
# --- 本 FastAPI/pydantic 组合下会异常，模块级稳定）。


class EchoBody(BaseModel):
    name: str


demo = APIRouter()


@demo.get("/demo/bad")
def _demo_bad():
    raise bad_request("nope")


@demo.get("/demo/notfound")
def _demo_nf():
    raise not_found("Missing")


@demo.post("/demo/echo")
def _demo_echo(body: EchoBody):
    return {"name": body.name}


@pytest.fixture
def client(tmp_path):
    """Demo 应用：健康 + 供错误包络测试的几个临时端点。"""
    db_path = str(tmp_path / "test.db")
    app = create_app(
        Settings(
            _env_file=None,
            database_url=db_path,
            upload_dir=str(tmp_path / "uploads"),
        )
    )
    app.state.db.create_schema()
    app.include_router(demo)
    with TestClient(app) as c:
        yield c


class Api:
    """测试便捷层：建用户/登录/seed builtin（绑同一个 TestClient/app/db）。"""

    def __init__(self, client):
        self.c = client
        self.app = client.app
        self.settings = client.app.state.settings

    def seed_builtin(self):
        from samryetha.auth import AuthService

        with self.app.state.db.request_conn() as conn:
            AuthService(conn, settings=self.settings).ensure_builtin_accounts()

    def login(self, username: str, password: str | None = None):
        password = password or "password123"
        r = self.c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        return r

    def login_dev(self):
        self.seed_builtin()
        return self.login("dev", self.settings.dev_password)

    def register(self, username: str, password: str = "password123"):
        r = self.c.post("/api/auth/register", json={"username": username, "password": password})
        assert r.status_code == 201, r.text
        return r

    def activate(self, username: str, role: str | None = None):
        from sqlalchemy import update

        from samryetha.core.db import now_ms
        from samryetha.core.schema import users

        with self.app.state.db.request_conn() as conn:
            conn.execute(
                update(users)
                .where(users.c.username == username)
                .values(status="active", email_verified_at=now_ms())
            )

    def mkuser(self, username: str, password: str = "password123", role: str | None = None):
        self.register(username, password)
        from sqlalchemy import update

        from samryetha.core.db import now_ms
        from samryetha.core.schema import users

        with self.app.state.db.request_conn() as conn:
            values = {"status": "active", "email_verified_at": now_ms()}
            if role:
                values["role"] = role
            conn.execute(
                update(users).where(users.c.username == username).values(**values)
            )
        return username


@pytest.fixture
def api(client):
    return Api(client)


@pytest.fixture
def db(tmp_path):
    """裸 Database（供 security 等服务层测试）。"""
    db_path = str(tmp_path / "srv.db")
    database = Database(db_path)
    database.create_schema()
    yield database
    database.close()
