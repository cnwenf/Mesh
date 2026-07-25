"""project: projects, project_updates, milestones, cycles, project_members, project_templates

Stage-4 project increment (project.md §2 full, §3.2b templates). DDL mirrors
docs/specs/validation/schema_r2_validation.sql (projects / milestones / cycles
/ project_updates / project_members sections) so the runtime schema and the
spec validation script stay in lockstep, plus the §2.3 performance indexes
and the project_templates table (project.md §3.2b).

- projects: goal box + identifier prefix source. ``uq_projects_key`` is a
  PLAIN (non-partial) unique index — the prefix is permanently reserved, a
  soft-deleted/archived project's key can never be re-issued (README §6.3).
  ``lead_member_id`` composite FK → members uses PG16 column-level
  ``ON DELETE SET NULL (lead_member_id)`` (README §6.2 rule 6).
- project_updates: append-only health/status trail; the author FK is
  NOT NULL + RESTRICT (members are soft-deleted, so the signature is
  permanent — README §6.2 rule 6).
- The two composite FKs deferred by 0004 are added now:
  ``identifier_prefix_registry.project_id`` → projects with column-level
  ``ON DELETE SET NULL (project_id)`` (the registry row survives a physical
  project delete, prefix stays reserved) and ``member_project_access.project_id``
  → projects ON DELETE CASCADE.
- RLS defense-in-depth (README §6.2 rule 5) on every new tenant table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "projects",
    "project_updates",
    "milestones",
    "cycles",
    "project_members",
    "project_templates",
)


def upgrade() -> None:
    # -- projects: goal box + identifier prefix source (project.md §2.2) --------
    op.execute(
        """
        CREATE TABLE projects (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name           TEXT NOT NULL,
          key            TEXT NOT NULL,
          description    TEXT NULL,
          icon           TEXT NULL,
          color          TEXT NULL,
          status         TEXT NOT NULL DEFAULT 'planning'
                         CHECK (status IN ('planning','active','paused','completed','cancelled')),
          health         TEXT NULL CHECK (health IN ('on_track','at_risk','off_track')),
          visibility     TEXT NOT NULL DEFAULT 'public'
                         CHECK (visibility IN ('public','private')),
          lead_member_id UUID NULL,
          start_date     DATE NULL,
          target_date    DATE NULL,
          progress_cache REAL NULL,
          issue_seq      BIGINT NOT NULL DEFAULT 0 CHECK (issue_seq >= 0),
          archived_at    TIMESTAMPTZ NULL,
          deleted_at     TIMESTAMPTZ NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (target_date IS NULL OR start_date IS NULL OR target_date >= start_date),
          -- lead_member composite FK + PG16 column-level SET NULL (README §6.2 rule 6)
          FOREIGN KEY (workspace_id, lead_member_id) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (lead_member_id)
        )
        """
    )
    # Prefix permanent reservation (README §6.3): plain (NON-partial) unique.
    op.execute("CREATE UNIQUE INDEX uq_projects_key ON projects(workspace_id, key)")
    # Composite-FK reference target (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_projects_ws_id ON projects(workspace_id, id)")
    # Same-workspace name de-dup over live projects only.
    op.execute(
        "CREATE UNIQUE INDEX uq_projects_name ON projects(workspace_id, name) "
        "WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_projects_workspace ON projects(workspace_id, status) "
        "WHERE deleted_at IS NULL AND archived_at IS NULL"
    )
    op.execute("CREATE INDEX idx_projects_lead ON projects(lead_member_id)")

    # -- project_updates: append-only health/status trail (project.md §2.2) -----
    op.execute(
        """
        CREATE TABLE project_updates (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id       UUID NOT NULL,
          author_member_id UUID NOT NULL,
          health           TEXT NULL CHECK (health IN ('on_track','at_risk','off_track')),
          status           TEXT NULL
                           CHECK (status IN ('planning','active','paused','completed','cancelled')),
          message          TEXT NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE,
          -- trail author can never dangle: members are soft-deleted + RESTRICT
          FOREIGN KEY (workspace_id, author_member_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_project_updates_project ON project_updates(project_id, created_at DESC)"
    )

    # -- milestones: target-date goal boxes (project.md §2.2) -------------------
    op.execute(
        """
        CREATE TABLE milestones (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id   UUID NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT NULL,
          target_date  DATE NULL,
          state        TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_milestones_ws_id ON milestones(workspace_id, id)")
    op.execute("CREATE INDEX idx_milestones_project ON milestones(project_id, state)")

    # -- cycles: iteration / sprint time boxes (project.md §2.2) ----------------
    op.execute(
        """
        CREATE TABLE cycles (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id   UUID NULL,
          name         TEXT NOT NULL,
          starts_at    DATE NOT NULL,
          ends_at      DATE NOT NULL,
          state        TEXT NOT NULL DEFAULT 'planned'
                       CHECK (state IN ('planned','active','completed')),
          auto_roll    BOOLEAN NOT NULL DEFAULT false,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (ends_at >= starts_at),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_cycles_ws_id ON cycles(workspace_id, id)")
    op.execute("CREATE INDEX idx_cycles_workspace ON cycles(workspace_id, starts_at)")
    op.execute("CREATE INDEX idx_cycles_state ON cycles(workspace_id, state)")

    # -- project_members: project-level membership / visibility ------------------
    op.execute(
        """
        CREATE TABLE project_members (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          project_id   UUID NOT NULL,
          member_id    UUID NOT NULL,
          role         TEXT NOT NULL DEFAULT 'member'
                       CHECK (role IN ('lead','member','viewer')),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
            ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_project_members ON project_members(project_id, member_id)"
    )
    op.execute("CREATE INDEX idx_project_members_member ON project_members(member_id)")

    # -- project_templates: prefill blueprints (project.md §3.2b) ----------------
    op.execute(
        """
        CREATE TABLE project_templates (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name          TEXT NOT NULL,
          template_body JSONB NOT NULL,
          created_by    UUID NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- creator never dangles: members are soft-deleted + RESTRICT
          FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_project_templates_ws_id ON project_templates(workspace_id, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_project_templates_ws_name ON project_templates(workspace_id, name)"
    )

    # -- deferred composite FKs from 0004 (README §6.2 / §6.3) -------------------
    # Prefix registry: column-level SET NULL keeps the row (prefix permanently
    # reserved with a NULL pointer) when a project is physically deleted.
    op.execute(
        """
        ALTER TABLE identifier_prefix_registry
          ADD CONSTRAINT fk_identifier_prefix_registry_project_id_projects
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
          ON DELETE SET NULL (project_id)
        """
    )
    # Guest project-access grants follow the project's lifetime.
    op.execute(
        """
        ALTER TABLE member_project_access
          ADD CONSTRAINT fk_member_project_access_project_id_projects
          FOREIGN KEY (workspace_id, project_id) REFERENCES projects(workspace_id, id)
          ON DELETE CASCADE
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
    # project.md §3.1 exposes /projects/{id}, /milestones/{id}, /cycles/{id} and
    # /project-templates/{id} without a workspace prefix. The app role reads
    # under fail-closed RLS, so the tenant workspace must be resolved BEFORE a
    # tenant context can be set — narrow, parameterised owner-executed bypasses
    # (same pattern as 0004's mesh_workspace_id_by_old_slug). The membership /
    # visibility gate afterwards still runs under the fail-closed policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_project_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT p.workspace_id FROM projects p WHERE p.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_milestone_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT m.workspace_id FROM milestones m WHERE m.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_cycle_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT c.workspace_id FROM cycles c WHERE c.id = p_id
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_project_template_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT t.workspace_id FROM project_templates t WHERE t.id = p_id
        $$
        """
    )
    for fn in (
        "mesh_project_workspace_id(uuid)",
        "mesh_milestone_workspace_id(uuid)",
        "mesh_cycle_workspace_id(uuid)",
        "mesh_project_template_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"projects, project_updates, milestones, cycles, project_members, project_templates "
        f"TO {APP_ROLE}"
    )


def downgrade() -> None:
    for fn in (
        "mesh_project_template_workspace_id(uuid)",
        "mesh_cycle_workspace_id(uuid)",
        "mesh_milestone_workspace_id(uuid)",
        "mesh_project_workspace_id(uuid)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM {APP_ROLE}")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.execute(
        "ALTER TABLE member_project_access "
        "DROP CONSTRAINT IF EXISTS fk_member_project_access_project_id_projects"
    )
    op.execute(
        "ALTER TABLE identifier_prefix_registry "
        "DROP CONSTRAINT IF EXISTS fk_identifier_prefix_registry_project_id_projects"
    )
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS project_templates")
    op.execute("DROP TABLE IF EXISTS project_members")
    op.execute("DROP TABLE IF EXISTS cycles")
    op.execute("DROP TABLE IF EXISTS milestones")
    op.execute("DROP TABLE IF EXISTS project_updates")
    op.execute("DROP TABLE IF EXISTS projects")
