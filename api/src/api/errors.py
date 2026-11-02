"""Uniform error body `{code, message, detail}` for every non-2xx response (contract Error)."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    401: "unauthorized",
    404: "not_found",
    406: "not_acceptable",
    409: "conflict",
    429: "rate_limited",
}


class ApiError(Exception):
    """Raise from routes to return a contract error with an explicit code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail
        self.headers = headers


def error_response(
    status_code: int,
    code: str,
    message: str,
    detail: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "detail": detail},
        headers=headers,
    )


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.code, exc.message, exc.detail, exc.headers)


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code >= 500:
        code = "internal_error"
    else:
        code = _STATUS_CODES.get(exc.status_code, "validation_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, message, None, headers)


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # ctx may hold non-serialisable objects, so keep only the documented keys.
    detail = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    return error_response(422, "validation_error", "Request body is invalid.", detail)


async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error", exc_info=exc)
    return error_response(
        500, "internal_error", "Something went wrong on our side. Please try again later."
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unexpected)
