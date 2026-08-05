"""Shared input validators for user-controlled values.

Canonical error codes come from auth.md §3.1 (README §9 T32 alignment):
invalid timezone → 422 ``invalid_timezone``; unsupported locale → 422
``unsupported_locale``; user-controlled URLs are https-only (README §6.16 —
``javascript:``/``data:`` and plain ``http:`` are rejected). Every module that
accepts these values uses these validators so the codes never drift.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from mesh.config import SUPPORTED_LOCALES, SUPPORTED_THEMES
from mesh.errors import BusinessRuleError, ValidationError

# Detail values are user input echoed back for client-side field highlighting;
# they are truncated so a pathological value cannot bloat the error envelope.
_DETAIL_MAX_LENGTH = 128

# Backslash is the LIKE/ILIKE escape character every search path agrees on
# (issue list §3.2, roster search §3.4). Pair ``escape_like`` output with
# ``ilike(pattern, escape=LIKE_ESCAPE_CHAR)`` — without the ``escape=`` clause
# the doubled backslashes would themselves act as wildcards.
LIKE_ESCAPE_CHAR = "\\"


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so ``term`` matches as a literal substring.

    The query stays parameterised (no injection surface) — this only stops
    user-supplied ``%`` / ``_`` / ``\\`` from widening the match set (a raw
    ``q=%`` would otherwise hit the whole table).
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    """Raise 422 ``invalid_theme_mode`` unless ``value`` is a supported theme mode.

    theme.md §3.3: the named ``invalid_theme_mode`` code is the single
    authority for invalid theme values across all owners (auth
    ``users.settings.theme`` via PATCH /users/me, workspace
    ``settings.default_theme`` via PATCH /workspaces/{id}), aligned with
    ``unsupported_locale`` / ``invalid_timezone``. ``details.theme`` /
    ``details.supported`` let frontends render localized copy (§6.18).
    """
    if value not in SUPPORTED_THEMES:
        raise BusinessRuleError(
            "unsupported theme",
            code="invalid_theme_mode",
            details={"theme": value, "supported": list(SUPPORTED_THEMES)},
        )


def validate_https_url(value: str, *, field: str) -> None:
    """Raise 400 ``validation_error`` unless ``value`` is an https URL.

    README §6.16: user-controlled URL fields (avatar_url, logo_url, ...) accept
    https only — ``javascript:``/``data:`` schemes are XSS attack surface and
    plain http is a mixed-content weak point.
    """
    try:
        parsed = urlsplit(value)
        # Accessing port also validates malformed/overflowing port syntax.
        _ = parsed.port
    except (TypeError, ValueError):
        parsed = None
    valid = (
        parsed is not None
        and parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and "%" not in parsed.hostname
        and parsed.netloc != ""
        and "\\" not in value
        and not any(character.isspace() or ord(character) < 32 for character in value)
    )
    if not valid:
        raise ValidationError(
            f"{field} must be an https URL",
            code="validation_error",
            details={field: value[:_DETAIL_MAX_LENGTH]},
        )
