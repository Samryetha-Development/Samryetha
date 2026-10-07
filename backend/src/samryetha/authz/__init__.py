"""Public authorization API — the single entry point for `can` / `assert_can`."""

from .service import (
    Abilities,
    Actor,
    AuthorizationResource,
    assert_can,
    can,
    is_active,
    is_global_mod,
)

__all__ = [
    "Abilities",
    "Actor",
    "AuthorizationResource",
    "assert_can",
    "can",
    "is_active",
    "is_global_mod",
]
