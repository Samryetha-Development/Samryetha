#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TranslationBootstrap — 启动翻译站（i18n 服务 :3002 + Vite 站点 :5200），并自动拉起 Lako。

翻译站共用主站的登录态（i18n/.env 的 I18N_AUTH_DB_URL 指向 backend/data/app.db，
只读 samryetha_session），而登录本身由 Lako 完成，所以这里同样先确保 Lako 就绪，
再起 i18n 服务和翻译站前端。Lako 已在运行则自动复用。

用法：
    python TranslationBootstrap.py                # Lako + 翻译站
    python TranslationBootstrap.py --no-lako      # Lako 已在别处跑着，只起翻译站
    python TranslationBootstrap.py --skip-install # 依赖装过了，直接起服务
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from LakoBootstrap import (
    ROOT,
    ProcessManager,
    Service,
    check_prereqs,
    ensure_env,
    init_console,
    is_port_open,
    run,
    start_lako,
    LAKO_API_PORT,
    LAKO_WEB_PORT,
)

I18N = ROOT / "i18n"
SITE = I18N / "site"

I18N_PORT = 3002
SITE_PORT = 5200


def translation_services() -> list[Service]:
    return [
        Service(
            "i18n-service",
            ["uv", "run", "python", "-m", "i18n_svc.main"],
            I18N,
            I18N_PORT,
        ),
        Service(
            "i18n-site",
            ["npm", "run", "dev", "--", "--strictPort"],
            SITE,
            SITE_PORT,
        ),
    ]


def catalog_has_entries(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("select count(*) from catalog_entries").fetchone()
            return bool(row and row[0] > 0)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def ensure_translation_setup(skip_install: bool = False) -> None:
    env_file = I18N / ".env"
    ensure_env(I18N / ".env.example", env_file)
    if env_file.exists():
        body = env_file.read_text(encoding="utf-8").replace(" ", "")
        if "I18N_AUTH_DB_URL=" not in body:
            print("[!] i18n/.env 未设置 I18N_AUTH_DB_URL，翻译站将无法识别主站登录态")
            print("    建议: I18N_AUTH_DB_URL=../backend/data/app.db")

    if skip_install:
        print("[=] 跳过翻译站依赖安装")
    else:
        run(["uv", "sync"], I18N)
        run(["npm", "install"], SITE)

    if catalog_has_entries(I18N / "data" / "i18n.db"):
        print("[=] i18n catalog 已有数据，跳过 seed")
    else:
        print("\n==> 导入 i18n seed（空库首次）")
        run(["uv", "run", "python", "seed.py"], I18N)


def main() -> None:
    init_console()
    parser = argparse.ArgumentParser(description="Samryetha 翻译站开发环境引导")
    parser.add_argument(
        "--skip-install", action="store_true", help="跳过 uv sync / npm install"
    )
    parser.add_argument(
        "--no-lako", action="store_true", help="不自动启动 Lako（假定已在运行）"
    )
    args = parser.parse_args()

    print(f"TranslationBootstrap @ {ROOT}\n")
    check_prereqs(["uv", "node", "npm"])

    pm = ProcessManager()
    if args.no_lako:
        if not is_port_open(LAKO_API_PORT) or not is_port_open(LAKO_WEB_PORT):
            print(
                f"[!] --no-lako 但 Lako 未就绪（api :{LAKO_API_PORT} / web :{LAKO_WEB_PORT}）"
            )
    else:
        start_lako(pm, skip_install=args.skip_install)

    ensure_translation_setup(skip_install=args.skip_install)
    pm.start_all(translation_services(), wait=1.0)

    print("\n  lako api        -> http://localhost:8000")
    print("  lako web        -> http://localhost:4010")
    print("  i18n service    -> http://localhost:3002")
    print("  translation site-> http://localhost:5200")
    print("  Ctrl+C 一起退出\n")
    try:
        pm.run_forever()
    finally:
        pm.shutdown()


if __name__ == "__main__":
    main()
