"""SQLite 连接封装 — 镜像主站 backend/src/samryetha/db.py 的风格。

i18n 服务维护两个 Database 实例：
  db      — i18n 自己的库（catalog_entries、submissions）
  auth_db — 读取 samryetha_session / users / sessions（可与 i18n db 同库）

WAL / foreign_keys / busy_timeout / synchronous 均与主站保持一致。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from .schema import metadata


def now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _make_engine(url: str) -> Engine:
    if url == ":memory:":
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
    else:
        parent = os.path.dirname(url)
        if parent:
            os.makedirs(parent, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{url}",
            connect_args={"check_same_thread": False, "timeout": 5},
        )

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


class Database:
    def __init__(self, url: str) -> None:
        self.database_url = url
        self.engine: Engine = _make_engine(url)

    def create_schema(self) -> None:
        """建 i18n 自身的表（对既有库是 no-op）。"""
        metadata.create_all(self.engine)

    def ensure_schema_drift(self) -> None:
        """幂等补齐 schema 演进中新增的列（无迁移框架兜底）。"""
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
                    if col.primary_key or col.unique or (col.nullable is False and col.server_default is None):
                        raise RuntimeError(
                            f"Column {table.name}.{col.name} cannot be auto-added to an existing table"
                        )
                    parts = [col.name, col.type.compile(dialect=dialect)]
                    if col.server_default is not None:
                        parts.append(f"DEFAULT {col.server_default.arg}")
                    if col.nullable is False:
                        parts.append("NOT NULL")
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {' '.join(parts)}")

    @contextmanager
    def request_conn(self) -> Iterator[Connection]:
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

    def close(self) -> None:
        self.engine.dispose()


class AuthDatabase:
    """只读包装器：连接主站（或测试独立）auth DB，用于验证 samryetha_session。

    auth DB 不由本服务管理 schema；仅做 SELECT。
    """

    def __init__(self, url: str) -> None:
        self.database_url = url
        self.engine: Engine = _make_engine(url)

    @contextmanager
    def request_conn(self) -> Iterator[Connection]:
        """每请求一个连接 + 只读事务（不提交写入）。"""
        conn = self.engine.connect()
        try:
            yield conn
        finally:
            conn.close()

    def close(self) -> None:
        self.engine.dispose()


def run_scalar(conn: Connection, statement) -> object | None:  # noqa: ANN001
    row = conn.execute(statement).first()
    if row is None:
        return None
    return row[0]
