"""存量全局 `moderator` 订正：启动只告警、改库必审计、dry-run 不动库。

Legacy global `moderator` correction: startup only reports, every write is audited, and a dry run
changes nothing.

这些用例盯着三条契约（缺一条都会让"静默提权"以另一种形式回来）：

1. **启动绝不改库**：`role='moderator'` 的行在应用启动后仍然是 `moderator`，只多一条 WARNING；
2. **改库必有审计**：`--apply` 路径改了几条就必须有同样条数的 `moderation_actions` 行，且带
   actor / target / from→to / 来源脚本；这条断言用一个 no-op 补丁验证过**会变红**（见
   `test_the_audit_assertion_goes_red_when_the_audit_write_is_silenced`）；
3. **dry-run 不写库**：计划调用之后角色与审计表都零变化——同一份断言在 `--apply` 之后必须变红，
   否则"没改库"这句话就是空话（见 `test_dry_run_is_a_real_discriminator`）。

Three contracts are under test here; losing any one of them brings the silent escalation back in some
other shape: startup never writes (1); every applied change has a matching audit row carrying actor,
target, from->to and the source script (2, proven able to go red by a no-op patch); and a dry run
writes nothing (3, proven to be a real discriminator because the same assertion flips after --apply).
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from samryetha.auth import moderator_roles
from samryetha.auth.moderator_roles import (
    AUDIT_ACTION,
    apply_moderator_correction,
    find_legacy_moderator_accounts,
    plan_moderator_correction,
    report_legacy_moderator_accounts,
)
from samryetha.core.config import Settings
from samryetha.core.db import now_ms
from samryetha.core.schema import moderation_actions, users
from samryetha.main import create_app


def _insert_user(conn, username: str, *, role: str, status: str = "active") -> int:
    """直接落一行用户：本模块只关心 `role`，不需要走注册/登录流程。

    Insert one user row directly: this module only cares about `role`, so the register/login flow would
    be noise.
    """
    return int(
        conn.execute(
            users.insert()
            .values(
                username=username,
                email=f"{username}@example.invalid",
                display_name=username.title(),
                password_hash="not-a-real-hash",
                role=role,
                status=status,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
            .returning(users.c.id)
        ).scalar_one()
    )


def _role_of(conn, username: str) -> str:
    return str(conn.execute(select(users.c.role).where(users.c.username == username)).scalar_one())


def _audit_rows(conn, *, action: str = AUDIT_ACTION) -> list[dict[str, object]]:
    rows = conn.execute(
        select(
            moderation_actions.c.id,
            moderation_actions.c.actor_user_id,
            moderation_actions.c.target_type,
            moderation_actions.c.target_id,
            moderation_actions.c.reason,
        ).where(moderation_actions.c.action == action).order_by(moderation_actions.c.id)
    ).all()
    return [
        {
            "id": int(row[0]),
            "actor": int(row[1]),
            "targetType": str(row[2]),
            "targetId": int(row[3]),
            "reason": str(row[4] or ""),
        }
        for row in rows
    ]


def _assert_change_is_audited(conn, *, changed: int) -> None:
    """核心断言：改了几条就必须有几条审计，且审计说清谁/对谁/从什么到什么。

    单独成函数是为了让负对照复用它——把审计写盘换成 no-op 之后，**同一个断言**必须失败。

    The core assertion: as many audit rows as changes, and each says who, to whom and from which role
    to which. It lives in a function so the negative control can reuse it verbatim - with the audit
    write silenced, this very assertion has to fail.
    """
    rows = _audit_rows(conn)
    assert changed == 1, f"this assertion is written for exactly one changed account, got {changed}"
    assert len(rows) == changed, (
        f"a role change must leave one audit row per account: changed={changed} audit={len(rows)}"
    )
    assert rows[0]["targetType"] == "user"
    assert rows[0]["reason"].startswith("source=script"), rows[0]["reason"]
    assert "from=moderator to=admin" in rows[0]["reason"], rows[0]["reason"]


# ================================================================ 启动：只告警，绝不改库


def test_startup_reports_legacy_moderator_but_never_promotes(tmp_path, caplog):
    """启动后角色必须仍是 moderator；同时必须留下带数量与 id 的 WARNING。

    这里刻意**不建管理员**：旧实现不需要任何 actor 就能提权，而新实现连"想改"都不该发生。
    The role must still be moderator after startup, and a WARNING carrying the count and the ids must
    exist. No administrator is created on purpose: the old implementation escalated without any actor,
    while the new one must not even think about writing.
    """
    app = create_app(Settings(
        _env_file=None,
        database_url=str(tmp_path / "startup.db"),
        upload_dir=str(tmp_path / "uploads"),
        preview_cache_dir=str(tmp_path / "preview-cache"),
        preview_converter="off",
    ))
    app.state.db.create_schema()
    with app.state.db.request_conn() as conn:
        legacy_id = _insert_user(conn, "legacy", role="moderator")

    caplog.set_level(logging.WARNING, logger="samryetha.auth.moderator_roles")
    with TestClient(app):
        with app.state.db.request_conn() as conn:
            assert _role_of(conn, "legacy") == "moderator", (
                "startup must not promote a legacy moderator any more"
            )
            assert _audit_rows(conn) == [], "startup must not write role-change audit rows either"

    warnings = [record for record in caplog.records if record.name == "samryetha.auth.moderator_roles"]
    assert warnings, "startup must report legacy moderators, silence was the original defect"
    message = warnings[0].getMessage()
    assert "legacy_looking_moderator_roles" in message
    assert "count=1" in message
    assert str(legacy_id) in message, "the warning must name the affected user ids"
    assert "migrate_moderator_roles.py" in message, "the warning must point at the correction script"


def test_report_helper_is_read_only(db):
    """`report_legacy_moderator_accounts` 只返回数量，不改角色、不写审计。"""
    with db.request_conn() as conn:
        _insert_user(conn, "legacy", role="moderator")
        assert report_legacy_moderator_accounts(conn, source="test") == 1
        assert _role_of(conn, "legacy") == "moderator"
        assert _audit_rows(conn) == []


# ================================================================ dry-run：不写库


def test_dry_run_plan_changes_nothing(db):
    """计划调用之后：角色未变、审计表为空、计划如实报告 found/changed。"""
    with db.request_conn() as conn:
        admin_id = _insert_user(conn, "root", role="admin")
        legacy_id = _insert_user(conn, "legacy", role="moderator")

        result = plan_moderator_correction(conn, actor=admin_id)

        assert result["found"] == 1
        assert result["applied"] is False
        assert result["changed"] == 0
        assert result["auditRows"] == 0
        assert result["actor"] == admin_id
        assert result["accounts"] == [
            {"id": legacy_id, "username": "legacy", "from": "moderator", "to": "admin"}
        ]
        assert _role_of(conn, "legacy") == "moderator", "a dry run must not change any role"
        assert _audit_rows(conn) == [], "a dry run must not write audit rows"


def test_dry_run_is_a_real_discriminator(db):
    """同一份"未改动"断言在 apply 之后必须变红——否则"dry-run 不改库"是空话。

    做法：先 dry-run 断言"角色未变、审计为空"，再执行 apply，然后断言这两条**不再成立**。
    The same "nothing changed" assertion must go red after apply, otherwise "the dry run writes
    nothing" is empty words: assert unchanged while dry, apply, then assert the inverse.
    """
    with db.request_conn() as conn:
        admin_id = _insert_user(conn, "root", role="admin")
        _insert_user(conn, "legacy", role="moderator")

        plan_moderator_correction(conn, actor=admin_id)
        assert _role_of(conn, "legacy") == "moderator"
        assert _audit_rows(conn) == []

        apply_moderator_correction(conn, actor=admin_id)

        assert _role_of(conn, "legacy") == "admin", "after apply the same assertion must fail"
        assert len(_audit_rows(conn)) == 1, "after apply the same assertion must fail"


# ================================================================ apply：改库 + 审计 + 幂等


def test_apply_promotes_with_an_audit_row(db):
    """一次订正：角色变 admin，且审计行带 actor / target / from→to / 来源脚本。"""
    with db.request_conn() as conn:
        admin_id = _insert_user(conn, "root", role="admin")
        legacy_id = _insert_user(conn, "legacy", role="moderator")

        result = apply_moderator_correction(conn, actor=admin_id)

        assert result["found"] == 1
        assert result["changed"] == 1
        assert result["auditRows"] == 1
        assert result["actor"] == admin_id
        assert _role_of(conn, "legacy") == "admin"
        _assert_change_is_audited(conn, changed=result["changed"])
        rows = _audit_rows(conn)
        assert rows[0]["actor"] == admin_id, "the audit row must name who performed the change"
        assert rows[0]["targetId"] == legacy_id, "the audit row must name which account changed"


def test_apply_is_idempotent(db):
    """第二次执行：0 改动、0 审计；第一次的那条审计保持不变（不会越跑越多）。"""
    with db.request_conn() as conn:
        admin_id = _insert_user(conn, "root", role="admin")
        _insert_user(conn, "legacy", role="moderator")

        first = apply_moderator_correction(conn, actor=admin_id)
        assert (first["changed"], first["auditRows"]) == (1, 1)

        second = apply_moderator_correction(conn, actor=admin_id)
        assert (second["found"], second["changed"], second["auditRows"]) == (0, 0, 0)
        assert find_legacy_moderator_accounts(conn) == []
        assert len(_audit_rows(conn)) == 1, "a repeated run must not append another audit row"


def test_apply_refuses_without_an_administrator_actor(db):
    """没有可用管理员时拒绝执行：宁可不动，也不做一次没有主语的提权。"""
    with db.request_conn() as conn:
        _insert_user(conn, "legacy", role="moderator")
        with pytest.raises(moderator_roles.ModeratorRoleError, match="audit actor"):
            apply_moderator_correction(conn)
        assert _role_of(conn, "legacy") == "moderator"
        assert _audit_rows(conn) == []


def test_apply_refuses_an_actor_that_is_not_an_administrator(db):
    """`--actor` 指到一个非管理员时必须拒绝，且不改库。"""
    with db.request_conn() as conn:
        student_id = _insert_user(conn, "stu", role="student")
        _insert_user(conn, "legacy", role="moderator")
        with pytest.raises(moderator_roles.ModeratorRoleError, match="must be an administrator"):
            apply_moderator_correction(conn, actor=student_id)
        assert _role_of(conn, "legacy") == "moderator"
        assert _audit_rows(conn) == []


# ================================================================ 负对照：审计写盘被静音后必须判红


def test_the_audit_assertion_goes_red_when_the_audit_write_is_silenced(db, monkeypatch):
    """把审计写盘换成 no-op（即"静默提权"的旧形态）后，核心断言必须失败。

    这条用例存在的意义只有一个：证明 `_assert_change_is_audited` 不是永远为真的假绿断言。
    Silencing the audit write (the old "silent escalation" shape) must make the core assertion fail.
    This case exists for one reason: to prove `_assert_change_is_audited` is not vacuously true.
    """
    monkeypatch.setattr(moderator_roles, "_record_audit", lambda *args, **kwargs: None)
    with db.request_conn() as conn:
        admin_id = _insert_user(conn, "root", role="admin")
        _insert_user(conn, "legacy", role="moderator")

        result = apply_moderator_correction(conn, actor=admin_id)

        # 角色仍然被改了——所以"安静"的不是这次改动，而是它的痕迹，这正是被修的缺陷形态。
        # The role did change - so it is the trace that went quiet, which is exactly the defect shape.
        assert result["changed"] == 1
        assert _role_of(conn, "legacy") == "admin"
        assert _audit_rows(conn) == []

        with pytest.raises(AssertionError, match="one audit row per account"):
            _assert_change_is_audited(conn, changed=result["changed"])


# ================================================================ 脚本入口


def test_script_dry_run_then_apply(tmp_path, capsys):
    """脚本入口：默认 dry-run 不写库，`--apply --actor` 才写，再跑一次为 0 改动。"""
    import importlib.util
    from pathlib import Path

    # `backend/scripts` 不是包（无 __init__.py），按文件路径加载；这样测试打到的是真实的脚本入口。
    # `backend/scripts` is not a package (no __init__.py), so load it by path: the test then exercises
    # the real script entry point.
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_moderator_roles.py"
    spec = importlib.util.spec_from_file_location("migrate_moderator_roles_script", script_path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    database_url = str(tmp_path / "cli.db")
    app = create_app(Settings(
        _env_file=None,
        database_url=database_url,
        upload_dir=str(tmp_path / "uploads"),
        preview_cache_dir=str(tmp_path / "preview-cache"),
        preview_converter="off",
    ))
    app.state.db.create_schema()
    with app.state.db.request_conn() as conn:
        _insert_user(conn, "root", role="admin")
        _insert_user(conn, "legacy", role="moderator")

    assert script.main(["--database-url", database_url]) == 0
    with app.state.db.request_conn() as conn:
        assert _role_of(conn, "legacy") == "moderator", "the script must default to dry-run"
        assert _audit_rows(conn) == []

    assert script.main(["--database-url", database_url, "--apply", "--actor", "root"]) == 0
    with app.state.db.request_conn() as conn:
        assert _role_of(conn, "legacy") == "admin"
        assert len(_audit_rows(conn)) == 1

    assert script.main(["--database-url", database_url, "--apply", "--actor", "root"]) == 0
    with app.state.db.request_conn() as conn:
        assert len(_audit_rows(conn)) == 1, "re-running the script must be a no-op"
    assert "applied: 0 account(s)" in capsys.readouterr().out
