"""view swimlane cell positions

Revision ID: 0040
Revises: 0039
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    # Existing one-dimensional ordering rows remain in the implicit empty
    # swimlane. The uniqueness contract stays one row per (view, issue).
    op.execute("ALTER TABLE view_issue_positions ADD COLUMN sub_group_key TEXT NOT NULL DEFAULT ''")
    op.execute("DROP INDEX idx_vip_view_group_pos")
    op.execute(
        "CREATE INDEX idx_vip_view_group_pos "
        "ON view_issue_positions(view_id, group_key, sub_group_key, position)"
    )
    op.execute(
        """
        CREATE TABLE view_quick_create_requests (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          view_id           UUID NOT NULL,
          actor_member_id   UUID NOT NULL,
          issue_id          UUID NOT NULL,
          idempotency_key   TEXT NOT NULL,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT view_quick_create_requests_view_id_views
            FOREIGN KEY (workspace_id, view_id) REFERENCES views(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT view_quick_create_requests_actor_id_members
            FOREIGN KEY (workspace_id, actor_member_id) REFERENCES members(workspace_id, id)
            ON DELETE CASCADE,
          CONSTRAINT view_quick_create_requests_issue_id_issues
            FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_view_quick_create_idem "
        "ON view_quick_create_requests(view_id, actor_member_id, idempotency_key)"
    )
    op.execute("ALTER TABLE view_quick_create_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_view_quick_create_requests_tenant "
        "ON view_quick_create_requests "
        "USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON view_quick_create_requests TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mesh_view_quick_create_requests_tenant ON view_quick_create_requests")
    op.execute("DROP TABLE IF EXISTS view_quick_create_requests")
    op.execute("DROP INDEX idx_vip_view_group_pos")
    op.execute("CREATE INDEX idx_vip_view_group_pos ON view_issue_positions(view_id, group_key, position)")
    op.execute("ALTER TABLE view_issue_positions DROP COLUMN sub_group_key")
