"""Data import/export jobs (import-export.md §2 — 本模块 owns).

``data_jobs`` is the unified job entity for CSV/JSON imports and async
exports (``kind`` distinguishes). ``data_job_rows`` is the per-row ledger
that makes crash recovery idempotent: each row claims its stable
``row_key`` (``UNIQUE(job_id, row_key)``) with a pre-allocated
``target_id`` BEFORE the entity is created, so replaying a committed
batch can never create a second entity (R3/R4, T31).

Fencing follows the README §6.4 ``lease_seq`` paradigm (applied locally):
``lease_seq`` increments on every claim/resume; each batch transaction
locks the job row and validates ``lease_owner + lease_seq + unexpired``
before writing — a resurrected stale worker's batch is rejected wholesale.

Cross-module composite FKs (README §6.2):
- ``source_attachment_id`` → ``attachments(workspace_id, id)`` ON DELETE
  RESTRICT — the source file is the audit + idempotent-rerun basis and
  cannot be physically deleted while the job exists (§2.2 R3);
- ``result_attachment_id`` → ``attachments(workspace_id, id)`` ON DELETE
  SET NULL (result_attachment_id) — PG16 column-level: only the reference
  column is nulled, ``workspace_id`` stays NOT NULL (§6.2 rule 6);
- ``requested_by`` → ``members(workspace_id, id)`` ON DELETE RESTRICT —
  members are soft-deleted, so RESTRICT never blocks normal removal.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

DATA_JOB_KIND_VALUES = ("import", "export")
DATA_JOB_ENTITY_VALUES = ("issues", "projects")
DATA_JOB_FORMAT_VALUES = ("csv", "json")
DATA_JOB_STATUS_VALUES = (
    "pending",
    "validating",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
)
DATA_JOB_TERMINAL_STATUSES = frozenset({"completed", "completed_with_errors", "failed"})

DATA_JOB_ROW_STATUS_VALUES = ("pending", "created", "updated", "skipped", "failed")
DATA_JOB_ROW_TARGET_VALUES = ("issue", "project")


class DataJob(Base):
    """An import/export job (import-export.md §2.2)."""

    __tablename__ = "data_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(TEXT, nullable=False)
    entity_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    format: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    # Field mapping (import: source column → Mesh field + transforms;
    # export: field selection / column order, §2.4).
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Task params (import: target_project_id/options/validated_at; export:
    # scope/filters/locale/options, §2.4).
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Import source file (export: NULL). RESTRICT — audit/rerun basis (§2.2 R3).
    source_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    # Source content sha256, frozen on first successful validate (§2.2 R3).
    source_content_hash: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Export product / import error report (column-level SET NULL, §6.2 rule 6).
    result_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    succeeded_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Per-row error PREVIEW (first N entries; full detail in the report
    # attachment, §2.4).
    error_report: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    # Durable recovery point {last_committed_batch, last_row_key, batch_size,
    # resumed_count, resumed_at} — advanced inside each batch transaction (§3.8).
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    lease_owner: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Monotonic fencing token — incremented on every claim/resume (§3.8 R4).
    lease_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    failure_reason: Mapped[str | None] = mapped_column(TEXT, default=None)
    # Creator-scoped Idempotency-Key de-dup (README §6.14; attachment house
    # pattern — NULL never conflicts).
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {DATA_JOB_KIND_VALUES!r}", name="data_jobs_kind"),
        CheckConstraint(f"entity_type IN {DATA_JOB_ENTITY_VALUES!r}", name="data_jobs_entity_type"),
        CheckConstraint(f"format IN {DATA_JOB_FORMAT_VALUES!r}", name="data_jobs_format"),
        CheckConstraint(f"status IN {DATA_JOB_STATUS_VALUES!r}", name="data_jobs_status"),
        CheckConstraint("total_rows >= 0", name="data_jobs_total_rows_nonneg"),
        CheckConstraint("succeeded_rows >= 0", name="data_jobs_succeeded_rows_nonneg"),
        CheckConstraint("failed_rows >= 0", name="data_jobs_failed_rows_nonneg"),
        CheckConstraint("lease_seq >= 0", name="data_jobs_lease_seq_nonneg"),
        # Counting invariant (§2.2).
        CheckConstraint(
            "succeeded_rows + failed_rows <= total_rows",
            name="data_jobs_counts_within_total",
        ),
        # Imports always have a source file; exports never do (§2.2).
        CheckConstraint(
            "(kind = 'import' AND source_attachment_id IS NOT NULL) "
            "OR (kind = 'export' AND source_attachment_id IS NULL)",
            name="data_jobs_source_presence",
        ),
        # Composite-FK reference target (README §6.2).
        Index("uq_data_jobs_ws_id", "workspace_id", "id", unique=True),
        # Workspace job list / my-jobs list (§2.5).
        Index("idx_data_jobs_ws_created", "workspace_id", text("created_at DESC")),
        Index(
            "idx_data_jobs_requester",
            "workspace_id",
            "requested_by",
            text("created_at DESC"),
        ),
        # Active jobs (monitoring / compensating sweep — claiming goes via the
        # outbox, §2.5).
        Index(
            "idx_data_jobs_active",
            "created_at",
            postgresql_where=text("status NOT IN ('completed','completed_with_errors','failed')"),
        ),
        # Reaper: lease-expired running jobs (§2.5 R3).
        Index(
            "idx_data_jobs_lease_expired",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        # Per-requester create idempotency (NULL never conflicts).
        Index(
            "uq_data_jobs_idem",
            "workspace_id",
            "requested_by",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # Same-tenant composite FKs (README §6.2 rules 2/6).
        ForeignKeyConstraint(
            ("workspace_id", "source_attachment_id"),
            ("attachments.workspace_id", "attachments.id"),
            ondelete="RESTRICT",
            name="data_jobs_source_attachment_id_attachments",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "result_attachment_id"),
            ("attachments.workspace_id", "attachments.id"),
            ondelete="SET NULL (result_attachment_id)",
            name="data_jobs_result_attachment_id_attachments",
        ),
        ForeignKeyConstraint(
            ("workspace_id", "requested_by"),
            ("members.workspace_id", "members.id"),
            ondelete="RESTRICT",
            name="data_jobs_requested_by_members",
        ),
    )


class DataJobRow(Base):
    """Per-row result ledger — row-level idempotency key + recovery truth (§2.5)."""

    __tablename__ = "data_job_rows"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Physical data-row number in the source file (1-based; locating/debug).
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Stable row-level idempotency key (§2.5 R3/R4): prefer the mapped
    # external_ref ('ref:<value>'); duplicate refs and unmapped rows fall
    # back to the content-addressed 'row:<n>:<sha256>'.
    row_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    target_type: Mapped[str | None] = mapped_column(TEXT, default=None)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    # {field, code, message} — required when status='failed' (CHECK below).
    error: Mapped[dict | None] = mapped_column(JSONB, default=None)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("row_number >= 1", name="data_job_rows_row_number_pos"),
        CheckConstraint(f"status IN {DATA_JOB_ROW_STATUS_VALUES!r}", name="data_job_rows_status"),
        CheckConstraint(
            f"target_type IS NULL OR target_type IN {DATA_JOB_ROW_TARGET_VALUES!r}",
            name="data_job_rows_target_type",
        ),
        CheckConstraint("attempts >= 0", name="data_job_rows_attempts_nonneg"),
        # created/updated rows carry their entity; failed rows carry the error.
        CheckConstraint(
            "(status IN ('created','updated') AND target_type IS NOT NULL AND target_id IS NOT NULL) "
            "OR (status = 'failed' AND error IS NOT NULL) "
            "OR (status IN ('pending','skipped'))",
            name="data_job_rows_status_fields",
        ),
        Index("uq_data_job_rows_ws_id", "workspace_id", "id", unique=True),
        # R3: row-level idempotency — replaying a committed batch cannot
        # create the entity twice.
        Index("uq_data_job_rows_job_row_key", "job_id", "row_key", unique=True),
        # Resume scan + per-job status aggregation (§2.5).
        Index("idx_data_job_rows_job_status", "job_id", "status"),
        ForeignKeyConstraint(
            ("workspace_id", "job_id"),
            ("data_jobs.workspace_id", "data_jobs.id"),
            ondelete="CASCADE",
            name="fk_data_job_rows_job",
        ),
    )
