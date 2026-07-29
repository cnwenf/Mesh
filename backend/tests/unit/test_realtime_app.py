"""Realtime gateway application factory startup validation.

The gateway is an independently deployable unit (README §2.2) that verifies
session JWTs on the first WebSocket frame (§6.16), so it must carry the same
production signing-key fail-safe as the API factory — otherwise an operator
who forgets ``MESH_JWT_SECRET`` on the gateway boots fail-open on the public
default key and any forged token passes first-frame auth.
"""

from __future__ import annotations

import pytest

from mesh.config import ConfigError, load_settings
from mesh.realtime.app import create_app


def test_realtime_create_app_refuses_dev_signing_key_in_production(db_url, redis_url):
    """auth.md §5.5: production must never verify tokens on the well-known
    dev key — it is public in the repository."""
    settings = load_settings(
        database_url=db_url, redis_url=redis_url, auth_mode="production"
    )  # default DEV_JWT_SECRET
    with pytest.raises(ConfigError) as excinfo:
        create_app(settings)
    assert excinfo.value.missing_fields == ("jwt_secret",)
    assert "MESH_JWT_SECRET" in excinfo.value.detail


async def test_realtime_create_app_accepts_dev_mode_default_key(db_url, redis_url):
    """Regression: the fail-safe is production-only — dev keeps the default key."""
    settings = load_settings(
        database_url=db_url, redis_url=redis_url, auth_mode="dev"
    )  # default DEV_JWT_SECRET
    app = create_app(settings)
    assert app.state.authenticator is not None
    await app.state.redis.aclose()
    await app.state.engine.dispose()


async def test_realtime_create_app_accepts_strong_secret_in_production():
    """A fully production-grade config (strong signing key AND strong middleware
    credentials — MES-83) is accepted. The factory does not dial the datastores,
    so strong placeholder URLs stand in for the operator's real secrets; the
    gateway never touches object storage, so storage defaults are not checked."""
    strong = "v3ry-str0ng-r4nd0m-s3cret-0123456789"
    settings = load_settings(
        database_url=f"postgresql+asyncpg://mesh:{strong}@postgres.internal:5432/mesh",
        redis_url=f"redis://:{strong}@redis.internal:6379/0",
        auth_mode="production",
        jwt_secret="realtime-factory-test-signing-secret",
        device_code_pepper="realtime-factory-test-device-pepper",
    )
    app = create_app(settings)
    assert app.state.authenticator is not None
    await app.state.redis.aclose()
    await app.state.engine.dispose()
