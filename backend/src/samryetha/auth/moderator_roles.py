"""存量 `role='moderator'` 账号的显式订正：启动只告警，改库必须走可审计的一次性脚本。

Explicit correction of legacy `role='moderator'` accounts: startup only reports, and any role change
has to go through an auditable one-off script.

## 为什么不再在启动时自动合并

角色并入 admin 是提交 db14a91（"merge moderator into admin (#18)"）带来的迁移意图，但此前的实现是
**服务每次启动无条件执行** `UPDATE users SET role='admin' WHERE role='moderator'`——不记审计、不写日志、
不留改动清单。这会同时踩两个坑：

1. **提权无痕**：把一批账号静默提升为全局管理员，事后无法回答"谁在什么时候因为什么被提权了"；
2. **决策错位**：启动迁移没有 actor、没有 review 环节，等于让进程替运维做了一次权限决策。

因此现在的分工是：

- 启动时 `report_legacy_moderator_accounts()` **只读**：数一遍、把数量与用户 id 打进 WARNING 日志，
  指向订正脚本，**一行都不改**；
- 真正的改库由 `backend/scripts/migrate_moderator_roles.py` 显式执行：默认 dry-run，`--apply` 才写，
  且必须指定（或能解析出）一个管理员作为审计 actor。

Why the automatic startup merge is gone: the role merge came from commit db14a91 ("merge moderator
into admin (#18)"), but its implementation ran an unconditional `UPDATE ... WHERE role='moderator'` on
every boot, with no audit row, no log line and no list of what changed. That hit two problems at once:

1. **Untraceable privilege escalation** - a batch of accounts silently became global administrators,
   and afterwards nobody could answer who was promoted, when, and why;
2. **The decision sat in the wrong place** - a boot-time migration has no actor and no review step, so
   the process was making a permission decision on the operator's behalf.

The split is therefore: startup `report_legacy_moderator_accounts()` is read-only (count, user ids, a
WARNING pointing at the script, zero writes), and the actual change goes through
`backend/scripts/migrate_moderator_roles.py`, which is dry-run by default, writes only with `--apply`,
and requires an administrator to be named (or resolved) as the audit actor.

## 为什么"不迁移"是安全的

全局权限只认 `role == "admin"`（`authz/service.py` 的 `is_global_mod`），所以一条 `moderator` 行**今天
本来就没有任何全局特权**：不迁移不会让任何人掉权，只是不再替他们**加**权。这也让"启动只告警"成为
一个零风险的中间态。

Global privileges are only granted to `role == "admin"` (see `is_global_mod` in `authz/service.py`), so a
`moderator` row holds no global privilege today at all: not migrating takes no power away from anyone,
it merely stops handing power out. That is what makes "startup only reports" a zero-risk intermediate
state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from ..core.db import now_ms
from ..core.schema import moderation_actions, users

__all__ = [
    "AUDIT_ACTION",
    "LEGACY_ROLE",
    "TARGET_ROLE",
    "LegacyModeratorAccount",
    "ModeratorRoleError",
    "apply_moderator_correction",
    "find_legacy_moderator_accounts",
    "plan_moderator_correction",
    "report_legacy_moderator_accounts",
]

logger = logging.getLogger("samryetha.auth.moderator_roles")

# 历史全局角色与它的归位目标；审计动作用一个稳定字符串，便于事后检索。
# The legacy global role and the role it settles into; a stable action string makes it searchable.
LEGACY_ROLE = "moderator"
TARGET_ROLE = "admin"
AUDIT_ACTION = "user.role.moderator_to_admin"

SCRIPT_HINT = "backend/scripts/migrate_moderator_roles.py"


class ModeratorRoleError(RuntimeError):
    """订正无法安全执行（典型：库中找不到可作审计 actor 的有效管理员）。

    The correction cannot run safely (typically: no active administrator to act as the audit actor).
    """


@dataclass(frozen=True)
class LegacyModeratorAccount:
    id: int
    username: str
    email: str | None


def find_legacy_moderator_accounts(conn: Connection) -> list[LegacyModeratorAccount]:
    """只读：列出所有仍处于 `moderator` 的账号，按 id 升序（顺序稳定，便于对账）。

    Read-only: every account still on `moderator`, ascending by id (a stable order for reconciliation).
    """
    rows = conn.execute(
        select(users.c.id, users.c.username, users.c.email)
        .where(users.c.role == LEGACY_ROLE)
        .order_by(users.c.id)
    ).all()
    return [
        LegacyModeratorAccount(
            id=int(row[0]),
            username=str(row[1]),
            email=None if row[2] is None else str(row[2]),
        )
        for row in rows
    ]


def _resolve_actor(conn: Connection, actor: int | str | None) -> int:
    """解析审计 actor：支持显式用户 id、显式用户名，缺省取第一个 active 管理员。

    审计表 `moderation_actions.actor_user_id` 是非空外键，所以"没有 actor 就不许改库"——
    这正是本修复的核心约束：宁可不动，也不做一次无主语的提权。

    Resolve the audit actor: an explicit user id, an explicit username, or else the first active
    administrator. `moderation_actions.actor_user_id` is a NOT NULL foreign key, so "no actor, no
    write" - which is exactly the constraint at the heart of this fix: better to leave the data alone
    than to perform a promotion with no subject.
    """
    if actor is not None:
        # 数字按用户 id 解析，其余按用户名解析：两种写法都支持，便于运维按现场情况指定。
        # Digits resolve as a user id and anything else as a username, so operators can name the actor
        # whichever way is convenient on site.
        statement = (
            select(users.c.id, users.c.role).where(users.c.id == actor)
            if isinstance(actor, int)
            else select(users.c.id, users.c.role).where(users.c.username == str(actor))
        )
        row = conn.execute(statement).first()
        if row is None:
            raise ModeratorRoleError(f"审计 actor 不存在：{actor!r} / audit actor not found: {actor!r}")
        if str(row[1]) != TARGET_ROLE:
            raise ModeratorRoleError(
                f"审计 actor 必须是管理员（{actor!r} 当前角色为 {row[1]}）"
                f" / the audit actor must be an administrator ({actor!r} is {row[1]})"
            )
        return int(row[0])

    row = conn.execute(
        select(users.c.id)
        .where(users.c.role == TARGET_ROLE, users.c.status == "active", users.c.deleted_at.is_(None))
        .order_by(users.c.id)
        .limit(1)
    ).first()
    if row is None:
        raise ModeratorRoleError(
            "库中没有可作审计 actor 的有效管理员；请用 --actor 指定一个管理员账号"
            " / no active administrator to act as the audit actor; pass --actor"
        )
    return int(row[0])


def plan_moderator_correction(conn: Connection, *, actor: int | str | None = None) -> dict[str, Any]:
    """只读计划：列出将被改动的账号与目标角色；缺 admin 时把原因写进计划而不是抛异常。

    A read-only plan: the accounts that would change and the target role. A missing administrator is
    reported inside the plan instead of raised, so a dry run never fails just for lack of one.
    """
    accounts = find_legacy_moderator_accounts(conn)
    actor_id: int | None = None
    actor_problem: str | None = None
    try:
        actor_id = _resolve_actor(conn, actor)
    except ModeratorRoleError as error:
        actor_problem = str(error)
    return {
        "found": len(accounts),
        "accounts": [
            {"id": account.id, "username": account.username, "from": LEGACY_ROLE, "to": TARGET_ROLE}
            for account in accounts
        ],
        "auditAction": AUDIT_ACTION,
        "actor": actor_id,
        "actorProblem": actor_problem,
        "applied": False,
        "changed": 0,
        "auditRows": 0,
    }


def _record_audit(
    conn: Connection,
    *,
    actor_id: int,
    account: LegacyModeratorAccount,
    reason: str | None,
    now: int,
) -> bool:
    """写一条角色变更审计（`moderation_actions`），成功返回 True。

    单独抽成函数有三个目的：① 审计字段（谁、对谁、从什么到什么、为什么）只有一处拼装；
    ② 调用方按**返回值**计数，所以"报告写了几条审计"与"真的写了几条"是同一个事实，审计写盘一旦
    被绕开，统计值会立刻不同意；③ 测试可以把它换成 no-op，用来证明"必须有审计"这条断言**会变红**，
    而不是永远为真。

    Write one role-change audit row into `moderation_actions` and return True. Three reasons it is a
    function of its own: the audit fields (who, to whom, from which role to which, why) are assembled in
    exactly one place; the caller counts on the **return value**, so "how many audit rows are reported"
    and "how many were really written" are the same fact, and the reported number stops agreeing the
    moment the write is bypassed; and a test can replace it with a no-op to prove the "an audit row must
    exist" assertion really does go red instead of being vacuously true.
    """
    conn.execute(
        insert(moderation_actions).values(
            actor_user_id=actor_id,
            action=AUDIT_ACTION,
            target_type="user",
            target_id=account.id,
            reason=reason or (
                f"source=script {SCRIPT_HINT}; from={LEGACY_ROLE} to={TARGET_ROLE}; "
                f"username={account.username}"
            ),
            created_at=now,
        )
    )
    return True


def apply_moderator_correction(
    conn: Connection,
    *,
    actor: int | str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """执行订正：逐条改角色 + 同一事务写一条审计，返回改动清单。

    幂等来自两处：`WHERE role='moderator'` 让第二次执行匹配到 0 行；审计行只在 **rowcount>0** 时写，
    所以重复执行既不改库也不再追加审计（与"真的改了 N 条"严格一一对应）。

    Perform the correction: flip each role and write one audit row in the same transaction, returning
    the change list. Idempotency comes from two places: `WHERE role='moderator'` matches nothing on a
    second run, and an audit row is written only when **rowcount > 0**, so a repeated run changes no
    row and appends no audit row - the audit trail stays in strict one-to-one correspondence with the
    changes that really happened.
    """
    accounts = find_legacy_moderator_accounts(conn)
    if not accounts:
        return {
            "found": 0,
            "accounts": [],
            "auditAction": AUDIT_ACTION,
            "actor": None,
            "actorProblem": None,
            "applied": True,
            "changed": 0,
            "auditRows": 0,
        }

    actor_id = _resolve_actor(conn, actor)
    now = now_ms()
    changed: list[dict[str, Any]] = []
    audit_rows = 0
    for account in accounts:
        result = conn.execute(
            update(users)
            .where(users.c.id == account.id, users.c.role == LEGACY_ROLE)
            .values(role=TARGET_ROLE, updated_at=now)
        )
        if not result.rowcount:
            # 并发下已被别人改过：不改库、不写审计（审计只记真实发生的变化）。
            # Changed concurrently by someone else: no write and no audit row; the trail records only
            # changes that really happened.
            continue
        # 按 `_record_audit` 的返回值计数：统计值只反映"真的写进去的审计行"。
        # Count on the return value: the reported number only ever reflects audit rows really written.
        if _record_audit(conn, actor_id=actor_id, account=account, reason=reason, now=now):
            audit_rows += 1
        changed.append({"id": account.id, "username": account.username, "from": LEGACY_ROLE, "to": TARGET_ROLE})

    return {
        "found": len(accounts),
        "accounts": changed,
        "auditAction": AUDIT_ACTION,
        "actor": actor_id,
        "actorProblem": None,
        "applied": True,
        "changed": len(changed),
        "auditRows": audit_rows,
    }


def report_legacy_moderator_accounts(conn: Connection, *, source: str = "startup") -> int:
    """启动时调用：只数、只记日志，绝不改库。返回发现的账号数。

    日志里带齐"改了几条、哪几个用户 id、从什么角色到什么角色、该走哪个脚本"——
    即使运维不立刻订正，证据也已经留在日志里，而不是像以前那样连痕迹都没有。

    Called at startup: count and log only, never write. It returns the number of accounts found. The
    log line carries what the audit trail needs (how many, which ids, from which role to which, and
    which script to run), so the evidence exists even if the operator does not act immediately -
    unlike before, when nothing was recorded at all.
    """
    accounts = find_legacy_moderator_accounts(conn)
    if not accounts:
        return 0
    logger.warning(
        "legacy_looking_moderator_roles source=%s count=%d user_ids=%s from=%s to=%s action=manual "
        "hint=run %s (dry-run by default, --apply to write an audited change)",
        source,
        len(accounts),
        ",".join(str(account.id) for account in accounts),
        LEGACY_ROLE,
        TARGET_ROLE,
        SCRIPT_HINT,
    )
    return len(accounts)
