"""Guards that keep `assert` out of runtime validation paths.

守护用例：禁止把 `assert` 用作运行时校验。Python 以 `-O` 运行时断言会被整段剥离，
校验会静默消失，因此生产源码与审计脚本里的运行时校验都必须显式抛出异常。
`assert` used for runtime validation is stripped entirely under `python -O`, so the check
vanishes silently; production source and audit scripts must raise explicit exceptions instead.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from samryetha.auth import AuthService
from samryetha.core.ids import UserID
from samryetha.tasks.models import TaskCreate
from samryetha.tasks.repository import TaskRepository
from samryetha.tasks.service import TaskService

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = BACKEND_ROOT / "src" / "samryetha"
AUDIT_SCRIPT = BACKEND_ROOT / "scripts" / "audit_architecture.py"


def _load_audit_script() -> ModuleType:
    """Load the audit script as a module without running its ``main``.

    以模块方式导入审计脚本但不执行其 ``main``（该脚本的入口由 ``__main__`` 守卫保护）。
    """
    spec = importlib.util.spec_from_file_location("_samryetha_audit_architecture", AUDIT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load audit script at {AUDIT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_application_source_uses_no_assert_statements() -> None:
    """Production modules must never rely on ``assert`` for runtime validation.

    生产模块不得用 ``assert`` 做运行时校验：``-O`` 下断言被剥离，校验会静默消失，
    必须改为显式异常（如 ``RuntimeError`` / 领域异常）。
    """
    offenders = [
        f"{path.relative_to(BACKEND_ROOT).as_posix()}:{node.lineno}"
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if isinstance(node, ast.Assert)
    ]
    assert offenders == [], (
        "runtime validation must raise explicit exceptions, because `assert` is stripped under "
        f"`python -O`: {offenders}"
    )


def _write_module(directory: Path, name: str, exports: list[str]) -> None:
    """Write a throwaway module whose ``__all__`` lists ``exports``.

    写一个临时模块：``__all__`` 声明 ``exports``，模块体内只实现 ``present``。
    """
    body = f"__all__ = {exports!r}\n\n\ndef present() -> None:\n    return None\n"
    (directory / f"{name}.py").write_text(body, encoding="utf-8")


def _run_export_check(directory: Path, module_name: str, *, optimize: bool) -> int:
    """Run the audit script's export check in a child interpreter and return its exit code.

    在子解释器里运行审计脚本的导出检查并返回退出码。子进程输出直接丢弃（本机沙箱拒绝为
    捕获输出创建管道），退出码本身就是判定信号：非零即代表检查判定模块不合格。
    """
    program = _load_audit_script().EXPORT_CHECK_PROGRAM
    env = dict(os.environ)
    env["PYTHONPATH"] = str(directory)
    if optimize:
        env["PYTHONOPTIMIZE"] = "1"
    else:
        env.pop("PYTHONOPTIMIZE", None)
    completed = subprocess.run(
        [sys.executable, "-c", program, module_name],
        cwd=str(directory),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode


def test_export_check_accepts_module_with_all_exports_present(tmp_path: Path) -> None:
    """A module whose ``__all__`` entries all exist must pass the check.

    ``__all__`` 中的名字全部存在时，检查必须通过（退出码 0），保证修复没有收紧原语义。
    """
    _write_module(tmp_path, "complete_exports", ["present"])
    assert _run_export_check(tmp_path, "complete_exports", optimize=False) == 0


def test_export_check_rejects_missing_export_under_python_optimize(tmp_path: Path) -> None:
    """A missing ``__all__`` entry must fail the check even with ``PYTHONOPTIMIZE=1``.

    ``__all__`` 中有一个名字不存在时，检查必须失败；``PYTHONOPTIMIZE=1`` 下同样必须失败。
    修复前该检查用 ``assert``，优化模式会把断言剥离、子进程恒以 0 退出（此用例为红），
    修复后改为显式 ``SystemExit``，优化模式下依旧以非零退出码报出缺失的导出名。
    """
    _write_module(tmp_path, "missing_exports", ["present", "absent_symbol"])
    assert _run_export_check(tmp_path, "missing_exports", optimize=False) != 0
    assert _run_export_check(tmp_path, "missing_exports", optimize=True) != 0


def test_create_task_reports_unloadable_record_with_explicit_error(db, monkeypatch) -> None:
    """``create_task`` must surface an unloadable insert as ``RuntimeError``, never as ``assert``.

    ``create_task`` 取不回新建任务时必须抛显式 ``RuntimeError``：既钉住不变量本身，
    也保证有人把显式异常改回 ``assert`` 时本用例立刻变红。
    """
    with db.request_conn() as conn:
        author_id = UserID(AuthService(conn).register("assertguarded", "password123"))
        monkeypatch.setattr(TaskRepository, "task", lambda self, task_id: None)
        with pytest.raises(RuntimeError, match="Created task could not be loaded"):
            TaskService(conn).create_task(author_id, TaskCreate(title="Guarded task"))
