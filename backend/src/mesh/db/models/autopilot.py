"""Autopilot module data model (docs/specs/features/autopilot.md §2).

Tables (all tenant-scoped, README §6.1/§6.2):

* ``autopilots`` — rule definitions (trigger + filter + actions + guardrails
  + ``next_run_at`` scheduling state + misfire policy);
* ``autopilot_runs`` — one row per triggered execution of a rule, carrying
  the replayable trigger snapshot, cascade lineage and token/duration stats;
* ``autopilot_run_attempts`` — retry detail per run (``UNIQUE(run_id,
  attempt_number)`` — the audit chain never reuses a number);
* ``autopilot_artifacts`` — decoupled product references (comment/issue/
  notification/agent output/HTTP response) produced by a run;
* ``webhook_events`` — inbound external events: signature result + dedup +
  full audit trail (``UNIQUE(workspace_id, idempotency_key)``);
* ``webhook_secrets`` — inbound webhook credential pairs (autopilot.md
  §3.1/§5.3): the URL token is stored HASHED (lookup only), the HMAC secret
  is stored as Fernet CIPHERTEXT (needed to recompute signatures, never
  echoed — plaintext shown exactly once at creation/rotation).

The unified approvals entity (README §6.10) gains its deferred physical
composite FK ``approvals.subject_run_id → autopilot_runs(workspace_id, id)``
here, now that the referenced table exists (migration 0023).

Composite FKs follow the README §6.2 same-tenant pattern; nullable composite
references use the PG16 column-level ``ON DELETE SET NULL (<column>)`` form
(§6.2 rule 6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# autopilot.md §2.2 — trigger types (schedule + the §6.9 event triggers +
# inbound webhook).
TRIGGER_TYPE_VALUES = (
    "schedule",
    "issue_status_changed",
    "issue_created",
    "issue_field_changed",
    "comment_created",
    "agent_mentioned",
    "webhook_received",
)

# autopilot.md §4.4 rule state machine.
RULE_STATUS_VALUES = ("active", "paused", "archived")

# autopilot.md §4.4 run state machine.
RUN_STATUS_VALUES = (
    "pending",
    "running",
    "waiting_approval",
    "retrying",
    "succeeded",
    "failed",
    "cancelled",
)

RETRY_BACKOFF_VALUES = ("fixed", "linear", "exponential")

# autopilot.md §2.4 artifact types.
ARTIFACT_TYPE_VALUES = ("comment", "issue", "notification", "agent_output", "http_response")

# autopilot.md §2.5 signature verification outcomes. ``invalid``/``missing``
# are ALWAYS rejected (401, never dispatched); ``skipped`` only exists for
# ``is_test=true`` test-run simulation and never produces a run.
SIGNATURE_STATUS_VALUES = ("valid", "invalid", "missing", "skipped")

# autopilot.md §2.5 inbound event processing lifecycle.
PROCESS_STATUS_VALUES = (
    "received",
    "matched",
    "dispatched",
    "deduped",
    "rejected",
    "processed",
    "failed",
)

WEBHOOK_SECRET_STATUS_VALUES = ("active", "revoked")


class Autopilot(Base):
    """Automation rule definition (autopilot.md §2.2).

    A rule = trigger (when) + filter (whether) + ordered actions (what,
    usually "hand a prompt to the executor agent"). Execution capability
    comes entirely from ``executor_agent_id`` — the rule only dispatches.
    Guardrails (rate limit / dedup / concurrency / approval gate / kill
    switch / cascade depth / budgets) are first-class and default-ON.
    """

    __tablename__ = "autopilots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "executor_agent_id"],
            ["agents.workspace_id", "agents.id"],
            ondelete="SET NULL (executor_agent_id)",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["members.workspace_id", "members.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"trigger_type IN {TRIGGER_TYPE_VALUES!r}", name="autopilots_trigger_type_check"
        ),
        CheckConstraint(f"status IN {RULE_STATUS_VALUES!r}", name="autopilots_status_check"),
        CheckConstraint(f"retry_backoff IN {RETRY_BACKOFF_VALUES!r}", name="autopilots_backoff_check"),
        CheckConstraint("max_retries >= 0", name="autopilots_max_retries_check"),
        CheckConstraint("retry_base_seconds > 0", name="autopilots_retry_base_check"),
        CheckConstraint("retry_max_seconds > 0", name="autopilots_retry_max_check"),
        CheckConstraint("rate_limit_max >= 0", name="autopilots_rate_limit_max_check"),
        CheckConstraint(
            "rate_limit_window_seconds > 0", name="autopilots_rate_limit_window_check"
        ),
        CheckConstraint("concurrency_limit >= 1", name="autopilots_concurrency_check"),
        # Composite-FK referencing prerequisite (README §6.2): tables
        # referenced across tables expose UNIQUE(workspace_id, id).
        Index("uq_autopilot_ws_id", "workspace_id", "id", unique=True),
        # Scheduler scan: due active schedule rules (partial, §2.7).
        Index(
            "idx_autopilot_schedule",
            "next_run_at",
            postgresql_where=text(
                "status = 'active' AND trigger_type = 'schedule' AND deleted_at IS NULL"
            ),
        ),
        # Event matcher: candidate rules by trigger type + status.
        Index(
            "idx_autopilot_trigger",
            "trigger_type",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Name uniqueness within the soft-delete scope (§2.2).
        Index(
            "uq_autopilot_ws_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    description: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    trigger_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    trigger_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    filter_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    action_config: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    executor_agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    guardrails: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    retry_backoff: Mapped[str] = mapped_column(TEXT, 
        nullable=False, server_default=text("'exponential'")
    )
    retry_base_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )
    retry_max_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1800")
    )
    rate_limit_max: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("10")
    )
    rate_limit_window_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3600")
    )
    concurrency_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    require_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    next_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class AutopilotRun(Base):
    """One execution of a rule (autopilot.md §2.3).

    ``trigger_snapshot`` is the replayable event input; ``parent_run_id`` /
    ``cascade_depth`` trace agent→agent cascades (loop protection, §2.6);
    ``execution_id`` links the ``run_agent_prompt`` logical execution
    (runtime.md ``task_executions``, README §6.4). Pending approvals are
    reverse-looked-up via ``approvals.subject_run_id`` (README §6.10) — the
    run carries NO redundant approval column.
    """

    __tablename__ = "autopilot_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "autopilot_id"],
            ["autopilots.workspace_id", "autopilots.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "webhook_event_id"],
            ["webhook_events.workspace_id", "webhook_events.id"],
            ondelete="SET NULL (webhook_event_id)",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "execution_id"],
            ["task_executions.workspace_id", "task_executions.id"],
            ondelete="SET NULL (execution_id)",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_run_id"],
            ["autopilot_runs.workspace_id", "autopilot_runs.id"],
            ondelete="SET NULL (parent_run_id)",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "triggered_by"],
            ["members.workspace_id", "members.id"],
            ondelete="SET NULL (triggered_by)",
        ),
        CheckConstraint(f"status IN {RUN_STATUS_VALUES!r}", name="autopilot_runs_status_check"),
        CheckConstraint("cascade_depth >= 0", name="autopilot_runs_cascade_depth_check"),
        CheckConstraint("retry_count >= 0", name="autopilot_runs_retry_count_check"),
        CheckConstraint("prompt_tokens >= 0", name="autopilot_runs_prompt_tokens_check"),
        CheckConstraint("completion_tokens >= 0", name="autopilot_runs_completion_tokens_check"),
        # Referenced by approvals.subject_run_id (README §6.10) and by the
        # attempt table — composite-FK prerequisite (README §6.2).
        Index("uq_autopilot_run_ws_id", "workspace_id", "id", unique=True),
        Index("idx_run_autopilot_started", "autopilot_id", text("started_at DESC")),
        Index("idx_run_workspace_started", "workspace_id", text("created_at DESC")),
        Index(
            "idx_run_status",
            "status",
            postgresql_where=text(
                "status IN ('running','retrying','waiting_approval','pending')"
            ),
        ),
        Index(
            "idx_run_parent",
            "parent_run_id",
            postgresql_where=text("parent_run_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    autopilot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    trigger_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    webhook_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cascade_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'pending'"))
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        Computed("(COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0))", persisted=True),
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class AutopilotRunAttempt(Base):
    """Retry detail — one row per attempt (autopilot.md §2.4).

    ``execution_id`` is a LOGICAL association to ``task_executions`` (§2.4:
    the attempt belongs to the run's workspace through its run) — no
    physical FK, mirroring the spec's "逻辑关联" wording.
    """

    __tablename__ = "autopilot_run_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["autopilot_runs.workspace_id", "autopilot_runs.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("attempt_number >= 1", name="autopilot_run_attempts_number_check"),
        Index("uq_run_attempt", "run_id", "attempt_number", unique=True),
        # Composite-FK referencing prerequisite (README §6.2, §2.7).
        Index("uq_autopilot_run_attempts_ws_id", "workspace_id", "id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class AutopilotArtifact(Base):
    """Decoupled run product reference (autopilot.md §2.4).

    ``ref_id`` is a polymorphic LOGICAL FK (the row carries
    ``workspace_id``; deletion consistency via service-layer cleanup,
    README §6.2 rule 4).
    """

    __tablename__ = "autopilot_artifacts"
    __table_args__ = (
        CheckConstraint(
            f"artifact_type IN {ARTIFACT_TYPE_VALUES!r}",
            name="autopilot_artifacts_type_check",
        ),
        Index("idx_artifact_run", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("autopilot_runs.id", ondelete="CASCADE"), nullable=False
    )
    artifact_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    ref_table: Mapped[str] = mapped_column(TEXT, nullable=False)
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    summary: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class WebhookEvent(Base):
    """Inbound external event record: signature + dedup + audit (autopilot.md §2.5).

    Rejected events (signature ``invalid``/``missing``) are stored with an
    ``idempotency_key`` in the SEPARATE ``rejected:<raw-hash>`` namespace so
    an attacker cannot pre-occupy a legitimate event's dedup key with an
    unsigned forgery (§2.5 去重防预占).
    """

    __tablename__ = "webhook_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "autopilot_id"],
            ["autopilots.workspace_id", "autopilots.id"],
            ondelete="SET NULL (autopilot_id)",
        ),
        CheckConstraint(
            f"signature_status IN {SIGNATURE_STATUS_VALUES!r}",
            name="webhook_events_signature_status_check",
        ),
        CheckConstraint(
            f"process_status IN {PROCESS_STATUS_VALUES!r}",
            name="webhook_events_process_status_check",
        ),
        # Composite-FK referencing prerequisite (autopilot_runs.webhook_event_id).
        Index("uq_webhook_event_ws_id", "workspace_id", "id", unique=True),
        Index("uq_webhook_event_idem", "workspace_id", "idempotency_key", unique=True),
        Index(
            "idx_webhook_event_route",
            "autopilot_id",
            "process_status",
            text("received_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    autopilot_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    event_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    signature_status: Mapped[str] = mapped_column(TEXT, nullable=False)
    process_status: Mapped[str] = mapped_column(TEXT, 
        nullable=False, server_default=text("'received'")
    )
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class WebhookSecret(Base):
    """Inbound webhook credential pair (autopilot.md §3.1 / §5.3).

    ``token_hash`` = SHA-256 of the URL routing token (lookup only — the
    endpoint path ``/webhooks/inbound/{token}`` resolves through it, so the
    token itself is never stored). ``encrypted_secret`` = Fernet ciphertext
    of the HMAC signing secret (must be recoverable to recompute
    signatures; plaintext shown exactly once at creation/rotation, never
    echoed in responses or logs). Same ciphertext-only contract as
    ``runtime_credentials`` (README §6.16).
    """

    __tablename__ = "webhook_secrets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["members.workspace_id", "members.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN {WEBHOOK_SECRET_STATUS_VALUES!r}",
            name="webhook_secrets_status_check",
        ),
        Index("uq_webhook_secrets_ws_id", "workspace_id", "id", unique=True),
        Index("uq_webhook_secrets_token_hash", "token_hash", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'default'"))
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    encrypted_secret: Mapped[str] = mapped_column(TEXT, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
