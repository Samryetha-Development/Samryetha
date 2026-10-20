"""Offline preview-cache maintenance. Stop all API workers before using --apply."""
from __future__ import annotations

import argparse
from pathlib import Path
import time

from samryetha.core.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Delete selected files; default is a dry run")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--max-bytes", type=int, default=None)
    args = parser.parse_args()
    if args.max_age_days < 0 or (args.max_bytes is not None and args.max_bytes < 0):
        parser.error("Limits must be nonnegative")
    settings = Settings()
    directory = Path(settings.preview_cache_dir)
    limit = args.max_bytes if args.max_bytes is not None else settings.preview_cache_max_bytes
    cutoff = time.time() - args.max_age_days * 86400
    files = sorted((path for path in directory.iterdir() if path.is_file() and path.suffix in {".pdf", ".failed", ".part"}),
                   key=lambda path: path.stat().st_mtime) if directory.exists() else []
    total = sum(path.stat().st_size for path in files)
    selected: list[Path] = []
    for path in files:
        if path.suffix != ".pdf" or path.stat().st_mtime < cutoff or total > limit:
            total -= path.stat().st_size
            selected.append(path)
    if args.apply:
        for path in selected:
            path.unlink()
    print(f"{'Removed' if args.apply else 'Would remove'} {len(selected)} files; remaining bytes: {total}")


if __name__ == "__main__":
    main()
