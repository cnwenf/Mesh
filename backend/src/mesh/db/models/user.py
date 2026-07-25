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
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

USER_STATUS_VALUES = ("active", "invited", "disabled", "deleted")
SESSION_TYPE_VALUES = ("web", "cli", "api")


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
    type: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'web'"))
    user_agent: Mapped[str | None] = mapped_column(TEXT, default=None)
    ip_address: Mapped[str | None] = mapped_column(INET, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    last_active_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)

    __table_args__ = (
        CheckConstraint(f"type IN {SESSION_TYPE_VALUES!r}", name="type"),
        Index("uq_sessions_token_hash", "token_hash", unique=True),
        Index(
            "idx_sessions_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
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
