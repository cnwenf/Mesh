"""Exception → §6.14 error envelope rendering.

Rules:
- ``MeshError`` renders its own envelope/status/headers.
- FastAPI request validation → 400 ``validation_error`` with per-field details.
- Plain HTTP exceptions map to their canonical snake_case code.
- Unknown exceptions → 500 ``internal_error`` with a fixed neutral message;
  the real exception is logged server-side only (never leaks to the client).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from mesh.errors import INTERNAL_ERROR_MESSAGE, MeshError

logger = logging.getLogger("mesh.api")

_STATUS_CODE_MAP: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "business_rule_violation",
    423: "locked",
    429: "rate_limited",
    500: "internal_error",
    502: "storage_error",
    503: "service_unavailable",
}

_DEFAULT_MESSAGES: dict[int, str] = {
    400: "request validation failed",
    401: "authentication required",
    403: "not allowed",
    404: "resource not found",
    409: "conflicting state",
    410: "resource gone",
    413: "payload too large",
    415: "unsupported media type",
    422: "business validation failed",
    423: "resource locked",
    429: "rate limit exceeded",
    500: INTERNAL_ERROR_MESSAGE,
    502: "storage backend error",
    503: "service unavailable",
}


def install_error_handlers(app: FastAPI) -> None:
    """Install the canonical error handlers on ``app``."""

    @app.exception_handler(MeshError)
    async def _handle_mesh_error(request: Request, exc: MeshError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=exc.to_envelope(), headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "loc": [str(part) for part in err["loc"]],
                "msg": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(exc.status_code, "error")
        if exc.status_code >= 500:
            message = _DEFAULT_MESSAGES.get(exc.status_code, INTERNAL_ERROR_MESSAGE)
        elif isinstance(exc.detail, str):
            message = exc.detail
        else:
            message = _DEFAULT_MESSAGES.get(exc.status_code, "error")
        headers = dict(exc.headers) if exc.headers else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
            headers=headers,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}},
        )
