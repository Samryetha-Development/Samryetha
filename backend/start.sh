#!/usr/bin/env bash
# pm2/生产入口：跑 Python(FastAPI) 后端。需先 `uv sync`。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec uv run python -m samryetha.main
