#!/usr/bin/env python3
"""存量全局角色 `moderator` 的一次性订正：默认 dry-run，`--apply` 才写，且必须留审计。

One-off correction of legacy global `moderator` roles: dry-run by default, writes only with `--apply`,
and always leaves an audit trail.

## 背景

服务启动时曾经**无条件**把 `role='moderator'` 改成 `'admin'`，不写审计、不记日志。那已移除：启动现在
只打一条 WARNING（数量 + 用户 id + 该跑哪个脚本），改库只发生在你显式执行本脚本的时候。

The service used to rewrite `role='moderator'` to `'admin'` unconditionally at every boot, with no audit
and no log line. That is gone: startup now only emits a WARNING (count + user ids + which script to
run), and the database changes only when you run this script explicitly.

## 生产执行步骤

```bash
# 1) 先看计划（只读，不改库）
python backend/scripts/migrate_moderator_roles.py            # 用 backend/.env 的 DATABASE_URL
python backend/scripts/migrate_moderator_roles.py --database-url sqlite:////var/lib/samryetha/app.db

# 2) 确认计划里的 id / 用户名都是你认为该升的那几个

# 3) 执行（必须能解析出一个管理员作审计 actor）
python backend/scripts/migrate_moderator_roles.py --apply --actor <管理员用户名或 id>

# 4) 对账：库里的 role + moderation_actions 里的审计行
python backend/scripts/migrate_moderator_roles.py            # 应输出 found=0
sqlite3 app.db "select id,username,role from users where role<>'student' order by id;"
sqlite3 app.db "select id,actor_user_id,target_id,reason,created_at from moderation_actions \
                where action='user.role.moderator_to_admin' order by id;"
```

## 回滚

本脚本只把 `moderator -> admin`，且每条改动都留了审计行，因此回滚是**定向**的：对审计行里出现的
`target_id` 把角色改回 `moderator` 即可（这条路径同样建议写一条审计，例如
`action='user.role.admin_to_moderator'`），不需要也不会动其他人的数据。若只是"不想执行"，什么都不做：
启动日志会继续提示，而 `moderator` 行本来就没有全局特权（全局权限只认 `admin`）。

This script only moves `moderator -> admin`, and every change left an audit row, so a rollback is
targeted: set the `target_id`s recorded in those audit rows back to `moderator` (that direction should
leave its own audit row too, e.g. `action='user.role.admin_to_moderator'`). No other row is touched.
Doing nothing is also safe: startup keeps warning, and a `moderator` row holds no global privilege
anyway (only `admin` does).
"""

from __future__ import annotations

import argparse
import json
import sys

from samryetha.auth.moderator_roles import (
    ModeratorRoleError,
    apply_moderator_correction,
    plan_moderator_correction,
)
from samryetha.core.config import Settings
from samryetha.core.db import Database


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="订正存量全局角色 moderator（默认 dry-run，--apply 才写）"
                    " / correct legacy global moderator roles (dry-run unless --apply)",
    )
    parser.add_argument("--apply", action="store_true", help="真正写入（含审计）/ actually write (with audit)")
    parser.add_argument("--actor", default=None, help="审计 actor：管理员用户名或 id / audit actor: admin username or id")
    parser.add_argument("--reason", default=None, help="审计 reason 覆盖 / override the audit reason")
    parser.add_argument("--database-url", default=None, help="覆盖 DATABASE_URL / override DATABASE_URL")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))

    overrides: dict[str, object] = {"_env_file": None}
    if args.database_url:
        overrides["database_url"] = args.database_url
    settings = Settings(**overrides)

    # actor 允许写成用户 id 的数字串；用户名保持原样。
    # The actor may be a numeric user id; anything else is treated as a username.
    actor: int | str | None = args.actor
    if isinstance(actor, str) and actor.isdigit():
        actor = int(actor)

    db = Database(settings.database_url)
    # 既有库可能还没补列：ensure_schema_drift 幂等，且不会改动任何业务数据。
    # An existing database may be missing columns: ensure_schema_drift is idempotent and touches no
    # business data.
    db.ensure_schema_drift()

    mode = "apply" if args.apply else "dry-run"
    try:
        with db.request_conn() as conn:
            if args.apply:
                result = apply_moderator_correction(conn, actor=actor, reason=args.reason)
            else:
                result = plan_moderator_correction(conn, actor=actor)
    except ModeratorRoleError as error:
        print(f"refused ({mode}): {error}")
        return 3

    payload = {"mode": mode, "databaseUrl": settings.database_url, **result}
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.apply:
        print(f"applied: {result['changed']} account(s) moderator -> admin, "
              f"{result['auditRows']} audit row(s) in moderation_actions")
    elif result["found"]:
        print(f"dry-run: {result['found']} account(s) would change; re-run with --apply --actor <admin>")
        if result["actorProblem"]:
            print(f"note: {result['actorProblem']}")
    else:
        print("dry-run: nothing to do (no account on role='moderator')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
