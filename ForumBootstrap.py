#!/usr/bin/env python3
"""Prepare and run the Samryetha forum services.

Lako is an external OIDC provider and is not started from this repository. Configure
``backend/.env`` with the issuer exposed by the standalone Lako deployment.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
BACKEND_PORT = 3001
FRONTEND_PORT = 3000


def run(command: list[str], cwd: Path) -> None:
    print(f"[run] {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def check_prereqs(commands: list[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise SystemExit(f"Missing required commands: {', '.join(missing)}")


def ensure_env(example: Path, target: Path) -> None:
    if target.exists():
        return
    if not example.exists():
        raise SystemExit(f"Missing environment template: {example}")
    shutil.copy2(example, target)
    print(f"[+] Created {target.relative_to(ROOT)}")


def ensure_forum_setup(skip_install: bool = False) -> None:
    ensure_env(BACKEND / ".env.example", BACKEND / ".env")
    if skip_install:
        print("[=] Skipping dependency installation")
        return
    run(["uv", "sync"], BACKEND)
    run(["pnpm", "install"], FRONTEND)


@dataclass(frozen=True)
class Service:
    name: str
    command: list[str]
    cwd: Path


def forum_services() -> list[Service]:
    return [
        Service("forum-backend", ["uv", "run", "python", "-m", "samryetha.main"], BACKEND),
        Service("forum-frontend", ["pnpm", "dev"], FRONTEND),
    ]


def start_services(services: list[Service]) -> int:
    processes: list[tuple[Service, subprocess.Popen[bytes]]] = []
    try:
        for service in services:
            print(f"[+] Starting {service.name}")
            processes.append((service, subprocess.Popen(service.command, cwd=service.cwd)))
            time.sleep(0.5)
        print(f"\n  forum backend  -> http://localhost:{BACKEND_PORT}")
        print(f"  forum frontend -> http://localhost:{FRONTEND_PORT}")
        print("  Ctrl+C stops both services\n")
        while True:
            for service, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[!] {service.name} exited with code {code}", file=sys.stderr)
                    return code
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        for _, process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for _, process in reversed(processes):
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare and run Samryetha")
    parser.add_argument("--dev", action="store_true", help="Start backend and frontend after setup")
    parser.add_argument("--skip-install", action="store_true", help="Skip uv/pnpm installation")
    args = parser.parse_args()

    check_prereqs(["uv", "node", "pnpm"])
    ensure_forum_setup(args.skip_install)
    if args.dev:
        raise SystemExit(start_services(forum_services()))
    print("[+] Samryetha is ready. Use --dev to start it.")


if __name__ == "__main__":
    main()
