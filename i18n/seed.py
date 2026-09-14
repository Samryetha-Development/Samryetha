#!/usr/bin/env python3
"""将 seed/ 目录下的 JSON 文件导入 i18n DB。

用法：
  uv run python seed.py [--db PATH] [--locale LOCALE]

  --db      目标 SQLite 路径（默认读 I18N_DATABASE_URL 或 ./data/i18n.db）
  --locale  只导入指定 locale（默认全部）
  --dry-run 仅打印条目，不写入数据库

seed JSON 格式见 seed/zh-CN.json。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed i18n catalog from JSON files")
    parser.add_argument("--db", default="", help="SQLite path (overrides I18N_DATABASE_URL)")
    parser.add_argument("--locale", default="", help="Import only this locale")
    parser.add_argument("--dry-run", action="store_true", help="Print entries without writing")
    args = parser.parse_args()

    db_url = args.db or os.environ.get("I18N_DATABASE_URL", "./data/i18n.db")
    seed_dir = Path(__file__).parent / "seed"

    if not seed_dir.exists():
        print(f"Seed directory not found: {seed_dir}", file=sys.stderr)
        sys.exit(1)

    # 收集要导入的文件
    files = sorted(seed_dir.glob("*.json"))
    if args.locale:
        files = [f for f in files if f.stem == args.locale]
        if not files:
            print(f"No seed file found for locale: {args.locale}", file=sys.stderr)
            sys.exit(1)

    if args.dry_run:
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            locale = data["locale"]
            entries = data["entries"]
            print(f"[dry-run] locale={locale}, entries={len(entries)}")
            for e in entries:
                print(f"  {e['key']!r:40s} = {e['value']!r}")
        return

    # 实际写入
    # 延迟 import（避免顶层依赖 sqlalchemy 在脚本未安装时报错）
    try:
        from sqlalchemy import create_engine, event, insert, select, text, update
    except ImportError:
        print("sqlalchemy not installed. Run: uv sync", file=sys.stderr)
        sys.exit(1)

    # 确保目录存在
    parent = os.path.dirname(db_url)
    if parent:
        os.makedirs(parent, exist_ok=True)

    engine = create_engine(
        "sqlite:///" + db_url,
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # 先建表
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from i18n_svc.schema import metadata
    from i18n_svc.schema import catalog_entries

    metadata.create_all(engine)

    import time
    now_ms = int(time.time() * 1000)

    total_inserted = 0
    total_updated = 0

    with engine.begin() as conn:
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            locale = data["locale"]
            entries = data["entries"]
            print(f"Seeding locale={locale} ({len(entries)} entries)…")

            for e in entries:
                key = e["key"]
                value = e["value"]
                description = e.get("description")

                existing = conn.execute(
                    select(catalog_entries).where(
                        catalog_entries.c.locale == locale,
                        catalog_entries.c.key == key,
                    )
                ).first()

                if existing:
                    conn.execute(
                        update(catalog_entries)
                        .where(
                            catalog_entries.c.locale == locale,
                            catalog_entries.c.key == key,
                        )
                        .values(value=value, description=description, updated_at=now_ms)
                    )
                    total_updated += 1
                else:
                    conn.execute(
                        insert(catalog_entries).values(
                            locale=locale,
                            key=key,
                            value=value,
                            description=description,
                            created_at=now_ms,
                            updated_at=now_ms,
                        )
                    )
                    total_inserted += 1

    print(f"Done. inserted={total_inserted}, updated={total_updated}")


if __name__ == "__main__":
    main()
