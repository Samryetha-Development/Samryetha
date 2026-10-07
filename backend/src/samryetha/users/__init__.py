"""Public user domain API."""

from .service import (
    FAKE_EMAIL_DOMAIN,
    get_by_id,
    get_by_username,
    get_public_profile,
    make_handle,
    next_discriminator,
    normalize_username,
    promote_pending_profile,
    register_user_row,
    to_dto,
    update_profile,
    user_row_from_mapping,
)

__all__ = [
    "FAKE_EMAIL_DOMAIN", "get_by_id", "get_by_username", "get_public_profile", "make_handle",
    "next_discriminator", "normalize_username", "promote_pending_profile", "register_user_row", "to_dto",
    "update_profile", "user_row_from_mapping",
]
