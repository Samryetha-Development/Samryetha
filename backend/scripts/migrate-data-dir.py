#!/usr/bin/env python3
"""迁移后端可变数据（uploads + SQLite）从仓库内 backend/ 到 SAMRYETHA_DATA_DIR。

Migrate the backend's mutable data (uploads + SQLite) out of the repository (backend/) and
into SAMRYETHA_DATA_DIR (default: a per-OS data directory OUTSIDE the repo).

用法 / Usage（在 backend/ 下运行 / run from backend/）：
    uv run python scripts/migrate-data-dir.py           # dry-run：只打印将做什么，不写任何文件
    uv run python scripts/migrate-data-dir.py --apply   # 复制并校验（复制不移动，保留旧目录）

安全语义 / Safety：
- 默认 **dry-run**，只有显式 `--apply` 才写盘。
- **复制而非移动**（copy, never move），旧目录永远保留；确认无误后由人工清理。
- 校验覆盖文件数、总字节数与逐文件 SHA-256；任何一处不一致都会报错并中止。
- 复制前建议先停掉后端进程（pm2 stop samryetha-backend），避免 SQLite WAL/SHM 边写边拷。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from samryetha.core.config import default_data_dir

# 旧位置：相对路径默认值原先解析到的目录（backend/ 下）。
# Old locations: where the previous relative-path defaults used to land (under backend/).
BACKEND = Path(__file__).resolve().parents[1]
OLD_UPLOADS = BACKEND / "uploads"
OLD_DATA = BACKEND / "data"


def sha256_file(path: Path) -> str:
    """逐块计算单文件 SHA-256。Stream a single file's SHA-256 chunk by chunk."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(dir_path: Path) -> dict[str, tuple[int, str]]:
    """返回 {相对路径: (字节数, sha256)}；目录不存在返回空。
    Return {relative path: (size, sha256)}; empty when the directory does not exist.
    """
    if not dir_path.is_dir():
        return {}
    out: dict[str, tuple[int, str]] = {}
    for p in sorted(dir_path.rglob("*")):
        if p.is_file():
            rel = p.relative_to(dir_path).as_posix()
            out[rel] = (p.stat().st_size, sha256_file(p))
    return out


def collect(src: Path, dst: Path, apply: bool) -> tuple[int, int]:
    """统计 src 内容；apply=True 时复制到 dst 并逐文件校验。返回 (文件数, 总字节数)。
    Enumerate src; when apply=True, copy into dst and verify each file. Returns (count, bytes).
    """
    src_snap = snapshot(src)
    if not src_snap:
        return (0, 0)
    total = sum(size for size, _ in src_snap.values())
    if not apply:
        print(f"  [dry-run] 将复制 {len(src_snap)} 个文件 / {total} 字节：{src} -> {dst}")
        return (len(src_snap), total)
    dst.mkdir(parents=True, exist_ok=True)
    for rel, (size, digest) in src_snap.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
        # 复制后立即按源快照逐文件校验大小与哈希，不一致即报错并中止。
        # Verify each file against the source snapshot immediately after copying; on mismatch
        # raise and stop, keeping the old directory untouched.
        if target.stat().st_size != size or sha256_file(target) != digest:
            raise RuntimeError(f"校验失败（复制后大小/哈希不一致）: {rel}")
    print(f"  [ok] 已复制 {len(src_snap)} 个文件 / {total} 字节：{src} -> {dst}")
    return (len(src_snap), total)


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 Samryetha 后端可变数据到仓库之外")
    parser.add_argument("--apply", action="store_true", help="真正复制（默认只 dry-run，不写任何文件）")
    args = parser.parse_args()

    data_dir = Path(os.environ.get("SAMRYETHA_DATA_DIR") or default_data_dir()).expanduser()
    new_uploads = data_dir / "uploads"
    record_path = data_dir / "migration-record.json"

    print(f"数据根目录（新位置）: {data_dir}")
    print(f"旧上传目录: {OLD_UPLOADS}")
    print(f"旧数据库目录: {OLD_DATA}")
    print(f"模式: {'APPLY（会写盘）' if args.apply else 'DRY-RUN（不写任何文件）'}")

    if not OLD_UPLOADS.is_dir() and not OLD_DATA.is_dir():
        print("[skip] 未发现旧数据目录（backend/uploads / backend/data），无需迁移。")
        return 0

    n_u, b_u = collect(OLD_UPLOADS, new_uploads, args.apply)
    # 数据库：旧 backend/data/*（app.db 及 -wal/-shm 侧车文件）→ 数据根目录下。
    # Database: old backend/data/* (app.db plus -wal/-shm sidecars) -> directly under the data root.
    n_d, b_d = collect(OLD_DATA, data_dir, args.apply)

    print("\n迁移摘要 / Summary:")
    print(f"  上传文件: {n_u} 个 / {b_u} 字节")
    print(f"  数据库文件: {n_d} 个 / {b_d} 字节")
    print(f"  目标目录: {data_dir}")

    if args.apply:
        record = {
            "sourceUploads": str(OLD_UPLOADS),
            "sourceData": str(OLD_DATA),
            "destination": str(data_dir),
            "filesUploads": n_u,
            "filesData": n_d,
            "bytesUploads": b_u,
            "bytesData": b_d,
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ok] 迁移记录已写入：{record_path}")
        print("\n[注意] 旧目录已保留，请人工确认新目录数据完整后再删除：")
        print(f"  rm -rf {OLD_UPLOADS}")
        print(f"  rm -rf {OLD_DATA}")
    else:
        print("\n[dry-run] 未写任何文件。确认无误后加 --apply 真正执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
