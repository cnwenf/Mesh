"""auth: users, sessions, one-time tokens, oauth identities, login attempts

Global identity tables for the auth module (auth.md §2.2–§2.4.1, §3.6). These
carry no ``workspace_id`` and are exempt from workspace RLS (README §6.1 全局
身份层 / §6.2 rule 5) — workspace membership/roles live in ``members`` (member.md).
Refresh/reset/verification tokens store SHA-256 hashes only; the audit table is
append-only (DB-level enforcement, auth.md §5.5).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- users (global login identity, auth.md §2.2) ---------------------------
    op.execute(
        """
        CREATE TABLE users (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email               TEXT NOT NULL,
          email_verified_at   TIMESTAMPTZ NULL,
          password_hash       TEXT NULL,
          password_changed_at TIMESTAMPTZ NULL,
          display_name        TEXT NOT NULL,
          avatar_url          TEXT NULL,
          status              TEXT NOT NULL DEFAULT 'active',
          timezone            TEXT NULL,
          settings            JSONB NOT NULL DEFAULT '{}',
          mfa_secret          TEXT NULL,
          mfa_backup_codes    JSONB NOT NULL DEFAULT '[]',
          mfa_enabled_at      TIMESTAMPTZ NULL,
          last_login_at       TIMESTAMPTZ NULL,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_users_status
            CHECK (status IN ('active','invited','disabled','deleted')),
          CONSTRAINT ck_users_display_name_len
            CHECK (char_length(display_name) BETWEEN 1 AND 80)
        )
        """
    )
    # Email is stored lower-case normalised (service layer); unique index gives
    # case-insensitive-equivalent uniqueness (auth.md §2.2).
    op.execute("CREATE UNIQUE INDEX uq_users_email ON users (email)")
    op.execute("CREATE INDEX idx_users_status ON users (status)")

    # -- sessions / refresh tokens (auth.md §2.4) ------------------------------
    op.execute(
        """
        CREATE TABLE sessions (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash     TEXT NOT NULL,
          type           TEXT NOT NULL DEFAULT 'web',
          user_agent     TEXT NULL,
          ip_address     INET NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_active_at TIMESTAMPTZ NULL,
          expires_at     TIMESTAMPTZ NOT NULL,
          revoked_at     TIMESTAMPTZ NULL,
          CONSTRAINT ck_sessions_type CHECK (type IN ('web','cli','api'))
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_sessions_token_hash ON sessions (token_hash)")
    op.execute(
        "CREATE INDEX idx_sessions_user_active ON sessions (user_id) WHERE revoked_at IS NULL"
    )

    # -- one-time tokens (auth.md §2.4.1) --------------------------------------
    op.execute(
        """
        CREATE TABLE password_reset_tokens (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash  TEXT NOT NULL,
          expires_at  TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_password_reset_tokens_token_hash "
        "ON password_reset_tokens (token_hash)"
    )
    op.execute(
        """
        CREATE TABLE email_verification_tokens (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          token_hash  TEXT NOT NULL,
          expires_at  TIMESTAMPTZ NOT NULL,
          consumed_at TIMESTAMPTZ NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_email_verification_tokens_token_hash "
        "ON email_verification_tokens (token_hash)"
    )

    # -- oauth identities (auth.md §2.3) ---------------------------------------
    op.execute(
        """
        CREATE TABLE oauth_identities (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          provider         TEXT NOT NULL,
          provider_subject TEXT NOT NULL,
          provider_email   TEXT NULL,
          access_token_ref TEXT NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_oauth_identities_provider_subject "
        "ON oauth_identities (provider, provider_subject)"
    )
    op.execute("CREATE INDEX idx_oauth_identities_user_id ON oauth_identities (user_id)")

    # -- login attempts (auth.md §3.6 — (IP, email) lockout) -------------------
    op.execute(
        """
        CREATE TABLE login_attempts (
          id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email      TEXT NOT NULL,
          ip_address INET NULL,
          succeeded  BOOLEAN NOT NULL DEFAULT false,
          user_agent TEXT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_login_attempts_email_ip_time "
        "ON login_attempts (email, ip_address, created_at)"
    )

    # -- audit_logs append-only enforcement (auth.md §5.5) ---------------------
    # The audit table itself lands with the member-dependent RBAC increment; the
    # trigger function is created here so that table's migration only attaches it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_audit_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_logs is append-only: % not permitted', TG_OP;
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS mesh_audit_append_only()")
    op.execute("DROP TABLE IF EXISTS login_attempts")
    op.execute("DROP TABLE IF EXISTS oauth_identities")
    op.execute("DROP TABLE IF EXISTS email_verification_tokens")
    op.execute("DROP TABLE IF EXISTS password_reset_tokens")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DROP TABLE IF EXISTS users")
