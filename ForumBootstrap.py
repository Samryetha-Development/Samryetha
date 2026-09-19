#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ForumBootstrap — 启动 Samryetha 论坛（backend :3001 + frontend :3000），并自动拉起 Lako。

论坛登录走 Lako OIDC（backend/.env 的 OIDC_ISSUER=http://localhost:4010），
所以这里先确保 Lako api/web 就绪，再起论坛自己的两个进程，全部交给同一个
ProcessManager，Ctrl+C 一次全退。Lako 已在运行则自动复用，不会重复启动。

用法：
    python ForumBootstrap.py                # Lako + 论坛
    python ForumBootstrap.py --no-lako      # Lako 已在别处跑着，只起论坛
    python ForumBootstrap.py --skip-install # 依赖装过了，直接起服务
"""

from __future__ import annotations

import argparse
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
    LAKO,
    LAKO_API_PORT,
    LAKO_WEB_PORT,
)

BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"

BACKEND_PORT = 3001
FRONTEND_PORT = 3000


def forum_services() -> list[Service]:
    return [
        Service(
            "forum-backend",
            ["uv", "run", "python", "-m", "samryetha.main"],
            BACKEND,
            BACKEND_PORT,
        ),
        Service(
            "forum-frontend",
            ["pnpm", "dev"],
            FRONTEND,
            FRONTEND_PORT,
        ),
    ]


def ensure_forum_setup(skip_install: bool = False) -> None:
    ensure_env(BACKEND / ".env.example", BACKEND / ".env")
    if skip_install:
        print("[=] 跳过论坛依赖安装")
        return
    run(["uv", "sync"], BACKEND)
    run(["pnpm", "install"], FRONTEND)
    ui_deps = [
        ROOT / "packages" / "ui-commons" / "dist",
        LAKO / "packages" / "ui" / "dist",
    ]
    if all(path.exists() for path in ui_deps):
        print("[=] 共享 UI 包已构建")
    else:
        print("\n==> 构建共享 UI 包 (@lako/ui + ui-commons)")
        run(["pnpm", "run", "build:ui"], FRONTEND)


def main() -> None:
    init_console()
    parser = argparse.ArgumentParser(description="Samryetha 论坛开发环境引导")
    parser.add_argument(
        "--skip-install", action="store_true", help="跳过 uv sync / pnpm install"
    )
    parser.add_argument(
        "--no-lako", action="store_true", help="不自动启动 Lako（假定已在运行）"
    )
    args = parser.parse_args()

    print(f"ForumBootstrap @ {ROOT}\n")
    check_prereqs(["uv", "node", "pnpm"])

    pm = ProcessManager()
    if args.no_lako:
        if not is_port_open(LAKO_API_PORT) or not is_port_open(LAKO_WEB_PORT):
            print(
                f"[!] --no-lako 但 Lako 未就绪（api :{LAKO_API_PORT} / web :{LAKO_WEB_PORT}）"
            )
    else:
        start_lako(pm, skip_install=args.skip_install)

    ensure_forum_setup(skip_install=args.skip_install)
    pm.start_all(forum_services(), wait=1.0)

    print("\n  lako api      -> http://localhost:8000")
    print("  lako web      -> http://localhost:4010")
    print("  forum backend -> http://localhost:3001")
    print("  forum frontend-> http://localhost:3000")
    print("  Ctrl+C 一起退出\n")
    try:
        pm.run_forever()
    finally:
        pm.shutdown()


if __name__ == "__main__":
    main()
