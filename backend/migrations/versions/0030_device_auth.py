"""auth: device-code authorization table + CLI/device session columns

Implements the auth.md §2.4.2 / §2.4 / §3.8 increment (MES-80):

* ``device_authorizations`` — OAuth device grants. Codes are stored ONLY as
  HMAC-SHA256 hashes keyed by the server-side pepper (``MESH_DEVICE_CODE_PEPPER``,
  fail-closed in production). The user_code uniqueness index is PARTIAL — it
  covers active codes only, so the >=20-bit code space is not exhausted by
  accumulated terminal rows.
* ``sessions`` — ``workspace_id`` (the workspace a CLI/device session is bound
  to; CHECK-mandatory for ``type='cli'``), ``granted_scopes`` (fixed
  requested ∩ role intersection), ``device_authorization_id`` (UNIQUE — single
  consumption yields at most one session), and ``previous_token_hash`` /
  ``rotated_at`` (bounded idempotent refresh rotation, auth.md §3.8).

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE device_authorizations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            device_code_hash TEXT NOT NULL,
            user_code_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            requested_scopes TEXT[] NOT NULL DEFAULT '{}',
            granted_scopes TEXT[] NULL,
            approved_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
            workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL,
            failed_attempts INT NOT NULL DEFAULT 0,
            request_ip INET NULL,
            approved_authenticated_at TIMESTAMPTZ NULL,
            approved_at TIMESTAMPTZ NULL,
            denied_at TIMESTAMPTZ NULL,
            consumed_at TIMESTAMPTZ NULL,
            invalidated_at TIMESTAMPTZ NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT status CHECK (status IN
                ('pending','approved','denied','consumed','expired','invalidated'))
        )
        """
    )
    # 128-bit device codes: unique across all history.
    op.execute(
        "CREATE UNIQUE INDEX uq_device_auth_device_code"
        " ON device_authorizations (device_code_hash)"
    )
    # Low-entropy user codes: uniqueness over ACTIVE codes only so terminal
    # rows release their hash (auth.md §2.4.2, review R2-M3).
    op.execute(
        "CREATE UNIQUE INDEX uq_device_auth_user_code_active"
        " ON device_authorizations (user_code_hash)"
        " WHERE status IN ('pending', 'approved')"
    )
    # Reaper sweep support (pending grants ordered by expiry).
    op.execute(
        "CREATE INDEX idx_device_auth_pending"
        " ON device_authorizations (expires_at) WHERE status = 'pending'"
    )

    op.execute(
        "ALTER TABLE sessions"
        " ADD COLUMN previous_token_hash TEXT NULL,"
        " ADD COLUMN rotated_at TIMESTAMPTZ NULL,"
        " ADD COLUMN workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE CASCADE,"
        " ADD COLUMN granted_scopes TEXT[] NOT NULL DEFAULT '{}',"
        " ADD COLUMN device_authorization_id UUID NULL"
        "   REFERENCES device_authorizations(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE sessions"
        " ADD CONSTRAINT cli_workspace_bound"
        " CHECK (type <> 'cli' OR workspace_id IS NOT NULL)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_sessions_device_auth"
        " ON sessions (device_authorization_id)"
    )

    # Global identity-layer table (like users/sessions): no workspace RLS; the
    # app role needs full DML for the device-code service.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON device_authorizations TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sessions_device_auth")
    op.execute("ALTER TABLE sessions DROP CONSTRAINT IF EXISTS cli_workspace_bound")
    op.execute(
        "ALTER TABLE sessions"
        " DROP COLUMN IF EXISTS device_authorization_id,"
        " DROP COLUMN IF EXISTS granted_scopes,"
        " DROP COLUMN IF EXISTS workspace_id,"
        " DROP COLUMN IF EXISTS rotated_at,"
        " DROP COLUMN IF EXISTS previous_token_hash"
    )
    op.execute("DROP TABLE IF EXISTS device_authorizations")
