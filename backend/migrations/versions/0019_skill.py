"""skill: four-layer decoupling — sources / skills / versions / installations / bindings

Stage-6 skill increment (skill.md §2, README §6.1 / §6.2 / §6.11).

Tables:

- skill_sources: provenance + trust level (builtin > user > marketplace > url).
- skills: the logical definition plus the current-version pointer.
- skill_versions: IMMUTABLE snapshots (no updated_at). Overlap unique key
  ``UNIQUE(workspace_id, skill_id, id)`` is the target of every same-skill
  overlapping composite FK (README §6.2 rule 7): ``skills.current_version_id``,
  ``skill_installations.skill_version_id`` and ``agent_skills.skill_version_id``
  can therefore only ever reference a version of THEIR OWN skill — cross-skill
  pointers fail at INSERT.
- skill_installations: a version brought into a workspace/agent scope;
  overlap unique key ``UNIQUE(workspace_id, id, skill_id)`` lets agent_skills
  share skill_id across both of its overlapping FKs.
- agent_skills: agent ↔ installed-version bindings (canary/rollback pin any
  historic version).
- skill_scripts / skill_references / skill_triggers: version leaf tables
  (isolation inherits through the version → skill parent chain).
- skill_import_tasks: the asynchronous import pipeline ledger
  (skill.md §3.1/§3.5; does NOT reuse the approvals table).

``skills.current_version_id`` uses PostgreSQL 16 column-level
``ON DELETE SET NULL (current_version_id)`` (README §6.2 rule 6) so deleting
a version nulls only the pointer — never the tenant key.

RLS defense-in-depth (README §6.2 rule 5) on every tenant table.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "skill_sources",
    "skills",
    "skill_versions",
    "skill_installations",
    "agent_skills",
    "skill_import_tasks",
)


def upgrade() -> None:
    # -- skill_sources: provenance + trust level (skill.md §2.6) ----------------
    op.execute(
        """
        CREATE TABLE skill_sources (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          source_type  TEXT NOT NULL DEFAULT 'user'
                       CHECK (source_type IN ('builtin','user','marketplace','url')),
          name         TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 300),
          uri          TEXT NULL,
          trust_level  TEXT NOT NULL DEFAULT 'untrusted'
                       CHECK (trust_level IN ('trusted','reviewed','untrusted')),
          auth_ref     TEXT NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at   TIMESTAMPTZ NULL
        )
        """
    )
    # Composite-FK reference target (README §6.2 rule 1).
    op.execute("CREATE UNIQUE INDEX uq_skill_source_ws_id ON skill_sources(workspace_id, id)")
    op.execute("CREATE INDEX idx_source_workspace_type ON skill_sources(workspace_id, source_type)")

    # -- skills: the logical definition (skill.md §2.2) --------------------------
    # NOTE: the overlapping composite FK on current_version_id is added AFTER
    # skill_versions exists (it needs the overlap unique target).
    op.execute(
        """
        CREATE TABLE skills (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          source_id            UUID NOT NULL,
          name                 TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
          slug                 TEXT NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
          summary              TEXT NOT NULL CHECK (char_length(summary) BETWEEN 1 AND 1000),
          status               TEXT NOT NULL DEFAULT 'draft'
                               CHECK (status IN ('draft','published','deprecated','disabled')),
          current_version_id   UUID NULL,
          required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          tags                 TEXT[] NOT NULL DEFAULT '{}'::text[],
          icon                 TEXT NULL,
          created_by           UUID NOT NULL,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at           TIMESTAMPTZ NULL,
          -- source must live in the SAME workspace (README §6.2 rule 2)
          FOREIGN KEY (workspace_id, source_id)
            REFERENCES skill_sources(workspace_id, id) ON DELETE RESTRICT,
          -- creator is a roster member of the SAME workspace (README §6.1/§6.2)
          FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    # Composite-FK reference target (README §6.2 rule 1).
    op.execute("CREATE UNIQUE INDEX uq_skill_ws_id ON skills(workspace_id, id)")
    # Slug unique within the workspace (soft-delete scoped).
    op.execute(
        "CREATE UNIQUE INDEX uq_skill_workspace_slug ON skills(workspace_id, slug) "
        "WHERE deleted_at IS NULL"
    )
    op.execute("CREATE INDEX idx_skill_workspace_status ON skills(workspace_id, status)")
    op.execute("CREATE INDEX idx_skill_sources ON skills(source_id)")
    op.execute("CREATE INDEX idx_skill_tags ON skills USING GIN (tags)")

    # -- skill_versions: immutable snapshots (skill.md §2.3) ---------------------
    op.execute(
        """
        CREATE TABLE skill_versions (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          skill_id              UUID NOT NULL,
          version               TEXT NOT NULL CHECK (char_length(version) BETWEEN 1 AND 64),
          instructions          TEXT NOT NULL,
          status                TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','published','deprecated')),
          changelog             TEXT NULL,
          io_contract           JSONB NULL,
          required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          manifest              JSONB NOT NULL DEFAULT '{}'::jsonb,
          content_hash          TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
          created_by            UUID NOT NULL,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- owning skill must live in the SAME workspace (README §6.2 rule 2)
          FOREIGN KEY (workspace_id, skill_id)
            REFERENCES skills(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    # OVERLAP unique key — target of the same-skill overlapping composite FKs
    # from skills / skill_installations / agent_skills (README §6.2 rule 7).
    op.execute(
        "CREATE UNIQUE INDEX uq_skill_version_ws_skill_id "
        "ON skill_versions(workspace_id, skill_id, id)"
    )
    # Generic composite-FK reference target (README §6.2 rule 1).
    op.execute("CREATE UNIQUE INDEX uq_skill_version_ws_id ON skill_versions(workspace_id, id)")
    # A skill's version number is unique (immutable snapshots).
    op.execute("CREATE UNIQUE INDEX uq_skill_versions ON skill_versions(skill_id, version)")
    op.execute(
        "CREATE INDEX idx_skill_version_skill ON skill_versions(skill_id, created_at DESC)"
    )

    # -- same-skill overlap FK for skills.current_version_id (README §6.2 7/6) ---
    # PG16 column-level SET NULL keeps workspace_id untouched.
    op.execute(
        "ALTER TABLE skills ADD CONSTRAINT skills_current_version_id_skill_versions "
        "FOREIGN KEY (workspace_id, id, current_version_id) "
        "REFERENCES skill_versions(workspace_id, skill_id, id) "
        "ON DELETE SET NULL (current_version_id)"
    )

    # -- skill_installations: version brought into a scope (skill.md §2.4) -------
    op.execute(
        """
        CREATE TABLE skill_installations (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          skill_id             UUID NOT NULL,
          skill_version_id     UUID NOT NULL,
          scope                TEXT NOT NULL DEFAULT 'workspace'
                               CHECK (scope IN ('workspace','agent')),
          agent_id             UUID NULL,
          install_status       TEXT NOT NULL DEFAULT 'installed'
                               CHECK (install_status IN ('installed','updated_available','disabled')),
          auto_update          BOOLEAN NOT NULL DEFAULT false,
          granted_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          installed_by         UUID NOT NULL,
          installed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at           TIMESTAMPTZ NULL,
          -- agent-scoped installations must name the agent (skill.md §2.4)
          CHECK (scope = 'workspace' OR agent_id IS NOT NULL),
          FOREIGN KEY (workspace_id, skill_id)
            REFERENCES skills(workspace_id, id) ON DELETE CASCADE,
          -- installed version must belong to the installed skill (README §6.2 rule 7)
          FOREIGN KEY (workspace_id, skill_id, skill_version_id)
            REFERENCES skill_versions(workspace_id, skill_id, id) ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, agent_id)
            REFERENCES agents(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, installed_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    # Composite-FK reference targets (README §6.2 rules 1/7).
    op.execute(
        "CREATE UNIQUE INDEX uq_skill_installation_ws_id ON skill_installations(workspace_id, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_skill_installation_ws_skill_id "
        "ON skill_installations(workspace_id, id, skill_id)"
    )
    # One installation per scope (soft-delete scoped); NULLS NOT DISTINCT so
    # two workspace-scope rows for the same skill collide as intended.
    op.execute(
        "CREATE UNIQUE INDEX uq_install_scope "
        "ON skill_installations(workspace_id, skill_id, scope, agent_id) "
        "NULLS NOT DISTINCT WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_install_workspace ON skill_installations(workspace_id, install_status)"
    )
    op.execute("CREATE INDEX idx_install_skill_versions ON skill_installations(skill_version_id)")
    op.execute(
        "CREATE INDEX idx_install_updated ON skill_installations(install_status) "
        "WHERE install_status = 'updated_available'"
    )

    # -- agent_skills: agent ↔ installed version bindings (skill.md §2.5) --------
    op.execute(
        """
        CREATE TABLE agent_skills (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          agent_id              UUID NOT NULL,
          skill_id              UUID NOT NULL,
          skill_installation_id UUID NOT NULL,
          skill_version_id      UUID NOT NULL,
          enabled               BOOLEAN NOT NULL DEFAULT true,
          auto_trigger          BOOLEAN NOT NULL DEFAULT true,
          priority              INT NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, agent_id)
            REFERENCES agents(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, skill_id)
            REFERENCES skills(workspace_id, id) ON DELETE CASCADE,
          -- installation belongs to the SAME skill (shared skill_id,
          -- README §6.2 rule 7)
          FOREIGN KEY (workspace_id, skill_installation_id, skill_id)
            REFERENCES skill_installations(workspace_id, id, skill_id) ON DELETE CASCADE,
          -- bound version belongs to the SAME skill (README §6.2 rule 7)
          FOREIGN KEY (workspace_id, skill_id, skill_version_id)
            REFERENCES skill_versions(workspace_id, skill_id, id) ON DELETE RESTRICT
        )
        """
    )
    # An agent binds a given installation only once.
    op.execute(
        "CREATE UNIQUE INDEX uq_agent_skills ON agent_skills(agent_id, skill_installation_id)"
    )
    op.execute("CREATE INDEX idx_agent_skill_agent ON agent_skills(agent_id, enabled)")
    op.execute("CREATE INDEX idx_agent_skill_install ON agent_skills(skill_installation_id)")

    # -- version leaf tables (skill.md §2.7) --------------------------------------
    op.execute(
        """
        CREATE TABLE skill_scripts (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          skill_version_id      UUID NOT NULL REFERENCES skill_versions(id) ON DELETE CASCADE,
          path                  TEXT NOT NULL CHECK (char_length(path) BETWEEN 1 AND 512),
          runtime               TEXT NOT NULL DEFAULT 'shell',
          entrypoint            BOOLEAN NOT NULL DEFAULT false,
          content_ref           TEXT NOT NULL,
          content_hash          TEXT NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
          required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_script_version_path ON skill_scripts(skill_version_id, path)"
    )

    op.execute(
        """
        CREATE TABLE skill_references (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          skill_version_id UUID NOT NULL REFERENCES skill_versions(id) ON DELETE CASCADE,
          path             TEXT NOT NULL CHECK (char_length(path) BETWEEN 1 AND 512),
          media_type       TEXT NOT NULL DEFAULT 'text/markdown',
          content_ref      TEXT NOT NULL,
          summary          TEXT NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_reference_version ON skill_references(skill_version_id)")

    op.execute(
        """
        CREATE TABLE skill_triggers (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          skill_version_id UUID NOT NULL REFERENCES skill_versions(id) ON DELETE CASCADE,
          trigger_type     TEXT NOT NULL DEFAULT 'keyword'
                           CHECK (trigger_type IN ('keyword','semantic','tag')),
          pattern          TEXT NOT NULL,
          weight           NUMERIC(5,2) NOT NULL DEFAULT 1.0 CHECK (weight >= 0),
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_trigger_version ON skill_triggers(skill_version_id)")
    # Keyword matching full-text index (skill.md §2.8).
    op.execute(
        "CREATE INDEX idx_trigger_keyword ON skill_triggers "
        "USING GIN (to_tsvector('simple', pattern)) WHERE trigger_type = 'keyword'"
    )

    # -- skill_import_tasks: asynchronous import pipeline ledger (skill.md §3.1) -
    op.execute(
        """
        CREATE TABLE skill_import_tasks (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          created_by           UUID NOT NULL,
          source_type          TEXT NOT NULL
                               CHECK (source_type IN ('builtin','user','marketplace','url')),
          uri                  TEXT NULL,
          ref                  TEXT NULL,
          status               TEXT NOT NULL DEFAULT 'parsing'
                               CHECK (status IN ('parsing','validating','sandbox_preview',
                                                 'awaiting_review','ready','installing',
                                                 'installed','failed','rejected')),
          stage                TEXT NOT NULL DEFAULT 'manifest_parse',
          percent              INT NOT NULL DEFAULT 0 CHECK (percent BETWEEN 0 AND 100),
          preview              JSONB NULL,
          requires_approval    BOOLEAN NOT NULL DEFAULT false,
          skill_id             UUID NULL,
          skill_version_id     UUID NULL,
          installation_id      UUID NULL,
          granted_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
          error                TEXT NULL,
          decision_comment     TEXT NULL,
          reviewed_by          UUID NULL,
          reviewed_at          TIMESTAMPTZ NULL,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, reviewed_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_skill_import_task_ws_id ON skill_import_tasks(workspace_id, id)"
    )
    op.execute(
        "CREATE INDEX idx_skill_import_tasks_status ON skill_import_tasks(status) "
        "WHERE status IN ('parsing','validating','sandbox_preview')"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) --------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges -------------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"skill_sources, skills, skill_versions, skill_installations, agent_skills, "
        f"skill_scripts, skill_references, skill_triggers, skill_import_tasks TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE skills DROP CONSTRAINT IF EXISTS skills_current_version_id_skill_versions"
    )
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS skill_import_tasks")
    op.execute("DROP TABLE IF EXISTS skill_triggers")
    op.execute("DROP TABLE IF EXISTS skill_references")
    op.execute("DROP TABLE IF EXISTS skill_scripts")
    op.execute("DROP TABLE IF EXISTS agent_skills")
    op.execute("DROP TABLE IF EXISTS skill_installations")
    op.execute("DROP TABLE IF EXISTS skill_versions")
    op.execute("DROP TABLE IF EXISTS skills")
    op.execute("DROP TABLE IF EXISTS skill_sources")
