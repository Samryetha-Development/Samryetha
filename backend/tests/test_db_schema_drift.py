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


def test_ensure_schema_drift_adds_moderation_window_columns(db):
    """确认窗口那一组列必须能补进既有库。

    运行库直接打开既有 SQLite、不跑 DDL，所以 `moderation_queue` 新增的
    hold_until / resolution / resolved_at / recheck / overturned 全靠 ensure_schema_drift
    补列。约束是：NOT NULL 的列必须带 server_default（recheck 有、其余可空），
    否则会主动报错——这条用例就是防止以后有人加一列没默认值的 NOT NULL 上去。

    顺带钉住两个渲染坑：`server_default=""` 必须写成 `DEFAULT ''`（拼字符串会生成
    `DEFAULT  NOT NULL` 语法错误），以及没写类型的 FK 列（NullType）不能编译类型。
    """
    new_columns = ("hold_until", "resolution", "resolved_at", "recheck", "overturned", "submitted_text", "superseded_at")
    with db.engine.begin() as conn:
        # 列上挂着索引，得先拆索引再拆列（SQLite 不允许带列删掉被索引引用的列）。
        conn.exec_driver_sql("DROP INDEX IF EXISTS moderation_queue_hold_idx")
        for column in new_columns:
            conn.exec_driver_sql(f"ALTER TABLE moderation_queue DROP COLUMN {column}")
    assert not (set(new_columns) & _columns(db, "moderation_queue"))

    db.ensure_schema_drift()

    assert set(new_columns) <= _columns(db, "moderation_queue")
    # 索引也要跟着回来，否则复审 worker 的扫描在存量库上会退化成全表扫。
    assert "moderation_queue_hold_idx" in {
        row[1] for row in db.engine.connect().exec_driver_sql("PRAGMA index_list(moderation_queue)")
    }
    # 补列后既有行要能读出来（recheck 的 NOT NULL 默认值回填为空串）
    with db.engine.connect() as conn:
        conn.exec_driver_sql("SELECT id, recheck, overturned, resolution FROM moderation_queue LIMIT 1")

    # 再跑一次仍是 no-op（索引是 IF NOT EXISTS，不会重复建而报错）
    db.ensure_schema_drift()


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


def test_ensure_schema_drift_adds_pending_profile_columns(db):
    """资料待审列必须能补进既有库（and 必须是可空——存量行没有待审资料）。"""
    new_columns = ("pending_display_name", "pending_bio")
    with db.engine.begin() as conn:
        for column in new_columns:
            conn.exec_driver_sql(f"ALTER TABLE users DROP COLUMN {column}")
    assert not (set(new_columns) & _columns(db, "users"))

    db.ensure_schema_drift()

    assert set(new_columns) <= _columns(db, "users")
    with db.engine.connect() as conn:
        conn.exec_driver_sql("SELECT id, pending_display_name, pending_bio FROM users LIMIT 1")


def test_ensure_schema_drift_backfills_hold_until_once(db):
    """补 hold_until 时必须给"补列前就在队里"的 pending 行回填窗口截止时间。

    复审 worker 只扫 `hold_until IS NOT NULL`（NULL 的语义是"不设窗口、一直等人"，
    那是 AUTOMOD_CONFIRM_WINDOW_SECONDS=0 的行为）。缺列升级上来的存量 pending 行
    hold_until 是 NULL，不回填就既不落定也不放行，永久卡住。

    关键是**只能回填一次**：之后用 window=0 故意入队的行不能被下次启动覆盖。
    """
    with db.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO users (username, email, display_name, password_hash)"
            " VALUES ('a', 'a@example.com', 'A', 'h')"
        )
        conn.exec_driver_sql(
            "INSERT INTO moderation_queue"
            " (content_type, content_id, author_id, excerpt, decision, score, signals, review_state, created_at)"
            " VALUES ('discussion', 1, 1, 'x', 'block', 100, '[]', 'pending', 1000)"
        )
        # 模拟缺列的老库
        conn.exec_driver_sql("DROP INDEX IF EXISTS moderation_queue_hold_idx")
        conn.exec_driver_sql("ALTER TABLE moderation_queue DROP COLUMN hold_until")

    db.ensure_schema_drift()

    with db.engine.connect() as conn:
        backfilled = conn.exec_driver_sql(
            "SELECT hold_until FROM moderation_queue WHERE content_id = 1"
        ).first()[0]
    # 按默认窗口（60 秒）补上
    assert backfilled == 1000 + 60_000

    with db.engine.begin() as conn:
        # 之后新入队、故意不设窗口的行（window=0 → hold_until=NULL）
        conn.exec_driver_sql(
            "INSERT INTO moderation_queue"
            " (content_type, content_id, author_id, excerpt, decision, score, signals, review_state, created_at)"
            " VALUES ('discussion', 2, 1, 'y', 'block', 100, '[]', 'pending', 2000)"
        )

    db.ensure_schema_drift()

    with db.engine.connect() as conn:
        untouched = conn.exec_driver_sql(
            "SELECT hold_until FROM moderation_queue WHERE content_id = 2"
        ).first()[0]
    assert untouched is None, "window=0 的语义（一直等人）被启动时的回填覆盖了"


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
