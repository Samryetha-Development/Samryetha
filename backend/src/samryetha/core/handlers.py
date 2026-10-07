"""错误序列化与异常处理 — 镜像 backend/src/app/error.ts + Fastify error handler。

包络统一为 ``{error:{code,message,requestId,details?}}``，含 422(验证)/400(空 body)/429(限频)/500 分支。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import TypedDict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core import ErrorDetails
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import APIError, ErrorCode, build_error_body, code_for_status

logger = logging.getLogger("samryetha")


class ValidationDetail(TypedDict):
    field: str
    message: str
    code: str


def _request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = "req_" + uuid.uuid4().hex[:8]
    return rid


# ---------------------------------------------------------------- pydantic → zod 形状

_VALIDATION_CODE_MAP = {
    "missing": "invalid_type",
    "int_type": "invalid_type",
    "string_type": "invalid_type",
    "float_type": "invalid_type",
    "bool_type": "invalid_type",
    "model_attributes_type": "invalid_type",
    "int_parsing": "invalid_type",
    "float_parsing": "invalid_type",
    "string_parsing": "invalid_type",
    "json_invalid": "invalid_json",
    "string_too_short": "too_small",
    "string_too_long": "too_big",
    "string_pattern_mismatch": "invalid_string",
    "greater_than": "too_small",
    "greater_than_equal": "too_small",
    "less_than": "too_big",
    "less_than_equal": "too_big",
    "literal_error": "invalid_enum_value",
    "enum": "invalid_enum_value",
    "value_error": "custom",
}


def _validation_detail(item: ErrorDetails) -> ValidationDetail:
    loc = [str(part) for part in item["loc"]]
    if loc and loc[0] in ("body", "path", "query"):
        loc = loc[1:]
    field = ".".join(loc) if loc else "body"
    etype = item["type"]
    return {
        "field": field,
        "message": item["msg"] or "Invalid value",
        "code": _VALIDATION_CODE_MAP.get(etype, "custom"),
    }


def JSONEnvelope(
    status: int,
    payload: Mapping[str, object],
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status, content=payload, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def on_api_error(request: Request, exc: APIError):
        return JSONEnvelope(exc.status, build_error_body(exc.code, exc.message, _request_id(request), exc.details))

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        # 空 JSON body → 400（复刻 Fastify FST_ERR_CTP_EMPTY_JSON_BODY）
        if request.headers.get("content-type", "").lower().startswith("application/json"):
            try:
                raw = await request.body()
            except Exception:
                raw = b""
            if not raw.strip():
                return JSONEnvelope(
                    400,
                    build_error_body(ErrorCode.BadRequest, "Request body must not be empty", _request_id(request)),
                )
        details = [_validation_detail(e) for e in exc.errors()]
        # 把具体字段错误拼进 message，避免只返回笼统的 "Validation failed"
        summary = "; ".join(f"{d['field']}: {d['message']}" for d in details)
        return JSONEnvelope(
            422,
            build_error_body(
                ErrorCode.ValidationError,
                f"Validation failed — {summary}" if summary else "Validation failed",
                _request_id(request),
                details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def on_http_exception(request: Request, exc: StarletteHTTPException):
        # 404/405 等统一进 {"error":{...}} 包络，避免泄漏 Starlette 默认 {"detail":...} 形状。
        # exc.headers 原样透传：405 的 Allow、401 的 WWW-Authenticate 是协议约定，不能吞掉。
        return JSONEnvelope(
            exc.status_code,
            build_error_body(code_for_status(exc.status_code), str(exc.detail), _request_id(request)),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def on_unhandled(request: Request, exc: Exception):
        logger.error("unhandled error", exc_info=exc)
        return JSONEnvelope(
            500,
            build_error_body(ErrorCode.InternalError, "Internal server error", _request_id(request)),
        )
