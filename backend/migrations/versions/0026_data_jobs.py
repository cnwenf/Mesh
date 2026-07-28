"""import-export: data_jobs + data_job_rows; data_job_finished notification type

Platform-capability increment (import-export.md §2 / §3.8 / §3.10, README
§6.2 / §6.6 / §6.13). Single-head chain 0001 → 0026.

Tables (all tenant-scoped, RLS fail-closed per README §6.2 rule 5):

- ``data_jobs`` — the unified import/export job entity (import-export.md
  §2.2). State machine ``pending → validating → pending`` (dry-run) and
  ``pending → running → completed / completed_with_errors / failed``.
  Carries the R3 recovery columns (``source_content_hash`` frozen at first
  successful validate, ``checkpoint`` advanced inside each batch
  transaction) and the R4 fencing columns (``lease_owner`` / ``lease_seq``
  monotonic token / ``lease_expires_at``) — the README §6.4 ``lease_seq``
  paradigm applied locally. Composite FKs per README §6.2:
  source attachment RESTRICT (audit + idempotent-rerun basis, §2.2 R3),
  result attachment column-level ``ON DELETE SET NULL
  (result_attachment_id)`` (§6.2 rule 6, PG16), requester RESTRICT (members
  are soft-deleted so RESTRICT never blocks normal removal).
- ``data_job_rows`` — the per-row ledger (§2.5 R3/R4). ``UNIQUE(job_id,
  row_key)`` is claimed with a pre-allocated ``target_id`` BEFORE the
  entity is created, so replaying a committed batch cannot create a second
  entity (T31). The status CHECK enforces created/updated rows carry their
  target and failed rows carry the error.

Also rebuilt:
- ``notifications_type`` CHECK gains the type ``data_job_finished`` (appended after autopilot)
  (import-export.md §3.10 — the README §6.13 data-job three rows;
  ``notification_preferences`` event validation is Python-side via
  ``ALLOWED_PREFERENCE_EVENT_TYPES``, no preference CHECK exists to alter).

Bootstrap read (RLS is fail-closed; workspace unknown until the lookup):
- ``mesh_data_job_workspace_id`` — job id → workspace id for the
  workspace-less ``/data-jobs/{id}`` routes (same pattern as
  ``mesh_comment_workspace_id`` / ``mesh_attachment_workspace_id``).

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = ("data_jobs", "data_job_rows")
DML_TABLES = ", ".join(TENANT_TABLES)


def upgrade() -> None:
    # -- data_jobs (import-export.md §2.2 / §2.5) ------------------------------
    op.execute(
        """
        CREATE TABLE data_jobs (
          id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id         UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          kind                 TEXT NOT NULL CHECK (kind IN ('import','export')),
          entity_type          TEXT NOT NULL CHECK (entity_type IN ('issues','projects')),
          format               TEXT NOT NULL CHECK (format IN ('csv','json')),
          status               TEXT NOT NULL DEFAULT 'pending'
                               CHECK (status IN ('pending','validating','running','completed',
                                                 'completed_with_errors','failed')),
          mapping              JSONB NOT NULL DEFAULT '{}',
          params               JSONB NOT NULL DEFAULT '{}',
          source_attachment_id UUID NULL,
          source_content_hash  TEXT NULL,
          result_attachment_id UUID NULL,
          total_rows           INT NOT NULL DEFAULT 0 CHECK (total_rows >= 0),
          succeeded_rows       INT NOT NULL DEFAULT 0 CHECK (succeeded_rows >= 0),
          failed_rows          INT NOT NULL DEFAULT 0 CHECK (failed_rows >= 0),
          error_report         JSONB NOT NULL DEFAULT '[]',
          checkpoint           JSONB NOT NULL DEFAULT '{}',
          lease_owner          TEXT NULL,
          lease_seq            BIGINT NOT NULL DEFAULT 0 CHECK (lease_seq >= 0),
          lease_expires_at     TIMESTAMPTZ NULL,
          requested_by         UUID NOT NULL,
          started_at           TIMESTAMPTZ NULL,
          finished_at          TIMESTAMPTZ NULL,
          failure_reason       TEXT NULL,
          idempotency_key      TEXT NULL,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT data_jobs_counts_within_total
            CHECK (succeeded_rows + failed_rows <= total_rows),
          CONSTRAINT data_jobs_source_presence
            CHECK ((kind = 'import' AND source_attachment_id IS NOT NULL)
                OR (kind = 'export' AND source_attachment_id IS NULL))
        )
        """
    )
    # Composite-FK target index (README §6.2).
    op.execute("CREATE UNIQUE INDEX uq_data_jobs_ws_id ON data_jobs (workspace_id, id)")
    # Workspace / requester job lists (§2.5).
    op.execute("CREATE INDEX idx_data_jobs_ws_created ON data_jobs (workspace_id, created_at DESC)")
    op.execute(
        "CREATE INDEX idx_data_jobs_requester ON data_jobs (workspace_id, requested_by, created_at DESC)"
    )
    # Active jobs (monitoring / compensating sweep — claiming goes via outbox).
    op.execute(
        "CREATE INDEX idx_data_jobs_active ON data_jobs (created_at) "
        "WHERE status NOT IN ('completed','completed_with_errors','failed')"
    )
    # Reaper: lease-expired running jobs (§2.5 R3).
    op.execute(
        "CREATE INDEX idx_data_jobs_lease_expired ON data_jobs (lease_expires_at) WHERE status = 'running'"
    )
    # Per-requester create idempotency (NULL never conflicts, README §6.14).
    op.execute(
        "CREATE UNIQUE INDEX uq_data_jobs_idem "
        "ON data_jobs (workspace_id, requested_by, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    # R3: source attachment RESTRICT — the source file is the audit +
    # idempotent-rerun basis and cannot be physically deleted while the job
    # exists (API translates the violation to 409 source_in_use; soft delete
    # via deleted_at is unaffected).
    op.execute(
        "ALTER TABLE data_jobs ADD CONSTRAINT data_jobs_source_attachment_id_attachments "
        "FOREIGN KEY (workspace_id, source_attachment_id) "
        "REFERENCES attachments(workspace_id, id) ON DELETE RESTRICT"
    )
    # Result attachment: PG16 column-level SET NULL — only the reference
    # column is nulled, workspace_id stays NOT NULL (README §6.2 rule 6).
    op.execute(
        "ALTER TABLE data_jobs ADD CONSTRAINT data_jobs_result_attachment_id_attachments "
        "FOREIGN KEY (workspace_id, result_attachment_id) "
        "REFERENCES attachments(workspace_id, id) ON DELETE SET NULL (result_attachment_id)"
    )
    # Requester RESTRICT — members are soft-deleted, RESTRICT never blocks
    # normal removal and the attribution never dangles.
    op.execute(
        "ALTER TABLE data_jobs ADD CONSTRAINT data_jobs_requested_by_members "
        "FOREIGN KEY (workspace_id, requested_by) "
        "REFERENCES members(workspace_id, id) ON DELETE RESTRICT"
    )

    # -- data_job_rows (import-export.md §2.5 R3/R4) ----------------------------
    op.execute(
        """
        CREATE TABLE data_job_rows (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          job_id       UUID NOT NULL,
          row_number   INT NOT NULL CHECK (row_number >= 1),
          row_key      TEXT NOT NULL,
          status       TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','created','updated','skipped','failed')),
          target_type  TEXT NULL CHECK (target_type IN ('issue','project')),
          target_id    UUID NULL,
          error        JSONB NULL,
          attempts     INT NOT NULL DEFAULT 0 CHECK (attempts >= 0),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT data_job_rows_status_fields CHECK (
            (status IN ('created','updated') AND target_type IS NOT NULL AND target_id IS NOT NULL)
            OR (status = 'failed' AND error IS NOT NULL)
            OR (status IN ('pending','skipped'))
          ),
          CONSTRAINT fk_data_job_rows_job FOREIGN KEY (workspace_id, job_id)
            REFERENCES data_jobs(workspace_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_data_job_rows_ws_id ON data_job_rows (workspace_id, id)")
    # R3: row-level idempotency key — replaying a committed batch cannot
    # create a second entity for the same row (T31).
    op.execute("CREATE UNIQUE INDEX uq_data_job_rows_job_row_key ON data_job_rows (job_id, row_key)")
    # Resume scan + per-job status aggregation.
    op.execute("CREATE INDEX idx_data_job_rows_job_status ON data_job_rows (job_id, status)")

    # -- RLS (defense-in-depth, README §6.2 rule 5) -----------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER bootstrap read (workspace-less /data-jobs/{id}) -------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_data_job_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT j.workspace_id FROM data_jobs j WHERE j.id = p_id
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_data_job_workspace_id(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_data_job_workspace_id(uuid) TO {APP_ROLE}")

    # -- app-role privileges -----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")

    # -- notifications_type gains data_job_finished (import-export.md §3.10) -----
    # The README §6.13 data-job three rows (R3) need a notification type; the
    # matrix semantics (success normal default-OFF / partial normal inbox /
    # failed critical) are enforced in comment_inbox.notifications.policy_for.
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_type")
    op.execute(
        """
        ALTER TABLE notifications ADD CONSTRAINT notifications_type CHECK (type IN
          ('assigned','mentioned','subscribed_update','comment_created','status_changed',
           'execution_finished','review_requested','due_soon','autopilot_alert',
           'autopilot_notice','data_job_finished'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notifications DROP CONSTRAINT notifications_type")
    op.execute(
        """
        ALTER TABLE notifications ADD CONSTRAINT notifications_type CHECK (type IN
          ('assigned','mentioned','subscribed_update','comment_created','status_changed',
           'execution_finished','review_requested','due_soon','autopilot_alert',
           'autopilot_notice'))
        """
    )
    op.execute(f"REVOKE EXECUTE ON FUNCTION mesh_data_job_workspace_id(uuid) FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS mesh_data_job_workspace_id(uuid)")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS data_job_rows")
    op.execute("DROP TABLE IF EXISTS data_jobs")
