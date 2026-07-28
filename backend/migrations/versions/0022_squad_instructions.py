"""squad: squads.instructions — persistent leader directives

Adds the ``instructions`` column to ``squads`` (squad.md §2.2): standing
directives the leader reads on every takeover, distinct from per-task briefs
and pinned context messages.

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE squads ADD COLUMN instructions TEXT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE squads DROP COLUMN IF EXISTS instructions")
