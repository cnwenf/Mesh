"""Canonical API error model — docs/specs/README.md §6.14 (唯一权威).

Error envelope (exact shape, no extra fields)::

    {"error": {"code": "<snake_case>", "message": "...", "details": {...}}}

``message`` never leaks stack traces, SQL statements or internal identifiers;
the 500 handler renders a fixed neutral message and logs the real exception
server-side only.
"""

from __future__ import annotations

from typing import Any

INTERNAL_ERROR_MESSAGE = "internal server error"


class MeshError(Exception):
    """Base class for every error rendered through the §6.14 envelope."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = INTERNAL_ERROR_MESSAGE

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message if message is not None else self.__class__.message
        self.details = details
        self.headers = headers
        if code is not None:
            self.code = code
        super().__init__(self.message)

    def to_envelope(self) -> dict[str, dict[str, Any]]:
        """Render the exact §6.14 envelope for this error."""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


class ValidationError(MeshError):
    """400 — request-level validation failure (includes ``filter_too_complex``)."""

    status_code = 400
    code = "validation_error"
    message = "request validation failed"


class UnauthorizedError(MeshError):
    """401 — missing or invalid credentials."""

    status_code = 401
    code = "unauthorized"
    message = "authentication required"


class ForbiddenError(MeshError):
    """403 — authenticated but not allowed."""

    status_code = 403
    code = "forbidden"
    message = "not allowed"


class NotFoundError(MeshError):
    """404 — resource does not exist (or is not visible to the caller)."""

    status_code = 404
    code = "not_found"
    message = "resource not found"


class ConflictError(MeshError):
    """409 — unique-constraint, optimistic-lock or state-machine conflict."""

    status_code = 409
    code = "conflict"
    message = "conflicting state"


class GoneError(MeshError):
    """410 — resource is gone."""

    status_code = 410
    code = "gone"
    message = "resource gone"


class PayloadTooLargeError(MeshError):
    """413 — payload too large."""

    status_code = 413
    code = "payload_too_large"
    message = "payload too large"


class UnsupportedMediaTypeError(MeshError):
    """415 — unsupported media type."""

    status_code = 415
    code = "unsupported_media_type"
    message = "unsupported media type"


class BusinessRuleError(MeshError):
    """422 — business validation failure with a named ``code``."""

    status_code = 422
    code = "business_rule_violation"
    message = "business validation failed"


class LockedError(MeshError):
    """423 — resource locked."""

    status_code = 423
    code = "locked"
    message = "resource locked"


class RateLimitedError(MeshError):
    """429 — rate limited; carries ``Retry-After`` when known."""

    status_code = 429
    code = "rate_limited"
    message = "rate limit exceeded"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(message, details=details, headers=headers, code=code)
        self.retry_after = retry_after


class StorageError(MeshError):
    """502 — downstream storage error."""

    status_code = 502
    code = "storage_error"
    message = "storage backend error"


class ServiceUnavailableError(MeshError):
    """503 — a readiness dependency is unavailable."""

    status_code = 503
    code = "service_unavailable"
    message = "service unavailable"


class UpstreamError(MeshError):
    """502 — an external platform API call failed (integrations.md §3.5
    ``upstream_error``: outbound adapter failures)."""

    status_code = 502
    code = "upstream_error"
    message = "upstream platform error"
