"""agent: agents, agent_config_versions, members.agent_id composite FK

Stage-6 agent increment (agent.md §2.3 / §2.7, README §6.1 / §6.2). DDL
mirrors docs/specs/validation/schema_r2_validation.sql (agents /
agent_config_versions sections).

- agents: agent-specific configuration (profile, model_config JSONB,
  lifecycle state machine, visibility) plus the reserved
  ``default_runtime_id`` column (the composite FK → runtimes lands with the
  runtime.md increment, same deferred-FK pattern members.agent_id used).
- agent_config_versions: immutable configuration snapshots. The overlap
  unique key ``UNIQUE(workspace_id, agent_id, id)`` is the target of
  ``agents.active_config_version_id``'s OVERLAPPING composite FK — the
  same-parent constraint (README §6.2 rule 7, T27) that makes "agent A's
  active pointer references agent B's version" an INSERT-time failure.
  Column-level ``ON DELETE SET NULL (active_config_version_id)`` (PG16,
  README §6.2 rule 6) so a version cleanup never nulls the tenant key.
- members.agent_id: the deferred composite FK
  ``(workspace_id, agent_id) → agents(workspace_id, id)`` (README §6.1 /
  §6.2) — an agent roster row can now only reference an agent of the SAME
  workspace; cross-workspace references fail at INSERT (T1).
- RLS defense-in-depth (README §6.2 rule 5) on both new tenant tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = ("agents", "agent_config_versions")


def upgrade() -> None:
    # -- agents: AI teammate identity + configuration (agent.md §2.3) ----------
    # NOTE: the overlapping composite FK on active_config_version_id is added
    # AFTER agent_config_versions exists (it needs the overlap unique target).
    op.execute(
        """
        CREATE TABLE agents (
          id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id             UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name                     TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
          avatar_url               TEXT NULL,
          role_tag                 TEXT NULL CHECK (role_tag IS NULL OR char_length(role_tag) BETWEEN 1 AND 64),
          owner_user_id            UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
          slug                     TEXT NULL CHECK (slug IS NULL OR char_length(slug) BETWEEN 1 AND 64),
          bio                      TEXT NULL,
          badge_kind               TEXT NOT NULL DEFAULT 'ai' CHECK (badge_kind IN ('ai')),
          lifecycle_status         TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_status IN ('active','paused','disabled','archived')),
          visibility               TEXT NOT NULL DEFAULT 'workspace' CHECK (visibility IN ('workspace','private')),
          system_instructions      TEXT NULL,
          model_config             JSONB NOT NULL DEFAULT '{}'::jsonb,
          default_runtime_id       UUID NULL,
          trigger_on_assign        BOOLEAN NOT NULL DEFAULT true,
          active_config_version_id UUID NULL,
          created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at               TIMESTAMPTZ NULL
        )
        """
    )
    # Composite-FK reference target (README §6.2 rule 1).
    op.execute("CREATE UNIQUE INDEX uq_agents_ws_id ON agents(workspace_id, id)")
    # §2.3 performance / filter indexes.
    op.execute("CREATE INDEX idx_agents_owner ON agents(owner_user_id)")
    op.execute(
        "CREATE INDEX idx_agents_lifecycle ON agents(workspace_id, lifecycle_status) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("CREATE INDEX idx_agents_visibility ON agents(workspace_id, visibility)")
    op.execute("CREATE INDEX idx_agents_default_runtime ON agents(default_runtime_id)")

    # -- agent_config_versions: immutable snapshots (agent.md §2.7) ------------
    op.execute(
        """
        CREATE TABLE agent_config_versions (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          agent_id       UUID NOT NULL,
          snapshot       JSONB NOT NULL,
          change_summary TEXT NULL,
          changed_by     UUID NOT NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- owning agent must live in the SAME workspace (README §6.2 rule 2)
          FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id)
            ON DELETE CASCADE,
          -- audit actor must be a roster member of the SAME workspace;
          -- members are soft-deleted so the trail survives (RESTRICT backstop)
          FOREIGN KEY (workspace_id, changed_by) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    # OVERLAP unique key — target of the agents.active_config_version_id
    # overlapping composite FK (README §6.2 rule 7, T27).
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_config_versions_ws_agent_id "
        "ON agent_config_versions(workspace_id, agent_id, id)"
    )
    # Generic composite-FK reference target (README §6.2 rule 1).
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_config_versions_ws_id "
        "ON agent_config_versions(workspace_id, id)"
    )
    op.execute(
        "CREATE INDEX idx_config_versions_agent_time "
        "ON agent_config_versions(agent_id, created_at DESC)"
    )

    # -- same-parent overlap FK (README §6.2 rule 7) ---------------------------
    # agents.active_config_version_id may only reference a version belonging
    # to THIS agent (positional overlap: agents.id ↔ versions.agent_id).
    # PG16 column-level SET NULL keeps workspace_id untouched (README §6.2
    # rule 6).
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_active_config_version_id_agent_config_versions "
        "FOREIGN KEY (workspace_id, id, active_config_version_id) "
        "REFERENCES agent_config_versions(workspace_id, agent_id, id) "
        "ON DELETE SET NULL (active_config_version_id)"
    )

    # -- members.agent_id deferred composite FK (README §6.1 / §6.2) -----------
    # The column existed bare since 0004 (the polymorphic CHECK already
    # enforced the human/agent shape); now every agent roster row must
    # reference an agent of the SAME workspace.
    op.execute(
        "ALTER TABLE members ADD CONSTRAINT members_agent_id_agents "
        "FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id) "
        "ON DELETE CASCADE"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON agents, agent_config_versions TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE members DROP CONSTRAINT IF EXISTS members_agent_id_agents")
    op.execute(
        "ALTER TABLE agents DROP CONSTRAINT IF EXISTS "
        "agents_active_config_version_id_agent_config_versions"
    )
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS agent_config_versions")
    op.execute("DROP TABLE IF EXISTS agents")
