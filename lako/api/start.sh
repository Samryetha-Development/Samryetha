#!/usr/bin/env bash
# pm2/生产入口：跑 Lako api（FastAPI）。需先 `uv sync`，且库已 alembic upgrade head。
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${LAKO_API_PORT:-8000}"
