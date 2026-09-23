#!/usr/bin/env python3
"""单向生成：frontend/src/lib/locales/*.ts（唯一真源，运行时实际 import）→ 同目录 *.json。

背景：运行时代码 import 的是 .ts；.json 只供给 i18n 同步链（json → seed → catalog）。
.ts 新增 key 时 .json 不会自动跟上，导致远端 catalog 缺 key、非英语用户回退英文。
本脚本消除手工同步，链路变为：.ts →(.json)→ seed → catalog。

规则：
  - 8 个 locale 的 key 集合与顺序必须与 en.ts 完全一致（否则 exit 1，强制约定）；
  - .json 内容 == 对应 .ts 的有序键值（2 空格缩进、原文直写、尾换行）；
  - TS 双引号字面量按 JSON 转义解码（与 JSON 字符串转义集兼容）。

用法：
  python3 scripts/gen_locale_json.py           # 生成/刷新全部 .json
  python3 scripts/gen_locale_json.py --check   # 只对比，漂移则 exit 1 并打印差集（给 CI 用）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent.parent / "src" / "lib" / "locales"
LOCALES = ["en", "zh-CN", "zh-TW", "ja", "ko", "es", "fr", "de"]

# "key": "value", —— TS 双引号字面量，转义集与 JSON 字符串兼容
PAIR_RE = re.compile(r'"((?:[^"\\]|\\.)+)":\s*"((?:[^"\\]|\\.)*)",?')


def parse_ts(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith('"'):
            continue
        match = PAIR_RE.fullmatch(stripped)
        if match is None:
            print(f"{path.name}:{lineno}: unparsable line: {stripped[:80]}", file=sys.stderr)
            sys.exit(1)
        key = json.loads(f'"{match.group(1)}"')
        value = json.loads(f'"{match.group(2)}"')
        pairs.append((key, value))
    return pairs


def dump_json(pairs: list[tuple[str, str]]) -> str:
    return json.dumps(dict(pairs), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只对比不写入")
    args = parser.parse_args()

    catalogs: dict[str, list[tuple[str, str]]] = {}
    for locale in LOCALES:
        path = LOCALES_DIR / f"{locale}.ts"
        if not path.is_file():
            print(f"missing locale file: {path}", file=sys.stderr)
            return 1
        catalogs[locale] = parse_ts(path)
        keys = [k for k, _ in catalogs[locale]]
        if len(set(keys)) != len(keys):
            dupes = sorted({k for k in keys if keys.count(k) > 1})
            print(f"{locale}.ts has duplicate keys: {dupes}", file=sys.stderr)
            return 1

    master = [k for k, _ in catalogs["en"]]
    ok = True
    for locale in LOCALES:
        if [k for k, _ in catalogs[locale]] != master:
            print(f"{locale}.ts key order/set differs from en.ts", file=sys.stderr)
            ok = False
    if not ok:
        return 1

    dirty = False
    for locale in LOCALES:
        out = dump_json(catalogs[locale])
        path = LOCALES_DIR / f"{locale}.json"
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != out:
            dirty = True
            if args.check:
                print(f"drift: {locale}.json is stale (run without --check to regenerate)")
            else:
                path.write_text(out, encoding="utf-8")
                print(f"wrote {locale}.json ({len(catalogs[locale])} keys)")
    if args.check and dirty:
        return 1
    if not dirty:
        print(f"all 8 .json files in sync ({len(master)} keys each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
