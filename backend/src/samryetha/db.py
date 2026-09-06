"""SQLite 连接 + 请求级事务 + schema 反射。

镜像 backend/src/infrastructure/db/client.ts 的 PRAGMA 与"单连接请求事务"语义：
- journal_mode=WAL, foreign_keys=ON, busy_timeout=5000, synchronous=NORMAL
- 每请求一个连接 + 一个事务（BEGIN 隐式；成功提交 / 异常回滚），与 outbox 同事务原子提交。
- 时间戳统一毫秒 int；schema 见 schema.py。
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
        def _set_pragma(dbapi_conn, _record):  # noqa: ANN001
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
        """无迁移框架的兜底：对已存在的表，按 schema.py 幂等补齐缺失列。

        schema.py 是唯一真源，但运行时直接打开既有 SQLite 不跑 DDL；当 schema 演进
        （如新增列）而库里还没有时，这里 ALTER TABLE ADD COLUMN 补齐。
        只处理已经存在的表；缺表交给 create_schema（create_all 幂等）。
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

    def raw_conn(self) -> Connection:
        """裸连接（VACUUM INTO 等特殊场景；业务代码不要用）。"""
        return self.engine.connect()

    def close(self) -> None:
        self.engine.dispose()


def run_scalar(conn: Connection, statement) -> object | None:  # noqa: ANN001
    """SELECT 1 等单值查询。"""
    row = conn.execute(statement).first()
    if row is None:
        return None
    return row[0]
