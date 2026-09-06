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
