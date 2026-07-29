"""Auth identity models (auth.md §2.2–§2.4.1).

These are GLOBAL identity tables — they carry no ``workspace_id`` ownership
column and are exempt from workspace RLS (README §6.1 全局身份层 / §6.2 rule 5).
Workspace membership and roles live in ``members`` (member.md owns that table);
this module only authenticates the global login identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

USER_STATUS_VALUES = ("active", "invited", "disabled", "deleted")
SESSION_TYPE_VALUES = ("web", "cli", "api")
DEVICE_AUTH_STATUS_VALUES = (
    "pending",
    "approved",
    "denied",
    "consumed",
    "expired",
    "invalidated",
)


class User(Base):
    """A global login identity (auth.md §2.2). Never carries ``member_id``."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(TEXT, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    password_hash: Mapped[str | None] = mapped_column(TEXT, default=None)
    password_changed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    display_name: Mapped[str] = mapped_column(TEXT, nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(TEXT, default=None)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    timezone: Mapped[str | None] = mapped_column(TEXT, default=None)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'"))
    mfa_secret: Mapped[str | None] = mapped_column(TEXT, default=None)
    mfa_backup_codes: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'")
    )
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"status IN {USER_STATUS_VALUES!r}", name="status"),
        CheckConstraint("char_length(display_name) BETWEEN 1 AND 80", name="display_name_len"),
        Index("uq_users_email", "email", unique=True),
        Index("idx_users_status", "status"),
    )


class Session(Base):
    """A revocable session / refresh token (auth.md §2.4). Stores hash only."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    # The pre-rotation refresh hash (auth.md §3.8 bounded idempotent rotation):
    # identifies the loser credential during the grace window — a grace hit
    # issues ONLY a fresh access token and is cleared afterwards.
    previous_token_hash: Mapped[str | None] = mapped_column(TEXT, default=None)
    rotated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    type: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'web'"))
    # Workspace a CLI/device session is bound to (chosen on the browser
    # approval page, auth.md §3.1.1); web sessions carry NULL and resolve the
    # workspace per request. The CHECK below makes it mandatory for cli.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        default=None,
    )
    # Fixed granted scopes = requested ∩ approver role perms; refresh renewal
    # re-intersects with the holder's CURRENT role (auth.md §2.4).
    granted_scopes: Mapped[list] = mapped_column(
        ARRAY(TEXT), nullable=False, server_default=text("'{}'")
    )
    # The device grant that produced this session (single consumption → at
    # most one session per grant, enforced by the UNIQUE index).
    device_authorization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_authorizations.id", ondelete="SET NULL"),
        default=None,
    )
    user_agent: Mapped[str | None] = mapped_column(TEXT, default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    last_active_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    # Last primary authentication (password / TOTP); forwarded across silent
    # refreshes to back step-up re-authentication (auth.md §5.5). Device cli
    # sessions inherit the APPROVER's authenticated_at snapshot — never the
    # consumption moment (R6-H3); NULL is a valid "no recent primary auth".
    authenticated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(f"type IN {SESSION_TYPE_VALUES!r}", name="type"),
        CheckConstraint(
            "type <> 'cli' OR workspace_id IS NOT NULL", name="cli_workspace_bound"
        ),
        Index("uq_sessions_token_hash", "token_hash", unique=True),
        Index("uq_sessions_device_auth", "device_authorization_id", unique=True),
        Index(
            "idx_sessions_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class DeviceAuthorization(Base):
    """An OAuth device-code grant (auth.md §2.4.2, cli.md §3.2).

    Codes are stored ONLY as HMAC-SHA256 hashes keyed by a server-side pepper
    (never bare SHA-256 — the low-entropy user_code would be brute-forceable).
    State machine, terminal states irreversible::

        pending ──approve──► approved ──token endpoint──► consumed
        pending ──deny────► denied
        pending/approved ──TTL──► expired
        pending ──guess/abuse limit──► invalidated
    """

    __tablename__ = "device_authorizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    device_code_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    user_code_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    requested_scopes: Mapped[list] = mapped_column(
        ARRAY(TEXT), nullable=False, server_default=text("'{}'")
    )
    granted_scopes: Mapped[list | None] = mapped_column(ARRAY(TEXT), default=None)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        default=None,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        default=None,
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    request_ip: Mapped[str | None] = mapped_column(INET, default=None)
    # Snapshot of the approver web session's authenticated_at at approval time
    # (R6-H3) — copied into the cli session on consumption.
    approved_authenticated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    approved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    denied_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    invalidated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"status IN {DEVICE_AUTH_STATUS_VALUES!r}", name="status"),
        # 128-bit device codes: no space pressure — unique across ALL history.
        Index("uq_device_auth_device_code", "device_code_hash", unique=True),
        # Low-entropy user codes: uniqueness over ACTIVE codes only so the
        # 20-bit space is not exhausted by accumulated terminal rows (R2-M3).
        Index(
            "uq_device_auth_user_code_active",
            "user_code_hash",
            unique=True,
            postgresql_where=text("status IN ('pending', 'approved')"),
        ),
        Index(
            "idx_device_auth_pending",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )


class PasswordResetToken(Base):
    """Single-use password reset token (auth.md §2.4.1). Hash only, 1h TTL."""

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("uq_password_reset_tokens_token_hash", "token_hash", unique=True),)


class EmailVerificationToken(Base):
    """Single-use email verification token (auth.md §2.4.1). Hash only, 24h TTL."""

    __tablename__ = "email_verification_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("uq_email_verification_tokens_token_hash", "token_hash", unique=True),
    )


class OAuthIdentity(Base):
    """Third-party OAuth login binding (auth.md §2.3). Global identity table."""

    __tablename__ = "oauth_identities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(TEXT, nullable=False)
    provider_subject: Mapped[str] = mapped_column(TEXT, nullable=False)
    provider_email: Mapped[str | None] = mapped_column(TEXT, default=None)
    access_token_ref: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("uq_oauth_identities_provider_subject", "provider", "provider_subject", unique=True),
        Index("idx_oauth_identities_user_id", "user_id"),
    )


class LoginAttempt(Base):
    """Login audit + (IP, email) failure counting (auth.md §3.6/§5.5).

    Global (pre-authentication, so not workspace-scoped). Each row records one
    login attempt; the service counts recent failures per ``(ip, email)`` to
    drive lockout, avoiding a pure-email lockout DoS.
    """

    __tablename__ = "login_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(TEXT, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    user_agent: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("idx_login_attempts_email_ip_time", "email", "ip_address", "created_at"),
    )
