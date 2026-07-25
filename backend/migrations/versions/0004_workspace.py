"""workspace: members roster, invitations, redemptions, slug history, prefix registry, audit

Stage-2 workspace increment (workspace.md §2 full, member.md §2.2 roster table,
auth.md §2.6 audit). DDL mirrors docs/specs/validation/schema_r2_validation.sql
(members / invitations / redemptions / slug_history / prefix_registry sections)
so the runtime schema and the spec validation script stay in lockstep.

- members: the unified roster (member.md owns the table; created here because
  invitations, audit and RBAC all reference it). The ``agent_id`` composite FK
  to ``agents(workspace_id, id)`` is DEFERRED — the agents table arrives with
  the agent.md increment, which adds the FK (mirrors the validation script's
  own deferred-FK pattern for not-yet-existing referenced tables, e.g.
  ``agents.default_runtime_id``).
- identifier_prefix_registry / member_project_access: the composite FKs to
  ``projects(workspace_id, id)`` are DEFERRED to the project.md increment for
  the same reason; every other composite FK (README §6.2) is physical now.
- audit_logs: append-only at the DB level (0003's trigger function attached
  here + UPDATE/DELETE revoked from the app role, auth.md §5.5).
- RLS defense-in-depth (README §6.2 rule 5) on every tenant table; the two
  workspace-unknown bootstrap reads (token accept, my-workspaces list) go
  through narrow SECURITY DEFINER functions so policies stay fail-closed.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "members",
    "workspace_invitations",
    "workspace_invitation_redemptions",
    "workspace_slug_history",
    "identifier_prefix_registry",
    "member_project_access",
)


def upgrade() -> None:
    # -- members: unified roster (member.md §2.2, README §6.1) ------------------
    # members.agent_id composite FK → agents(workspace_id, id) is deferred until
    # the agents table exists (agent.md increment adds it, validation-SQL style).
    op.execute(
        """
        CREATE TABLE members (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          member_type      TEXT NOT NULL CHECK (member_type IN ('human','agent')),
          user_id          UUID NULL REFERENCES users(id) ON DELETE CASCADE,
          agent_id         UUID NULL,
          role             TEXT NOT NULL DEFAULT 'member'
                           CHECK (role IN ('owner','admin','member','guest')),
          status           TEXT NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active','disabled','removed')),
          display_override TEXT NULL,
          joined_at        TIMESTAMPTZ NULL,
          disabled_at      TIMESTAMPTZ NULL,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (
            (member_type = 'human' AND user_id IS NOT NULL AND agent_id IS NULL)
            OR (member_type = 'agent' AND agent_id IS NOT NULL AND user_id IS NULL)
          ),
          CHECK (member_type = 'human' OR role <> 'owner')
        )
        """
    )
    # Composite-FK reference target (README §6.2) + polymorphic roster uniques.
    op.execute("CREATE UNIQUE INDEX uq_members_ws_id ON members(workspace_id, id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_members_ws_user ON members(workspace_id, user_id) "
        "WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_members_ws_agent ON members(workspace_id, agent_id) "
        "WHERE agent_id IS NOT NULL"
    )
    op.execute("CREATE INDEX idx_members_workspace ON members(workspace_id, status)")
    op.execute("CREATE INDEX idx_members_user ON members(user_id)")
    op.execute("CREATE INDEX idx_members_agent ON members(agent_id)")
    op.execute("CREATE INDEX idx_members_type ON members(workspace_id, member_type)")

    # -- audit_logs: append-only audit trail (auth.md §2.6/§5.5) ----------------
    # actor_member_id composite FK → members enforces same-tenant actors when
    # workspace_id is non-NULL; account-level events (workspace_id NULL) are
    # unchecked per SQL composite-FK semantics (auth.md §2.6 note).
    op.execute(
        """
        CREATE TABLE audit_logs (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NULL REFERENCES workspaces(id),
          actor_member_id UUID NULL,
          actor_kind      TEXT NOT NULL CHECK (actor_kind IN ('member','system')),
          action          TEXT NOT NULL,
          resource_type   TEXT NULL,
          resource_id     UUID NULL,
          ip_address      INET NULL,
          user_agent      TEXT NULL,
          metadata        JSONB NOT NULL DEFAULT '{}',
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, actor_member_id) REFERENCES members(workspace_id, id)
        )
        """
    )
    op.execute("CREATE INDEX idx_audit_ws_time ON audit_logs(workspace_id, created_at DESC)")
    op.execute(
        "CREATE INDEX idx_audit_actor ON audit_logs(workspace_id, actor_member_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_audit_action ON audit_logs(workspace_id, action, created_at DESC)")
    # DB-level append-only: the trigger rejects UPDATE/DELETE outright; the app
    # role additionally loses those privileges (belt and suspenders, auth.md §5.5).
    op.execute(
        """
        CREATE TRIGGER trg_audit_logs_append_only
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION mesh_audit_append_only()
        """
    )

    # -- workspace_invitations: link lifecycle (workspace.md §2.3/§4.4) ---------
    # max_uses / expires_at are NOT NULL (no unlimited / never-expiring links,
    # MES-4). status ∈ active/revoked/exhausted/expired — there is no
    # pending/accepted: redemption records live in their own table (§2.4).
    op.execute(
        """
        CREATE TABLE workspace_invitations (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          email        TEXT NULL,
          token_hash   TEXT NOT NULL,
          token_prefix TEXT NOT NULL,
          role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin','member','guest')),
          invited_by   UUID NOT NULL,
          max_uses     INT NOT NULL CHECK (max_uses > 0),
          used_count   INT NOT NULL DEFAULT 0 CHECK (used_count >= 0),
          expires_at   TIMESTAMPTZ NOT NULL,
          status       TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active','revoked','expired','exhausted')),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, invited_by) REFERENCES members(workspace_id, id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_ws_invitations_token_hash ON workspace_invitations(token_hash)")
    op.execute(
        "CREATE UNIQUE INDEX uq_ws_invitations_ws_id ON workspace_invitations(workspace_id, id)"
    )
    op.execute(
        "CREATE INDEX idx_ws_invitations_workspace ON workspace_invitations(workspace_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_ws_invitations_email ON workspace_invitations(workspace_id, email) "
        "WHERE email IS NOT NULL"
    )
    # At most one active directed invitation per (workspace, email) (§2.7).
    op.execute(
        "CREATE UNIQUE INDEX uq_ws_invitations_active_email "
        "ON workspace_invitations(workspace_id, email) "
        "WHERE email IS NOT NULL AND status = 'active'"
    )

    # -- workspace_invitation_redemptions: who joined via which link (§2.4) -----
    op.execute(
        """
        CREATE TABLE workspace_invitation_redemptions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          invitation_id UUID NOT NULL,
          user_id       UUID NOT NULL REFERENCES users(id),
          member_id     UUID NOT NULL,
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          redeemed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, invitation_id)
            REFERENCES workspace_invitations(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id)
        )
        """
    )
    # Idempotent acceptance: one row per (link, user) — named unique index per
    # workspace.md §2.7 (the DB basis for §3.2 acceptance idempotency).
    op.execute(
        "CREATE UNIQUE INDEX uq_ws_inv_redemptions_inv_user "
        "ON workspace_invitation_redemptions(invitation_id, user_id)"
    )
    op.execute(
        "CREATE INDEX idx_ws_inv_redemptions_member "
        "ON workspace_invitation_redemptions(workspace_id, member_id)"
    )

    # -- workspace_slug_history: old-slug redirects (workspace.md §2.5) ---------
    op.execute(
        """
        CREATE TABLE workspace_slug_history (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          old_slug     TEXT NOT NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_slug_history_old_slug ON workspace_slug_history(old_slug)")

    # -- identifier_prefix_registry: workspace-level exclusive prefixes (§2.6) --
    # project_id composite FK → projects(workspace_id, id) is deferred until the
    # projects table exists (project.md increment adds the column-level
    # ON DELETE SET NULL (project_id) FK).
    op.execute(
        """
        CREATE TABLE identifier_prefix_registry (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          key          TEXT NOT NULL,
          kind         TEXT NOT NULL CHECK (kind IN ('project','inbox','retired')),
          project_id   UUID NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_prefix_registry_ws_key ON identifier_prefix_registry(workspace_id, key)"
    )
    op.execute(
        "CREATE INDEX idx_prefix_registry_ws ON identifier_prefix_registry(workspace_id, kind)"
    )

    # -- member_project_access: guest project-level visibility (member.md §2.3) -
    # project_id composite FK → projects(workspace_id, id) deferred (project.md).
    op.execute(
        """
        CREATE TABLE member_project_access (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          member_id    UUID NOT NULL,
          project_id   UUID NOT NULL,
          permission   TEXT NOT NULL DEFAULT 'read' CHECK (permission IN ('read','write')),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, member_id) REFERENCES members(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_member_access ON member_project_access(member_id, project_id)"
    )
    op.execute("CREATE INDEX idx_member_access_member ON member_project_access(member_id)")

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    # Fail-closed tenant policies: without the GUC set, current_setting raises
    # and the restricted app role sees nothing. The API sets mesh.workspace_id
    # per tenant-bound transaction (mesh.db.tenant.set_tenant_context).
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )
    # audit_logs also carries account-level rows (workspace_id NULL); those stay
    # visible without a tenant context, tenant rows stay fail-closed.
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY mesh_audit_logs_tenant ON audit_logs "
        "USING (workspace_id IS NULL OR "
        "workspace_id = current_setting('mesh.workspace_id', true)::uuid)"
    )

    # -- SECURITY DEFINER bootstrap functions -----------------------------------
    # The invitation accept/preview flows must resolve a workspace from a token
    # hash BEFORE any tenant context exists (the accepter is not a member yet),
    # and GET /workspaces lists a user's memberships across workspaces. Both
    # reads are narrow, parameterised, owner-executed bypasses; policies on the
    # tables themselves stay fail-closed. search_path is pinned (definer safety).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_invitation_by_token_hash(p_hash text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, role text, status text,
          max_uses int, used_count int, expires_at timestamptz,
          email text, token_prefix text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT i.id, i.workspace_id, i.role, i.status,
                 i.max_uses, i.used_count, i.expires_at,
                 i.email, i.token_prefix
            FROM workspace_invitations i
           WHERE i.token_hash = p_hash
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_my_workspaces(p_user_id uuid)
        RETURNS TABLE (workspace_id uuid, role text, status text, joined_at timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT m.workspace_id, m.role, m.status, m.joined_at
            FROM members m
           WHERE m.user_id = p_user_id
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_invitation_by_token_hash(text) FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_my_workspaces(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_invitation_by_token_hash(text) TO {APP_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_my_workspaces(uuid) TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    # Default privileges (0002) already cover new tables for role mesh; make the
    # grants explicit anyway, then strip audit write-back (append-only, §5.5).
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"members, workspace_invitations, workspace_invitation_redemptions, "
        f"workspace_slug_history, identifier_prefix_registry, member_project_access "
        f"TO {APP_ROLE}"
    )
    op.execute(f"GRANT SELECT, INSERT ON audit_logs TO {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE}")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_my_workspaces(uuid) FROM mesh_app")
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_invitation_by_token_hash(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_my_workspaces(uuid)")
    op.execute("DROP FUNCTION IF EXISTS mesh_invitation_by_token_hash(text)")
    op.execute("DROP POLICY IF EXISTS mesh_audit_logs_tenant ON audit_logs")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS member_project_access")
    op.execute("DROP TABLE IF EXISTS identifier_prefix_registry")
    op.execute("DROP TABLE IF EXISTS workspace_slug_history")
    op.execute("DROP TABLE IF EXISTS workspace_invitation_redemptions")
    op.execute("DROP TABLE IF EXISTS workspace_invitations")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs")
    op.execute("DROP TABLE IF EXISTS audit_logs")
    op.execute("DROP TABLE IF EXISTS members")
