"""integrations: registry, bindings, inbound events, external identities,
outbound webhook subscriptions + deliveries, vcs links.

Stage-7 platform-capability increment D (integrations.md §2 / README §6.2 /
§6.5 / §6.6 / §6.7 / §6.9 / §6.16 / §6.17). DDL mirrors
docs/specs/features/integrations.md §2.8 and docs/specs/validation/
schema_r2_validation.sql verbatim. Migration number 0033 (single-head chain
0001 → 0033, chained after device_auth_revoke_delete 0032; renumbered from
0030 to keep a single alembic head after the device-auth chain
0030/0031/0032 landed on main during review).

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
- ``integration_message_queue`` — inbound-message conversation-level FIFO
  queue (MES-82 §2.10): serial-lane in-flight exclusion (parallel exempt),
  orphan-audit delete protection (SET NULL + terminal-state CHECK,
  fail-closed), emoji-ack window columns (§3.8). DDL mirrors the executable
  reference in docs/specs/validation/schema_r2_validation.sql (T39).
- ``execution_context_appends`` — runtime context-append ledger (MES-82,
  runtime.md "运行期上下文追加"): per-execution monotonic ``seq`` + attempt
  scoped injection receipt. DDL mirrors the T39 executable reference.

Cross-table alignment (MES-82 rebase coordination, README §6.6/§6.9):

- ``integrations.kind`` CHECK includes ``im_dingtalk`` (integrations.md
  §2.7 authority) + ``stream_state`` JSONB persistence column (§2.2/§3.9).
- ``integration_bindings`` / ``external_identities`` provider CHECKs
  include ``dingtalk``.
- ``notification_delivery.provider`` CHECK extended with ``dingtalk``
  (comment-inbox.md §2 row) + integration/binding routing FKs (§6.2 rule 6
  column-level SET NULL).
- ``task_executions.context_injected_through_seq`` water-level column
  (runtime.md runtime context appends).
- ``outbox_events.available_at`` earliest-claim column + rekeyed
  ``idx_outbox_pending (available_at, created_at)`` (README §6.6 authority:
  claim ``status='pending' AND available_at <= now()``; retryable
  non-failure results only move ``available_at``, never consuming the
  failure budget).

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

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-29
"""
from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
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
    "integration_message_queue",
    "execution_context_appends",
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
                       ('im_feishu','im_slack','im_dingtalk','vcs_github','vcs_gitlab',
                        'webhook_outbound')),
          stream_state JSONB NOT NULL DEFAULT '{}',
          health_state TEXT NOT NULL DEFAULT 'unknown'
                       CHECK (health_state IN ('unknown','healthy','auth_failed','unreachable')),
          last_error   TEXT NULL,
          last_success_at TIMESTAMPTZ NULL,
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
                              ('feishu','slack','dingtalk','github','gitlab','webhook')),
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
                                ('feishu','slack','dingtalk','github','gitlab')),
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
          event_type      TEXT NOT NULL DEFAULT '',
          payload         JSONB NOT NULL DEFAULT '{}',
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

    # ============ integration_message_queue (MES-82 §2.10) ============
    # DDL mirrors the executable reference in schema_r2_validation.sql (T39)
    # constraint-for-constraint; the DingTalk connector behavior built on it
    # ships in the MES-82 implementation slices.
    op.execute(
        """
        CREATE TABLE integration_message_queue (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          integration_id       UUID NULL,
          binding_id           UUID NULL,
          integration_event_id UUID NULL,
          binding_display      TEXT NOT NULL DEFAULT '',
          project_id_snapshot  UUID NULL,
          conversation_key     TEXT NOT NULL,
          seq                  BIGINT NOT NULL CHECK (seq > 0),
          dispatch_mode        TEXT NOT NULL
                               CHECK (dispatch_mode IN ('serial_conversation','parallel')),
          state                TEXT NOT NULL DEFAULT 'pending'
                               CHECK (state IN ('pending','dispatching','processing',
                                                'cancelling','done','failed','cancelled')),
          execution_id         UUID NULL,
          target_agent_id      UUID NULL,
          message_excerpt      TEXT NOT NULL DEFAULT '',
          sender_identity_key  TEXT NOT NULL DEFAULT '',
          ack_leader_id        UUID NULL,
          ack_window_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          ack_attempted_at     TIMESTAMPTZ NULL,
          ack_sent_at          TIMESTAMPTZ NULL,
          ack_represented_at   TIMESTAMPTZ NULL,
          ack_merged_into      UUID NULL,
          lease_expires_at     TIMESTAMPTZ NULL,
          enqueued_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          started_at           TIMESTAMPTZ NULL,
          finished_at          TIMESTAMPTZ NULL,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_imq_ws_id UNIQUE (workspace_id, id),
          CONSTRAINT uq_imq_event UNIQUE (integration_id, integration_event_id),
          CONSTRAINT uq_imq_conversation_seq UNIQUE (conversation_key, seq),
          CONSTRAINT fk_imq_integration FOREIGN KEY (workspace_id, integration_id)
            REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id),
          -- Delete protection (fail-closed): parent SET NULL + orphan rows must
          -- be terminal (a non-terminal item losing its parent is rejected).
          CONSTRAINT ck_imq_orphan_terminal CHECK (
               (integration_id IS NOT NULL OR state IN ('done','failed','cancelled'))
           AND (binding_id     IS NOT NULL OR state IN ('done','failed','cancelled'))),
          CONSTRAINT fk_imq_binding FOREIGN KEY (workspace_id, binding_id)
            REFERENCES integration_bindings(workspace_id, id)
            ON DELETE SET NULL (binding_id),
          CONSTRAINT fk_imq_event FOREIGN KEY (workspace_id, integration_event_id)
            REFERENCES integration_events(workspace_id, id)
            ON DELETE SET NULL (integration_event_id),
          CONSTRAINT fk_imq_execution FOREIGN KEY (workspace_id, execution_id)
            REFERENCES task_executions(workspace_id, id) ON DELETE SET NULL (execution_id),
          CONSTRAINT fk_imq_target_agent FOREIGN KEY (workspace_id, target_agent_id)
            REFERENCES agents(workspace_id, id) ON DELETE SET NULL (target_agent_id)
        )
        """
    )
    # Serial in-flight exclusion (parallel exempt); covers every in-flight
    # state (dispatching/processing/cancelling).
    op.execute(
        "CREATE UNIQUE INDEX uq_imq_conversation_active "
        "ON integration_message_queue(conversation_key) "
        "WHERE state IN ('dispatching','processing','cancelling') "
        "AND dispatch_mode = 'serial_conversation'"
    )
    op.execute(
        "CREATE INDEX idx_imq_conversation_pending "
        "ON integration_message_queue(conversation_key, seq) WHERE state = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_imq_lease ON integration_message_queue(lease_expires_at) "
        "WHERE state IN ('dispatching','processing','cancelling')"
    )
    op.execute(
        "CREATE INDEX idx_imq_integration_state "
        "ON integration_message_queue(integration_id, state, enqueued_at DESC, id)"
    )
    op.execute(
        "CREATE INDEX idx_imq_ws_state ON integration_message_queue(workspace_id, state)"
    )
    op.execute(
        "CREATE INDEX idx_imq_binding_state ON integration_message_queue(binding_id, state)"
    )

    # ============ execution_context_appends (MES-82, runtime.md) ============
    op.execute(
        """
        CREATE TABLE execution_context_appends (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          execution_id        UUID NOT NULL,
          seq                 BIGINT NOT NULL CHECK (seq > 0),
          source              TEXT NOT NULL CHECK (source IN ('im_btw')),
          payload             JSONB NOT NULL,
          injected_at         TIMESTAMPTZ NULL,
          injected_attempt_id UUID NULL,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_eca_ws_id UNIQUE (workspace_id, id),
          CONSTRAINT uq_eca_execution_seq UNIQUE (execution_id, seq),
          CONSTRAINT fk_eca_execution FOREIGN KEY (workspace_id, execution_id)
            REFERENCES task_executions(workspace_id, id) ON DELETE CASCADE,
          CONSTRAINT fk_eca_injected_attempt FOREIGN KEY (workspace_id, injected_attempt_id)
            REFERENCES execution_attempts(workspace_id, id)
            ON DELETE SET NULL (injected_attempt_id)
        )
        """
    )
    # Per-attempt pending set (matches the daemon GET filter: no receipt, or
    # receipt cleared by requeue).
    op.execute(
        "CREATE INDEX idx_eca_execution_pending "
        "ON execution_context_appends(execution_id, seq) "
        "WHERE injected_attempt_id IS NULL"
    )

    # -- MES-82 cross-table alignment -----------------------------------------
    # runtime.md server-persisted context-injection water level (per-execution
    # monotonic high-water mark; daemon receipts reference attempts).
    op.execute(
        "ALTER TABLE task_executions "
        "ADD COLUMN IF NOT EXISTS context_injected_through_seq BIGINT NOT NULL DEFAULT 0"
    )
    # notification_delivery IM routing: provider CHECK extension (+dingtalk,
    # comment-inbox.md §2) + integration/binding routing FKs (README §6.2
    # rule 6: column-level SET NULL — ledger rows survive integration delete).
    op.execute("ALTER TABLE notification_delivery DROP CONSTRAINT IF EXISTS notification_delivery_provider")
    op.execute(
        "ALTER TABLE notification_delivery ADD CONSTRAINT notification_delivery_provider "
        "CHECK (provider IS NULL OR provider IN ('feishu','slack','dingtalk','email_smtp'))"
    )
    op.execute(
        "ALTER TABLE notification_delivery ADD CONSTRAINT fk_delivery_integration "
        "FOREIGN KEY (workspace_id, integration_id) "
        "REFERENCES integrations(workspace_id, id) ON DELETE SET NULL (integration_id)"
    )
    op.execute(
        "ALTER TABLE notification_delivery ADD CONSTRAINT fk_delivery_binding "
        "FOREIGN KEY (workspace_id, binding_id) "
        "REFERENCES integration_bindings(workspace_id, id) ON DELETE SET NULL (binding_id)"
    )
    # outbox earliest-claim column (README §6.6 authority): claim predicate
    # ``status='pending' AND available_at <= now()``; retryable non-failure
    # results only move available_at (short backoff) without consuming the
    # delivery_attempts failure budget, and the index filter prevents hot
    # loops. Existing rows become immediately claimable (DEFAULT now()).
    op.execute(
        "ALTER TABLE outbox_events "
        "ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute("DROP INDEX IF EXISTS idx_outbox_pending")
    op.execute(
        "CREATE INDEX idx_outbox_pending ON outbox_events (available_at, created_at) "
        "WHERE status = 'pending'"
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

    # feishu inbound events carry no app id in the payload — the integration
    # is located by TRIING the stored encrypt keys (spec §3.2 "经 app_id /
    # encrypt_key"); the candidate set per kind is small.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_integrations_active_by_kind(p_kind text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text, kind text,
          config jsonb, secret_ref text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT i.id, i.workspace_id, i.status, i.kind, i.config, i.secret_ref
          FROM integrations i
          WHERE i.kind = p_kind
            AND i.deleted_at IS NULL
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_integrations_active_by_kind(text) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION mesh_integrations_active_by_kind(text) TO {APP_ROLE}"
    )

    # Full-row bootstrap read by id (binding-routed ingestion — the tenant
    # GUC is unknown until the integration row is resolved).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_integration_by_id(p_id uuid)
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text, kind text,
          config jsonb, secret_ref text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT i.id, i.workspace_id, i.status, i.kind, i.config, i.secret_ref
          FROM integrations i
          WHERE i.id = p_id AND i.deleted_at IS NULL
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_integration_by_id(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_integration_by_id(uuid) TO {APP_ROLE}")

    # Resource-scoped endpoints whose paths carry no workspace segment
    # (§3.3: /integrations/vcs/*, /issues/{id}/vcs-links) resolve the owning
    # workspace through these bootstrap reads before setting the tenant GUC.
    for fn, table in (
        ("mesh_integration_workspace_id", "integrations"),
        ("mesh_vcs_link_workspace_id", "vcs_links"),
        ("mesh_issue_workspace_id", "issues"),
    ):
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {fn}(p_id uuid) RETURNS uuid
            LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
              SELECT t.workspace_id FROM {table} t WHERE t.id = p_id
            $$
            """
        )
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn}(uuid) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn}(uuid) TO {APP_ROLE}")

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
    # MES-82 cross-table alignment (reverse of upgrade).
    op.execute("DROP INDEX IF EXISTS idx_outbox_pending")
    op.execute(
        "CREATE INDEX idx_outbox_pending ON outbox_events (created_at) WHERE status = 'pending'"
    )
    op.execute("ALTER TABLE outbox_events DROP COLUMN IF EXISTS available_at")
    op.execute("ALTER TABLE notification_delivery DROP CONSTRAINT IF EXISTS fk_delivery_binding")
    op.execute("ALTER TABLE notification_delivery DROP CONSTRAINT IF EXISTS fk_delivery_integration")
    op.execute("ALTER TABLE notification_delivery DROP CONSTRAINT IF EXISTS notification_delivery_provider")
    op.execute(
        "ALTER TABLE notification_delivery ADD CONSTRAINT notification_delivery_provider "
        "CHECK (provider IS NULL OR provider IN ('feishu','slack','email_smtp'))"
    )
    op.execute("ALTER TABLE task_executions DROP COLUMN IF EXISTS context_injected_through_seq")
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_integration_by_id(uuid) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_integration_by_id(uuid)")
    for fn in (
        "mesh_integration_workspace_id",
        "mesh_vcs_link_workspace_id",
        "mesh_issue_workspace_id",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {fn}(uuid) FROM mesh_app")
        op.execute(f"DROP FUNCTION IF EXISTS {fn}(uuid)")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION external_identity_unlink_allowed(uuid, uuid) FROM mesh_app"
    )
    op.execute("DROP FUNCTION IF EXISTS external_identity_unlink_allowed(uuid, uuid)")
    op.execute(
        "REVOKE EXECUTE ON FUNCTION mesh_binding_by_external_ref(text, text) FROM mesh_app"
    )
    op.execute("DROP FUNCTION IF EXISTS mesh_binding_by_external_ref(text, text)")
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_integrations_active_by_kind(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_integrations_active_by_kind(text)")
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
