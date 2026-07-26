"""auth security: sessions.authenticated_at for step-up re-authentication

Adds ``sessions.authenticated_at`` (auth.md §5.5): the last primary
authentication (password / TOTP), forwarded across silent refreshes so sensitive
operations can require a recent re-authentication (``403 reauth_required``).

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN authenticated_at TIMESTAMPTZ NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN authenticated_at")
