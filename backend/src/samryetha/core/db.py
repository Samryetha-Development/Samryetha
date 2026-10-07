"""SQLite 连接 + 请求级事务 + schema 反射。

镜像 backend/src/infrastructure/db/client.ts 的 PRAGMA 与"单连接请求事务"语义：
- journal_mode=WAL, foreign_keys=ON, busy_timeout=5000, synchronous=NORMAL
- 每请求一个连接 + 一个事务（BEGIN 隐式；成功提交 / 异常回滚），与 outbox 同事务原子提交。
- 时间戳统一毫秒 int；schema 见 schema.py。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Column, create_engine, event
from sqlalchemy.engine import Connection, Dialect, Engine
from sqlalchemy.schema import DefaultClause, FetchedValue

from .schema import metadata


def now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _render_default(server_default: FetchedValue, dialect: Dialect) -> str:
    """把 server_default 渲染成 SQL 字面量。

    不能直接对 `server_default.arg` 做字符串拼接：`server_default=""` 的 arg 就是空串，
    `f"DEFAULT {arg}"` 会生成 `DEFAULT  NOT NULL` 这种语法错误——而空串默认值在本项目里
    很常见（Text 列普遍 `server_default=""`），所以这里必须按字面量正确加引号。
    """
    if not isinstance(server_default, DefaultClause):
        raise RuntimeError("Fetched database defaults cannot be rendered in ALTER TABLE")
    arg = server_default.arg
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return str(arg.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


def _add_column_sql(col: Column[object], dialect: Dialect) -> str:
    """把一列渲染成 `ALTER TABLE ... ADD COLUMN` 可用的定义。

    `col.type.compile()` 对没有写类型的列（schema.py 里
    `Column("reviewer_id", ForeignKey("users.id"))` 就是这种，类型是 NullType）会抛
    CompileError；SQLite 允许无类型列，所以这类列退化成不带类型。
    """
    try:
        type_sql = col.type.compile(dialect=dialect)
    except Exception:  # noqa: BLE001 — NullType 等无法编译的类型：SQLite 允许省略
        type_sql = ""
    parts = [col.name, type_sql] if type_sql else [col.name]
    if col.server_default is not None:
        parts.extend(["DEFAULT", _render_default(col.server_default, dialect)])
    if col.nullable is False:
        parts.append("NOT NULL")
    return " ".join(parts)


class Database:
    def __init__(self, url: str) -> None:
        # 备份(VACUUM INTO/restore)需要知道库文件路径
        self.database_url = url
        if url == ":memory:":
            self.engine: Engine = create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                pool_pre_ping=True,
            )
        else:
            # 确保目录存在（TS 端也这么做）
            parent = os.path.dirname(url)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self.engine = create_engine(
                f"sqlite:///{url}",
                connect_args={"check_same_thread": False, "timeout": 5},
            )

        @event.listens_for(self.engine, "connect")
        def _set_pragma(dbapi_conn: sqlite3.Connection, _record: object) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    def create_schema(self) -> None:
        """仅用于全新库/测试：按 schema.py 建所有表（对既有库是 no-op，运行时不会调用）。"""
        metadata.create_all(self.engine)

    def ensure_schema_drift(self) -> None:
        """无迁移框架的兜底：对已存在的表，按 schema.py 幂等补齐缺失列与索引。

        schema.py 是唯一真源，但运行时直接打开既有 SQLite 不跑 DDL；当 schema 演进
        （如新增列）而库里还没有时，这里 ALTER TABLE ADD COLUMN 补齐。
        只处理已经存在的表；缺表交给 create_schema（create_all 幂等）。

        索引同样要补：新增列往往配套新索引，而 create_all 对既有表是 no-op。
        缺索引只影响性能、不影响正确性，但漏掉它会让新加的扫描查询在存量库上退化成全表扫。

        个别新增列还需要**一次性回填**才算真正落地（见下面的 hold_until），
        回填只在"这一列是本次刚补上的"时执行，所以对已经升过级的库是 no-op。
        """
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.dialects import sqlite as sqlite_dialect

        dialect = sqlite_dialect.dialect()
        existing_tables = set(sa_inspect(self.engine).get_table_names())
        with self.engine.begin() as conn:
            for table in metadata.sorted_tables:
                if table.name not in existing_tables:
                    continue
                existing_cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
                for col in table.columns:
                    if col.name in existing_cols:
                        continue
                    # 只补能安全 ADD 的：非 PK/UNIQUE，NOT NULL 必须有默认值（否则存量行无法回填）
                    if col.primary_key or col.unique or (col.nullable is False and col.server_default is None):
                        raise RuntimeError(
                            f"Column {table.name}.{col.name} cannot be auto-added to an existing table"
                        )
                    parts = _add_column_sql(col, dialect)
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {parts}")
                for index in table.indexes:
                    columns = ", ".join(col.name for col in index.columns)
                    unique = "UNIQUE " if index.unique else ""
                    conn.exec_driver_sql(
                        f"CREATE {unique}INDEX IF NOT EXISTS {index.name} ON {table.name} ({columns})"
                    )

    @contextmanager
    def request_conn(self) -> Generator[Connection]:
        """每请求一个连接 + 一个事务。成功后提交，异常时回滚并向上抛。"""
        conn = self.engine.connect()
        trans = conn.begin()
        try:
            yield conn
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

    def retire_content_review(self) -> None:
        """Release legacy content without dropping historical columns or queue records."""
        with self.engine.begin() as conn:
            # Acquire the write lock before testing the one-time migration marker.
            conn.exec_driver_sql("UPDATE app_settings SET value = value WHERE key = 'content_review_removed_v1'")
            if conn.exec_driver_sql(
                "SELECT 1 FROM app_settings WHERE key = 'content_review_removed_v1'"
            ).first() is not None:
                return
            tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in ("discussions", "replies", "direct_messages", "file_resources"):
                if table not in tables:
                    continue
                columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
                if "moderation_status" in columns:
                    conn.exec_driver_sql(f"UPDATE {table} SET moderation_status = 'approved'")
            user_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)")}
            if {"pending_display_name", "pending_bio"} <= user_columns:
                conn.exec_driver_sql(
                    "UPDATE users SET display_name = COALESCE(pending_display_name, display_name), "
                    "bio = COALESCE(pending_bio, bio), pending_display_name = NULL, pending_bio = NULL"
                )
            if "profile_moderation_status" in user_columns:
                conn.exec_driver_sql("UPDATE users SET profile_moderation_status = 'approved'")
            if "moderation_queue" in tables:
                # An explicitly deleted parent remains deleted; only review-blocked
                # attachments belonging to live discussions are released.
                conn.exec_driver_sql(
                    "UPDATE attachments SET state = 'attached' WHERE state = 'orphaned' "
                    "AND discussion_id IN (SELECT id FROM discussions WHERE deleted_at IS NULL) "
                    "AND id IN (SELECT content_id FROM moderation_queue "
                    "WHERE content_type = 'attachment' AND (decision = 'block' OR review_state = 'rejected'))"
                )
            conn.exec_driver_sql(
                "UPDATE outbox_events SET status = 'pending', available_at = ? "
                "WHERE status = 'held' AND event_type IN ('discussion.created', 'reply.created', 'mention.created')",
                (now_ms(),),
            )
            conn.exec_driver_sql("INSERT INTO app_settings (key, value) VALUES ('content_review_removed_v1', 'true')")

    def raw_conn(self) -> Connection:
        """裸连接（VACUUM INTO 等特殊场景；业务代码不要用）。"""
        return self.engine.connect()

    def close(self) -> None:
        self.engine.dispose()
