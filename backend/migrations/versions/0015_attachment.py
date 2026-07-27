"""attachment: attachment_blobs, attachments, attachment_links, upload_sessions, attachment_quotas

Stage-5 collaboration-layer attachment module (attachment.md §2 full). DDL
mirrors docs/specs/validation/schema_r2_validation.sql (附件 section) plus the
§2.5/§2.6 optional ledger tables.

- attachment_blobs: the CONTENT truth table (R2). One row per unique content
  per workspace — ``UNIQUE (workspace_id, content_hash)`` serializes
  concurrent de-dup (T24); ``ref_count`` is maintained atomically by the
  service layer (create +1 / soft-delete -1, same transaction); scan verdict,
  magic-byte MIME and thumbnails hang off the blob so a quarantine scan runs
  once for every sharer. ``UNIQUE (workspace_id, id)`` is the composite-FK
  reference target for ``attachments.blob_id`` (README §6.2).
- attachments: independent attachment RECORDS referencing a shared blob.
  Two orthogonal state machines (attachment.md §2.2/§2.3): ``upload_status``
  here (session level) and ``scan_status`` on blobs (content level). The
  composite FKs to ``attachment_blobs`` and ``members`` are same-tenant
  (README §6.2); both are RESTRICT — dedup only SHARES blobs, deleting a
  blob truth row never silently orphans attachment records.
- attachment_links: polymorphic LOGICAL FK (issue / comment / chat_message),
  no physical constraint for the polymorphic target (README §6.2 rule 4); the
  row carries ``workspace_id`` and consistency is service-enforced.
- upload_sessions: multipart upload ledger (§2.5).
- attachment_quotas: optional per-workspace limit overrides (§2.6).
- RLS defense-in-depth (README §6.2 rule 5) on every table, plus a narrow
  SECURITY DEFINER workspace resolver for the workspace-less paths
  (/attachments/{id}, /multipart/{id}).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = (
    "attachment_blobs",
    "attachments",
    "attachment_links",
    "upload_sessions",
    "attachment_quotas",
)


def upgrade() -> None:
    # -- attachment_blobs: content truth (attachment.md §2.2, R2) --------------
    op.execute(
        """
        CREATE TABLE attachment_blobs (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          content_hash     TEXT NOT NULL,
          storage_provider TEXT NOT NULL DEFAULT 's3',
          storage_bucket   TEXT NOT NULL,
          storage_key      TEXT NOT NULL,
          file_size        BIGINT NOT NULL CHECK (file_size > 0),
          mime_type        TEXT NULL,
          extension        TEXT NULL,
          is_image         BOOLEAN NOT NULL DEFAULT false,
          image_width      INT NULL,
          image_height     INT NULL,
          thumbnail_keys   JSONB NULL,
          scan_status      TEXT NOT NULL DEFAULT 'pending'
                           CHECK (scan_status IN ('pending','clean','infected','error','skipped')),
          scan_detail      JSONB NULL,
          ref_count        INT NOT NULL DEFAULT 0 CHECK (ref_count >= 0),
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Concurrent de-dup serialization (T24): one blob row per content.
    op.execute(
        "ALTER TABLE attachment_blobs ADD CONSTRAINT uq_attachment_blobs_ws_hash "
        "UNIQUE (workspace_id, content_hash)"
    )
    # Composite-FK reference target for attachments.blob_id (README §6.2).
    op.execute(
        "CREATE UNIQUE INDEX uq_attachment_blobs_ws_id ON attachment_blobs(workspace_id, id)"
    )
    # Quarantine sweep (worker SKIP LOCKED, README §2.2) + GC candidates.
    op.execute(
        "CREATE INDEX idx_blobs_quarantine ON attachment_blobs(created_at) "
        "WHERE scan_status = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_blobs_refcount ON attachment_blobs(storage_key) "
        "WHERE ref_count = 0"
    )

    # -- attachments: independent records referencing shared blobs (§2.3) ------
    op.execute(
        """
        CREATE TABLE attachments (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          uploader_id   UUID NOT NULL,
          blob_id       UUID NOT NULL,
          file_name     TEXT NOT NULL,
          file_size     BIGINT NOT NULL CHECK (file_size > 0),
          upload_status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (upload_status IN ('pending','uploading','completed','failed','expired')),
          idempotency_key TEXT NULL,
          expires_at    TIMESTAMPTZ NULL,
          deleted_at    TIMESTAMPTZ NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, blob_id) REFERENCES attachment_blobs(workspace_id, id)
            ON DELETE RESTRICT,
          FOREIGN KEY (workspace_id, uploader_id) REFERENCES members(workspace_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    # Composite-FK reference target for attachment_links / upload_sessions.
    op.execute(
        "CREATE UNIQUE INDEX uq_attachments_ws_id ON attachments(workspace_id, id)"
    )
    op.execute(
        "CREATE INDEX idx_attachments_uploader ON attachments(workspace_id, uploader_id, created_at)"
    )
    # Orphan sweep: incomplete uploads past expires_at.
    op.execute(
        "CREATE INDEX idx_attachments_pending ON attachments(expires_at) "
        "WHERE upload_status <> 'completed'"
    )
    op.execute(
        "CREATE INDEX idx_attachments_active ON attachments(workspace_id, created_at) "
        "WHERE deleted_at IS NULL"
    )
    # Idempotent upload-request (README §6.5/§6.14 — duplicate keys return the
    # first result; workspace-scoped, NULL never collides).
    op.execute(
        "CREATE UNIQUE INDEX uq_attachments_idem ON attachments(workspace_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )

    # -- attachment_links: polymorphic logical FK (§2.4) ------------------------
    op.execute(
        """
        CREATE TABLE attachment_links (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          attachment_id UUID NOT NULL,
          linked_type   TEXT NOT NULL CHECK (linked_type IN ('issue','comment','chat_message')),
          linked_id     UUID NOT NULL,
          display       TEXT NOT NULL DEFAULT 'card' CHECK (display IN ('inline','card')),
          position      INT NOT NULL DEFAULT 0,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, attachment_id) REFERENCES attachments(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    # One association per (attachment, target).
    op.execute(
        "ALTER TABLE attachment_links ADD CONSTRAINT uq_attachment_link "
        "UNIQUE (attachment_id, linked_type, linked_id)"
    )
    op.execute(
        "CREATE INDEX idx_links_target ON attachment_links(workspace_id, linked_type, linked_id, position)"
    )

    # -- upload_sessions: multipart ledger (§2.5) -------------------------------
    op.execute(
        """
        CREATE TABLE upload_sessions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          attachment_id UUID NOT NULL,
          upload_id     TEXT NOT NULL,
          part_size     INT NOT NULL CHECK (part_size > 0),
          parts         JSONB NOT NULL DEFAULT '[]',
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          FOREIGN KEY (workspace_id, attachment_id) REFERENCES attachments(workspace_id, id)
            ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_upload_sessions_ws_id ON upload_sessions(workspace_id, id)"
    )

    # -- attachment_quotas: optional per-workspace overrides (§2.6) -------------
    op.execute(
        """
        CREATE TABLE attachment_quotas (
          workspace_id   UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
          max_file_bytes BIGINT NOT NULL CHECK (max_file_bytes > 0),
          total_bytes    BIGINT NOT NULL CHECK (total_bytes > 0),
          used_bytes     BIGINT NOT NULL DEFAULT 0 CHECK (used_bytes >= 0),
          allowed_mimes  JSONB NULL,
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- RLS defense-in-depth (README §6.2 rule 5) ------------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- SECURITY DEFINER resolver for workspace-less paths ---------------------
    # attachment.md §3.1 exposes /attachments/{id} and /multipart/{id} without a
    # workspace prefix. The app role reads under fail-closed RLS, so the tenant
    # workspace must be resolved BEFORE a tenant context can be set — narrow,
    # parameterised owner-executed bypass (same pattern as 0009). Membership /
    # host-visibility gates afterwards still run under the policies.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mesh_attachment_workspace_id(p_id uuid)
        RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT a.workspace_id FROM attachments a WHERE a.id = p_id
        $$
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION mesh_attachment_workspace_id(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION mesh_attachment_workspace_id(uuid) TO {APP_ROLE}")

    # -- app-role privileges ----------------------------------------------------
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"attachment_blobs, attachments, attachment_links, upload_sessions, attachment_quotas "
        f"TO {APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(f"REVOKE EXECUTE ON FUNCTION mesh_attachment_workspace_id(uuid) FROM {APP_ROLE}")
    op.execute("DROP FUNCTION IF EXISTS mesh_attachment_workspace_id(uuid)")
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS mesh_{table}_tenant ON {table}")
    op.execute("DROP TABLE IF EXISTS attachment_quotas")
    op.execute("DROP TABLE IF EXISTS upload_sessions")
    op.execute("DROP TABLE IF EXISTS attachment_links")
    op.execute("DROP TABLE IF EXISTS attachments")
    op.execute("DROP TABLE IF EXISTS attachment_blobs")
