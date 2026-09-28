"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from oneledger_domain.money import MoneyParseError
from oneledger_shared.config import get_settings
from oneledger_shared.errors import AppError
from oneledger_shared.logging import configure_logging, request_id_var
from sqlalchemy.exc import DBAPIError, IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from . import __version__
from .routes import register_routes

log = logging.getLogger("oneledger.api")


def _envelope(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {"code": code, "message": message, "request_id": request_id_var.get(), "details": details or {}}
        },
        headers={"Cache-Control": "no-store"},
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Request IDs, private/no-store caching, security headers and metadata-only access logs."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id")
        if not rid or len(rid) > 64 or not all(c.isalnum() or c == "-" for c in rid):
            rid = str(uuid.uuid4())
        token = request_id_var.set(rid)
        started = time.monotonic()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("Cache-Control", "private, no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        route = request.scope.get("route")
        log.info(
            "request",
            extra={
                "request_id": rid,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
        )
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    docs = not settings.is_production_like
    app = FastAPI(
        title="OneLedger API",
        version=__version__,
        description="Private personal-finance ledger API. Money values are decimal strings; date ranges are "
        "half-open [start_date, end_date_exclusive).",
        docs_url="/api/docs" if docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if docs else None,
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Field locations and messages only -- never echo submitted values.
        fields = [{"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))[:200]} for e in exc.errors()]
        return _envelope(422, "VALIDATION_FAILED", "The request is invalid.", {"fields": fields[:20]})

    @app.exception_handler(MoneyParseError)
    async def _money(_: Request, exc: MoneyParseError) -> JSONResponse:
        return _envelope(422, "INVALID_AMOUNT", "The amount is invalid for this currency.")

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        text = str((getattr(exc.orig, "diag", None) and exc.orig.diag.message_primary) or "")  # type: ignore[union-attr]
        code = (
            text
            if text
            in (
                "ALLOCATION_SUM_MISMATCH",
                "CATEGORY_CYCLE",
                "TRANSFER_LEG_EXCEEDS_ALLOCATION",
                "RAW_EVIDENCE_IMMUTABLE",
            )
            else "CONFLICT"
        )
        return _envelope(409, code, "The change conflicts with existing data.")

    @app.exception_handler(DBAPIError)
    async def _dbapi(_: Request, exc: DBAPIError) -> JSONResponse:
        text = str((getattr(exc.orig, "diag", None) and exc.orig.diag.message_primary) or "")  # type: ignore[union-attr]
        if text in ("ALLOCATION_SUM_MISMATCH", "CATEGORY_CYCLE", "CATEGORY_TOO_DEEP"):
            return _envelope(409, text, "The change violates a ledger invariant.")
        log.error("database error", extra={"error_code": type(exc.orig).__name__})
        return _envelope(503, "DATABASE_UNAVAILABLE", "The database is temporarily unavailable.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error", extra={"error_code": type(exc).__name__})
        return _envelope(500, "INTERNAL_ERROR", "Something went wrong. Use the request ID when reporting it.")

    register_routes(app)
    return app


app = create_app()
