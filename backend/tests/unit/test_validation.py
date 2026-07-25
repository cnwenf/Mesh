"""Shared input validators (README §6.16 / §6.18, auth-canonical error codes)."""

from __future__ import annotations

import pytest

from mesh.errors import BusinessRuleError, ValidationError
from mesh.validation import (
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


def test_unsupported_theme_is_422_validation_error():
    with pytest.raises(BusinessRuleError) as excinfo:
        validate_theme("neon")
    assert excinfo.value.code == "validation_error"
    assert excinfo.value.status_code == 422


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
