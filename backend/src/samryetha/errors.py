"""统一错误模型 — 镜像 backend/src/app/error.ts + Fastify error handler 序列化。

Wire envelope: ``{ "error": { "code", "message", "requestId", "details"? } }``
All business errors raise :class:`ApiError`; global handlers in main.py translate
into the envelope (422 / 400-empty-body / 429 / 500 branches included).
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    BAD_REQUEST = "BAD_REQUEST"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_NOT_VERIFIED = "EMAIL_NOT_VERIFIED"
    BANNED = "BANNED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    GONE = "GONE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TOKEN_INVALID_OR_EXPIRED = "TOKEN_INVALID_OR_EXPIRED"
    EMAIL_ALREADY_VERIFIED = "EMAIL_ALREADY_VERIFIED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details
        self.name = "ApiError"


# --- helpers (defaults mirror error.ts) ---
def bad_request(message: str = "Bad request") -> ApiError:
    return ApiError(ErrorCode.BAD_REQUEST, message, 400)


def auth_required(message: str = "Authentication required") -> ApiError:
    return ApiError(ErrorCode.AUTH_REQUIRED, message, 401)


def invalid_credentials(message: str = "Invalid email or password") -> ApiError:
    return ApiError(ErrorCode.INVALID_CREDENTIALS, message, 401)


def forbidden(message: str = "You don't have permission to do this") -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN, message, 403)


def email_not_verified(message: str = "Please verify your email first") -> ApiError:
    return ApiError(ErrorCode.EMAIL_NOT_VERIFIED, message, 403)


def banned(message: str = "Your account has been suspended") -> ApiError:
    return ApiError(ErrorCode.BANNED, message, 403)


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, message, 404)


def conflict(message: str = "Conflict") -> ApiError:
    return ApiError(ErrorCode.CONFLICT, message, 409)


def gone(message: str = "Gone") -> ApiError:
    return ApiError(ErrorCode.GONE, message, 410)


def rate_limited(retry_after_ms: int, message: str = "Too many requests") -> ApiError:
    return ApiError(ErrorCode.RATE_LIMITED, message, 429, {"retryAfterMs": retry_after_ms})


def token_invalid(message: str = "Token is invalid or expired") -> ApiError:
    return ApiError(ErrorCode.TOKEN_INVALID_OR_EXPIRED, message, 400)


def internal_error(message: str = "Internal server error") -> ApiError:
    return ApiError(ErrorCode.INTERNAL_ERROR, message, 500)


def service_unavailable(message: str = "Service unavailable") -> ApiError:
    return ApiError(ErrorCode.SERVICE_UNAVAILABLE, message, 503)


def validation_failed(details: list[dict]) -> ApiError:
    """手工构造 422 验证错误（复刻 Zod refine 等），envelope 与全局一致。"""
    return ApiError(ErrorCode.VALIDATION_ERROR, "Validation failed", 422, details)


def build_error_body(code: str, message: str, request_id: str, details: Any = None) -> dict[str, Any]:
    """Serialize the error envelope. ``details`` omitted when None (like JSON.stringify skipping undefined)."""
    body: dict[str, Any] = {"code": code, "message": message, "requestId": request_id}
    if details is not None:
        body["details"] = details
    return {"error": body}
