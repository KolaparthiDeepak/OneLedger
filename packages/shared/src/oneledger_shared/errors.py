"""Application errors with stable, safe error codes.

Messages must never contain financial payloads, secrets or raw provider errors.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"

    def __init__(self, message: str = "Resource not found.", **kw: Any):
        super().__init__(message, **kw)


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class AuthenticationError(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"

    def __init__(self, message: str = "Authentication required.", **kw: Any):
        super().__init__(message, **kw)


class PermissionDenied(AppError):
    status_code = 403
    code = "FORBIDDEN"

    def __init__(self, message: str = "Not permitted.", **kw: Any):
        super().__init__(message, **kw)


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
