"""issue: issue_statuses, issues, issue_dependencies, issue_activity, issue_templates

Stage-4 issue increment (issue.md §2 full, §3.9 templates). DDL mirrors
docs/specs/validation/schema_r2_validation.sql (issue_statuses / issues /
issue_dependencies / issue_activity sections) plus the §2.3 performance
indexes and the issue_templates table (issue.md §3.9).

- issue_statuses: two-layer state model (custom status → stable category).
  Scope-unique name and unique-default are PARTIAL EXPRESSION unique indexes
  (COALESCE cannot appear in a table-level UNIQUE, README §6.3); at-least-one
  default per scope is guaranteed transactionally by the service layer.
- issues: R2 immutable numbering namespace (identifier_namespace_key / number
  / identifier fixed at creation, README §6.3) with the namespace-level AND
  workspace-level uniqueness pair; column-level ``ON DELETE SET NULL (col)``
  composite FKs for nullable references (PG16, README §6.2 rule 6);
  ``status_id`` RESTRICT (issues must migrate off a status before delete);
  composite SELF-FK parent (README §6.2 rule 7) with ``CHECK (parent_id <> id)``
  — deeper cycles are prevented by the service's advisory-lock serialized
  reachability walk (issue.md §2.5, README §9 T12).
- issue_dependencies: directed graph separate from the parent tree;
  ``UNIQUE (issue_id, depends_on_id, type)`` de-duplicates edges,
  ``CHECK (issue_id <> depends_on_id)`` forbids self edges.
- issue_activity: append-only change trail.
- issue_templates: prefilled blueprints (issue.md §3.9); creator RESTRICT.
- RLS defense-in-depth (README §6.2 rule 5) on every new tenant table, plus
  narrow SECURITY DEFINER workspace resolvers for the workspace-less paths
  (/issues/{id}, /statuses/{id}, /issue-templates/{id}).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "issue_statuses",
    "issues",
    "issue_dependencies",
    "issue_activity",
    "issue_templates",
)


def upgrade() -> None:
    # -- issue_statuses: custom statuses with stable categories ----------------
    op.execute(
        """
        CREATE TABLE issue_statuses (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id   UUID NULL,
          name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 50),
          category     TEXT NOT NULL CHECK (category IN
            ('backlog','todo','in_progress','in_review','blocked','done','cancelled')),
          color        TEXT NULL,
          position     REAL NOT NULL DEFAULT 0,
          is_default   BOOLEAN NOT NULL DEFAULT false,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    # Scope-unique name / unique default — partial expression indexes
    # (README §6.3): COALESCE NULL project scopes onto a sentinel UUID.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_issue_statuses_name
          ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_issue_statuses_default
          ON issue_statuses (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'))
          WHERE is_default
        """
    )
    # Composite-FK reference target for issues.status_id (README §6.2).
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_statuses_ws_id ON issue_statuses(workspace_id, id)"
    )
    op.execute(
        """
        CREATE INDEX idx_issue_statuses_scope
          ON issue_statuses(workspace_id,
            COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), category)
        """
    )

    # -- issues: the atomic work unit (issue.md §2.2) ---------------------------
    op.execute(
        """
        CREATE TABLE issues (
          id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id               UUID NULL,
          identifier_namespace_key TEXT NOT NULL,
          number                   BIGINT NOT NULL,
          identifier               TEXT NOT NULL,
          title                    TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 255),
          description              TEXT NULL,
          status_id                UUID NOT NULL,
          state_category           TEXT NOT NULL CHECK (state_category IN
            ('backlog','todo','in_progress','in_review','blocked','done','cancelled')),
          priority                 TEXT NOT NULL DEFAULT 'none' CHECK (priority IN
            ('none','low','medium','high','urgent')),
          assignee_id              UUID NULL,
          reporter_id              UUID NULL,
          estimate                 NUMERIC NULL,
          estimate_unit            TEXT NULL CHECK (estimate_unit IN ('points','hours')),
          due_date                 DATE NULL,
          start_date               DATE NULL,
          milestone_id             UUID NULL,
          cycle_id                 UUID NULL,
          parent_id                UUID NULL,
          position                 REAL NOT NULL DEFAULT 0,
          completed_at             TIMESTAMPTZ NULL,
          version                  INT NOT NULL DEFAULT 1 CHECK (version >= 1),
          deleted_at               TIMESTAMPTZ NULL,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (parent_id <> id),
          CHECK (due_date IS NULL OR start_date IS NULL OR due_date >= start_date),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE SET NULL (project_id),
          FOREIGN KEY (workspace_id, status_id) REFERENCES issue_statuses(workspace_id, id)
            ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, assignee_id) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (assignee_id),
          FOREIGN KEY (workspace_id, reporter_id) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (reporter_id),
          FOREIGN KEY (workspace_id, milestone_id) REFERENCES milestones(workspace_id, id)
            ON DELETE SET NULL (milestone_id),
          FOREIGN KEY (workspace_id, cycle_id) REFERENCES cycles(workspace_id, id)
            ON DELETE SET NULL (cycle_id)
          -- the composite SELF-FK parent (README §6.2 rule 7) is deferred
          -- until uq_issues_ws_id exists (FK needs the unique target)
        )
        """
    )
    # Named unique indexes (drift guard compares names): numbering uniqueness
    # (README §6.3) — namespace-level + workspace-level — plus the
    # composite-FK reference target (README §6.2). Declared as UNIQUE INDEXES
    # (not table-level UNIQUE constraints) so autogenerate sees the same
    # construct as the ORM's Index(..., unique=True) declarations.
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_namespace_number "
        "ON issues(workspace_id, identifier_namespace_key, number)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_issues_identifier ON issues(workspace_id, identifier)"
    )
    op.execute("CREATE UNIQUE INDEX uq_issues_ws_id ON issues(workspace_id, id)")
    # Deferred composite SELF-FK parent (README §6.2 rule 7: explicit
    # same-tenant, not "natural") — needs the unique index above as target.
    op.execute(
        "ALTER TABLE issues ADD CONSTRAINT issues_parent_id_issues "
        "FOREIGN KEY (workspace_id, parent_id) REFERENCES issues(workspace_id, id) "
        "ON DELETE CASCADE"
    )
    # §2.3 performance indexes.
    op.execute(
        "CREATE INDEX idx_issues_workspace ON issues(workspace_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_project_status ON issues(project_id, state_category) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_assignee ON issues(assignee_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_reporter ON issues(reporter_id) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_parent ON issues(parent_id) WHERE parent_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_cycle ON issues(cycle_id) WHERE cycle_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_milestone ON issues(milestone_id) WHERE milestone_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_due ON issues(due_date) "
        "WHERE due_date IS NOT NULL AND deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_issues_position ON issues(project_id, state_category, position)"
    )
    op.execute(
        "CREATE INDEX idx_issues_priority ON issues(workspace_id, priority) "
        "WHERE deleted_at IS NULL"
    )

    # -- issue_dependencies: directed graph (issue.md §2.2) ---------------------
    op.execute(
        """
        CREATE TABLE issue_dependencies (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id      UUID NOT NULL,
          depends_on_id UUID NOT NULL,
          type          TEXT NOT NULL DEFAULT 'relates_to' CHECK (type IN
            ('blocks','blocked_by','relates_to','duplicates')),
          created_by    UUID NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (issue_id <> depends_on_id),
          FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, depends_on_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE INDEX idx_issue_deps_issue ON issue_dependencies(issue_id)")
    op.execute("CREATE INDEX idx_issue_deps_on ON issue_dependencies(depends_on_id)")
    # One edge per (issue, depends_on, type) — unique index so the ORM's
    # Index(..., unique=True) declaration matches reflection exactly.
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_dependencies_edge "
        "ON issue_dependencies(issue_id, depends_on_id, type)"
    )

    # -- issue_activity: append-only change trail -------------------------------
    op.execute(
        """
        CREATE TABLE issue_activity (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          issue_id        UUID NOT NULL,
          actor_member_id UUID NULL,
          field           TEXT NOT NULL,
          old_value       JSONB NULL,
          new_value       JSONB NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, actor_member_id) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (actor_member_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_issue_activity_issue ON issue_activity(issue_id, created_at DESC)"
    )

    # -- issue_templates: prefilled blueprints (issue.md §3.9) ------------------
    op.execute(
        """
        CREATE TABLE issue_templates (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id    UUID NULL,
          name          TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
          description   TEXT NULL,
          template_body JSONB NOT NULL,
          created_by    UUID NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE SET NULL (project_id),
          FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_issue_templates_ws_id ON issue_templates(workspace_id, id)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_issue_templates_name
          ON issue_templates (workspace_id, COALESCE(project_id,'00000000-0000-0000-0000-000000000000'), name)
        """
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER resolvers for workspace-less paths --------------------
    # issue.md §3.1 exposes /issues/{id} (and /statuses/{id},
    # /issue-templates/{id}) without a workspace prefix. The app role reads
    # under fail-closed RLS, so the tenant workspace must be resolved BEFORE a
    # tenant context can be set — narrow, parameterised owner-executed
    # bypasses (same pattern as 0006). The membership / visibility gate
    # afterwards still runs under the fail-closed policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_issue_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT i.workspace_id FROM issues i WHERE i.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_issue_status_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT s.workspace_id FROM issue_statuses s WHERE s.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_issue_template_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT t.workspace_id FROM issue_templates t WHERE t.id = p_id
        $$
        """
    )
    for fn in (
        "mesh_issue_workspace_id(uuid)",
        "mesh_issue_status_workspace_id(uuid)",
        "mesh_issue_template_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"issue_statuses, issues, issue_dependencies, issue_activity, issue_templates "
        f"TO {APP_ROLE}"
    )


def downgrade() -> None:
    for fn in (
        "mesh_issue_template_workspace_id(uuid)",
        "mesh_issue_status_workspace_id(uuid)",
        "mesh_issue_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM {APP_ROLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS issue_templates")
    op.execute("DROP TABLE IF EXISTS issue_activity")
    op.execute("DROP TABLE IF EXISTS issue_dependencies")
    op.execute("ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_parent_id_issues")
    op.execute("DROP TABLE IF EXISTS issues")
    op.execute("DROP TABLE IF EXISTS issue_statuses")
