"""integrations: registry, bindings, inbound events, external identities,
outbound webhook subscriptions + deliveries, vcs links.

Stage-7 platform-capability increment D (integrations.md §2 / README §6.2 /
§6.5 / §6.6 / §6.7 / §6.9 / §6.16 / §6.17). DDL mirrors
docs/specs/features/integrations.md §2.8 and docs/specs/validation/
schema_r2_validation.sql verbatim. Migration number 0027 (single-head chain
0001 → 0027, chained after data_jobs 0026).

Tables:

- ``integrations`` — connector instances: kind + non-secret config + secret
  ciphertext reference (README §6.16, same contract as
  ``runtime_credentials.encrypted_value``). Tenant-scoped, RLS fail-closed.
- ``integration_bindings`` — external identity ↔ workspace/project binding
  with match rules + target agent. GLOBAL external-identity key
  ``UNIQUE(provider, provider_tenant_key, external_ref)`` (R3/§6.17: one
  external identity binds to at most one workspace) + exact-XOR scope CHECK.
- ``integration_events`` — inbound ingestion ledger, isomorphic to autopilot
  ``webhook_events`` (autopilot.md §2.5) but independent: signature result +
  ``UNIQUE(integration_id, external_event_id)`` dedup + full audit; rejected
  events live in the ``rejected:<raw-hash>`` namespace (anti pre-occupation).
- ``external_identities`` — GLOBAL identity table (R5/§6.1): external
  platform account ↔ ``users.id`` mapping. NO ``workspace_id`` ownership
  column, NO workspace RLS (same tier as ``users``); link origin recorded
  only as nullable audit column ``created_in_workspace_id ON DELETE SET
  NULL``; identity key ``UNIQUE(provider, provider_tenant_key,
  external_user_key)``.
- ``webhook_subscriptions`` — outbound developer webhook subscriptions
  (https-only URL + event filter + circuit-breaker state).
- ``webhook_subscription_deliveries`` — delivery ledger: retry/backoff,
  ``UNIQUE(subscription_id, event_ref)`` idempotency (README §6.5).
- ``vcs_links`` — VCS object ↔ Mesh entity link truth source (R3): partial
  unique active indexes, same-tenant composite FKs.

SECURITY DEFINER bootstrap reads (inbound endpoints are signature-
authenticated, NOT Bearer — the workspace is unknown until the lookup
succeeds; RLS is fail-closed, same pattern as autopilot 0023 / runtime 0019):

- ``mesh_integrations_by_kind_config_value(kind, key, value)`` — feishu
  (app_id) / slack (team_id) / github (installation_id) integration lookup.
- ``mesh_binding_by_external_ref(provider, external_ref)`` — gitlab-style
  routing through the global external-identity key.

Executable reference (README §6.17 / T29⑪):
- ``external_identity_unlink_allowed(identity_id, member_id)`` — owner-only
  unlink authorization; role columns do NOT participate (no admin bypass).

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

# Tenant tables get RLS + app-role grants. external_identities is a GLOBAL
# identity table (README §6.1 / §6.2 rule 5): NO workspace_id column, NO
# workspace RLS policy — deliberately absent (T29⑩ negative assertions).
TENANT_TABLES = (
    "integrations",
    "integration_bindings",
    "integration_events",
    "webhook_subscriptions",
    "webhook_subscription_deliveries",
    "vcs_links",
)

DML_TABLES = ", ".join(TENANT_TABLES + ("external_identities",))


def upgrade() -> None:
    # ============ integrations ============
    op.execute(
        """
        CREATE TABLE integrations (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          kind         TEXT NOT NULL CHECK (kind IN
                       ('im_feishu','im_slack','vcs_github','vcs_gitlab','webhook_outbound')),
          name         TEXT NOT NULL,
          status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
          config       JSONB NOT NULL DEFAULT '{}',
          secret_ref   TEXT NULL,
          created_by   UUID NOT NULL,
          deleted_at   TIMESTAMPTZ NULL,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT fk_integrations_created_by FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_integrations_ws_id ON integrations(workspace_id, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_integrations_ws_name "
        "ON integrations(workspace_id, name) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX idx_integrations_ws_kind "
        "ON integrations(workspace_id, kind) WHERE deleted_at IS NULL"
    )

    # ============ integration_bindings ============
    op.execute(
        """
        CREATE TABLE integration_bindings (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          integration_id      UUID NOT NULL,
          provider            TEXT NOT NULL CHECK (provider IN
                              ('feishu','slack','github','gitlab','webhook')),
          provider_tenant_key TEXT NOT NULL DEFAULT '',
          scope               TEXT NOT NULL DEFAULT 'workspace'
                              CHECK (scope IN ('workspace','project')),
          project_id          UUID NULL,
          external_ref        TEXT NOT NULL,
          match_config        JSONB NOT NULL DEFAULT '{}',
          bound_agent_id      UUID NULL,
          status              TEXT NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active','disabled')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_binding_scope CHECK (
              (scope = 'workspace' AND project_id IS NULL)
           OR (scope = 'project' AND project_id IS NOT NULL)),
          CONSTRAINT fk_binding_integration FOREIGN KEY (workspace_id, integration_id)
            REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_binding_project FOREIGN KEY (workspace_id, project_id)
            REFERENCES projects(workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_binding_agent FOREIGN KEY (workspace_id, bound_agent_id)
            REFERENCES agents(workspace_id, id) ON DELETE SET NULL (bound_agent_id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_integration_bindings_ws_id "
        "ON integration_bindings(workspace_id, id)"
    )
    # R3: GLOBAL external-identity key — one external identity (provider +
    # platform tenant + external object) binds to at most ONE workspace.
    op.execute(
        "CREATE UNIQUE INDEX uq_binding_external_identity "
        "ON integration_bindings(provider, provider_tenant_key, external_ref)"
    )
    op.execute(
        "CREATE INDEX idx_binding_integration "
        "ON integration_bindings(integration_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_binding_agent "
        "ON integration_bindings(workspace_id, bound_agent_id) "
        "WHERE bound_agent_id IS NOT NULL"
    )

    # ============ integration_events (isomorphic autopilot.webhook_events) ============
    op.execute(
        """
        CREATE TABLE integration_events (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          integration_id    UUID NOT NULL,
          external_event_id TEXT NOT NULL,
          event_type        TEXT NOT NULL,
          payload           JSONB NOT NULL,
          signature_status  TEXT NOT NULL
                            CHECK (signature_status IN ('valid','invalid','missing')),
          process_status    TEXT NOT NULL DEFAULT 'received'
                            CHECK (process_status IN
                              ('received','matched','dispatched','deduped',
                               'rejected','processed','failed')),
          received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT fk_event_integration FOREIGN KEY (workspace_id, integration_id)
            REFERENCES integrations(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_integration_events_ws_id "
        "ON integration_events(workspace_id, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_integration_event_dedup "
        "ON integration_events(integration_id, external_event_id)"
    )
    op.execute(
        "CREATE INDEX idx_event_integration_status "
        "ON integration_events(integration_id, process_status, received_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_event_ws_received "
        "ON integration_events(workspace_id, received_at DESC)"
    )

    # ============ external_identities (GLOBAL table, R5) ============
    op.execute(
        """
        CREATE TABLE external_identities (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          provider              TEXT NOT NULL CHECK (provider IN
                                ('feishu','slack','github','gitlab')),
          provider_tenant_key   TEXT NOT NULL DEFAULT '',
          external_user_key     TEXT NOT NULL,
          user_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          created_in_workspace_id UUID NULL REFERENCES workspaces(id)
                                ON DELETE SET NULL (created_in_workspace_id),
          verified_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_external_identity "
        "ON external_identities(provider, provider_tenant_key, external_user_key)"
    )
    op.execute("CREATE INDEX idx_external_identities_user ON external_identities(user_id)")
    op.execute(
        "CREATE INDEX idx_external_identities_created_in_ws "
        "ON external_identities(created_in_workspace_id) "
        "WHERE created_in_workspace_id IS NOT NULL"
    )

    # ============ webhook_subscriptions ============
    op.execute(
        """
        CREATE TABLE webhook_subscriptions (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          integration_id UUID NULL,
          url            TEXT NOT NULL,
          secret_ref     TEXT NOT NULL,
          event_types    TEXT[] NOT NULL DEFAULT '{}',
          status         TEXT NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','paused','disabled')),
          fail_count     INT NOT NULL DEFAULT 0 CHECK (fail_count >= 0),
          created_by     UUID NOT NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT fk_subscription_integration FOREIGN KEY (workspace_id, integration_id)
            REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id),
          CONSTRAINT fk_subscription_created_by FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_webhook_subscriptions_ws_id "
        "ON webhook_subscriptions(workspace_id, id)"
    )
    op.execute(
        "CREATE INDEX idx_subscription_ws_status "
        "ON webhook_subscriptions(workspace_id, status)"
    )

    # ============ webhook_subscription_deliveries ============
    op.execute(
        """
        CREATE TABLE webhook_subscription_deliveries (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          subscription_id UUID NOT NULL,
          event_ref       TEXT NOT NULL,
          state           TEXT NOT NULL DEFAULT 'pending'
                          CHECK (state IN ('pending','sent','failed')),
          attempts        INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          next_retry_at   TIMESTAMPTZ NULL,
          response_status INT NULL,
          last_error      TEXT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT fk_delivery_subscription FOREIGN KEY (workspace_id, subscription_id)
            REFERENCES webhook_subscriptions(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_delivery_subscription_event "
        "ON webhook_subscription_deliveries(subscription_id, event_ref)"
    )
    op.execute(
        "CREATE INDEX idx_delivery_retry "
        "ON webhook_subscription_deliveries(next_retry_at) WHERE state = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_delivery_subscription "
        "ON webhook_subscription_deliveries(subscription_id, created_at DESC)"
    )

    # ============ vcs_links ============
    op.execute(
        """
        CREATE TABLE vcs_links (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          integration_id        UUID NOT NULL,
          provider              TEXT NOT NULL CHECK (provider IN ('github','gitlab')),
          provider_tenant_key   TEXT NOT NULL DEFAULT '',
          external_object_type  TEXT NOT NULL CHECK (external_object_type IN
                                ('repository','pull_request','merge_request',
                                 'issue','commit','branch')),
          external_object_ref   TEXT NOT NULL,
          mesh_entity_type      TEXT NOT NULL CHECK (mesh_entity_type IN ('issue','project')),
          mesh_entity_id        UUID NOT NULL,
          link_source           TEXT NOT NULL DEFAULT 'manual'
                                CHECK (link_source IN
                                  ('manual','auto_keyword','auto_branch','auto_commit')),
          status                TEXT NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active','stale','deleted')),
          external_state        JSONB NOT NULL DEFAULT '{}',
          created_by            UUID NULL,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT fk_vcs_links_integration FOREIGN KEY (workspace_id, integration_id)
            REFERENCES integrations(workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_vcs_links_created_by FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (created_by)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_vcs_links_ws_id ON vcs_links(workspace_id, id)")
    op.execute(
        "CREATE UNIQUE INDEX uq_vcs_links_external_object "
        "ON vcs_links(provider, provider_tenant_key, external_object_type, external_object_ref) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_vcs_links_mesh_entity "
        "ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, external_object_ref) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE INDEX idx_vcs_links_entity_status "
        "ON vcs_links(workspace_id, mesh_entity_type, mesh_entity_id, status)"
    )
    op.execute(
        "CREATE INDEX idx_vcs_links_integration_status ON vcs_links(integration_id, status)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) — tenant tables only -------
    # external_identities is a GLOBAL identity table (README §6.1 R5): no
    # workspace_id column, no workspace RLS policy (T29⑩ negative assertions).
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges ---------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")

    # -- SECURITY DEFINER bootstrap reads (inbound endpoints are signature-
    #    authenticated, NOT Bearer — workspace unknown until lookup) ----------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_integrations_by_kind_config_value(
          p_kind text, p_key text, p_value text
        )
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text, kind text,
          config jsonb, secret_ref text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT i.id, i.workspace_id, i.status, i.kind, i.config, i.secret_ref
          FROM integrations i
          WHERE i.kind = p_kind
            AND i.deleted_at IS NULL
            AND i.config ->> p_key = p_value
        $$
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "mesh_integrations_by_kind_config_value(text, text, text) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"mesh_integrations_by_kind_config_value(text, text, text) TO {APP_ROLE}"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_binding_by_external_ref(
          p_provider text, p_external_ref text
        )
        RETURNS TABLE (
          id uuid, workspace_id uuid, integration_id uuid,
          provider_tenant_key text, status text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT b.id, b.workspace_id, b.integration_id,
                 b.provider_tenant_key, b.status
          FROM integration_bindings b
          WHERE b.provider = p_provider
            AND b.external_ref = p_external_ref
        $$
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "mesh_binding_by_external_ref(text, text) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"mesh_binding_by_external_ref(text, text) TO {APP_ROLE}"
    )

    # -- executable reference: owner-only unlink authorization (R5, T29⑪) -----
    # Role columns deliberately do NOT participate — no admin bypass. The
    # backend service implementation must be line-for-line equivalent.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION external_identity_unlink_allowed(
          p_identity uuid, p_member uuid
        ) RETURNS BOOLEAN
        LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1
              FROM external_identities ei
              JOIN members m ON m.id = p_member
             WHERE ei.id = p_identity
               AND m.user_id = ei.user_id
               AND m.status = 'active'
          )
        $$
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION external_identity_unlink_allowed(uuid, uuid) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION external_identity_unlink_allowed(uuid, uuid) TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE EXECUTE ON FUNCTION external_identity_unlink_allowed(uuid, uuid) FROM mesh_app"
    )
    op.execute("DROP FUNCTION IF EXISTS external_identity_unlink_allowed(uuid, uuid)")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION mesh_binding_by_external_ref(text, text) FROM mesh_app"
    )
    op.execute("DROP FUNCTION IF EXISTS mesh_binding_by_external_ref(text, text)")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "mesh_integrations_by_kind_config_value(text, text, text) FROM mesh_app"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mesh_integrations_by_kind_config_value(text, text, text)"
    )
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    for table in reversed(TENANT_TABLES + ("external_identities",)):
        op.execute(f"DROP TABLE IF EXISTS {table}")
