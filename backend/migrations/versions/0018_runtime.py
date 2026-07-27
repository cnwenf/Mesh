"""runtime: runtimes, task_executions, execution_attempts + supporting tables

Stage-6 agent-layer increment B (runtime.md §2 / README §6.4 / §6.5 / §6.10).
DDL mirrors docs/specs/validation/schema_r2_validation.sql (runtime section)
and runtime.md §2.1–§2.5.

Tables (all tenant-scoped, RLS fail-closed per README §6.2 rule 5):

- ``runtimes`` — the "workstation" an agent runs on (platform-managed or
  self-hosted; both speak the same register/heartbeat/claim/report machine
  protocol, runtime.md §1.1). Labels / capabilities / capacity live
  server-side; claim matching trusts ONLY these stored values (§2.5).
- ``task_executions`` — ONE logical execution row per trigger (README §6.4):
  carries the idempotency key (§6.5), the frozen ``config_snapshot`` (§6.11)
  and the authoritative ``required_capabilities`` scheduling field — a STRICT
  string array (R3; any non-string element is rejected by CHECK so an object
  entry can never poison the JSONB ``<@`` claim match forever).
- ``execution_attempts`` — physical tries (README §6.4): lease + ``lease_seq``
  fencing, per-attempt ``working_branch`` (§6.5), ``reclaimed`` audit state.
  ``UNIQUE(execution_id, attempt_number)``; requeue INSERTs a new row, never
  overwrites (audit chain intact, T4).
- ``task_log_segments`` — byte-offset index; content lives in object storage
  (runtime.md §2.3). ``UNIQUE(attempt_id, start_offset)`` keeps offsets
  contiguous and non-overlapping per attempt.
- ``repo_checkouts`` — one per attempt (§6.5 per-attempt branch).
- ``runtime_credentials`` — ciphertext-only secrets (``encrypted_value``; the
  server never echoes plaintext, README §6.16).
- ``execution_credentials`` — per-attempt credential injection audit +
  one-shot envelope fencing (runtime.md §2.2 protocol). ``refetch_count``
  bounds the refetch protocol (default limit 3, §2.2).
- ``runtime_heartbeats`` — optional heartbeat detail window.
- ``approvals`` — the UNIFIED approval entity (README §6.10). ``tool_call``
  subjects FK into ``task_executions`` now; ``autopilot_action`` /
  ``squad_plan`` subject columns exist bare (their composite FKs land with
  the autopilot / squad increments — the same deferred-FK pattern
  ``members.agent_id`` and ``agents.default_runtime_id`` used).

Deferred composite FK landed here:
- ``agents.(workspace_id, default_runtime_id) → runtimes(workspace_id, id)``
  (column reserved since 0017; claim enforces default-runtime affinity).

Bootstrap reads (RLS is fail-closed; workspace unknown until the lookup):
- ``mesh_runtime_by_token_hash`` — daemon bearer token → runtime row.
- ``mesh_runtime_by_activation_hash`` — one-shot activation code → runtime row.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "runtimes",
    "task_executions",
    "execution_attempts",
    "task_log_segments",
    "repo_checkouts",
    "runtime_credentials",
    "execution_credentials",
    "runtime_heartbeats",
    "approvals",
)

# Full DML tables for the restricted app role (heartbeats / approvals included:
# the daemon writes heartbeats, the console + daemon write approvals).
DML_TABLES = ", ".join(TENANT_TABLES)


def upgrade() -> None:
    # -- CHECK helper functions (executable twins of the validation-SQL
    #    reference; IMMUTABLE so CHECK constraints may call them) -------------
    # Scheduling fields (runtimes.capabilities, task_executions.required_capabilities)
    # are STRICT string arrays (README §6.4 R3): an object element would make the
    # claim ``<@`` match miss forever and strand the task (T28).
    op.execute(
        """
        CREATE FUNCTION jsonb_is_string_array(v jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
          SELECT jsonb_typeof(v) = 'array'
             AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(v) e WHERE jsonb_typeof(e) <> 'string')
        $$
        """
    )
    # Authorization snapshot capability_grants must be STRICT [{capability,
    # permission}] objects with a mandatory enum permission (README §6.11 R4).
    op.execute(
        """
        CREATE FUNCTION jsonb_is_capability_grants(v jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
          SELECT jsonb_typeof(v) = 'array'
             AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements(v) e
                WHERE jsonb_typeof(e) <> 'object'
                   OR jsonb_typeof(e->'capability') <> 'string'
                   OR (e->'permission') IS NULL
                   OR jsonb_typeof(e->'permission') <> 'string'
                   OR NOT (e->>'permission' IN ('read_only','write','confirm_required'))
             )
        $$
        """
    )
    # Label maps (runtimes.labels, task_executions.label_requirements):
    # flat string→string objects, matched by JSONB containment at claim time.
    op.execute(
        """
        CREATE FUNCTION jsonb_is_string_map(v jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
          SELECT jsonb_typeof(v) = 'object'
             AND NOT EXISTS (
               SELECT 1 FROM jsonb_each(v) kv WHERE jsonb_typeof(kv.value) <> 'string'
             )
        $$
        """
    )

    # -- runtimes (runtime.md §2.2) -------------------------------------------
    op.execute(
        """
        CREATE TABLE runtimes (
          id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id               UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name                       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
          kind                       TEXT NOT NULL DEFAULT 'self_hosted'
                                     CHECK (kind IN ('platform_managed','self_hosted')),
          status                     TEXT NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending','online','unavailable','paused',
                                                       'draining','decommissioned')),
          activation_token_hash      TEXT NULL,
          activation_expires_at      TIMESTAMPTZ NULL,
          activated_at               TIMESTAMPTZ NULL,
          runtime_token_id           UUID NULL REFERENCES api_tokens(id) ON DELETE SET NULL,
          runtime_token_hash         TEXT NULL,
          capabilities               JSONB NOT NULL DEFAULT '[]'::jsonb
                                     CHECK (jsonb_is_string_array(capabilities)),
          labels                     JSONB NOT NULL DEFAULT '{}'::jsonb
                                     CHECK (jsonb_is_string_map(labels)),
          hostname                   TEXT NULL,
          os                         TEXT NULL,
          cpu_cores                  INT NULL CHECK (cpu_cores IS NULL OR cpu_cores > 0),
          memory_mb                  INT NULL CHECK (memory_mb IS NULL OR memory_mb > 0),
          max_concurrent             INT NOT NULL DEFAULT 1 CHECK (max_concurrent >= 0),
          current_load               INT NOT NULL DEFAULT 0 CHECK (current_load >= 0),
          last_heartbeat_at          TIMESTAMPTZ NULL,
          heartbeat_interval_seconds INT NOT NULL DEFAULT 15 CHECK (heartbeat_interval_seconds > 0),
          lease_grace_seconds        INT NOT NULL DEFAULT 45 CHECK (lease_grace_seconds > 0),
          version                    TEXT NULL,
          created_by                 UUID NULL,
          created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at                 TIMESTAMPTZ NULL,
          -- registration actor (console); owns the daemon api_token issued at
          -- activation (api_tokens.owner_member_id is NOT NULL)
          FOREIGN KEY (workspace_id, created_by) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (created_by)
        )
        """
    )
    # Composite-FK reference target (README §6.2 rule 1).
    op.execute("CREATE UNIQUE INDEX uq_runtimes_ws_id ON runtimes(workspace_id, id)")
    # runtime.md §2.4: list online runtimes + heartbeat freshness.
    op.execute(
        "CREATE INDEX idx_runtimes_status ON runtimes(status, last_heartbeat_at) "
        "WHERE deleted_at IS NULL"
    )
    # Bootstrap token lookup index (mesh_runtime_by_token_hash).
    op.execute(
        "CREATE UNIQUE INDEX uq_runtimes_runtime_token_hash "
        "ON runtimes(runtime_token_hash) WHERE runtime_token_hash IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_runtimes_activation_token_hash "
        "ON runtimes(activation_token_hash) WHERE activation_token_hash IS NOT NULL"
    )

    # -- task_executions: logical execution (README §6.4 authority) -----------
    op.execute(
        """
        CREATE TABLE task_executions (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          agent_id             UUID NULL,
          issue_id             UUID NULL,
          trigger              TEXT NOT NULL DEFAULT 'assign'
                               CHECK (trigger IN ('assign','mention','autopilot','manual',
                                                  'chat','integration')),
          status               TEXT NOT NULL DEFAULT 'queued'
                               CHECK (status IN ('queued','claimed','running','cancelling',
                                                 'completed','failed','timeout','cancelled',
                                                 'awaiting_approval')),
          idempotency_key      TEXT NULL,
          priority             INT NOT NULL DEFAULT 100,
          task_spec            JSONB NOT NULL DEFAULT '{}'::jsonb,
          label_requirements   JSONB NOT NULL DEFAULT '{}'::jsonb
                               CHECK (jsonb_is_string_map(label_requirements)),
          required_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb
                               CHECK (jsonb_is_string_array(required_capabilities)),
          trigger_event_id     UUID NULL,
          config_snapshot      JSONB NOT NULL DEFAULT '{}'::jsonb
                               CHECK (jsonb_typeof(config_snapshot) = 'object'
                                      AND (NOT config_snapshot ? 'capability_grants'
                                           OR jsonb_is_capability_grants(
                                                config_snapshot->'capability_grants'))),
          max_attempts         INT NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
          queued_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          finished_at          TIMESTAMPTZ NULL,
          timeout_seconds      INT NOT NULL DEFAULT 1800 CHECK (timeout_seconds > 0),
          cancel_requested_by  UUID NULL,
          cancel_requested_at  TIMESTAMPTZ NULL,
          result               JSONB NULL,
          failure_reason       TEXT NULL,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- executor must live in the SAME workspace (README §6.2 rule 2)
          FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id)
            ON DELETE SET NULL (agent_id),
          -- trigger source issue, same workspace (assignment observability)
          FOREIGN KEY (workspace_id, issue_id) REFERENCES issues(workspace_id, id)
            ON DELETE SET NULL (issue_id),
          -- cancel actor is a roster member of the SAME workspace
          FOREIGN KEY (workspace_id, cancel_requested_by) REFERENCES members(workspace_id, id)
            ON DELETE SET NULL (cancel_requested_by)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_task_executions_ws_id ON task_executions(workspace_id, id)")
    # Idempotency backstop (README §6.5): same trigger event never enqueues twice.
    # NULL never conflicts (partial index).
    op.execute(
        "CREATE UNIQUE INDEX uq_task_executions_idem "
        "ON task_executions(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    # runtime.md §2.4: the claim hot path (queued, per workspace, by priority).
    op.execute(
        "CREATE INDEX idx_executions_claimable "
        "ON task_executions(workspace_id, priority, queued_at) WHERE status = 'queued'"
    )
    # Assignment observability: history by agent / issue (runtime.md §2.4).
    op.execute("CREATE INDEX idx_executions_agent_time ON task_executions(agent_id, queued_at DESC)")
    op.execute("CREATE INDEX idx_executions_issue_time ON task_executions(issue_id, queued_at DESC)")

    # -- execution_attempts: physical try (README §6.4 authority) -------------
    op.execute(
        """
        CREATE TABLE execution_attempts (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          execution_id          UUID NOT NULL,
          attempt_number        INT NOT NULL CHECK (attempt_number >= 1),
          runtime_id            UUID NULL,
          claimed_by_runtime_id UUID NULL,
          status                TEXT NOT NULL DEFAULT 'claimed'
                                CHECK (status IN ('claimed','running','cancelling','completed',
                                                  'failed','timeout','cancelled','reclaimed')),
          lease_expires_at      TIMESTAMPTZ NULL,
          lease_seq             INT NOT NULL DEFAULT 0 CHECK (lease_seq >= 0),
          claimed_at            TIMESTAMPTZ NULL,
          started_at            TIMESTAMPTZ NULL,
          finished_at           TIMESTAMPTZ NULL,
          working_branch        TEXT NULL,
          result                JSONB NULL,
          failure_reason        TEXT NULL,
          created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- requeue never reuses a number: audit chain intact (T4)
          UNIQUE (execution_id, attempt_number),
          FOREIGN KEY (workspace_id, execution_id) REFERENCES task_executions(workspace_id, id)
            ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, runtime_id) REFERENCES runtimes(workspace_id, id)
            ON DELETE SET NULL (runtime_id)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_attempts_ws_id ON execution_attempts(workspace_id, id)")
    # runtime.md §2.4: reaper lease sweep / per-runtime inflight sweep.
    op.execute(
        "CREATE INDEX idx_attempts_lease_expired ON execution_attempts(lease_expires_at) "
        "WHERE status IN ('claimed','running','cancelling')"
    )
    op.execute(
        "CREATE INDEX idx_attempts_runtime_inflight ON execution_attempts(runtime_id) "
        "WHERE status IN ('claimed','running','cancelling')"
    )
    op.execute(
        "CREATE INDEX idx_attempts_execution ON execution_attempts(execution_id, attempt_number)"
    )

    # -- task_log_segments: offset index, content in object storage (§2.3) ----
    op.execute(
        """
        CREATE TABLE task_log_segments (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          attempt_id   UUID NOT NULL,
          start_offset BIGINT NOT NULL CHECK (start_offset >= 0),
          end_offset   BIGINT NOT NULL CHECK (end_offset >= start_offset),
          storage_ref  TEXT NOT NULL,
          line_count   INT NOT NULL DEFAULT 0 CHECK (line_count >= 0),
          sealed       BOOLEAN NOT NULL DEFAULT false,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- offsets are contiguous and non-overlapping per attempt
          UNIQUE (attempt_id, start_offset),
          FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_task_log_segments_ws_id ON task_log_segments(workspace_id, id)")
    op.execute(
        "CREATE INDEX idx_log_segments_attempt_offset ON task_log_segments(attempt_id, start_offset)"
    )

    # -- repo_checkouts: one per attempt (§6.5 per-attempt branch) ------------
    op.execute(
        """
        CREATE TABLE repo_checkouts (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          attempt_id     UUID NOT NULL,
          repo_url       TEXT NOT NULL,
          base_ref       TEXT NOT NULL,
          working_branch TEXT NOT NULL,
          commit_sha     TEXT NULL,
          local_path     TEXT NULL,
          status         TEXT NOT NULL DEFAULT 'cloning'
                         CHECK (status IN ('cloning','ready','diff_ready','recycled','failed')),
          diff_ref       TEXT NULL,
          recycled_at    TIMESTAMPTZ NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (attempt_id),
          FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_repo_checkouts_ws_id ON repo_checkouts(workspace_id, id)")

    # -- runtime_credentials: ciphertext-only secrets (README §6.16) ----------
    op.execute(
        """
        CREATE TABLE runtime_credentials (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name            TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 120),
          kind            TEXT NOT NULL DEFAULT 'env'
                          CHECK (kind IN ('env','file','repo_token','ssh_key')),
          scope           TEXT NOT NULL DEFAULT 'execution',
          encrypted_value TEXT NOT NULL,
          env_name        TEXT NULL CHECK (env_name IS NULL OR env_name ~ '^[A-Z][A-Z0-9_]{0,63}$'),
          redact_in_logs  BOOLEAN NOT NULL DEFAULT true,
          expires_at      TIMESTAMPTZ NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at      TIMESTAMPTZ NULL
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_runtime_credentials_ws_id ON runtime_credentials(workspace_id, id)"
    )

    # -- execution_credentials: per-attempt injection audit + fencing (§2.2) --
    # refetch_count bounds the refetch protocol (default limit 3; exceeding it
    # freezes the execution for human review). Not in the §2.2 field table but
    # required to make the cap enforceable and testable.
    op.execute(
        """
        CREATE TABLE execution_credentials (
          attempt_id    UUID NOT NULL,
          credential_id UUID NOT NULL,
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          envelope_ref  TEXT NOT NULL,
          injected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at    TIMESTAMPTZ NULL,
          refetch_count INT NOT NULL DEFAULT 0 CHECK (refetch_count >= 0),
          PRIMARY KEY (attempt_id, credential_id),
          -- cross-tenant credential references rejected at INSERT (README §6.2)
          FOREIGN KEY (workspace_id, attempt_id) REFERENCES execution_attempts(workspace_id, id)
            ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, credential_id) REFERENCES runtime_credentials(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_execution_credentials_ws_attempt "
        "ON execution_credentials(workspace_id, attempt_id, credential_id)"
    )

    # -- runtime_heartbeats: optional detail window (runtime.md §2.2) ---------
    op.execute(
        """
        CREATE TABLE runtime_heartbeats (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          runtime_id   UUID NOT NULL,
          current_load INT NOT NULL DEFAULT 0 CHECK (current_load >= 0),
          metrics      JSONB NOT NULL DEFAULT '{}'::jsonb,
          health       TEXT NOT NULL DEFAULT 'healthy' CHECK (health IN ('healthy','degraded')),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, runtime_id) REFERENCES runtimes(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_runtime_heartbeats_ws_id ON runtime_heartbeats(workspace_id, id)")
    op.execute(
        "CREATE INDEX idx_runtime_heartbeats_runtime_time "
        "ON runtime_heartbeats(runtime_id, created_at DESC)"
    )

    # -- approvals: UNIFIED approval entity (README §6.10) ---------------------
    # tool_call subjects FK into task_executions now. autopilot_action /
    # squad_plan subject columns exist bare — their composite FKs land with the
    # autopilot / squad increments (deferred-FK pattern, like members.agent_id).
    op.execute(
        """
        CREATE TABLE approvals (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          subject_type         TEXT NOT NULL
                               CHECK (subject_type IN ('tool_call','autopilot_action','squad_plan')),
          subject_execution_id UUID NULL,
          subject_run_id       UUID NULL,
          subject_task_id      UUID NULL,
          requested_by_member_id UUID NOT NULL,
          action_summary       JSONB NOT NULL,
          status               TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
          requested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at           TIMESTAMPTZ NOT NULL,
          decided_by_member_id UUID NULL,
          decided_at           TIMESTAMPTZ NULL,
          decision_comment     TEXT NULL,
          idempotency_key      TEXT NULL,
          -- exactly one subject column non-null, matching subject_type
          CHECK (
               (subject_type = 'tool_call'        AND subject_execution_id IS NOT NULL
                                                 AND subject_run_id IS NULL AND subject_task_id IS NULL)
            OR (subject_type = 'autopilot_action' AND subject_run_id IS NOT NULL
                                                 AND subject_execution_id IS NULL AND subject_task_id IS NULL)
            OR (subject_type = 'squad_plan'       AND subject_task_id IS NOT NULL
                                                 AND subject_execution_id IS NULL AND subject_run_id IS NULL)
          ),
          FOREIGN KEY (workspace_id, subject_execution_id)
            REFERENCES task_executions(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, requested_by_member_id)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, decided_by_member_id)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_approvals_ws_id ON approvals(workspace_id, id)")
    op.execute(
        "CREATE INDEX idx_approvals_pending ON approvals(workspace_id, requested_at) "
        "WHERE status = 'pending'"
    )
    # One pending approval per subject (README §6.10 partial unique indexes).
    op.execute(
        "CREATE UNIQUE INDEX uq_approvals_pending_execution "
        "ON approvals(workspace_id, subject_execution_id) "
        "WHERE status = 'pending' AND subject_type = 'tool_call'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_approvals_pending_run "
        "ON approvals(workspace_id, subject_run_id) "
        "WHERE status = 'pending' AND subject_type = 'autopilot_action'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_approvals_pending_task "
        "ON approvals(workspace_id, subject_task_id) "
        "WHERE status = 'pending' AND subject_type = 'squad_plan'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_approvals_idem "
        "ON approvals(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )

    # -- agents.default_runtime_id deferred composite FK (runtime.md §2.5) ----
    # The column existed bare since 0017; default-runtime claim affinity now
    # has a referential target. PG16 column-level SET NULL keeps workspace_id
    # untouched when a runtime is dropped (README §6.2 rule 6).
    op.execute(
        "ALTER TABLE agents ADD CONSTRAINT agents_default_runtime_id_runtimes "
        "FOREIGN KEY (workspace_id, default_runtime_id) REFERENCES runtimes(workspace_id, id) "
        "ON DELETE SET NULL (default_runtime_id)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) -----------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges ----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")

    # -- SECURITY DEFINER bootstrap reads (RLS is fail-closed; the presented
    #    token's workspace is unknown until the lookup succeeds) ---------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_runtime_by_token_hash(p_hash text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text, deleted_at timestamptz,
          runtime_token_id uuid
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT r.id, r.workspace_id, r.status, r.deleted_at, r.runtime_token_id
          FROM runtimes r
          WHERE r.runtime_token_hash = p_hash
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_runtime_by_token_hash(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_runtime_by_token_hash(text) TO {APP_ROLE}")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_runtime_by_activation_hash(p_hash text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text,
          activation_expires_at timestamptz, activated_at timestamptz,
          deleted_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT r.id, r.workspace_id, r.status,
                 r.activation_expires_at, r.activated_at, r.deleted_at
          FROM runtimes r
          WHERE r.activation_token_hash = p_hash
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_runtime_by_activation_hash(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_runtime_by_activation_hash(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_runtime_by_activation_hash(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_runtime_by_activation_hash(text)")
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_runtime_by_token_hash(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_runtime_by_token_hash(text)")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_default_runtime_id_runtimes")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS jsonb_is_string_map(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS jsonb_is_capability_grants(jsonb)")
    op.execute("DROP FUNCTION IF EXISTS jsonb_is_string_array(jsonb)")
