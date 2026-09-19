#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Samryetha 统一引导 — 一键准备并拉起全部服务（Lako + 论坛 + 翻译站）。

服务与端口：
    lako api          8000   Lako 身份服务后端 (uvicorn)
    lako web          4010   Lako 授权页 (next dev)
    forum backend     3001   Samryetha 论坛 API (FastAPI)
    forum frontend    3000   Samryetha 论坛 SSR (vite/express)
    i18n service      3002   翻译 catalog / submissions API
    translation site  5200   翻译站前端 (vite)

论坛与翻译站都依赖 Lako（论坛走 OIDC 登录，翻译站共用论坛会话），
因此任何范围都会先备好并拉起 Lako。

用法：
    python bootstrap.py                  # 只做环境检查 + 装依赖 + 生成 .env
    python bootstrap.py --dev            # 以上全部，再拉起所有服务
    python bootstrap.py --dev --only forum        # 只拉 Lako + 论坛
    python bootstrap.py --dev --only translation  # 只拉 Lako + 翻译站
    python bootstrap.py --dev --only lako         # 只拉 Lako
    python bootstrap.py --dev --skip-install      # 依赖装过了，直接起服务

各服务的独立入口见 LakoBootstrap.py / ForumBootstrap.py / TranslationBootstrap.py。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ForumBootstrap import (
    BACKEND_PORT,
    FRONTEND_PORT,
    ensure_forum_setup,
    forum_services,
)
from LakoBootstrap import (
    LAKO_API_PORT,
    LAKO_WEB_PORT,
    ProcessManager,
    check_prereqs,
    ensure_lako_setup,
    init_console,
    lako_services,
    wait_for_http,
)
from TranslationBootstrap import (
    I18N_PORT,
    SITE_PORT,
    ensure_translation_setup,
    translation_services,
)

SCOPES = ("all", "lako", "forum", "translation")


def scoped_setup(scope: str, skip_install: bool) -> None:
    """按范围准备依赖与配置；Lako 是所有范围的前置，总是先备好。"""
    ensure_lako_setup(skip_install)
    if scope in ("all", "forum"):
        ensure_forum_setup(skip_install)
    if scope in ("all", "translation"):
        ensure_translation_setup(skip_install)


def scoped_services(scope: str):
    """按范围返回要拉起的服务列表（Lako 始终包含）。"""
    services = list(lako_services())
    if scope in ("all", "forum"):
        services += forum_services()
    if scope in ("all", "translation"):
        services += translation_services()
    return services


def print_urls(scope: str) -> None:
    rows = [
        ("lako api", LAKO_API_PORT, True),
        ("lako web", LAKO_WEB_PORT, True),
        ("forum backend", BACKEND_PORT, scope in ("all", "forum")),
        ("forum frontend", FRONTEND_PORT, scope in ("all", "forum")),
        ("i18n service", I18N_PORT, scope in ("all", "translation")),
        ("translation site", SITE_PORT, scope in ("all", "translation")),
    ]
    print()
    for name, port, enabled in rows:
        if enabled:
            print(f"  {name:<17}-> http://localhost:{port}")


def start_all(scope: str) -> None:
    pm = ProcessManager()
    pm.start_all(lako_services(), wait=1.0)
    wait_for_http(f"http://localhost:{LAKO_API_PORT}/health", timeout=60.0)

    others = [
        s
        for s in scoped_services(scope)
        if s.port not in (LAKO_API_PORT, LAKO_WEB_PORT)
    ]
    pm.start_all(others, wait=1.0)

    print_urls(scope)
    print("  Ctrl+C 一起退出\n")
    try:
        pm.run_forever()
    finally:
        pm.shutdown()


def main() -> None:
    init_console()
    parser = argparse.ArgumentParser(description="Samryetha 统一开发环境引导")
    parser.add_argument("--dev", action="store_true", help="准备完成后拉起所有服务")
    parser.add_argument(
        "--skip-install", action="store_true", help="跳过依赖安装（uv/pnpm/npm）"
    )
    parser.add_argument(
        "--only",
        choices=SCOPES,
        default="all",
        help="只拉取某组服务（lako/forum/translation），默认 all",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    print(f"Samryetha bootstrap @ {root}\n")

    check_prereqs(["uv", "node", "pnpm", "npm"])
    scoped_setup(args.only, args.skip_install)

    print("\n[+] 环境就绪。")
    if not args.dev:
        print("    加 --dev 拉起服务：python bootstrap.py --dev")
        return
    start_all(args.only)


if __name__ == "__main__":
    main()
