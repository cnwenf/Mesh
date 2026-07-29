"""users.last_active_workspace_id — active-workspace restoration hint.

search-command-palette.md §3.4 active-workspace resolution order: the SPA
restores the last active workspace from (1) the URL, (2) client-local
storage, (3) this server-side hint, (4) a single membership. The column is a
best-effort hint only — authorization never reads it; every tenant access
still passes the full membership gate.

``users`` is a GLOBAL identity table (no workspace ownership / RLS), owned by
the migrator role; the app role already holds table-level DML grants from
0003, so no new GRANT is required. The soft FK to workspaces uses
ON DELETE SET NULL: deleting a workspace must never block on this hint.

Single-head chain 0001 → 0030.

Revision ID: 0029
Revises: 0029
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No COMMENT ON COLUMN: the model-migration drift guard compares column
    # comments too, and the ORM column carries none (parity with every other
    # users column — the semantics live in the model docstring/comment).
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "last_active_workspace_id UUID NULL REFERENCES workspaces(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS last_active_workspace_id")
