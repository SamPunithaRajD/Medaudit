"""Application errors rendered with the contract error envelope:
{"error": {"code", "message", "details", "request_id"}}."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str | None = None, *, code: str | None = None,
                 details: list[dict[str, Any]] | None = None, status_code: int | None = None,
                 headers: dict[str, str] | None = None):
        self.message = message or self.__class__.__doc__ or "Request failed"
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or []
        self.headers = headers
        super().__init__(self.message)


class ValidationFailed(AppError):
    """Validation failed"""
    status_code, code = 422, "VALIDATION_ERROR"


class Unauthenticated(AppError):
    """Authentication required"""
    status_code, code = 401, "UNAUTHENTICATED"


class Forbidden(AppError):
    """You do not have permission to perform this action"""
    status_code, code = 403, "FORBIDDEN"


class NotFound(AppError):
    """Resource not found"""
    status_code, code = 404, "NOT_FOUND"


class Conflict(AppError):
    """Conflict"""
    status_code, code = 409, "CONFLICT"


class PayloadTooLarge(AppError):
    """File too large"""
    status_code, code = 413, "FILE_TOO_LARGE"


class UnsupportedMediaType(AppError):
    """Unsupported media type"""
    status_code, code = 415, "UNSUPPORTED_MEDIA_TYPE"


class RateLimited(AppError):
    """Too many requests"""
    status_code, code = 429, "RATE_LIMITED"


class ServiceUnavailable(AppError):
    """Service unavailable"""
    status_code, code = 503, "SERVICE_UNAVAILABLE"
