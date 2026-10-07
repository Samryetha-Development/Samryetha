"""Public auth domain API."""

from .service import (
    RESET_INVALID_MESSAGE,
    RESET_MESSAGE,
    RESET_TTL_MS,
    AuthLoginResult,
    change_password,
    ensure_builtin_accounts,
    forgot_password,
    login,
    logout,
    merge_moderator_roles,
    register,
    reset_password,
)

__all__ = [
    "RESET_INVALID_MESSAGE",
    "RESET_MESSAGE",
    "RESET_TTL_MS",
    "AuthLoginResult",
    "change_password",
    "ensure_builtin_accounts",
    "forgot_password",
    "login",
    "logout",
    "merge_moderator_roles",
    "register",
    "reset_password",
]
