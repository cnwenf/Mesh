"""label-property definition layer: labels, custom_field_defs, custom_field_options

Stage-4 label-property increment (label-property.md §2 definition layer only;
the issue-association tables issue_labels / issue_custom_field_values land
with the issue-module increment). DDL mirrors §2.2 / §2.4 / §2.5 and the
definition-layer indexes of §2.7, and tracks the identical clauses in
docs/specs/validation/schema_r2_validation.sql.

- labels: workspace-level (project_id NULL) OR project-scoped visual tags.
  Scope-internal name uniqueness uses the README §6.3 partial-EXPRESSION
  unique index spelling — ``COALESCE(project_id, '0000…')`` cannot appear in
  a table-level UNIQUE constraint.
- custom_field_defs: ten closed field types (§1.3) with per-type JSONB
  config; scope-internal field_key uniqueness via the same §6.3 expression
  index pattern.
- custom_field_options: enum options for single_select / multi_select
  fields; UNIQUE (field_def_id, name), ordered by position.
- Every table carries UNIQUE (workspace_id, id) for composite-FK
  referencing (README §6.2); the project scope columns are same-tenant
  composite FKs into projects. RLS defense-in-depth (README §6.2 rule 5)
  on all three tables, plus narrow SECURITY DEFINER resolvers for the
  workspace-less paths (PATCH/DELETE /labels/{id}, /custom-fields/{id}…).

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

TENANT_TABLES = (
    "labels",
    "custom_field_defs",
    "custom_field_options",
)


def upgrade() -> None:
    # -- labels: lightweight visual tags (label-property.md §2.2) --------------
    op.execute(
        """
        CREATE TABLE labels (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id   UUID NULL,
          name         TEXT NOT NULL,
          color        TEXT NOT NULL,
          description  TEXT NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (char_length(name) BETWEEN 1 AND 50),
          CHECK (color ~ '^#[0-9a-fA-F]{6}$'),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    # Scope-internal name uniqueness (README §6.3 — partial-EXPRESSION unique
    # index; COALESCE is forbidden in a table-level UNIQUE constraint).
    op.execute(
        "CREATE UNIQUE INDEX uq_labels_name "
        "ON labels(workspace_id, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), name)"
    )
    # Composite-FK reference target for issue_labels.label_id (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_labels_ws_id ON labels(workspace_id, id)")
    op.execute("CREATE INDEX idx_labels_workspace ON labels(workspace_id)")

    # -- custom_field_defs: typed extension-field definitions (§2.4) ------------
    op.execute(
        """
        CREATE TABLE custom_field_defs (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id    UUID NULL,
          name          TEXT NOT NULL,
          field_key     TEXT NOT NULL,
          type          TEXT NOT NULL,
          is_required   BOOLEAN NOT NULL DEFAULT false,
          required_on   JSONB NOT NULL DEFAULT '[]',
          default_value JSONB NULL,
          config        JSONB NOT NULL DEFAULT '{}',
          position      REAL NOT NULL DEFAULT 0,
          is_active     BOOLEAN NOT NULL DEFAULT true,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (char_length(name) BETWEEN 1 AND 100),
          CHECK (field_key ~ '^[a-z][a-z0-9_]{0,49}$'),
          CHECK (type IN ('text','textarea','number','date','datetime','single_select',
                          'multi_select','member','boolean','url')),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    # Scope-internal field_key uniqueness (README §6.3 expression index).
    op.execute(
        "CREATE UNIQUE INDEX uq_cfdefs_key ON custom_field_defs(workspace_id, "
        "COALESCE(project_id, '00000000-0000-0000-0000-000000000000'), field_key)"
    )
    # Composite-FK reference target for custom_field_options /
    # issue_custom_field_values (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_cfdefs_ws_id ON custom_field_defs(workspace_id, id)")
    op.execute(
        "CREATE INDEX idx_cfdefs_workspace_active ON custom_field_defs(workspace_id) "
        "WHERE is_active"
    )

    # -- custom_field_options: enum options for select-type fields (§2.5) -------
    op.execute(
        """
        CREATE TABLE custom_field_options (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          field_def_id UUID NOT NULL,
          name         TEXT NOT NULL,
          color        TEXT NULL,
          position     REAL NOT NULL DEFAULT 0,
          is_active    BOOLEAN NOT NULL DEFAULT true,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (field_def_id, name),
          FOREIGN KEY (workspace_id, field_def_id)
            REFERENCES custom_field_defs(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_cfopts_def ON custom_field_options(field_def_id, position)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER resolvers for workspace-less paths --------------------
    # label-property.md §3.1 exposes /labels/{id} and /custom-fields/{id}(/options…)
    # without a workspace prefix. The app role reads under fail-closed RLS, so
    # the tenant workspace must be resolved BEFORE a tenant context can be set —
    # narrow, parameterised owner-executed bypasses (same pattern as 0006's
    # mesh_project_workspace_id). The membership / role gate afterwards still
    # runs under the fail-closed policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_label_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT l.workspace_id FROM labels l WHERE l.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_custom_field_def_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT d.workspace_id FROM custom_field_defs d WHERE d.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_custom_field_option_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT o.workspace_id FROM custom_field_options o WHERE o.id = p_id
        $$
        """
    )
    for fn in (
        "mesh_label_workspace_id(uuid)",
        "mesh_custom_field_def_workspace_id(uuid)",
        "mesh_custom_field_option_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"labels, custom_field_defs, custom_field_options "
        f"TO {APP_ROLE}"
    )


def downgrade() -> None:
    for fn in (
        "mesh_custom_field_option_workspace_id(uuid)",
        "mesh_custom_field_def_workspace_id(uuid)",
        "mesh_label_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM {APP_ROLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS custom_field_options")
    op.execute("DROP TABLE IF EXISTS custom_field_defs")
    op.execute("DROP TABLE IF EXISTS labels")
