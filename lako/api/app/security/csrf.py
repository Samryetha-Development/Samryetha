import hmac

from fastapi import Request

from app.common.errors import ApiError

CSRF_COOKIE = "lako_csrf"


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("x-csrf-token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise ApiError(403, "CSRF_FAILED", "CSRF validation failed")
