"""Map every failure to one JSON shape: {"error": {"code", "message", "details"?}, "request_id"}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.errors import AppError
from app.core.logging import request_id_var

log = logging.getLogger(__name__)

_HTTP_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed", 429: "rate_limited"}


def _error(status: int, code: str, message: str, details: list[dict[str, Any]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse({"error": body, "request_id": request_id_var.get()}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        response = _error(exc.status_code, _HTTP_CODES.get(exc.status_code, "http_error"), str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{"field": ".".join(map(str, e["loc"][1:])), "message": e["msg"]} for e in exc.errors()]
        return _error(422, "validation_error", "Some fields are missing or invalid.", details)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "internal_error", "Something went wrong on our side. Please try again.")
