#!/usr/bin/env python3
"""单向同步：frontend/src/lib/locales/*.json → i18n/seed/*.json。

注意：.json 是生成物（见 frontend/scripts/gen_locale_json.py），真正的唯一真源是
同目录的 *.ts。改 .ts 后先跑生成脚本再跑本同步。

规则：
  - key 集合完全对齐（seed 的 key 集 == 前端 json 的 key 集）；
  - 值照抄不翻译（seed value == 前端 value 原样复制）；
  - 缺译允许：值可以是任何字符串（含空串/回退英文），但 key 必须存在；
  - 已有 seed 条目的 description 予以保留（避免丢失手工说明），新 key 无 description；
  - 顺序与前端 json 一致（保证 diff 稳定）。

用法：
  uv run python sync_from_frontend.py [--check]
  python3 sync_from_frontend.py [--check]

  --check  只对比不写入（与 check_sync.py 等价的轻量模式），不一致则 exit 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent
FRONTEND_LOCALES_DIR = I18N_DIR.parent / "frontend" / "src" / "lib" / "locales"
SEED_DIR = I18N_DIR / "seed"


def load_frontend() -> dict[str, dict[str, str]]:
    """读前端 8 个 .json，返回 {locale: {key: value}}（保持文件顺序）。"""
    if not FRONTEND_LOCALES_DIR.is_dir():
        print(f"Frontend locales dir not found: {FRONTEND_LOCALES_DIR}", file=sys.stderr)
        sys.exit(1)
    files = sorted(FRONTEND_LOCALES_DIR.glob("*.json"))
    if not files:
        print(f"No frontend locale files in {FRONTEND_LOCALES_DIR}", file=sys.stderr)
        sys.exit(1)
    out: dict[str, dict[str, str]] = {}
    for f in files:
        locale = f.stem
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            print(f"Unexpected format in {f}: top-level must be object", file=sys.stderr)
            sys.exit(1)
        out[locale] = {k: str(v) for k, v in data.items()}
    return out


def load_seed_entries(locale: str) -> tuple[list[dict], dict[str, str | None]]:
    """读现有 seed 文件，返回 (entries, {key: description})；文件缺失则返回空。"""
    p = SEED_DIR / f"{locale}.json"
    if not p.exists():
        return [], {}
    data = json.loads(p.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    desc = {e["key"]: e.get("description") for e in entries if isinstance(e, dict) and "key" in e}
    return entries, desc


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync i18n seed files from frontend locales (one-way)")
    parser.add_argument("--check", action="store_true", help="Only compare, do not write")
    args = parser.parse_args()

    frontend = load_frontend()
    failed = False

    for locale in sorted(frontend):
        fkeys = frontend[locale]
        _, old_desc = load_seed_entries(locale)
        seed_path = SEED_DIR / f"{locale}.json"

        if args.check:
            if not seed_path.exists():
                print(f"[MISS] {locale}: seed file missing: {seed_path}")
                failed = True
                continue
            data = json.loads(seed_path.read_text(encoding="utf-8"))
            skeys = {e["key"] for e in data.get("entries", [])}
            only_front = sorted(set(fkeys) - skeys)
            only_seed = sorted(skeys - set(fkeys))
            if only_front or only_seed:
                print(f"[DIFF] {locale}: frontend={len(fkeys)} seed={len(skeys)}")
                if only_front:
                    print(f"  frontend-only ({len(only_front)}): {only_front}")
                if only_seed:
                    print(f"  seed-only ({len(only_seed)}): {only_seed}")
                failed = True
            else:
                print(f"[OK] {locale}: {len(fkeys)} keys aligned")
            continue

        # 写 seed：值照抄，description 保留旧值
        entries = []
        for k, v in fkeys.items():
            e: dict = {"key": k, "value": v}
            d = old_desc.get(k)
            if d is not None:
                e["description"] = d
            entries.append(e)
        payload = {"locale": locale, "entries": entries}
        SEED_DIR.mkdir(parents=True, exist_ok=True)
        seed_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[SYNC] {locale}: {len(entries)} entries → {seed_path}")

    if args.check and failed:
        sys.exit(1)
    if args.check:
        return

    # 跑完后断言：两边 key 集必须完全对齐
    ok = True
    for locale, fkeys in sorted(frontend.items()):
        data = json.loads((SEED_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        skeys = {e["key"] for e in data.get("entries", [])}
        if set(fkeys) != skeys:
            only_front = sorted(set(fkeys) - skeys)
            only_seed = sorted(skeys - set(fkeys))
            print(f"[ASSERT-FAIL] {locale}: frontend-only={only_front} seed-only={only_seed}", file=sys.stderr)
            ok = False
    if not ok:
        sys.exit(1)
    print("Assert OK: all seed key sets aligned with frontend.")


if __name__ == "__main__":
    main()
