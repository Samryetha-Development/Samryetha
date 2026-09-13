"""Service-token auth shared by admin endpoints (bulk import, invites)."""

from __future__ import annotations

import hmac

from fastapi import Request

from app.common.config import get_settings
from app.common.errors import ApiError


def require_import_token(request: Request) -> None:
    """Bearer service token gate. Fail-closed when unset. Never expose publicly."""
    token = get_settings().admin_import_token
    if not token:
        raise ApiError(403, "ADMIN_IMPORT_DISABLED", "Admin provisioning is not configured")
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], token):
        raise ApiError(403, "ADMIN_IMPORT_DENIED", "Invalid admin token")
