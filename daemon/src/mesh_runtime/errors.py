"""Error taxonomy for the daemon.

HTTP failures are classified ONCE, at the API boundary, into a small hierarchy
that orchestration loops pattern-match on. The mapping is fixed by
docs/specs/features/runtime-executor.md §3.1:

- 401            -> FatalAuthError      stop claim, enter isolated/fatal
- 403            -> FatalAuthError      misconfiguration (e.g. tls_required); never downgrade
- 409 lease/term -> LeaseConflictError  kill provider, stop reporting, do not fix the server
- 410            -> GoneError           activation expired; needs a fresh code
- 429            -> RateLimitedError    obey Retry-After
- 5xx / network  -> ServerError         retryable with NETWORK backoff
"""

from __future__ import annotations

LEASE_CONFLICT_CODES = frozenset(
    {
        "lease_seq_mismatch",
        "attempt_terminal",
        "attempt_not_found",
        "runtime_mismatch",
    }
)


class DaemonError(Exception):
    """Base for all daemon-side errors."""


class FatalAuthError(DaemonError):
    """Token invalid / revoked or TLS misconfiguration. Stop claiming."""


class LeaseConflictError(DaemonError):
    """409 fencing: stale lease_seq or attempt already terminal/reclaimed.

    ``code`` distinguishes lease fencing (``lease_seq_mismatch`` /
    ``attempt_terminal``) from log offset drift (``offset_mismatch``); the
    latter carries the server's ``expected`` offset in ``details`` so the
    uploader can reconcile instead of dying.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class GoneError(DaemonError):
    """410: resource gone (activation code consumed/expired)."""


class RateLimitedError(DaemonError):
    """429: retry only after ``retry_after`` seconds (None = server gave none)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(DaemonError):
    """5xx or transport failure — retryable."""


class ProtocolError(DaemonError):
    """Server answered with a body the daemon cannot interpret. Fail-closed."""


def classify_response(
    status: int,
    body: dict | None,
    retry_after: float | None = None,
) -> None:
    """Raise the matching DaemonError for a non-2xx status; return for 2xx.

    ``body`` is the parsed JSON error envelope (``{"error": {"code": ...}}``)
    when available. Never includes response bodies in messages beyond the
    server's error ``code`` — no token/secret/path leakage.
    """
    if 200 <= status < 300:
        return
    code = _error_code(body)
    message = f"server returned {status}" + (f" ({code})" if code else "")
    if status in (401, 403):
        raise FatalAuthError(message)
    if status == 409:
        raise LeaseConflictError(message, code=code, details=_error_details(body))
    if status == 410:
        raise GoneError(message)
    if status == 429:
        raise RateLimitedError(message, retry_after=retry_after)
    if status >= 500:
        raise ServerError(message)
    # 4xx we did not enumerate (400/404/422...) are daemon bugs: fail-closed,
    # not silently retried.
    raise ProtocolError(message)


def _error_code(body: dict | None) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        raw = error.get("code")
        if isinstance(raw, str) and raw:
            return raw
    raw = body.get("code")
    if isinstance(raw, str) and raw:
        return raw
    return None


def _error_details(body: dict | None) -> dict:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("details"), dict):
            return error["details"]
    return {}
