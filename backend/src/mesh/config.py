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
DEFAULT_OUTBOX_RETENTION_DAYS = 7
DEFAULT_API_PORT = 8000
DEFAULT_WS_PORT = 8081

# Auth defaults (auth.md §3.4/§4.5/§3.6). The access TTL bounds the maximum
# revocation latency for stateless access JWTs; refresh TTL is extended when
# the user ticks "remember me".
DEFAULT_ACCESS_TOKEN_TTL = timedelta(minutes=15)
DEFAULT_REFRESH_TOKEN_TTL = timedelta(days=14)
DEFAULT_REMEMBER_REFRESH_TOKEN_TTL = timedelta(days=30)
DEFAULT_PASSWORD_RESET_TTL = timedelta(hours=1)
DEFAULT_EMAIL_VERIFICATION_TTL = timedelta(hours=24)
DEFAULT_REAUTH_WINDOW = timedelta(minutes=15)

# Login protection thresholds (auth.md §3.6 — (IP, email) tuple dimension).
DEFAULT_LOGIN_MAX_FAILURES = 5
DEFAULT_LOGIN_LOCK_DURATION = timedelta(minutes=15)

# Supported UI locales (auth.md §5.1 R3 — first-release list; extensions are
# registered through the i18n.md message catalog).
SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en")
SUPPORTED_THEMES: tuple[str, ...] = ("light", "dark", "system")

# A clearly-marked development signing key. Production MUST override
# ``MESH_JWT_SECRET``: :func:`validate_auth_settings` refuses this default when
# ``auth_mode=production``, and every app factory that signs or verifies tokens
# (``mesh.api.app.create_app``, ``mesh.realtime.app.create_app``) calls it at
# startup (fail-safe, mirroring the auth_mode pattern).
DEV_JWT_SECRET = "mesh-dev-insecure-signing-key-do-not-use-in-production"


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

    # Auth signing / encryption (auth.md §5.5). The JWT secret signs access
    # tokens; the Fernet key for at-rest secrets (MFA) is derived from it so a
    # single env var drives both. ``jwt_algorithm`` is fixed at the config
    # boundary — verification never trusts the token header's ``alg`` (§5.5).
    # The default is a public dev key: ``validate_auth_settings`` refuses it at
    # app-factory startup when ``auth_mode=production``.
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl: timedelta = DEFAULT_ACCESS_TOKEN_TTL
    refresh_token_ttl: timedelta = DEFAULT_REFRESH_TOKEN_TTL
    remember_refresh_token_ttl: timedelta = DEFAULT_REMEMBER_REFRESH_TOKEN_TTL
    password_reset_ttl: timedelta = DEFAULT_PASSWORD_RESET_TTL
    email_verification_ttl: timedelta = DEFAULT_EMAIL_VERIFICATION_TTL
    reauth_window: timedelta = DEFAULT_REAUTH_WINDOW

    # Login protection (auth.md §3.6).
    login_max_failures: int = Field(default=DEFAULT_LOGIN_MAX_FAILURES, ge=1)
    login_lock_duration: timedelta = DEFAULT_LOGIN_LOCK_DURATION

    # Transactional email (verification / reset). In ``auth_mode=dev`` tokens go
    # to the Redis dev-mailbox (test path); in production a real SMTP server is
    # used when ``smtp_host`` is set, else delivery is a logged no-op so the API
    # still boots (operator must configure SMTP for closed-loop email).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "noreply@mesh.local"
    smtp_use_tls: bool = True
    smtp_timeout: float = Field(default=10.0, gt=0)
    # Base URL used to build verification/reset links in outgoing email.
    app_base_url: str | None = None

    # OAuth (auth.md §1.2 A5/A6). Comma-separated exact-match allowlist of
    # redirect URIs for the dev ``mock`` provider (M1: open-redirect prevention).
    # Production providers carry their own allowlist; empty disables the mock.
    oauth_mock_redirect_uris: str | None = None

    # Process bind addresses.
    api_host: str = "0.0.0.0"
    api_port: int = DEFAULT_API_PORT
    ws_host: str = "0.0.0.0"
    ws_port: int = DEFAULT_WS_PORT

    # Outbox relay tuning (README §6.6 / §2.2).
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_poll_interval: float = Field(default=1.0, gt=0)
    outbox_max_attempts: int = Field(default=5, ge=1)

    # Outbox retention (§6.6): terminal (published/failed) rows are purged once
    # older than this window so outbox_events and its idempotency_key unique
    # index do not grow without bound. Pending rows are never purged. The
    # window far exceeds the relay retry budget, so the permanent-failure
    # alert fires long before a failed row is eligible for cleanup.
    outbox_event_retention: timedelta = Field(
        default=timedelta(days=DEFAULT_OUTBOX_RETENTION_DAYS)
    )
    outbox_retention_interval: float = Field(default=3600.0, gt=0)

    # Realtime retention window (README §6.7: default 7 days, configurable).
    realtime_event_retention: timedelta = Field(
        default=timedelta(days=DEFAULT_REALTIME_RETENTION_DAYS)
    )
    realtime_retention_interval: float = Field(default=3600.0, gt=0)
    realtime_replay_page_size: int = Field(default=200, ge=1, le=1000)
    ws_ping_interval: float = Field(default=30.0, gt=0)

    # Realtime gateway DoS hardening (README §6.16). Unauthenticated sockets
    # are closed after the auth window; inbound frames are limited per rolling
    # second and per-connection subscriptions are capped (a flooding or
    # over-subscribing client is answered with an error, not serviced).
    # ``ws_max_size_bytes`` is the single source of truth for the transport
    # frame ceiling: deployments must start uvicorn with the matching
    # ``--ws-max-size`` (docker-compose does this).
    ws_auth_timeout: float = Field(default=5.0, gt=0)
    ws_max_subscriptions: int = Field(default=256, ge=1)
    ws_max_frames_per_second: int = Field(default=30, ge=1)
    ws_max_size_bytes: int = Field(default=65536, ge=1024)

    # Invitation expiry sweep (workspace.md §4.4 timed expiry complement to the
    # lazy checks on accept/preview).
    invitation_sweep_interval: float = Field(default=300.0, gt=0)


def load_settings(**overrides: object) -> Settings:
    """Build :class:`Settings`, failing fast with a clear error on missing keys."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        missing = tuple(
            sorted({str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"})
        )
        raise ConfigError(missing, str(exc)) from exc


def validate_auth_settings(settings: Settings) -> None:
    """Fail-safe shared by every app factory that signs or verifies tokens.

    Production must never run on the well-known dev signing key (auth.md §5.5 —
    keys not in code/repo): the default is public in this repository, so any
    token forged with it would pass verification. Both ``mesh.api.app`` and
    ``mesh.realtime.app`` call this at startup — the gateway is an independently
    deployable unit (README §2.2) whose configuration can be incomplete even
    when the API's is fine, so the check must not live in a single factory.

    :raises ConfigError: when ``auth_mode=production`` and ``jwt_secret`` is
        still the public development default.
    """
    if settings.auth_mode == "production" and settings.jwt_secret == DEV_JWT_SECRET:
        raise ConfigError(
            ("jwt_secret",),
            "MESH_JWT_SECRET must be set to a strong secret in production",
        )
