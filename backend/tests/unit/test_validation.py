"""Shared input validators (README §6.16 / §6.18, auth-canonical error codes)."""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError, ValidationError
from mesh.validation import (
    LIKE_ESCAPE_CHAR,
    escape_like,
    validate_https_url,
    validate_iana_timezone,
    validate_locale,
    validate_theme,
)

pytestmark = pytest.mark.unit


def test_valid_iana_timezones_pass():
    validate_iana_timezone("UTC")
    validate_iana_timezone("Asia/Shanghai")
    validate_iana_timezone("America/New_York")


def test_invalid_timezone_is_422_invalid_timezone():
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_iana_timezone("Not/AZone")
    assert excinfo.value.code == "invalid_timezone"
    assert excinfo.value.status_code == 422
    assert excinfo.value.details == {"timezone": "Not/AZone"}


def test_supported_locales_pass():
    validate_locale("zh-CN")
    validate_locale("en")


def test_unsupported_locale_is_422_unsupported_locale():
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_locale("fr")
    assert excinfo.value.code == "unsupported_locale"
    assert excinfo.value.status_code == 422
    assert excinfo.value.details["locale"] == "fr"
    assert set(excinfo.value.details["supported"]) == {"zh-CN", "en"}


def test_supported_themes_pass():
    validate_theme("light")
    validate_theme("dark")
    validate_theme("system")


def test_unsupported_theme_is_422_invalid_theme_mode():
    # theme.md §3.3: invalid theme values use the named 422 invalid_theme_mode
    # code across all three owners (auth users.settings.theme, workspace
    # settings.default_theme), aligned with unsupported_locale/invalid_timezone.
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_theme("neon")
    assert excinfo.value.code == "invalid_theme_mode"
    assert excinfo.value.status_code == 422
    assert excinfo.value.details["theme"] == "neon"
    assert excinfo.value.details["supported"] == ["light", "dark", "system"]


def test_https_url_passes():
    validate_https_url("https://cdn.example/logo.png", field="logo_url")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "http://cdn.example/logo.png",
        "ftp://cdn.example/logo.png",
        "//cdn.example/logo.png",
        "cdn.example/logo.png",
        "https://",
        "https:///logo.png",
        "https://exa mple/logo.png",
        "https://cdn.example:not-a-port/logo.png",
        "https://%",
    ],
)
def test_non_https_urls_are_400_validation_error(url):
    with pytest.raises(ValidationError) as excinfo:
        validate_https_url(url, field="logo_url")
    assert excinfo.value.code == "validation_error"
    assert excinfo.value.status_code == 400
    assert excinfo.value.details == {"logo_url": url[:128]}


def test_url_field_name_shapes_details():
    with pytest.raises(ValidationError) as excinfo:
        validate_https_url("data:x", field="avatar_url")
    assert excinfo.value.details == {"avatar_url": "data:x"}


def test_escape_like_neutralizes_wildcards():
    """`%` / `_` become literals; every search path shares this one helper."""
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    # plain substrings pass through untouched
    assert escape_like("plain text") == "plain text"


def test_escape_like_doubles_existing_backslashes_first():
    # A trailing backslash must not team up with the later escapes to form
    # an unintended wildcard: escape it before touching % and _.
    assert escape_like("a\\") == "a\\\\"
    assert escape_like("%\\_") == "\\%\\\\\\_"


def test_like_escape_char_is_backslash():
    # The escape= clause on every ilike() must agree with escape_like's
    # output character — pin the shared constant.
    assert LIKE_ESCAPE_CHAR == "\\"
