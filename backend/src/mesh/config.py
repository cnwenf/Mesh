"""Application settings.

All configuration — including every secret — comes from environment variables
(``MESH_*`` prefix). Required values are validated at startup: a missing or
invalid value fails fast with a clear error instead of crashing later.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit

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

# HttpOnly session cookie carrying the refresh token (auth.md §5.5 web session
# form). Read by the HTML entry middleware (mesh.web.entry) to resolve the
# first-frame theme negotiation chain server-side (theme.md §2.3 ①).
SESSION_COOKIE_NAME = "mesh_session"

# A clearly-marked development signing key. Production MUST override
# ``MESH_JWT_SECRET``: :func:`validate_auth_settings` refuses this default when
# ``auth_mode=production``, and every app factory that signs or verifies tokens
# (``mesh.api.app.create_app``, ``mesh.realtime.app.create_app``) calls it at
# startup (fail-safe, mirroring the auth_mode pattern).
DEV_JWT_SECRET = "mesh-dev-insecure-signing-key-do-not-use-in-production"

# Middleware credential guard (MES-83). A publicly reachable Redis shipped with a
# guessable password is exactly how the incident happened; production must never
# come up on an empty, well-known, or too-short secret. ``validate_infra_settings``
# refuses any of these at startup when ``auth_mode=production``.
WEAK_SECRET_DENYLIST: frozenset[str] = frozenset(
    {
        "mesh",
        "mesh_app",
        "mesh_minio_secret",
        "minioadmin",
        "postgres",
        "password",
        "secret",
        "admin",
        "root",
        "changeme",
        "change-me",
        "letmein",
        "test",
    }
)
# Below this length a secret is rejected outright regardless of content.
MIN_SECRET_LENGTH = 16


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

    # Secure flag on the mesh_session cookie (auth.md §5.5 / theme.md §2.3 ①).
    # None (default) → derive from auth_mode (dev/http loopback = False so the
    # cookie is sent over plain http in local dev; production = True). Set
    # explicitly for a TLS-terminator deployment running auth_mode=production
    # behind a proxy that speaks http to the app.
    cookie_secure: bool | None = None

    # The API/gateway application connects with a restricted, non-owner role
    # (mesh_app) so PostgreSQL RLS applies to the app path (M1, §6.2 rule 5) —
    # RLS never applies to the table owner (mesh, also a superuser). Optional:
    # when unset the app falls back to database_url (owner) for backward
    # compatibility; compose sets it to the mesh_app role. Migrations and the
    # worker always use database_url (owner — cross-tenant relay/projector/
    # retention).
    app_database_url: str | None = None

    # Per-statement timeout backstop for the API/gateway app path (L7): a
    # runaway query is cancelled by PostgreSQL instead of holding a connection
    # indefinitely. Applies only to the app engine — the worker's cross-tenant
    # maintenance loops are exempt. 0 disables the timeout.
    app_statement_timeout: timedelta = Field(default=timedelta(seconds=30), ge=0)

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

    # Login-class endpoint rate limit (auth.md §3.6 — "阈值示例,可调": the
    # thresholds are tunable; covers register/login/forgot/mfa-verify/
    # change-password/reauth per (IP, email)).
    auth_rate_limit: int = Field(default=5, ge=1)
    auth_rate_window: timedelta = Field(default=timedelta(seconds=60), gt=0)

    # Device-code authorization (auth.md §2.4.2 / §3.1.1, cli.md §3.2). The
    # HMAC pepper keys the device_code/user_code hashes — low-entropy user
    # codes MUST NOT be stored under bare SHA-256 (offline dictionary attack),
    # so production startup fails closed when the pepper is absent (validated
    # in ``validate_auth_settings``, same baseline as the JWT secret).
    device_code_pepper: str | None = None
    device_code_ttl: timedelta = Field(default=timedelta(seconds=900), gt=0)
    device_poll_interval: int = Field(default=5, ge=1)
    device_auth_sweep_interval: float = Field(default=60.0, gt=0)

    # Refresh rotation race (auth.md §3.8): a rotated refresh token stays
    # acceptable for this window, issuing ONLY a fresh access token (never a
    # refresh, never a second rotation) so concurrent multi-tab / multi-process
    # refreshes converge on the winner's credential instead of logging out.
    refresh_rotation_grace_seconds: int = Field(default=30, ge=0)

    # Web session cookie attributes (auth.md R4-H1): HttpOnly + SameSite=Strict
    # + Path=/ are unconditional; ``Secure`` is on by default and only relaxed
    # for plaintext loopback dev stacks (same deliberate exception pattern as
    # MESH_DAEMON_TLS_REQUIRED — production leaves it true).
    session_cookie_secure: bool = True

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

    # theme.md §2.3 ①: directory holding the built frontend (index.html +
    # assets) that the personalized HTML entry serves with the per-request
    # __MESH_APPEARANCE__ injection. Populated in production via a shared
    # volume from the frontend image; when index.html is absent the entry
    # degrades to 404 and the JSON API is unaffected.
    frontend_dist_dir: str = "/srv/mesh/frontend"

    # Outbox relay tuning (README §6.6 / §2.2).
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    outbox_poll_interval: float = Field(default=1.0, gt=0)
    outbox_max_attempts: int = Field(default=5, ge=1)

    # Outbox retention (§6.6): terminal (published/failed) rows are purged once
    # older than this window so outbox_events and its idempotency_key unique
    # index do not grow without bound. Pending rows are never purged. The
    # window far exceeds the relay retry budget, so the permanent-failure
    # alert fires long before a failed row is eligible for cleanup.
    outbox_event_retention: timedelta = Field(default=timedelta(days=DEFAULT_OUTBOX_RETENTION_DAYS))
    outbox_retention_interval: float = Field(default=3600.0, gt=0)

    # Realtime retention window (README §6.7: default 7 days, configurable).
    realtime_event_retention: timedelta = Field(default=timedelta(days=DEFAULT_REALTIME_RETENTION_DAYS))
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

    # Chat module tunables (chat-session.md §3.3 / §3.5, README §6.8).
    # ``chat_generation_chunk_delay_seconds`` paces the built-in generation
    # provider (0 = as fast as possible; raise for a visible typewriter effect
    # in demos). The SSE stream heartbeats every ``chat_stream_ping_seconds``
    # (§5.2 — 15 s keeps intermediaries from dropping idle connections) and
    # caps a single connection at ``chat_stream_max_seconds``. Delta buffers
    # expire after ``chat_generation_buffer_ttl_seconds``; late subscribers
    # then degrade to the REST final-content path (chat-session.md §3.3).
    chat_generation_chunk_delay_seconds: float = Field(default=0.0, ge=0)
    chat_stream_ping_seconds: float = Field(default=15.0, gt=0)
    chat_stream_max_seconds: float = Field(default=600.0, gt=0)
    chat_generation_buffer_ttl_seconds: int = Field(default=3600, gt=0)
    chat_send_rate_limit: int = Field(default=30, ge=1)
    chat_send_rate_window_seconds: int = Field(default=60, ge=1)
    # A streaming message older than this (started_at) is considered stuck (engine
    # crash / lost task) and is reclaimed so the single-concurrency guard frees
    # up; a new send then proceeds instead of returning 409 forever.
    chat_streaming_stale_seconds: int = Field(default=600, ge=1)

    # Skill imports (skill.md §5.3 / §1.3 / §3.5). Server-side source fetches
    # are SSRF-guarded (public addresses only); ``skill_source_host_allowlist``
    # is the documented escape hatch (comma-separated hosts) for intranet
    # registries / loopback fixtures. ``skill_marketplace_url`` is the external
    # marketplace listings API the marketplace page consumes (empty = no
    # market). ``skill_import_sweep_interval`` drives the crash-recovery sweep
    # over in-flight import tasks.
    skill_source_host_allowlist: str | None = None
    skill_marketplace_url: str | None = None
    skill_import_sweep_interval: float = Field(default=1.0, gt=0)

    # Object storage for the attachment module (attachment.md §3). The bucket
    # is PRIVATE; every access goes through short-lived presigned URLs and the
    # byte stream never transits the API process (three-stage direct upload).
    # ``storage_endpoint`` is what the server process can reach (compose
    # network); ``storage_public_endpoint`` is what browsers/CLIs can reach —
    # presigned URLs are signed against the public endpoint because clients
    # talk to object storage directly. When unset it falls back to
    # ``storage_endpoint`` (single-NIC deployments).
    storage_endpoint: str = "http://127.0.0.1:9000"
    storage_public_endpoint: str | None = None
    storage_region: str = "us-east-1"
    # Object-storage credentials have NO guessable default (MES-83: a weak
    # default shipped in this public file let an attacker who reads the repo
    # take over any instance still using it). Empty here means "unset";
    # ``validate_infra_settings`` rejects empty/known/short values when
    # ``auth_mode=production``, and local dev gets strong per-checkout values
    # from compose / scripts/gen-dev-secrets.sh. Dev/test may set them
    # explicitly for a throwaway MinIO.
    storage_access_key: str = ""
    storage_secret_key: str = ""
    storage_bucket: str = "mesh-attachments"

    # Attachment limits & lifecycles (attachment.md §3.6/§4.6 — defaults are
    # configurable; per-workspace overrides live in ``attachment_quotas``).
    attachment_max_file_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    attachment_max_image_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    attachment_total_bytes: int = Field(default=10 * 1024 * 1024 * 1024, gt=0)
    # Signed PUT URLs expire with the upload record; signed GET URLs are
    # short-lived (~60s, single purpose, bound to method + object key).
    attachment_upload_ttl: timedelta = Field(default=timedelta(minutes=15), gt=0)
    attachment_download_url_ttl: timedelta = Field(default=timedelta(seconds=60), gt=0)
    # Soft-deleted attachments hard-delete after this window; orphaned uploads
    # past ``expires_at`` are reaped regardless of retention (§4.6).
    attachment_soft_delete_retention: timedelta = Field(default=timedelta(days=7), ge=0)
    # Multipart uploads (attachment.md §2.5/§3.1): files at or above the
    # threshold are split into ``multipart_part_bytes`` parts, signed in
    # batches of ``multipart_part_batch``.
    attachment_multipart_threshold: int = Field(default=64 * 1024 * 1024, gt=0)
    attachment_multipart_part_bytes: int = Field(default=8 * 1024 * 1024, ge=5 * 1024 * 1024)
    attachment_multipart_part_batch: int = Field(default=4, ge=1, le=100)
    # Quarantine pipeline tuning (attachment.md §3.3 — the processing worker).
    attachment_scan_interval: float = Field(default=5.0, gt=0)
    attachment_scan_batch_size: int = Field(default=10, ge=1, le=100)
    attachment_orphan_sweep_interval: float = Field(default=300.0, gt=0)
    attachment_gc_interval: float = Field(default=3600.0, gt=0)
    # Plain-text scan-skip whitelist (attachment.md §3.6): when enabled the
    # worker sets blob scan_status='skipped' for macro-free plain-text types
    # (still magic-byte sniffed and SHA-256 verified). Disable to force a full
    # AV pass on every upload.
    attachment_scan_skip_text: bool = True
    # Comment & inbox tuning (comment-inbox.md §3.5 / README §6.9 & §6.13).
    # Agent-to-agent mention chains deeper than this are silently dropped with
    # an audit record (A↔B @-loop protection); same-group notifications inside
    # the aggregation window merge into one row (payload.count increments).
    max_agent_chain_depth: int = Field(default=5, ge=1)
    notification_aggregation_window: float = Field(default=60.0, gt=0)
    notification_digest_interval: float = Field(default=21600.0, gt=0)
    # Due-soon reminder sweep (comment-inbox.md §2.2 ``due_soon`` producer):
    # open issues whose due date falls inside the horizon get one fan-out
    # per issue+due-date (relay-side matrix/routing applies).
    due_soon_sweep_interval: float = Field(default=900.0, gt=0)
    due_soon_horizon_hours: float = Field(default=24.0, gt=0)

    # -- Data jobs module (import-export.md) ------------------------------------
    # Rows per import batch — each batch is ONE database transaction carrying
    # the fencing check, entity creation, ledger rows, counters/checkpoint
    # advance, lease renewal and the progress event (§3.4 / §3.8).
    data_job_batch_size: int = Field(default=500, ge=1, le=5000)
    # Worker lease granted at claim and renewed at each batch commit (§3.8
    # R3: default 5 min). Stuck jobs are reclaimed after expiry (reaper).
    data_job_lease_ttl: timedelta = Field(default=timedelta(minutes=5), gt=0)
    # Reaper sweep interval: lease-expired recovery + stuck-pending
    # compensating re-enqueue (§3.8).
    data_job_reaper_interval: float = Field(default=15.0, gt=0)
    # A pending export (or a requested-but-unclaimed validate/run) older than
    # this grace window is re-enqueued by the compensating sweep (§3.8 —
    # eliminates permanently stuck jobs).
    data_job_stuck_grace: timedelta = Field(default=timedelta(minutes=5), ge=0)
    # Source file ceiling (streamed to scratch storage, never fully loaded;
    # aligned with the attachment upload cap).
    data_job_source_max_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    # Export product ceilings — estimated at create (413) and re-checked while
    # streaming (failed export_too_large, §3.5 / §5.2).
    data_job_export_max_rows: int = Field(default=200_000, gt=0)
    data_job_export_max_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    # Inline error_report preview cap; the full detail streams into the error
    # report attachment (§2.4 — JSONB must not bloat).
    data_job_error_preview_max: int = Field(default=1000, ge=1)
    # Mapping preview rows returned by validate (§3.3).
    data_job_preview_rows: int = Field(default=10, ge=1, le=100)
    # Create-endpoint rate limiting per user+workspace (§3.0, auth.md).
    data_job_create_limit: int = Field(default=30, ge=1)
    data_job_create_window_seconds: int = Field(default=60, gt=0)
    # Crash-recovery resume cap: a deterministic poison batch that crashes on
    # every resume is terminated (resume_limit_exceeded) instead of looping
    # forever (T31⑤ / H2).
    data_job_max_resumes: int = Field(default=50, ge=1)

    # -- Runtime module (runtime.md) ------------------------------------------
    # One-shot activation codes are short-lived (§3.1: default 15 min) and
    # stored hash-only; expiry lands in runtimes.activation_expires_at.
    runtime_activation_ttl: timedelta = Field(default=timedelta(minutes=15), gt=0)
    # Lease granted at claim / renew (seconds). Daemons renew around 1/3 of the
    # remaining lifetime (§4.8); expiry → reaper reclaim.
    runtime_lease_seconds: int = Field(default=120, gt=0)
    # Reaper sweep interval: lease-expired + heartbeat-lost recovery (§4.8).
    runtime_reaper_interval: float = Field(default=5.0, gt=0)
    # Heartbeat staleness window = interval × multiplier (§5.1: default 45s).
    runtime_heartbeat_timeout_multiplier: int = Field(default=3, gt=0)
    # Per-runtime heartbeat detail retention window (§2.2, optional detail).
    runtime_heartbeat_retention: timedelta = Field(default=timedelta(hours=1), gt=0)
    # Short-lived credential envelope TTL (§2.2 protocol: ≤2h).
    runtime_envelope_ttl: timedelta = Field(default=timedelta(hours=2), gt=0)
    # Per-attempt credential refetch cap; exceeding it freezes the execution
    # for human review (§2.2).
    runtime_credential_refetch_limit: int = Field(default=3, ge=1)
    # Log segment sealing threshold in bytes (§2.3).
    runtime_log_segment_bytes: int = Field(default=64 * 1024, gt=0)
    # Log retention TTL (§2.3).
    runtime_log_retention: timedelta = Field(default=timedelta(days=30), gt=0)
    # Pending approval expiry (§6.10: reaper sweep expires, execution cancels).
    runtime_approval_ttl: timedelta = Field(default=timedelta(hours=24), gt=0)
    # Machine API TLS red line (§3.5): refuse non-TLS on /api/v1/daemon/.
    # Local dev / compose (plain HTTP loopback) sets this to false; production
    # keeps the secure default.
    daemon_tls_required: bool = True
    # X-Forwarded-Proto is trusted ONLY from these direct peers (review M3:
    # a spoofed header from anywhere else must not bypass the TLS gate).
    # Configure your TLS-terminating LB's address here in production.
    daemon_trusted_proxies: str = "127.0.0.1,::1"
    # Signed release package metadata served by the registration wizard
    # (§3.1 — placeholder distribution endpoints; deployers replace them).
    runtime_release_version: str = "1.0.0"
    runtime_release_artifact_url: str = (
        "https://releases.mesh.example/runtime/1.0.0/mesh-runtime_1.0.0_linux_x86_64.tar.gz"
    )
    runtime_release_sha256: str = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    runtime_release_signature_url: str = (
        "https://releases.mesh.example/runtime/1.0.0/mesh-runtime_1.0.0_linux_x86_64.tar.gz.sig"
    )
    runtime_release_signing_key_url: str = "https://releases.mesh.example/mesh-release.pub"

    # -- Autopilot module (autopilot.md) ------------------------------------
    # Scheduler scan cadence (§4.5: 10–30s; PostgreSQL is the only scheduler
    # source of truth — atomic next_run_at claim, multi-replica safe).
    autopilot_schedule_interval: float = Field(default=15.0, gt=0)
    # Due schedule rules claimed per scan pass (SKIP LOCKED batch).
    autopilot_schedule_batch: int = Field(default=50, ge=1, le=1000)
    # Run executor / execution-terminal reconciler cadence (§4.5).
    autopilot_executor_interval: float = Field(default=2.0, gt=0)
    # Pending autopilot_action approval expiry (README §6.10: the reaper
    # expires, the run cancels with approval_expired).
    autopilot_approval_ttl: timedelta = Field(default=timedelta(hours=24), gt=0)
    # Misfire grace: a schedule slot missed by more than this many seconds is
    # handled per misfire_policy instead of firing as "on time" (§2.6).
    autopilot_misfire_grace_seconds: int = Field(default=300, ge=0)
    # misfire_policy='run_all' catch-up cap per scan (bounded replay).
    autopilot_run_all_cap: int = Field(default=50, ge=1)
    # Inbound webhook signature timestamp tolerance window (§3.2: ±300s).
    autopilot_webhook_timestamp_tolerance: timedelta = Field(
        default=timedelta(seconds=300), gt=0
    )

    # -- Analytics module (analytics.md §2.5/§2.6) ------------------------------
    # Snapshot freshness: a cached aggregate older than this is recomputed
    # (§2.6 default 15 min; ``workload`` is never cached).
    analytics_snapshot_ttl: timedelta = Field(default=timedelta(minutes=15), gt=timedelta(0))
    # When a cached aggregate is stale: ``False`` recomputes synchronously and
    # returns the fresh value (dashboard first paint); ``True`` returns the
    # stale value and refreshes in the background (stale-while-revalidate).
    analytics_stale_while_revalidate: bool = False

    # -- Integrations module (integrations.md §3.2/§3.4) -------------------
    # Inbound platform signature timestamp tolerance window (§3.2: ±300s).
    integration_signature_tolerance: timedelta = Field(
        default=timedelta(seconds=300), gt=0
    )
    # Outbound webhook delivery: retry/backoff + subscription circuit
    # breaker (§2.6 workspace-level constants).
    webhook_delivery_max_attempts: int = Field(default=8, ge=1)
    webhook_delivery_base_seconds: int = Field(default=30, ge=1)
    webhook_delivery_max_seconds: int = Field(default=3600, ge=1)
    webhook_delivery_timeout_seconds: int = Field(default=10, ge=1)
    webhook_circuit_break_threshold: int = Field(default=20, ge=1)
    webhook_delivery_poll_interval: float = Field(default=1.0, gt=0)
    webhook_delivery_batch_size: int = Field(default=50, ge=1, le=1000)


def load_settings(**overrides: object) -> Settings:
    """Build :class:`Settings`, failing fast with a clear error on missing keys."""
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        missing = tuple(sorted({str(err["loc"][0]) for err in exc.errors() if err["type"] == "missing"}))
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
    if settings.auth_mode == "production" and not settings.device_code_pepper:
        # auth.md §2.4.2 / §5.5: device/user codes are stored as HMAC-SHA256
        # keyed by this pepper; without it the low-entropy user_code space is
        # brute-forceable from a database leak. Fail closed like the JWT key.
        raise ConfigError(
            ("device_code_pepper",),
            "MESH_DEVICE_CODE_PEPPER must be set to a strong secret in production",
        )


def _url_password(url: str) -> str | None:
    """Return the password component of a database/Redis URL, or ``None``.

    ``urlsplit(...).password`` is ``None`` when the netloc carries no ``:pass@``
    — i.e. the service would be contacted unauthenticated (the MES-83 shape).
    """
    return urlsplit(url).password


def _is_strong_secret(value: str | None) -> bool:
    """A production secret must be present, long, and not a well-known default."""
    if value is None:
        return False
    stripped = value.strip()
    if len(stripped) < MIN_SECRET_LENGTH:
        return False
    return stripped.lower() not in WEAK_SECRET_DENYLIST


# Maps a Settings field to the env var an operator actually sets, so the startup
# error points at the knob to fix rather than the internal field name.
_INFRA_FIELD_ENV = {
    "database_url": "MESH_DATABASE_URL",
    "redis_url": "MESH_REDIS_URL",
    "app_database_url": "MESH_APP_DATABASE_URL",
    "storage_access_key": "MESH_STORAGE_ACCESS_KEY",
    "storage_secret_key": "MESH_STORAGE_SECRET_KEY",
}


def validate_infra_settings(settings: Settings, *, require_storage: bool = True) -> None:
    """Fail-safe for middleware credentials (MES-83).

    A publicly reachable datastore on a guessable password is the incident this
    guard prevents: production must connect to PostgreSQL / Redis / object storage
    with a strong, unique secret. Every process that talks to a datastore — the
    API and realtime gateway factories plus the worker — calls this at startup,
    so an under-configured production deploy fails fast with actionable guidance
    instead of coming up insecure. Dev/test (``auth_mode=dev``) keep the
    convenience defaults and skip the check entirely.

    :param require_storage: validate the object-storage credentials too. The
        realtime gateway never touches object storage (an independently deployable
        unit, README §2.2, whose configuration may legitimately omit storage), so
        it calls this with ``require_storage=False``; the API and worker connect
        to MinIO and keep the default ``True``.
    :raises ConfigError: when ``auth_mode=production`` and any datastore URL lacks
        a password, or any credential is empty, a known default, or too short.
    """
    if settings.auth_mode != "production":
        return

    weak_fields: list[str] = []
    if not _is_strong_secret(_url_password(settings.redis_url)):
        weak_fields.append("redis_url")
    if not _is_strong_secret(_url_password(settings.database_url)):
        weak_fields.append("database_url")
    # The restricted app role is optional; validate only when configured.
    if settings.app_database_url is not None and not _is_strong_secret(
        _url_password(settings.app_database_url)
    ):
        weak_fields.append("app_database_url")
    if require_storage:
        if not _is_strong_secret(settings.storage_access_key):
            weak_fields.append("storage_access_key")
        if not _is_strong_secret(settings.storage_secret_key):
            weak_fields.append("storage_secret_key")

    if weak_fields:
        env_vars = ", ".join(_INFRA_FIELD_ENV[field] for field in weak_fields)
        raise ConfigError(
            tuple(weak_fields),
            (
                "production middleware credentials must be strong and unique "
                f"(>= {MIN_SECRET_LENGTH} chars, not a known default): set real "
                f"secrets for {env_vars}. Redis must require a password."
            ),
        )
