"""view_issue_positions: per-view manual card order (kanban.md §2.7/§2.8)

The issue-coupled kanban increment. A single ``issues.position`` would leak one
view's drag order into every other view showing the same issue, so each view
keeps its own (view_id, issue_id) ordering row — a drag in view A never reorders
view B (README §6.14 ordering contract). Rows fall back to the canonical
``issues.position`` when absent (manual order wins, canonical default otherwise).

- Same-tenant composite FKs (README §6.2) to views/issues — both expose
  ``UNIQUE(workspace_id, id)`` (migrations 0011/0009) — ON DELETE CASCADE so a
  deleted view/issue drops its ordering rows.
- ``UNIQUE(view_id, issue_id)`` per-view uniqueness + the
  ``(view_id, group_key, position)`` ordering index (kanban §2.8).
- Fail-closed RLS defense-in-depth (README §6.2 rule 5) + ``mesh_app`` grants,
  mirroring migration 0011.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE view_issue_positions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          view_id       UUID NOT NULL,
          issue_id      UUID NOT NULL,
          group_key     TEXT NOT NULL DEFAULT '',
          position      REAL NOT NULL DEFAULT 0,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT view_issue_positions_view_id_views
            FOREIGN KEY (workspace_id, view_id) REFERENCES views(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT view_issue_positions_issue_id_issues
            FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    # One ordering row per (view, issue); a redrag upserts, never duplicates.
    op.execute(
        "CREATE UNIQUE INDEX uq_vip_view_issue ON view_issue_positions(view_id, issue_id)"
    )
    # In-view, in-group ordering path for the projection query (kanban §2.8).
    op.execute(
        "CREATE INDEX idx_vip_view_group_pos "
        "ON view_issue_positions(view_id, group_key, position)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    op.execute("ALTER TABLE view_issue_positions ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_view_issue_positions_tenant ON view_issue_positions "
        "USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
    )

    # -- app-role privileges ----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON view_issue_positions TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mesh_view_issue_positions_tenant ON view_issue_positions")
    op.execute("DROP TABLE IF EXISTS view_issue_positions")
