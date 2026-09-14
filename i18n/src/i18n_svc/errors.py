"""统一错误模型 — 与主站 backend/src/samryetha/errors.py 保持相同 wire envelope。

Wire: { "error": { "code", "message", "requestId", "details"? } }
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    BAD_REQUEST = "BAD_REQUEST"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def bad_request(message: str = "Bad request") -> ApiError:
    return ApiError(ErrorCode.BAD_REQUEST, message, 400)


def auth_required(message: str = "Authentication required") -> ApiError:
    return ApiError(ErrorCode.AUTH_REQUIRED, message, 401)


def forbidden(message: str = "You don't have permission to do this") -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN, message, 403)


def not_found(message: str = "Not found") -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, message, 404)


def conflict(message: str = "Conflict") -> ApiError:
    return ApiError(ErrorCode.CONFLICT, message, 409)


def build_error_body(code: str, message: str, request_id: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message, "requestId": request_id}
    if details is not None:
        body["details"] = details
    return {"error": body}
