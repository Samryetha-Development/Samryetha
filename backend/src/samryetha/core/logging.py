"""Keep HTTP diagnostics without recording login codes or signed URL queries."""

from __future__ import annotations

import copy
import logging

from uvicorn.config import LOGGING_CONFIG


class RedactAccessQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            # Uvicorn: client address, method, request target, HTTP version, status.
            target = args[2].partition("?")[0]
            record.args = (args[0], args[1], target, args[3], args[4])
        return True


def production_logging_config() -> dict[str, object]:
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"redact_query": {"()": RedactAccessQuery}}
    config["handlers"]["access"]["filters"] = ["redact_query"]
    return config
