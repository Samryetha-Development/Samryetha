#!/usr/bin/env python3
"""手动触发附件孤儿回收（F06）——清理未挂帖且超过 N 天的暂存附件。

做成**可手动触发**的维护脚本（可挂 cron/手动跑），而非不可关闭的常驻定时器。
默认回收超过 7 天的孤儿附件；用 --older-than-days 调整。

Manual trigger for orphaned forum-attachment recycle (F06): cleans up staged attachments that are
unposted and older than N days. It is a manually-triggered script (cron or by hand), not an
always-on timer. Default reaps attachments older than 7 days; tune with --older-than-days.

用法 / Usage:
    python backend/scripts/reap-orphan-attachments.py [--older-than-days N]
"""

from __future__ import annotations

import argparse

from samryetha.adapters.storage import Storage
from samryetha.attachments import AttachmentService
from samryetha.core.config import load_settings
from samryetha.core.db import Database


def main() -> int:
    parser = argparse.ArgumentParser(description="Reap orphaned forum attachments")
    parser.add_argument(
        "--older-than-days", type=int, default=7, help="reap attachments older than this many days (default 7)"
    )
    args = parser.parse_args()

    settings = load_settings()
    db = Database(settings.database_url)
    db.create_schema()
    db.ensure_schema_drift()
    storage = Storage(settings.upload_dir, settings.storage_secret)
    older_than_ms = args.older_than_days * 24 * 3600 * 1000
    try:
        with db.request_conn() as conn:
            reaped = AttachmentService(conn, storage=storage).reap_orphans(older_than_ms)
        print(f"reaped {reaped} orphaned attachment(s) older than {args.older_than_days} day(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
