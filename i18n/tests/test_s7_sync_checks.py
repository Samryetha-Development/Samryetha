"""同步校验脚本行为测试（check_sync.py / sync_from_frontend.py --check）。

用临时目录构造"前端 json"与"seed json"，验证脚本不仅比对 key 集合，
还会对 key 顺序与逐字值漂移报错（exit 1）。全部走 tmp_path，不触碰真实 locale 数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import check_sync
import sync_from_frontend


# ---------------------------------------------------------------- helpers

def _write_frontend(d: Path, locale: str, pairs: dict[str, str]) -> None:
    (d / f"{locale}.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _write_seed(d: Path, locale: str, entries: list[dict]) -> None:
    (d / f"{locale}.json").write_text(
        json.dumps({"locale": locale, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def sync_dirs(tmp_path, monkeypatch):
    """把两个脚本的目录常量指向临时目录，返回 (frontend_dir, seed_dir)。"""
    fe = tmp_path / "locales"
    seed = tmp_path / "seed"
    fe.mkdir()
    seed.mkdir()
    monkeypatch.setattr(check_sync, "FRONTEND_LOCALES_DIR", fe)
    monkeypatch.setattr(check_sync, "SEED_DIR", seed)
    monkeypatch.setattr(sync_from_frontend, "FRONTEND_LOCALES_DIR", fe)
    monkeypatch.setattr(sync_from_frontend, "SEED_DIR", seed)
    return fe, seed


# ---------------------------------------------------------------- check_sync

def test_check_sync_passes_when_aligned(sync_dirs):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "a", "value": "A"}, {"key": "b", "value": "B"}])
    assert check_sync.main() == 0


def test_check_sync_fails_on_value_drift(sync_dirs):
    """key 集合一致、顺序一致，但值被改动 → 必须 exit 1。"""
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "a", "value": "A"}, {"key": "b", "value": "WRONG"}])
    assert check_sync.main() == 1


def test_check_sync_fails_on_reordered_keys(sync_dirs):
    """key 集合与值都一致，但顺序不同 → 必须 exit 1。"""
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "b", "value": "B"}, {"key": "a", "value": "A"}])
    assert check_sync.main() == 1


def test_check_sync_fails_on_missing_key(sync_dirs):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "a", "value": "A"}])
    assert check_sync.main() == 1


def test_check_sync_fails_on_seed_only_locale(sync_dirs):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A"})
    _write_seed(seed, "en", [{"key": "a", "value": "A"}])
    _write_seed(seed, "ja", [{"key": "a", "value": "A"}])
    assert check_sync.main() == 1


def test_compare_locale_reports_precise_problems():
    # 缺/多 key + 值漂移（key 集合不同，此时不做顺序比较）
    problems = check_sync.compare_locale(
        "en",
        {"a": "A", "b": "B", "c": "C"},
        [
            {"key": "a", "value": "A"},
            {"key": "c", "value": "C2"},
            {"key": "d", "value": "D"},   # extra in seed
        ],
    )
    joined = "\n".join(problems)
    assert "missing in seed" in joined and "'b'" in joined
    assert "extra in seed" in joined and "'d'" in joined
    assert "value drift" in joined and "value drift: c" in joined

    # 顺序错位（key 集合与值都一致）
    order_problems = check_sync.compare_locale(
        "en",
        {"a": "A", "b": "B"},
        [{"key": "b", "value": "B"}, {"key": "a", "value": "A"}],
    )
    assert any("key order differs" in p for p in order_problems)


# ---------------------------------------------------------------- sync_from_frontend --check

def _run_sync_check(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["sync_from_frontend.py", "--check"])
    sync_from_frontend.main()


def test_sync_from_frontend_check_passes_when_aligned(sync_dirs, monkeypatch):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "a", "value": "A"}, {"key": "b", "value": "B"}])
    _run_sync_check(monkeypatch)  # 不抛 SystemExit 即通过


def test_sync_from_frontend_check_fails_on_value_drift(sync_dirs, monkeypatch):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A"})
    _write_seed(seed, "en", [{"key": "a", "value": "WRONG"}])
    with pytest.raises(SystemExit) as exc:
        _run_sync_check(monkeypatch)
    assert exc.value.code == 1


def test_sync_from_frontend_check_fails_on_reorder(sync_dirs, monkeypatch):
    fe, seed = sync_dirs
    _write_frontend(fe, "en", {"a": "A", "b": "B"})
    _write_seed(seed, "en", [{"key": "b", "value": "B"}, {"key": "a", "value": "A"}])
    with pytest.raises(SystemExit) as exc:
        _run_sync_check(monkeypatch)
    assert exc.value.code == 1
