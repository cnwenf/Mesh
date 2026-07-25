"""Settings loading and startup validation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from mesh.config import ConfigError, load_settings

REQUIRED = {"database_url": "postgresql+asyncpg://u:p@h:5432/db", "redis_url": "redis://h:6379/0"}


def test_missing_required_settings_raise_config_error(monkeypatch):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert set(excinfo.value.missing_fields) == {"database_url", "redis_url"}
    assert "database_url" in str(excinfo.value)


def test_missing_single_required_lists_only_that_field(monkeypatch):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    monkeypatch.setenv("MESH_DATABASE_URL", REQUIRED["database_url"])
    with pytest.raises(ConfigError) as excinfo:
        load_settings()
    assert excinfo.value.missing_fields == ("redis_url",)


def test_load_settings_reads_env_and_defaults(monkeypatch):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    monkeypatch.setenv("MESH_DATABASE_URL", REQUIRED["database_url"])
    monkeypatch.setenv("MESH_REDIS_URL", REQUIRED["redis_url"])
    settings = load_settings()
    assert settings.database_url == REQUIRED["database_url"]
    assert settings.redis_url == REQUIRED["redis_url"]
    # Fail-safe default: production auth unless explicitly overridden.
    assert settings.auth_mode == "production"
    assert settings.realtime_event_retention == timedelta(days=7)
    assert settings.outbox_batch_size == 50
    assert settings.outbox_max_attempts == 5


def test_overrides_take_precedence():
    settings = load_settings(**REQUIRED, auth_mode="production", api_port=9999)
    assert settings.auth_mode == "production"
    assert settings.api_port == 9999


def test_invalid_auth_mode_raises_config_error():
    with pytest.raises(ConfigError):
        load_settings(**REQUIRED, auth_mode="bogus")


def test_settings_are_immutable():
    settings = load_settings(**REQUIRED)
    with pytest.raises(ValidationError):
        settings.auth_mode = "production"  # type: ignore[misc]


def test_app_database_url_defaults_to_none():
    # Unset → the app path falls back to the owner URL (backward compatible).
    settings = load_settings(**REQUIRED)
    assert settings.app_database_url is None


def test_app_database_url_override():
    settings = load_settings(
        **REQUIRED, app_database_url="postgresql+asyncpg://app:pw@h:5432/db"
    )
    assert settings.app_database_url == "postgresql+asyncpg://app:pw@h:5432/db"
