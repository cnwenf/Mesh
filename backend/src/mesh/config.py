"""Application settings.

All configuration — including every secret — comes from environment variables
(``MESH_*`` prefix). Required values are validated at startup: a missing or
invalid value fails fast with a clear error instead of crashing later.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_REALTIME_RETENTION_DAYS = 7
DEFAULT_API_PORT = 8000
DEFAULT_WS_PORT = 8081


class ConfigError(RuntimeError):
    """Raised when required settings are missing or invalid at startup."""

    def __init__(self, missing_fields: tuple[str, ...], detail: str) -> None:
        self.missing_fields = missing_fields
        self.detail = detail
        joined = ", ".join(missing_fields) if missing_fields else "see detail"
        super().__init__(f"invalid Mesh configuration (missing/invalid: {joined})")


class Settings(BaseSettings):
    """Immutable runtime settings (README §3.1; secrets are env-only)."""

    model_config = SettingsConfigDict(env_prefix="MESH_", extra="ignore", frozen=True)

    # Required — no defaults, startup fails without them.
    database_url: str
    redis_url: str

    # Auth mode: defaults to "production" (fail-safe — forgetting the variable
    # never enables the dev authenticator). "dev" enables mesh-dev:<workspace>
    # tokens and must be set explicitly (docker compose sets it for local dev).
    auth_mode: Literal["dev", "production"] = "production"

    # The API/gateway application connects with a restricted, non-owner role
    # (mesh_app) so PostgreSQL RLS applies to the app path (M1, §6.2 rule 5) —
    # RLS never applies to the table owner (mesh, also a superuser). Optional:
    # when unset the app falls back to database_url (owner) for backward
    # compatibility; compose sets it to the mesh_app role. Migrations and the
    # worker always use database_url (owner — cross-tenant relay/projector/
    # retention).
    app_database_url: str | None = None

    # Process bind addresses.
    api_host: str = "0.0.0.0"
    api_port: int = DEFAULT_API_PORT
    ws_host: str = "0.0.0.0"
    ws_port: int = DEFAULT_WS_PORT

    # Outbox relay tuning (README §6.6 / §2.2).
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_poll_interval: float = Field(default=1.0, gt=0)
    outbox_max_attempts: int = Field(default=5, ge=1)

    # Realtime retention window (README §6.7: default 7 days, configurable).
    realtime_event_retention: timedelta = Field(
        default=timedelta(days=DEFAULT_REALTIME_RETENTION_DAYS)
    )
    realtime_retention_interval: float = Field(default=3600.0, gt=0)
    realtime_replay_page_size: int = Field(default=200, ge=1, le=1000)
    ws_ping_interval: float = Field(default=30.0, gt=0)


def load_settings(**overrides: object) -> Settings:
    """Build :class:`Settings`, failing fast with a clear error on missing keys."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        missing = tuple(
            sorted({str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"})
        )
        raise ConfigError(missing, str(exc)) from exc
