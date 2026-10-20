#!/usr/bin/env python3
"""只读排查内建账号身份（F07）——运维用，默认 dry-run，不做任何写操作。

用途：在已升级到 is_builtin 标记的生产库上人工确认"admin/dev 到底是不是真内建"。
自动回填无法逆向区分"旧代码已被利用过的冒名账号"，因此最终处置（保留/封禁）由运维决定；
本脚本只读列出证据，绝不自动改账号。

Read-only audit of the built-in account identity (F07). It never writes (opens the DB with
mode=ro). It helps ops manually confirm whether "admin"/"dev" are the genuine built-ins, since
the automated backfill cannot retroactively tell an already-exploited impostor apart.

用法 / Usage:
    python backend/scripts/audit-builtin-accounts.py [database_path]

不传 database_path 时，从环境变量 SAMRYETHA_DATABASE_URL 或 DATABASE_URL 读取，
并剥离 sqlite:/// 前缀。
"""

from __future__ import annotations

import os
import sqlite3
import sys


def _resolve_path(argv: list[str]) -> str | None:
    if len(argv) > 1:
        return argv[1]
    raw = os.environ.get("SAMRYETHA_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if raw:
        if raw == ":memory:":
            return None
        for prefix in ("sqlite:///", "sqlite://", "sqlite:"):
            if raw.startswith(prefix):
                return raw[len(prefix) :]
        return raw
    return None


def main() -> int:
    path = _resolve_path(sys.argv)
    if not path:
        print("用法: python backend/scripts/audit-builtin-accounts.py <database_path>")
        print("（或以 SAMRYETHA_DATABASE_URL / DATABASE_URL 指定 sqlite:///... 路径）")
        return 2

    if not os.path.exists(path):
        print(f"数据库不存在: {path}")
        return 2

    # 只读打开：mode=ro 强制不写；缺列时提示先跑升级/迁移。
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "is_builtin" not in cols:
            print("users 表还没有 is_builtin 列：请先完成升级（ensure_schema_drift + mark_builtin_identity）。")
            return 3

        print("=== ① is_builtin=1 的内建账号 ===")
        rows = conn.execute(
            "SELECT id, username, role, status, created_at, last_seen_at FROM users "
            "WHERE is_builtin = 1 ORDER BY id"
        ).fetchall()
        if not rows:
            print("（无）")
        for r in rows:
            print(f"  id={r[0]} username={r[1]!r} role={r[2]} status={r[3]} created={r[4]} last_seen={r[5]}")

        print("=== ② 疑似被占用/被提升过的账号（username∈{admin,dev} 但 is_builtin=0）===")
        rows = conn.execute(
            "SELECT id, username, role, status, email_verified_at, created_at, last_seen_at FROM users "
            "WHERE username IN ('admin','dev') AND COALESCE(is_builtin, 0) = 0 ORDER BY id"
        ).fetchall()
        if not rows:
            print("（无）")
        for r in rows:
            verdict = "疑似已被旧逻辑提升过" if r[2] == "admin" and r[4] is not None else "疑似冒名占名"
            print(
                f"  id={r[0]} username={r[1]!r} role={r[2]} status={r[3]} verified={r[4]} "
                f"created={r[5]} last_seen={r[6]} -> {verdict}"
            )
    finally:
        conn.close()
    print("只读排查完成：本脚本不修改任何数据。处置（保留/封禁冒名账号）请人工决定。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
