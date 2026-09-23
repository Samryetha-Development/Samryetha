#!/usr/bin/env python3
"""CI 校验：frontend/src/lib/locales/*.json 与 i18n/seed/*.json 的 key 集合必须一致。

不一致则 exit 1 并打印差集（frontend-only / seed-only，按 locale 分组）。

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
        fkeys = set(json.loads(f.read_text(encoding="utf-8")).keys())
        seed_path = SEED_DIR / f"{locale}.json"
        if not seed_path.exists():
            print(f"[MISS] {locale}: seed file missing: {seed_path}")
            failed = True
            continue
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        skeys = {e["key"] for e in data.get("entries", [])}
        only_front = sorted(fkeys - skeys)
        only_seed = sorted(skeys - fkeys)
        if only_front or only_seed:
            print(f"[DIFF] {locale}: frontend={len(fkeys)} seed={len(skeys)}")
            if only_front:
                print(f"  frontend-only ({len(only_front)}): {only_front}")
            if only_seed:
                print(f"  seed-only ({len(only_seed)}): {only_seed}")
            failed = True
        else:
            print(f"[OK] {locale}: {len(fkeys)} keys aligned")

    # 检查 seed 侧是否有多余 locale（前端没有的）
    frontend_locales = {f.stem for f in frontend_files}
    for s in sorted(SEED_DIR.glob("*.json")):
        if s.stem not in frontend_locales:
            print(f"[DIFF] {s.name}: seed-only locale (no frontend source)")
            failed = True

    if failed:
        print("check_sync FAILED: key sets differ", file=sys.stderr)
        return 1
    print("check_sync OK: all key sets aligned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
