"""Settings loading and startup validation."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from mesh.config import (
    DEV_JWT_SECRET,
    DEV_SEARCH_CURSOR_SECRET,
    ConfigError,
    load_settings,
    validate_auth_settings,
    validate_infra_settings,
    validate_search_settings,
)

REQUIRED = {"database_url": "postgresql+asyncpg://u:p@h:5432/db", "redis_url": "redis://h:6379/0"}

# A production-shaped credential set: every middleware secret is long, random and
# not on the weak denylist. Used to prove the guard accepts a correct deployment.
STRONG = "v3ry-str0ng-r4nd0m-s3cret-0123456789"
STRONG_INFRA = {
    "database_url": f"postgresql+asyncpg://mesh:{STRONG}@postgres:5432/mesh",
    "redis_url": f"redis://:{STRONG}@redis:6379/0",
    "storage_access_key": STRONG,
    "storage_secret_key": STRONG,
}


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
    assert settings.outbox_lock_timeout_seconds == 0.5
    assert settings.outbox_failure_backoff_seconds == 1.0
    assert settings.outbox_failure_backoff_max_seconds == 60.0
    assert settings.outbox_error_backoff_seconds == 1.0


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
    settings = load_settings(**REQUIRED, app_database_url="postgresql+asyncpg://app:pw@h:5432/db")
    assert settings.app_database_url == "postgresql+asyncpg://app:pw@h:5432/db"


@pytest.mark.parametrize(
    "value",
    [
        "mesh.example.com",
        "/mesh",
        "ftp://mesh.example.com",
        "https://user:password@mesh.example.com",
        "https://mesh.example.com?tenant=one",
        "https://mesh.example.com/#fragment",
    ],
)
def test_app_base_url_rejects_non_absolute_or_ambiguous_origins(value):
    with pytest.raises(ConfigError) as excinfo:
        load_settings(**REQUIRED, app_base_url=value)
    assert "app_base_url" in excinfo.value.detail


def test_app_base_url_accepts_http_loopback_and_https_subpath():
    assert load_settings(**REQUIRED, app_base_url="http://127.0.0.1:8000/").app_base_url == (
        "http://127.0.0.1:8000"
    )
    assert load_settings(**REQUIRED, app_base_url="https://mesh.example.com/root/").app_base_url == (
        "https://mesh.example.com/root"
    )


# --- Shared auth fail-safe (auth.md §5.5: keys not in code/repo) -------------
# validate_auth_settings is the single guard every app factory that signs or
# verifies tokens calls at startup — the API and the realtime gateway (an
# independently deployable unit, README §2.2) must not drift apart.


def test_validate_auth_settings_rejects_dev_key_in_production():
    settings = load_settings(**REQUIRED, auth_mode="production")  # default DEV_JWT_SECRET
    assert settings.jwt_secret == DEV_JWT_SECRET
    with pytest.raises(ConfigError) as excinfo:
        validate_auth_settings(settings)
    assert excinfo.value.missing_fields == ("jwt_secret",)
    assert "MESH_JWT_SECRET" in excinfo.value.detail


def test_validate_auth_settings_accepts_strong_secret_in_production():
    settings = load_settings(
        **REQUIRED,
        auth_mode="production",
        jwt_secret="a-real-production-secret-0123456789",
        device_code_pepper="a-real-device-code-pepper-0123456789",
    )
    validate_auth_settings(settings)  # does not raise


def test_validate_auth_settings_accepts_dev_key_in_dev_mode():
    # Regression: the guard is production-only — dev keeps the default key.
    settings = load_settings(**REQUIRED, auth_mode="dev")  # default DEV_JWT_SECRET
    validate_auth_settings(settings)  # does not raise


# --- Search cursor HMAC fail-safe (§3.2; mirrors the jwt_secret guard) ------
# validate_search_settings refuses the public dev cursor key in production;
# called by the API factory at startup (the gateway does not sign cursors).


def test_validate_search_settings_rejects_dev_key_in_production():
    settings = load_settings(**REQUIRED, auth_mode="production")  # default dev key
    assert settings.search_cursor_secret == DEV_SEARCH_CURSOR_SECRET
    with pytest.raises(ConfigError) as excinfo:
        validate_search_settings(settings)
    assert excinfo.value.missing_fields == ("search_cursor_secret",)
    assert "MESH_SEARCH_CURSOR_SECRET" in excinfo.value.detail


def test_validate_search_settings_accepts_strong_secret_in_production():
    settings = load_settings(
        **REQUIRED,
        auth_mode="production",
        jwt_secret="a-real-production-secret-0123456789",
        search_cursor_secret="a-real-production-cursor-secret-9876543210",
    )
    validate_search_settings(settings)  # does not raise


def test_validate_search_settings_accepts_dev_key_in_dev_mode():
    # Regression: the guard is production-only — dev keeps the default key.
    settings = load_settings(**REQUIRED, auth_mode="dev")  # default dev key
    validate_search_settings(settings)  # does not raise


def test_api_factory_refuses_dev_search_cursor_secret_in_production():
    # Fail-fast wiring: create_app rejects the public dev cursor key at
    # startup in production — raised before any engine/IO side effects.
    # device_code_pepper (auth.md §2.4.2) and jwt_secret are satisfied so the
    # earlier validate_auth_settings guard passes and this test isolates the
    # search-cursor guard it targets (create_app runs auth → search → infra).
    from mesh.api.app import create_app

    with pytest.raises(ConfigError) as excinfo:
        create_app(
            load_settings(
                **REQUIRED,
                auth_mode="production",
                jwt_secret="a-real-production-secret-0123456789",
                device_code_pepper="a-real-device-code-pepper-0123456789",
            )
        )  # search_cursor_secret still the public dev default
    assert excinfo.value.missing_fields == ("search_cursor_secret",)


def test_validate_auth_settings_accepts_strong_secret_in_dev_mode():
    settings = load_settings(**REQUIRED, auth_mode="dev", jwt_secret="custom-dev-secret")
    validate_auth_settings(settings)  # does not raise


# --- Middleware credential fail-safe (MES-83: weak default → exposed Redis) ---
# validate_infra_settings is the production-only guard every process that talks
# to a datastore calls at startup — API, realtime gateway and worker. It refuses
# empty / well-known / too-short credentials so a misconfigured production deploy
# fails fast instead of coming up on a guessable password.


def test_validate_infra_settings_accepts_strong_credentials_in_production():
    settings = load_settings(**STRONG_INFRA, auth_mode="production")
    validate_infra_settings(settings)  # does not raise


def test_validate_infra_settings_rejects_unauthenticated_redis_in_production():
    # No password in the Redis URL — the exact shape of the MES-83 incident.
    settings = load_settings(**{**STRONG_INFRA, "redis_url": "redis://redis:6379/0"}, auth_mode="production")
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "redis_url" in excinfo.value.missing_fields


def test_validate_infra_settings_rejects_weak_known_redis_password_in_production():
    settings = load_settings(
        **{**STRONG_INFRA, "redis_url": "redis://:mesh@redis:6379/0"}, auth_mode="production"
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "redis_url" in excinfo.value.missing_fields


def test_validate_infra_settings_rejects_short_redis_password_in_production():
    settings = load_settings(
        **{**STRONG_INFRA, "redis_url": "redis://:abc123@redis:6379/0"}, auth_mode="production"
    )
    with pytest.raises(ConfigError):
        validate_infra_settings(settings)


def test_validate_infra_settings_rejects_weak_database_password_in_production():
    settings = load_settings(
        **{**STRONG_INFRA, "database_url": "postgresql+asyncpg://mesh:mesh@postgres:5432/mesh"},
        auth_mode="production",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "database_url" in excinfo.value.missing_fields


def test_validate_infra_settings_rejects_passwordless_database_url_in_production():
    settings = load_settings(
        **{**STRONG_INFRA, "database_url": "postgresql+asyncpg://mesh@postgres:5432/mesh"},
        auth_mode="production",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "database_url" in excinfo.value.missing_fields


def test_validate_infra_settings_rejects_weak_app_database_password_in_production():
    settings = load_settings(
        **STRONG_INFRA,
        app_database_url="postgresql+asyncpg://mesh_app:mesh_app@postgres:5432/mesh",
        auth_mode="production",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "app_database_url" in excinfo.value.missing_fields


def test_validate_infra_settings_accepts_strong_app_database_password_in_production():
    settings = load_settings(
        **STRONG_INFRA,
        app_database_url=f"postgresql+asyncpg://mesh_app:{STRONG}@postgres:5432/mesh",
        auth_mode="production",
    )
    validate_infra_settings(settings)  # does not raise


def test_validate_infra_settings_rejects_default_storage_credentials_in_production():
    # storage_access_key / storage_secret_key default to "" (no guessable secret
    # ships in the repo, MES-83). Pass them explicitly so the assertion is
    # deterministic even where ambient MESH_STORAGE_* env vars are set (CI
    # injects strong per-run values for the real-storage e2e — an init kwarg
    # overrides the env, so this still exercises the empty-default rejection).
    settings = load_settings(
        database_url=STRONG_INFRA["database_url"],
        redis_url=STRONG_INFRA["redis_url"],
        auth_mode="production",
        storage_access_key="",
        storage_secret_key="",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert "storage_access_key" in excinfo.value.missing_fields
    assert "storage_secret_key" in excinfo.value.missing_fields


def test_validate_infra_settings_skipped_in_dev_mode():
    # Dev/test keep the convenience defaults — the guard is production-only.
    settings = load_settings(**REQUIRED, auth_mode="dev")
    validate_infra_settings(settings)  # does not raise


def test_validate_infra_settings_require_storage_false_skips_storage():
    # Gateway shape (README §2.2 independent unit): strong DB + Redis, no storage
    # config at all — legitimate, so require_storage=False must accept it.
    settings = load_settings(
        database_url=STRONG_INFRA["database_url"],
        redis_url=STRONG_INFRA["redis_url"],
        auth_mode="production",
    )
    validate_infra_settings(settings, require_storage=False)  # does not raise


def test_validate_infra_settings_require_storage_false_still_checks_db_and_redis():
    settings = load_settings(
        database_url=STRONG_INFRA["database_url"],
        redis_url="redis://redis:6379/0",  # no password
        auth_mode="production",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings, require_storage=False)
    assert excinfo.value.missing_fields == ("redis_url",)  # storage not reported


def test_validate_infra_settings_reports_every_weak_field_at_once():
    # Explicit weak storage credentials keep this deterministic under CI's
    # ambient strong MESH_STORAGE_* env (an init kwarg overrides the env).
    settings = load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@postgres:5432/mesh",
        redis_url="redis://redis:6379/0",
        auth_mode="production",
        storage_access_key="",
        storage_secret_key="",
    )
    with pytest.raises(ConfigError) as excinfo:
        validate_infra_settings(settings)
    assert set(excinfo.value.missing_fields) >= {
        "database_url",
        "redis_url",
        "storage_access_key",
        "storage_secret_key",
    }
    assert "MESH_" in excinfo.value.detail  # actionable env-var guidance


# --- MES-80: device-code increment settings (auth.md §2.4.2 / §3.8 / cli.md) --


def _base(monkeypatch, **env):
    monkeypatch.delenv("MESH_DATABASE_URL", raising=False)
    monkeypatch.delenv("MESH_REDIS_URL", raising=False)
    monkeypatch.setenv("MESH_DATABASE_URL", REQUIRED["database_url"])
    monkeypatch.setenv("MESH_REDIS_URL", REQUIRED["redis_url"])
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def test_device_code_settings_defaults(monkeypatch):
    settings = _base(monkeypatch)
    assert settings.device_code_ttl == timedelta(seconds=900)
    assert settings.device_poll_interval == 5
    assert settings.refresh_rotation_grace_seconds == 30
    assert settings.device_code_pepper is None


def test_device_code_settings_from_env(monkeypatch):
    settings = _base(
        monkeypatch,
        MESH_DEVICE_CODE_TTL="PT600S",
        MESH_DEVICE_POLL_INTERVAL="7",
        MESH_REFRESH_ROTATION_GRACE_SECONDS="45",
        MESH_DEVICE_CODE_PEPPER="s3cret-pepper",
    )
    assert settings.device_code_ttl == timedelta(seconds=600)
    assert settings.device_poll_interval == 7
    assert settings.refresh_rotation_grace_seconds == 45
    assert settings.device_code_pepper == "s3cret-pepper"


def test_validate_auth_settings_requires_pepper_in_production(monkeypatch):
    # auth.md §2.4.2 / §5.5: the device-code HMAC pepper is fail-closed in
    # production — a missing pepper must refuse startup, like the JWT secret.
    settings = _base(monkeypatch, MESH_AUTH_MODE="production", MESH_JWT_SECRET="x" * 40)
    with pytest.raises(ConfigError) as excinfo:
        validate_auth_settings(settings)
    assert "device_code_pepper" in excinfo.value.missing_fields


def test_validate_auth_settings_accepts_pepper_in_production(monkeypatch):
    settings = _base(
        monkeypatch,
        MESH_AUTH_MODE="production",
        MESH_JWT_SECRET="x" * 40,
        MESH_DEVICE_CODE_PEPPER="y" * 40,
    )
    validate_auth_settings(settings)  # must not raise


def test_validate_auth_settings_pepper_optional_in_dev(monkeypatch):
    settings = _base(monkeypatch, MESH_AUTH_MODE="dev")
    validate_auth_settings(settings)  # must not raise
