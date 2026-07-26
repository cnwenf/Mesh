"""views: saved projection configs (kanban.md §2.2/§2.8 definition layer)

Issue-decoupled kanban slice: the views table persists "how to project
issues" (filters / group_by / sort / display_fields / board_settings as
JSONB) — never issue sets. The projection query, per-view card positions
and atomic moves land with the issue-coupled increment.

- views: composite FKs to projects / members (README §6.2), both
  ON DELETE CASCADE; ``uq_views_ws_id`` is the composite-FK reference
  target for the downstream per-view position table.
- Scope uniqueness (README §6.3): workspace- OR project-level name and
  default-view uniqueness use partial EXPRESSION unique indexes over
  ``COALESCE(project_id, nil-uuid)`` — a table-level UNIQUE constraint
  cannot carry a COALESCE expression.
- RLS defense-in-depth (README §6.2 rule 5) + a narrow SECURITY DEFINER
  workspace resolver for the workspace-less ``/views/{id}`` paths (same
  pattern as migration 0006's project resolvers).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

NULL_SCOPE_SENTINEL = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    # -- views: saved projection config (kanban.md §2.2) -----------------------
    op.execute(
        """
        CREATE TABLE views (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id      UUID NULL,
          owner_member_id UUID NOT NULL,
          name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
          layout          TEXT NOT NULL DEFAULT 'board'
                          CHECK (layout IN ('board','list','timeline','table')),
          visibility      TEXT NOT NULL DEFAULT 'private'
                          CHECK (visibility IN ('private','shared')),
          filters         JSONB NOT NULL DEFAULT '{}'::jsonb,
          group_by        TEXT NULL,
          sub_group_by    TEXT NULL,
          sort            JSONB NOT NULL DEFAULT '[]'::jsonb,
          display_fields  JSONB NOT NULL DEFAULT '[]'::jsonb,
          board_settings  JSONB NOT NULL DEFAULT '{}'::jsonb,
          position        REAL NOT NULL DEFAULT 0,
          is_default      BOOLEAN NOT NULL DEFAULT false,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, owner_member_id) REFERENCES members(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    # Composite-FK reference target (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_views_ws_id ON views(workspace_id, id)")
    # Workspace- OR project-level name uniqueness (README §6.3).
    op.execute(
        "CREATE UNIQUE INDEX uq_views_name "
        f"ON views(workspace_id, COALESCE(project_id, '{NULL_SCOPE_SENTINEL}'), name)"
    )
    # One default view per scope (kanban §2.2; service clears the previous
    # default in-transaction, the partial expression index is the backstop).
    op.execute(
        "CREATE UNIQUE INDEX uq_views_default "
        f"ON views(workspace_id, COALESCE(project_id, '{NULL_SCOPE_SENTINEL}')) "
        "WHERE is_default"
    )
    op.execute("CREATE INDEX idx_views_workspace ON views(workspace_id, position)")
    op.execute(
        "CREATE INDEX idx_views_project ON views(project_id) WHERE project_id IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_views_owner ON views(owner_member_id)")
    op.execute("CREATE INDEX idx_views_visibility ON views(workspace_id, visibility)")

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    op.execute("ALTER TABLE views ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_views_tenant ON views "
        "USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
    )

    # -- SECURITY DEFINER resolver for the workspace-less /views/{id} path ------
    # kanban §3.1 exposes /views/{id} without a workspace prefix; the app role
    # reads under fail-closed RLS, so the tenant workspace is resolved BEFORE
    # a tenant context can be set (same pattern as 0006). The membership /
    # visibility gate afterwards still runs under the fail-closed policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_view_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT v.workspace_id FROM views v WHERE v.id = p_id
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_view_workspace_id(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_view_workspace_id(uuid) TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON views TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE EXECUTE ON FUNCTION mesh_view_workspace_id(uuid) FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS mesh_view_workspace_id(uuid)")
    op.execute("DROP POLICY IF EXISTS mesh_views_tenant ON views")
    op.execute("DROP TABLE IF EXISTS views")
