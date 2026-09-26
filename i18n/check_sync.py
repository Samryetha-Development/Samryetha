#!/usr/bin/env python3
"""CI 校验：frontend/src/lib/locales/*.json 与 i18n/seed/*.json 必须完全一致。

一致性契约（与 sync_from_frontend.py 相同）：
  - key 集合一致；
  - key 顺序与前端 json 一致；
  - 每个 key 的 value 与前端 json 逐字相同（值照抄不翻译）。

不一致则 exit 1，并打印精确差异（缺 key / 多 key / 顺序错位 / 值漂移，按 locale 分组）。

用法：
  uv run python check_sync.py
  python3 check_sync.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent
FRONTEND_LOCALES_DIR = I18N_DIR.parent / "frontend" / "src" / "lib" / "locales"
SEED_DIR = I18N_DIR / "seed"


def compare_locale(
    locale: str,
    frontend_items: dict[str, str],
    seed_entries: list[dict],
) -> list[str]:
    """返回该 locale 的问题列表（空列表表示 key 集合、顺序、值全部一致）。

    检查项：
      - 缺 key（frontend 有、seed 无）；
      - 多 key（seed 有、frontend 无）；
      - key 顺序与前端 json 不一致；
      - 每个 key 的 value 与前端不一致。
    """
    problems: list[str] = []
    fkeys = list(frontend_items.keys())
    skeys = [e.get("key") for e in seed_entries if isinstance(e, dict)]

    fset = set(fkeys)
    sset = set(skeys)

    only_front = [k for k in fkeys if k not in sset]
    only_seed = [k for k in skeys if k not in fset]
    if only_front:
        problems.append(f"missing in seed ({len(only_front)}): {only_front}")
    if only_seed:
        problems.append(f"extra in seed ({len(only_seed)}): {only_seed}")

    # 顺序：仅在 key 集合一致时比较（缺/多 key 时顺序比较无意义）。
    if not only_front and not only_seed and fkeys != skeys:
        pos = {k: i for i, k in enumerate(skeys)}
        first = next(i for i in range(len(fkeys)) if fkeys[i] != skeys[i])
        moved = [k for i, k in enumerate(fkeys) if pos[k] != i]
        problems.append(
            f"key order differs: first divergence at index {first} "
            f"(frontend={fkeys[first]!r} seed={skeys[first]!r}); "
            f"out-of-place keys={moved}"
        )

    # 值：只比较两边都存在的 key。
    seed_values = {e.get("key"): e.get("value") for e in seed_entries if isinstance(e, dict)}
    for k in fkeys:
        if k in seed_values and seed_values[k] != frontend_items[k]:
            problems.append(
                f"value drift: {k}: frontend={frontend_items[k]!r} != seed={seed_values[k]!r}"
            )
    return problems


def main() -> int:
    if not FRONTEND_LOCALES_DIR.is_dir():
        print(f"Frontend locales dir not found: {FRONTEND_LOCALES_DIR}", file=sys.stderr)
        return 1
    if not SEED_DIR.is_dir():
        print(f"Seed dir not found: {SEED_DIR}", file=sys.stderr)
        return 1

    frontend_files = sorted(FRONTEND_LOCALES_DIR.glob("*.json"))
    if not frontend_files:
        print(f"No frontend locale files in {FRONTEND_LOCALES_DIR}", file=sys.stderr)
        return 1

    failed = False
    for f in frontend_files:
        locale = f.stem
        data = json.loads(f.read_text(encoding="utf-8"))
        fmap = {k: str(v) for k, v in data.items()}
        seed_path = SEED_DIR / f"{locale}.json"
        if not seed_path.exists():
            print(f"[MISS] {locale}: seed file missing: {seed_path}")
            failed = True
            continue
        seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_entries = seed_data.get("entries", [])
        problems = compare_locale(locale, fmap, seed_entries)
        if problems:
            print(f"[DIFF] {locale}: frontend={len(fmap)} seed={len(seed_entries)}")
            for p in problems:
                print(f"  {p}")
            failed = True
        else:
            print(f"[OK] {locale}: {len(fmap)} keys aligned (order + values)")

    # 检查 seed 侧是否有多余 locale（前端没有的）
    frontend_locales = {f.stem for f in frontend_files}
    for s in sorted(SEED_DIR.glob("*.json")):
        if s.stem not in frontend_locales:
            print(f"[DIFF] {s.name}: seed-only locale (no frontend source)")
            failed = True

    if failed:
        print("check_sync FAILED: frontend and seed differ", file=sys.stderr)
        return 1
    print("check_sync OK: all locales aligned (keys, order, values)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
