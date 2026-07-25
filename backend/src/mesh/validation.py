"""Shared input validators for user-controlled values.

Canonical error codes come from auth.md §3.1 (README §9 T32 alignment):
invalid timezone → 422 ``invalid_timezone``; unsupported locale → 422
``unsupported_locale``; user-controlled URLs are https-only (README §6.16 —
``javascript:``/``data:`` and plain ``http:`` are rejected). Every module that
accepts these values uses these validators so the codes never drift.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from mesh.config import SUPPORTED_LOCALES, SUPPORTED_THEMES
from mesh.errors import BusinessRuleError, ValidationError

# Detail values are user input echoed back for client-side field highlighting;
# they are truncated so a pathological value cannot bloat the error envelope.
_DETAIL_MAX_LENGTH = 128


def validate_iana_timezone(value: str) -> None:
    """Raise 422 ``invalid_timezone`` unless ``value`` is a valid IANA name."""
    try:
        ZoneInfo(value)
    except Exception as exc:  # ZoneInfoNotFoundError / ValueError
        raise BusinessRuleError(
            "unsupported timezone",
            code="invalid_timezone",
            details={"timezone": value},
        ) from exc


def validate_locale(value: str) -> None:
    """Raise 422 ``unsupported_locale`` unless ``value`` is a supported locale."""
    if value not in SUPPORTED_LOCALES:
        raise BusinessRuleError(
            "unsupported locale",
            code="unsupported_locale",
            details={"locale": value, "supported": list(SUPPORTED_LOCALES)},
        )


def validate_theme(value: str) -> None:
    """Raise 422 ``validation_error`` unless ``value`` is a supported theme mode."""
    if value not in SUPPORTED_THEMES:
        raise BusinessRuleError(
            "unsupported theme",
            code="validation_error",
            details={"theme": value, "supported": list(SUPPORTED_THEMES)},
        )


def validate_https_url(value: str, *, field: str) -> None:
    """Raise 400 ``validation_error`` unless ``value`` is an https URL.

    README §6.16: user-controlled URL fields (avatar_url, logo_url, ...) accept
    https only — ``javascript:``/``data:`` schemes are XSS attack surface and
    plain http is a mixed-content weak point.
    """
    if not value.startswith("https://"):
        raise ValidationError(
            f"{field} must be an https URL",
            code="validation_error",
            details={field: value[:_DETAIL_MAX_LENGTH]},
        )
