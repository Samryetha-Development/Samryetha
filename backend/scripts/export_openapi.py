"""Regenerate docs/openapi.json from the FastAPI application."""

from __future__ import annotations

import json
from pathlib import Path

from samryetha.config import Settings
from samryetha.main import create_app


root = Path(__file__).resolve().parents[1]
app = create_app(
    Settings(
        _env_file=None,
        node_env="test",
        database_url=":memory:",
        upload_dir=str(root / "uploads"),
    )
)
(root / "docs" / "openapi.json").write_text(
    json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
