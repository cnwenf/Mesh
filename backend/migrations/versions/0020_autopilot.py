"""autopilot: rules, runs, attempts, artifacts, webhook inbound + secrets

Stage-7 agent-layer increment B (autopilot.md §2 / README §6.5 / §6.6 /
§6.9 / §6.10). DDL mirrors docs/specs/features/autopilot.md §2.2–§2.7 and
the README §6 canonical contracts. Migration number 0020 (single-head chain
0001 → 0020); parallel increments occupying 0020 on their own branches are
renumbered at merge time by the later-merging side (house convention).

Tables (all tenant-scoped, RLS fail-closed per README §6.2 rule 5):

- ``autopilots`` — rule definitions: trigger + filter + ordered actions +
  guardrails (default-ON: rate limit / dedup / concurrency / approval gate
  / kill switch / cascade depth / budgets), scheduling state ``next_run_at``
  (PostgreSQL is the ONLY scheduler source of truth — atomic claim, §4.5).
- ``autopilot_runs`` — one row per rule execution: replayable
  ``trigger_snapshot``, cascade lineage (``parent_run_id`` /
  ``cascade_depth``), token/duration stats and the ``total_tokens`` STORED
  generated column. Pending approvals are reverse-looked-up through
  ``approvals.subject_run_id`` (README §6.10 — NO redundant approval column).
- ``autopilot_run_attempts`` — retry detail, ``UNIQUE(run_id, attempt_number)``
  (the audit chain never reuses a number; requeue/retry inserts a new row).
- ``autopilot_artifacts`` — decoupled product references (polymorphic
  logical FK, §6.2 rule 4: the row carries ``workspace_id``).
- ``webhook_events`` — inbound external events: signature result + dedup
  (``UNIQUE(workspace_id, idempotency_key)``) + full audit. Rejected events
  live in the separate ``rejected:<raw-hash>`` idempotency namespace so an
  unsigned forgery cannot pre-occupy a legitimate event's dedup key (§2.5).
- ``webhook_secrets`` — inbound credential pairs (§3.1 / §5.3): URL token
  stored HASHED (lookup only), HMAC secret stored as Fernet CIPHERTEXT
  (recoverable to recompute signatures; plaintext shown once, never echoed
  — the ciphertext-only contract of ``runtime_credentials``, README §6.16).

Deferred composite FK landed here (README §6.10 R2 — logical association
upgraded to a physical same-tenant FK now that the target exists):
- ``approvals.(workspace_id, subject_run_id) → autopilot_runs(workspace_id, id)``.

Bootstrap read (RLS is fail-closed; the inbound webhook endpoint is
signature-authenticated, NOT Bearer — the workspace is unknown until the
token lookup succeeds, same pattern as the runtime daemon token lookups):
- ``mesh_webhook_secret_by_token_hash`` — URL token hash → secret row.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "autopilots",
    "autopilot_runs",
    "autopilot_run_attempts",
    "autopilot_artifacts",
    "webhook_events",
    "webhook_secrets",
)

# Full DML for the restricted app role: the console writes rules / secrets;
# the inbound webhook endpoint (signature-authenticated, no Bearer) writes
# webhook_events through the app role with the tenant GUC set after lookup.
DML_TABLES = ", ".join(TENANT_TABLES)


def upgrade() -> None:
    # -- autopilots: rule definitions (autopilot.md §2.2) --------------------
    op.execute(
        """
        CREATE TABLE autopilots (
          id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id              UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          name                      TEXT NOT NULL,
          description               TEXT NULL,
          trigger_type              TEXT NOT NULL
                                    CHECK (trigger_type IN ('schedule','issue_status_changed',
                                            'issue_created','issue_field_changed',
                                            'comment_created','agent_mentioned',
                                            'webhook_received')),
          trigger_config            JSONB NOT NULL DEFAULT '{}'::jsonb,
          filter_config             JSONB NOT NULL DEFAULT '{}'::jsonb,
          action_config             JSONB NOT NULL DEFAULT '[]'::jsonb,
          executor_agent_id         UUID NULL,
          status                    TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active','paused','archived')),
          guardrails                JSONB NOT NULL DEFAULT '{}'::jsonb,
          max_retries               INT NOT NULL DEFAULT 3 CHECK (max_retries >= 0),
          retry_backoff             TEXT NOT NULL DEFAULT 'exponential'
                                    CHECK (retry_backoff IN ('fixed','linear','exponential')),
          retry_base_seconds        INT NOT NULL DEFAULT 30 CHECK (retry_base_seconds > 0),
          retry_max_seconds         INT NOT NULL DEFAULT 1800 CHECK (retry_max_seconds > 0),
          rate_limit_max            INT NOT NULL DEFAULT 10 CHECK (rate_limit_max >= 0),
          rate_limit_window_seconds INT NOT NULL DEFAULT 3600
                                    CHECK (rate_limit_window_seconds > 0),
          concurrency_limit         INT NOT NULL DEFAULT 1 CHECK (concurrency_limit >= 1),
          require_approval          BOOLEAN NOT NULL DEFAULT false,
          next_run_at               TIMESTAMPTZ NULL,
          last_run_at               TIMESTAMPTZ NULL,
          created_by                UUID NOT NULL,
          deleted_at                TIMESTAMPTZ NULL,
          created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
          -- same-tenant composite FKs (README §6.2)
          FOREIGN KEY (workspace_id, executor_agent_id)
            REFERENCES agents(workspace_id, id) ON DELETE SET NULL (executor_agent_id),
          FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    # Composite-FK referencing prerequisite (README §6.2): autopilots is
    # referenced by autopilot_runs.autopilot_id and webhook_events.autopilot_id.
    op.execute("CREATE UNIQUE INDEX uq_autopilot_ws_id ON autopilots(workspace_id, id)")
    # Scheduler scan: due active schedule rules (autopilot.md §2.7).
    op.execute(
        "CREATE INDEX idx_autopilot_schedule ON autopilots(next_run_at) "
        "WHERE status = 'active' AND trigger_type = 'schedule' AND deleted_at IS NULL"
    )
    # Event matcher: candidate rules by trigger type + status.
    op.execute(
        "CREATE INDEX idx_autopilot_trigger ON autopilots(trigger_type, status) "
        "WHERE deleted_at IS NULL"
    )
    # Name uniqueness within the soft-delete scope.
    op.execute(
        "CREATE UNIQUE INDEX uq_autopilot_ws_name ON autopilots(workspace_id, name) "
        "WHERE deleted_at IS NULL"
    )

    # -- webhook_events: inbound external events (§2.5) -----------------------
    # Created BEFORE autopilot_runs: runs reference their inbound event.
    op.execute(
        """
        CREATE TABLE webhook_events (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          autopilot_id      UUID NULL,
          idempotency_key   TEXT NOT NULL,
          event_type        TEXT NOT NULL,
          headers           JSONB NULL,
          payload           JSONB NOT NULL,
          signature_status  TEXT NOT NULL
                            CHECK (signature_status IN ('valid','invalid','missing','skipped')),
          process_status    TEXT NOT NULL DEFAULT 'received'
                            CHECK (process_status IN ('received','matched','dispatched',
                                    'deduped','rejected','processed','failed')),
          received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, autopilot_id)
            REFERENCES autopilots(workspace_id, id) ON DELETE SET NULL (autopilot_id)
        )
        """
    )
    # Referenced by autopilot_runs.webhook_event_id (composite-FK prerequisite).
    op.execute("CREATE UNIQUE INDEX uq_webhook_event_ws_id ON webhook_events(workspace_id, id)")
    # Dedup key: first INSERT wins; a unique-conflict means "duplicate event"
    # (return 200 deduped, never dispatch twice).
    op.execute(
        "CREATE UNIQUE INDEX uq_webhook_event_idem ON webhook_events(workspace_id, idempotency_key)"
    )
    op.execute(
        "CREATE INDEX idx_webhook_event_route ON webhook_events(autopilot_id, process_status, received_at DESC)"
    )

    # -- autopilot_runs: one row per rule execution (§2.3) --------------------
    op.execute(
        """
        CREATE TABLE autopilot_runs (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          autopilot_id       UUID NOT NULL,
          workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          trigger_type       TEXT NOT NULL,
          trigger_snapshot   JSONB NOT NULL DEFAULT '{}'::jsonb,
          webhook_event_id   UUID NULL,
          execution_id       UUID NULL,
          parent_run_id      UUID NULL,
          cascade_depth      INT NOT NULL DEFAULT 0 CHECK (cascade_depth >= 0),
          status             TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending','running','waiting_approval',
                                     'retrying','succeeded','failed','cancelled')),
          started_at         TIMESTAMPTZ NULL,
          finished_at        TIMESTAMPTZ NULL,
          duration_ms        INT NULL,
          retry_count        INT NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
          error              JSONB NULL,
          prompt_tokens      INT NULL CHECK (prompt_tokens >= 0),
          completion_tokens  INT NULL CHECK (completion_tokens >= 0),
          total_tokens       INT GENERATED ALWAYS AS
                             (COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)) STORED,
          triggered_by       UUID NULL,
          is_test            BOOLEAN NOT NULL DEFAULT false,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, autopilot_id)
            REFERENCES autopilots(workspace_id, id) ON DELETE CASCADE,
          FOREIGN KEY (workspace_id, webhook_event_id)
            REFERENCES webhook_events(workspace_id, id) ON DELETE SET NULL (webhook_event_id),
          FOREIGN KEY (workspace_id, execution_id)
            REFERENCES task_executions(workspace_id, id) ON DELETE SET NULL (execution_id),
          FOREIGN KEY (workspace_id, triggered_by)
            REFERENCES members(workspace_id, id) ON DELETE SET NULL (triggered_by)
        )
        """
    )
    # Referenced by approvals.subject_run_id (README §6.10) and the attempt table.
    op.execute("CREATE UNIQUE INDEX uq_autopilot_run_ws_id ON autopilot_runs(workspace_id, id)")
    # Self-referencing cascade FK — added AFTER uq_autopilot_run_ws_id exists
    # (a composite FK needs the referenced UNIQUE(workspace_id, id)).
    op.execute(
        "ALTER TABLE autopilot_runs ADD CONSTRAINT autopilot_runs_parent_run_id_fkey "
        "FOREIGN KEY (workspace_id, parent_run_id) "
        "REFERENCES autopilot_runs(workspace_id, id) ON DELETE SET NULL (parent_run_id)"
    )
    op.execute("CREATE INDEX idx_run_autopilot_started ON autopilot_runs(autopilot_id, started_at DESC)")
    op.execute("CREATE INDEX idx_run_workspace_started ON autopilot_runs(workspace_id, created_at DESC)")
    op.execute(
        "CREATE INDEX idx_run_status ON autopilot_runs(status) "
        "WHERE status IN ('running','retrying','waiting_approval','pending')"
    )
    op.execute(
        "CREATE INDEX idx_run_parent ON autopilot_runs(parent_run_id) "
        "WHERE parent_run_id IS NOT NULL"
    )

    # -- autopilot_run_attempts: retry detail (§2.4) ---------------------------
    op.execute(
        """
        CREATE TABLE autopilot_run_attempts (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id       UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          run_id             UUID NOT NULL,
          attempt_number     INT NOT NULL CHECK (attempt_number >= 1),
          status             TEXT NOT NULL,
          execution_id       UUID NULL,
          started_at         TIMESTAMPTZ NULL,
          finished_at        TIMESTAMPTZ NULL,
          error              JSONB NULL,
          prompt_tokens      INT NULL,
          completion_tokens  INT NULL,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, run_id)
            REFERENCES autopilot_runs(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_run_attempt ON autopilot_run_attempts(run_id, attempt_number)")
    op.execute(
        "CREATE UNIQUE INDEX uq_autopilot_run_attempts_ws_id "
        "ON autopilot_run_attempts(workspace_id, id)"
    )

    # -- autopilot_artifacts: decoupled product references (§2.4) --------------
    op.execute(
        """
        CREATE TABLE autopilot_artifacts (
          id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id   UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          run_id         UUID NOT NULL REFERENCES autopilot_runs(id) ON DELETE CASCADE,
          artifact_type  TEXT NOT NULL
                         CHECK (artifact_type IN ('comment','issue','notification',
                                 'agent_output','http_response')),
          ref_table      TEXT NOT NULL,
          ref_id         UUID NOT NULL,
          summary        TEXT NULL,
          created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_artifact_run ON autopilot_artifacts(run_id)")

    # -- webhook_secrets: inbound credential pairs (§3.1 / §5.3) ---------------
    # token_hash = sha256(URL token) — lookup only, the token itself is never
    # stored; encrypted_secret = Fernet ciphertext of the HMAC signing secret
    # (ciphertext-only contract, README §6.16; plaintext shown once).
    op.execute(
        """
        CREATE TABLE webhook_secrets (
          id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          label             TEXT NOT NULL DEFAULT 'default',
          token_hash        TEXT NOT NULL,
          encrypted_secret  TEXT NOT NULL,
          status            TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','revoked')),
          created_by        UUID NOT NULL,
          revoked_at        TIMESTAMPTZ NULL,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, created_by)
            REFERENCES members(workspace_id, id) ON DELETE RESTRICT
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_webhook_secrets_ws_id ON webhook_secrets(workspace_id, id)")
    op.execute("CREATE UNIQUE INDEX uq_webhook_secrets_token_hash ON webhook_secrets(token_hash)")

    # -- notification types: autopilot-domain §6.13 matrix rows ---------------
    # autopilot_alert (circuit-break, critical) / autopilot_notice (kill-switch
    # receipt & plain notices, normal). The §6.13 matrix is the authority;
    # comment-inbox owns delivery. CHECK widened in place (same name).
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_type")
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT notifications_type "
        "CHECK (type IN ('assigned','mentioned','subscribed_update','comment_created',"
        "'status_changed','execution_finished','review_requested','due_soon',"
        "'autopilot_alert','autopilot_notice'))"
    )

    # -- unified approvals: deferred physical FK lands (README §6.10 R2) -------
    # subject_type='autopilot_action' rows now reference autopilot_runs with a
    # real same-tenant composite FK (the CHECK + uq_approvals_pending_run
    # partial unique index already exist from 0019).
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT approvals_subject_run_id_autopilot_runs "
        "FOREIGN KEY (workspace_id, subject_run_id) "
        "REFERENCES autopilot_runs(workspace_id, id) ON DELETE CASCADE"
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

    # -- SECURITY DEFINER bootstrap read (the inbound webhook endpoint is
    #    signature-authenticated, NOT Bearer — the workspace is unknown until
    #    the URL token lookup succeeds; RLS is fail-closed, same pattern as
    #    the runtime daemon token lookups in 0019) -----------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_webhook_secret_by_token_hash(p_hash text)
        RETURNS TABLE (
          id uuid, workspace_id uuid, status text, encrypted_secret text
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT s.id, s.workspace_id, s.status, s.encrypted_secret
          FROM webhook_secrets s
          WHERE s.token_hash = p_hash
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_webhook_secret_by_token_hash(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_webhook_secret_by_token_hash(text) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_type")
    op.execute(
        "ALTER TABLE notifications ADD CONSTRAINT notifications_type "
        "CHECK (type IN ('assigned','mentioned','subscribed_update','comment_created',"
        "'status_changed','execution_finished','review_requested','due_soon'))"
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_webhook_secret_by_token_hash(text) FROM mesh_app")
    op.execute("DROP FUNCTION IF EXISTS mesh_webhook_secret_by_token_hash(text)")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT IF EXISTS approvals_subject_run_id_autopilot_runs")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
