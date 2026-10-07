"""schema 漂移兜底：运行库缺列时 startup 幂等补列（无迁移框架）。"""

from __future__ import annotations

import sqlite3


def _columns(db, table: str) -> set[str]:
    with db.engine.connect() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}


def test_ensure_schema_drift_adds_missing_column(db):
    # 全新库 schema 完整，先拿 users 现列
    assert "recovery_email" in _columns(db, "users")
    # 模拟旧运行库：手动删掉 recovery_email（SQLite ≥3.35 支持 DROP COLUMN）
    with db.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE users DROP COLUMN recovery_email")
    assert "recovery_email" not in _columns(db, "users")

    # 幂等补列 → 列回来了
    db.ensure_schema_drift()
    assert "recovery_email" in _columns(db, "users")

    # 再跑一次是 no-op，不报错
    db.ensure_schema_drift()


def test_ensure_schema_drift_is_noop_on_fresh_schema(db):
    # 全新库已是最新 schema：补列跑一遍是 no-op，不报错、不删表、不改列。
    before = _columns(db, "users")
    db.ensure_schema_drift()
    db.ensure_schema_drift()
    assert _columns(db, "users") == before


def test_add_column_sql_handles_empty_default_and_untyped_fk():
    """补列的 SQL 渲染：空串默认值要加引号；没写类型的 FK 列不能编译类型。

    这两条都是踩过的坑：
      - `server_default=""` 直接拼进 f-string 会生成 `DEFAULT  NOT NULL`，语法错误；
      - `Column("reviewer_id", ForeignKey("users.id"))` 的类型是 NullType，
        `col.type.compile()` 会抛 CompileError——SQLite 允许无类型列，省略即可。
    """
    from sqlalchemy import BigInteger, Column, ForeignKey, Integer, Text
    from sqlalchemy.dialects import sqlite as sqlite_dialect

    from samryetha.core.db import _add_column_sql

    dialect = sqlite_dialect.dialect()
    assert _add_column_sql(Column("recheck", Text, nullable=False, server_default=""), dialect) == (
        "recheck TEXT DEFAULT '' NOT NULL"
    )
    assert _add_column_sql(Column("overturned", Integer, nullable=False, server_default="0"), dialect) == (
        "overturned INTEGER DEFAULT '0' NOT NULL"
    )
    assert _add_column_sql(Column("hold_until", BigInteger), dialect) == "hold_until BIGINT"
    assert _add_column_sql(Column("reviewer_id", ForeignKey("users.id")), dialect) == "reviewer_id"


def test_lifespan_normalizes_legacy_moderator_role(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from samryetha.core.config import Settings
    from samryetha.main import create_app
    from samryetha.core.schema import users

    app = create_app(Settings(_env_file=None, database_url=str(tmp_path / "legacy.db"), upload_dir=str(tmp_path / "uploads")))
    app.state.db.create_schema()
    with app.state.db.request_conn() as conn:
        conn.execute(users.insert().values(username="legacy", email="legacy@example.com", display_name="Legacy", password_hash="h", role="moderator"))
    with TestClient(app):
        with app.state.db.request_conn() as conn:
            assert conn.execute(select(users.c.role).where(users.c.username == "legacy")).scalar_one() == "admin"
