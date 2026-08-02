"""integration event visibility snapshots for project-safe ledger reads.

Existing rows intentionally backfill to ``unknown``: historical payloads do
not carry a trustworthy authorization source, so only owner/admin readers may
see them.  Project ids are immutable audit snapshots and deliberately have no
foreign key, allowing them to survive project deletion.

Revision ID: 0036
Revises: 0035
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration_events
          ADD COLUMN visibility_scope TEXT NOT NULL DEFAULT 'unknown',
          ADD COLUMN project_id_snapshot UUID NULL,
          ADD CONSTRAINT ck_event_visibility_scope CHECK (
               (visibility_scope = 'workspace' AND project_id_snapshot IS NULL)
            OR (visibility_scope = 'project' AND project_id_snapshot IS NOT NULL)
            OR (visibility_scope = 'unknown' AND project_id_snapshot IS NULL)
          )
        """
    )
    op.execute(
        """
        CREATE INDEX idx_event_visibility
          ON integration_events(
            workspace_id,
            integration_id,
            visibility_scope,
            project_id_snapshot,
            received_at DESC
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_event_visibility")
    op.execute(
        "ALTER TABLE integration_events DROP CONSTRAINT IF EXISTS ck_event_visibility_scope"
    )
    op.execute("ALTER TABLE integration_events DROP COLUMN IF EXISTS project_id_snapshot")
    op.execute("ALTER TABLE integration_events DROP COLUMN IF EXISTS visibility_scope")
