#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LakoBootstrap — 启动 Lako 身份服务（api :8000 + web :4010）。

本文件同时充当 ForumBootstrap / TranslationBootstrap 的共享库：
ProcessManager / Service / start_lako / run / wait_for_http 等都在这里，
两个依赖脚本 import 使用，因此有副作用的事只在 ``main()`` 里发生。

用法：
    python LakoBootstrap.py                 # 装依赖 + 迁移 + seed + 启动
    python LakoBootstrap.py --skip-install  # 依赖装过了，直接起服务

端口：lako api 8000（uvicorn）、lako web 4010（next dev）。
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAKO = ROOT / "lako"
LAKO_API = LAKO / "api"
LAKO_WEB = LAKO / "web"

LAKO_API_PORT = 8000
LAKO_WEB_PORT = 4010

IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------- console / shell


def init_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def run(cmd: list[str], cwd: Path, check: bool = True) -> bool:
    """跑一条命令并透传输出。Windows 下经 shell 以解析 .cmd/.ps1。"""
    print(f"  $ {' '.join(cmd)}   [{cwd.name}]")
    joined = " ".join(cmd) if IS_WINDOWS else cmd
    proc = subprocess.run(joined if IS_WINDOWS else cmd, cwd=cwd, shell=IS_WINDOWS)
    if check and proc.returncode != 0:
        sys.exit(f"[x] 命令失败: {' '.join(cmd)} (exit {proc.returncode})")
    return proc.returncode == 0


def capture(cmd: list[str]) -> str:
    joined = " ".join(cmd) if IS_WINDOWS else cmd
    proc = subprocess.run(
        joined if IS_WINDOWS else cmd, shell=IS_WINDOWS, capture_output=True, text=True
    )
    return (proc.stdout or "").strip()


def check_prereqs(tools: list[str]) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        sys.exit(f"[x] 缺少工具: {', '.join(missing)}，请先安装")
    print(
        "[ok] " + " / ".join(f"{t} {capture([t, '--version']) or '?'}" for t in tools)
    )


def ensure_env(example: Path, target: Path) -> None:
    if target.exists():
        print(f"[=] {target.relative_to(ROOT)} 已存在")
        return
    if not example.exists():
        print(
            f"[!] {example.relative_to(ROOT)} 不存在，无法生成 {target.relative_to(ROOT)}"
        )
        return
    shutil.copyfile(example, target)
    print(f"[+] 已生成 {target.relative_to(ROOT)}（按需修改）")


# ---------------------------------------------------------------- ports / health


def is_port_open(port: int, host: str = "localhost") -> bool:
    """localhost 可能解析到 IPv4 或 IPv6（Vite dev 在 Windows 上常绑 ::1），
    两个地址族都探一遍，避免把已运行的服务误判为未启动。"""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        families = [
            (socket.AF_INET, ("127.0.0.1", port)),
            (socket.AF_INET6, ("::1", port)),
        ]
    else:
        families = [(info[0], info[4]) for info in infos]
    for family, sockaddr in families:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(sockaddr) == 0:
                    return True
        except OSError:
            continue
    return False


def wait_for_http(url: str, timeout: float = 60.0, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status < 500:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# ---------------------------------------------------------------- process manager


@dataclass
class Service:
    name: str
    cmd: list[str]
    cwd: Path
    port: int
    env: dict[str, str] | None = None


class ProcessManager:
    """统一拉起/监控/回收一组服务；Ctrl+C 时整棵进程树一起退出。"""

    def __init__(self) -> None:
        self.procs: list[tuple[Service, subprocess.Popen]] = []

    def start(self, svc: Service, wait: float = 0.0) -> bool:
        if is_port_open(svc.port):
            print(f"[=] {svc.name} 已在 :{svc.port} 运行，复用现有进程")
            return False
        env = os.environ.copy()
        if svc.env:
            env.update(svc.env)
        print(f"\n==> 启动 {svc.name}  ({svc.cwd.relative_to(ROOT)})  :{svc.port}")
        if IS_WINDOWS:
            proc = subprocess.Popen(" ".join(svc.cmd), cwd=svc.cwd, shell=True, env=env)
        else:
            proc = subprocess.Popen(
                svc.cmd, cwd=svc.cwd, env=env, start_new_session=True
            )
        self.procs.append((svc, proc))
        if wait:
            time.sleep(wait)
        return True

    def start_all(self, services: list[Service], wait: float = 1.0) -> None:
        for index, svc in enumerate(services):
            self.start(svc, wait=wait if index < len(services) - 1 else 0.0)

    def run_forever(self) -> None:
        try:
            while True:
                for svc, proc in self.procs:
                    if proc.poll() is not None:
                        print(f"\n[!] {svc.name} 已退出 (exit {proc.returncode})")
                        return
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[.] 收到 Ctrl+C，关闭服务...")

    def shutdown(self) -> None:
        for _svc, proc in self.procs:
            if proc.poll() is not None:
                continue
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    shell=True,
                )
            else:
                killpg = getattr(os, "killpg", None)
                getpgid = getattr(os, "getpgid", None)
                if killpg is None or getpgid is None:
                    continue
                try:
                    killpg(getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
        for _svc, proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------- lako service set


def lako_services() -> list[Service]:
    return [
        Service(
            "lako-api",
            [
                "uv",
                "run",
                "uvicorn",
                "app.main:app",
                "--port",
                str(LAKO_API_PORT),
                "--reload",
            ],
            LAKO_API,
            LAKO_API_PORT,
        ),
        Service(
            "lako-web",
            ["pnpm", "exec", "next", "dev", "-p", str(LAKO_WEB_PORT)],
            LAKO_WEB,
            LAKO_WEB_PORT,
            env={"LAKO_API_INTERNAL_URL": f"http://localhost:{LAKO_API_PORT}"},
        ),
    ]


def ensure_lako_setup(skip_install: bool = False) -> None:
    ensure_env(LAKO_API / ".env.example", LAKO_API / ".env")
    if skip_install:
        print("[=] 跳过 Lako 依赖安装")
    else:
        run(["uv", "sync"], LAKO_API)
        run(["pnpm", "install"], LAKO)
        if (LAKO / "packages" / "ui" / "dist" / "index.js").exists():
            print("[=] @lako/ui 已构建")
        else:
            print("\n==> 构建 @lako/ui")
            run(["pnpm", "--dir", "packages/ui", "build"], LAKO)
    print("\n==> 迁移 Lako 数据库 (alembic upgrade head)")
    run(["uv", "run", "alembic", "upgrade", "head"], LAKO_API)
    print("\n==> 初始化 Lako seed（幂等）")
    run(["uv", "run", "python", "-m", "app.cli", "seed"], LAKO_API)


def start_lako(pm: ProcessManager, skip_install: bool = False) -> None:
    """确保 Lako 就绪并交给同一个 ProcessManager 托管（供依赖脚本复用）。"""
    api_up = is_port_open(LAKO_API_PORT)
    if not api_up:
        ensure_lako_setup(skip_install)
    else:
        print(f"[=] lako api 已在 :{LAKO_API_PORT} 运行，跳过初始化")
    pm.start_all(lako_services(), wait=1.0)
    if wait_for_http(f"http://localhost:{LAKO_API_PORT}/health", timeout=60.0):
        print(f"[ok] lako api 就绪 http://localhost:{LAKO_API_PORT}")
    else:
        print("[!] 等待 lako api 健康检查超时，继续启动依赖服务")


# ---------------------------------------------------------------- entrypoint


def main() -> None:
    init_console()
    parser = argparse.ArgumentParser(description="Lako 开发环境引导")
    parser.add_argument(
        "--skip-install", action="store_true", help="跳过 uv sync / pnpm install"
    )
    args = parser.parse_args()

    print(f"LakoBootstrap @ {ROOT}\n")
    check_prereqs(["uv", "node", "pnpm"])

    pm = ProcessManager()
    start_lako(pm, skip_install=args.skip_install)

    print("\n  lako api -> http://localhost:8000")
    print("  lako web -> http://localhost:4010")
    print("  Ctrl+C 一起退出\n")
    try:
        pm.run_forever()
    finally:
        pm.shutdown()


if __name__ == "__main__":
    main()
