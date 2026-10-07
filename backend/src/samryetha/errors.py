"""统一错误模型 — 镜像 backend/src/app/error.ts + Fastify error handler 序列化。

Wire envelope: ``{ "error": { "code", "message", "requestId", "details"? } }``
All business errors raise: class:`ApiError`; global handlers in main.py translate
into the envelope (422 / 400-empty-body / 429 / 500 branches included).
"""

from __future__ import annotations

from enum import StrEnum
class ErrorCode(StrEnum):
    BadRequest = "BAD_REQUEST"
    AuthRequired = "AUTH_REQUIRED"
    SessionExpired = "SESSION_EXPIRED"
    InvalidCredentials = "INVALID_CREDENTIALS"
    EmailUnverified = "EMAIL_NOT_VERIFIED"
    Banned = "BANNED"
    Forbidden = "FORBIDDEN"
    NotFound = "NOT_FOUND"
    MethodNotAllowed = "METHOD_NOT_ALLOWED"
    Conflict = "CONFLICT"
    Gone = "GONE"
    PayloadTooLarge = "PAYLOAD_TOO_LARGE"
    UnsupportedMediaType = "UNSUPPORTED_MEDIA_TYPE"
    ValidationError = "VALIDATION_ERROR"
    RateLimited = "RATE_LIMITED"
    TokenInvalidOrExpired = "TOKEN_INVALID_OR_EXPIRED"
    EmailAlreadyVertified = "EMAIL_ALREADY_VERIFIED"
    InternalError = "INTERNAL_ERROR"
    ServiceUnavailable = "SERVICE_UNAVAILABLE"


# 状态码 → 包络 code。Starlette 自己抛的 HTTPException（路由 404、方法 405 等）走这张表，
# 保证 code 与状态码语义一致，而不是一律 BAD_REQUEST（401 配 BAD_REQUEST 会自相矛盾）。
RefMap = {
    400: ErrorCode.BadRequest,
    401: ErrorCode.AuthRequired,
    403: ErrorCode.Forbidden,
    404: ErrorCode.NotFound,
    405: ErrorCode.MethodNotAllowed,
    409: ErrorCode.Conflict,
    410: ErrorCode.Gone,
    413: ErrorCode.PayloadTooLarge,
    415: ErrorCode.UnsupportedMediaType,
    422: ErrorCode.ValidationError,
    429: ErrorCode.RateLimited,
    500: ErrorCode.InternalError,
    503: ErrorCode.ServiceUnavailable,
}


def code_for_status(status: int) -> ErrorCode:
    """未知 4xx 归 BAD_REQUEST、5xx 归 INTERNAL_ERROR，沿用既有 envelope 词汇表。"""
    mapped = RefMap.get(status)
    if mapped is not None:
        return mapped
    return ErrorCode.InternalError if status >= 500 else ErrorCode.BadRequest


class APIError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.name = "ApiError"


# Backward-compatible public name used by existing callers and tests.
ApiError = APIError


# --- helpers (defaults mirror error.ts) ---
def bad_request(message: str = "Bad request") -> APIError:
    return APIError(ErrorCode.BadRequest, message, 400)


def auth_required(message: str = "Authentication required") -> APIError:
    return APIError(ErrorCode.AuthRequired, message, 401)


def invalid_credentials(message: str = "Invalid email or password") -> APIError:
    return APIError(ErrorCode.InvalidCredentials, message, 401)


def forbidden(message: str = "You don't have permission to do this") -> APIError:
    return APIError(ErrorCode.Forbidden, message, 403)


def email_not_verified(message: str = "Please verify your email first") -> APIError:
    return APIError(ErrorCode.EmailUnverified, message, 403)


def banned(message: str = "Your account has been suspended") -> APIError:
    return APIError(ErrorCode.Banned, message, 403)


def not_found(message: str = "Not found") -> APIError:
    return APIError(ErrorCode.NotFound, message, 404)


def conflict(message: str = "Conflict") -> APIError:
    return APIError(ErrorCode.Conflict, message, 409)


def gone(message: str = "Gone") -> APIError:
    return APIError(ErrorCode.Gone, message, 410)


def rate_limited(retry_after_ms: int, message: str = "Too many requests") -> APIError:
    return APIError(ErrorCode.RateLimited, message, 429, {"retryAfterMs": retry_after_ms})


def token_invalid(message: str = "Token is invalid or expired") -> APIError:
    return APIError(ErrorCode.TokenInvalidOrExpired, message, 400)


def internal_error(message: str = "Internal server error") -> APIError:
    return APIError(ErrorCode.InternalError, message, 500)


def service_unavailable(message: str = "Service unavailable") -> APIError:
    return APIError(ErrorCode.ServiceUnavailable, message, 503)


def validation_failed(details: list[dict[str, object]]) -> APIError:
    """手工构造 422 验证错误（复刻 Zod refine 等），envelope 与全局一致。"""
    return APIError(ErrorCode.ValidationError, "Validation failed", 422, details)


def build_error_body(
    code: str,
    message: str,
    request_id: str,
    details: object | None = None,
) -> dict[str, object]:
    """Serialize the error envelope. ``details`` omitted when None (like JSON.stringify skipping undefined)."""
    body: dict[str, object] = {"code": code, "message": message, "requestId": request_id}
    if details is not None:
        body["details"] = details
    return {"error": body}
