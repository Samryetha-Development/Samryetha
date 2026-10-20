"""数据目录外部化的回归测试。

Regression tests for the externalized data directory: the uploads + SQLite database must
default to a location OUTSIDE the repository so that any git update flow (clean / reset /
re-clone / archive overwrite) cannot delete them as untracked files.
"""

from __future__ import annotations

import logging
import os
import tempfile

from samryetha.core.config import Settings, default_data_dir, find_git_root


def _clear_data_env(monkeypatch) -> None:
    """清除数据路径相关的环境变量，确保测的是代码默认值而非外部注入。
    Clear the data-path environment variables so each test observes the code default
    instead of whatever the host shell exported.
    """
    for name in ("SAMRYETHA_DATA_DIR", "UPLOAD_DIR", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_default_data_dir_is_absolute_and_outside_repo() -> None:
    ddir = default_data_dir()
    # 默认目录必须是绝对路径，且不在任何 git 仓库内（向上找不到 .git）。
    # The default must be absolute and must not sit inside any repo (no .git found walking up).
    assert os.path.isabs(ddir)
    assert find_git_root(ddir) is None


def test_upload_and_db_derive_from_data_dir(monkeypatch) -> None:
    _clear_data_env(monkeypatch)
    s = Settings(_env_file=None)
    assert os.path.isabs(s.samryetha_data_dir)
    assert s.upload_dir == os.path.join(s.samryetha_data_dir, "uploads")
    assert s.database_url == os.path.join(s.samryetha_data_dir, "app.db")


def test_samryetha_data_dir_override(monkeypatch) -> None:
    _clear_data_env(monkeypatch)
    target = os.path.join(tempfile.gettempdir(), "samryetha-data-dir-test")
    monkeypatch.setenv("SAMRYETHA_DATA_DIR", target)
    s = Settings(_env_file=None)
    # 校验环境变量确实覆盖默认值，并驱动 upload_dir / database_url 的派生。
    # Assert the env var really overrides the default and drives the derived paths.
    assert s.samryetha_data_dir == os.path.abspath(target)
    assert s.upload_dir == os.path.join(os.path.abspath(target), "uploads")
    assert s.database_url == os.path.join(os.path.abspath(target), "app.db")


def test_explicit_upload_dir_and_database_url_still_win(monkeypatch) -> None:
    # 向后兼容：显式 UPLOAD_DIR / DATABASE_URL 仍优先于数据根派生，不破坏既有 .env。
    # Backward compatibility: explicit UPLOAD_DIR / DATABASE_URL still beat the derivation
    # so existing .env files that set them keep working unchanged.
    _clear_data_env(monkeypatch)
    up = os.path.join(tempfile.gettempdir(), "explicit-uploads")
    db = os.path.join(tempfile.gettempdir(), "explicit.db")
    s = Settings(_env_file=None, upload_dir=up, database_url=db)
    assert s.upload_dir == up
    assert s.database_url == db


def test_warns_when_data_dir_inside_repo(monkeypatch, caplog) -> None:
    # 红绿对照：把数据目录配进仓库内时**必须**告警——这是「更新会清空数据」的直接信号。
    # Red/green control: pointing the data dir inside a repo MUST emit a warning, which is the
    # direct signal that an update would wipe the data.
    _clear_data_env(monkeypatch)
    repo_root = find_git_root(os.getcwd())
    assert repo_root is not None  # 本测试假定运行在仓库内（CI 与本地均如此）
    inside = os.path.join(repo_root, "data-inside-repo")
    monkeypatch.setenv("SAMRYETHA_DATA_DIR", inside)
    with caplog.at_level(logging.WARNING, logger="samryetha.config"):
        Settings(_env_file=None)
    assert any("SAMRYETHA_DATA_DIR" in rec.message for rec in caplog.records)
