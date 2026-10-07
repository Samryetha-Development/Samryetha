"""Typed scalar readers for SQLAlchemy Core row mappings.

Consolidates the per-domain ``_int``/``_str``/``_required_*`` row parsers that
were copy-pasted across services and repositories into one place.
"""

from __future__ import annotations


def require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def opt_int(value: object, field: str) -> int | None:
    return None if value is None else require_int(value, field)


def opt_str(value: object, field: str) -> str | None:
    return None if value is None else require_str(value, field)
