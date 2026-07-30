"""Runtime models — the execution infrastructure (runtime.md §2).

``runtimes`` (runtime.md §2.2) are the "workstations" agents work on —
platform-managed or self-hosted, both speaking the identical
register/heartbeat/claim/report machine protocol (§1.1). Labels,
capabilities and capacity live SERVER-SIDE; claim matching trusts only
these stored values, never the daemon request body (§2.5 security red line).

``task_executions`` (README §6.4 authority) is ONE logical execution row per
trigger — it carries the idempotency key (§6.5), the frozen
``config_snapshot`` (§6.11) and the authoritative ``required_capabilities``
scheduling field, a STRICT string array enforced at schema level (R3: an
object element would make the claim ``<@`` match miss forever).

``execution_attempts`` (README §6.4 authority) are the physical tries: lease
+ ``lease_seq`` fencing, per-attempt ``working_branch`` (§6.5), and the
``reclaimed`` audit state. Requeue INSERTs attempt #N+1 — old rows are never
rewritten (audit chain intact, integration test T4).

``approvals`` (README §6.10) is the UNIFIED approval entity shared by
high-risk tool confirmation (this module), autopilot actions and squad plans
(their subject FKs land with those modules; the columns exist bare).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from mesh.db.base import Base

# ---------------------------------------------------------------------------
# Enum vocabularies (mirrored by schema CHECKs and API schema patterns)
# ---------------------------------------------------------------------------

RUNTIME_KIND_VALUES = ("platform_managed", "self_hosted")
RUNTIME_STATUS_VALUES = (
    "pending",
    "online",
    "unavailable",
    "paused",
    "draining",
    "decommissioned",
)
EXECUTION_TRIGGER_VALUES = ("assign", "mention", "autopilot", "manual", "chat", "integration")
EXECUTION_STATUS_VALUES = (
    "queued",
    "claimed",
    "running",
    "cancelling",
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "awaiting_approval",
)
ATTEMPT_STATUS_VALUES = (
    "claimed",
    "running",
    "cancelling",
    "completed",
    "failed",
    "timeout",
    "cancelled",
    "reclaimed",
)
CHECKOUT_STATUS_VALUES = ("cloning", "ready", "diff_ready", "recycled", "failed")
CREDENTIAL_KIND_VALUES = ("env", "file", "repo_token", "ssh_key")
HEALTH_VALUES = ("healthy", "degraded")
APPROVAL_SUBJECT_TYPES = ("tool_call", "autopilot_action", "squad_plan")
APPROVAL_STATUS_VALUES = ("pending", "approved", "rejected", "expired", "cancelled")

# Logical status machine (runtime.md §4.7, README §6.4). Terminal states have
# no outgoing edges. ``requeued`` is not a stored status — requeue returns the
# execution to ``queued`` with a NEW attempt row.
EXECUTION_TERMINAL_STATUSES = frozenset({"completed", "failed", "timeout", "cancelled"})
ATTEMPT_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "timeout", "cancelled", "reclaimed"}
)
# Attempts occupying runtime capacity: every non-terminal attempt holds one
# slot; terminal transitions release it exactly once (idempotent release).
ATTEMPT_INFLIGHT_STATUSES = frozenset({"claimed", "running", "cancelling"})

# Failure reason vocabulary (runtime.md §2.2 task_executions.failure_reason).
# §13.3 P0: extended with executor/budget/log failure reasons.
FAILURE_REASONS = frozenset(
    {
        "oom",
        "timeout",
        "nonzero_exit",
        "sandbox_violation",
        "lease_expired",
        "max_retries",
        "superseded",
        "agent_paused",
        "awaiting_approval",
        "approval_rejected",
        "approval_expired",
        "executor_unavailable",
        "executor_protocol_error",
        "daemon_restart",
        "budget_exceeded",
        "usage_unavailable",
        "log_backpressure",
    }
)


class Runtime(Base):
    """A workstation an agent executes on (runtime.md §2.2)."""

    __tablename__ = "runtimes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "created_by"],
            ["members.workspace_id", "members.id"],
            ondelete="SET NULL (created_by)",
            name="runtimes_workspace_id_created_by_fkey",
        ),
        CheckConstraint(
            f"kind IN {RUNTIME_KIND_VALUES!r}", name="runtimes_kind_check"
        ),
        CheckConstraint(
            f"status IN {RUNTIME_STATUS_VALUES!r}", name="runtimes_status_check"
        ),
        Index("uq_runtimes_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_runtimes_status",
            "status",
            "last_heartbeat_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_runtimes_runtime_token_hash",
            "runtime_token_hash",
            unique=True,
            postgresql_where=text("runtime_token_hash IS NOT NULL"),
        ),
        Index(
            "uq_runtimes_activation_token_hash",
            "activation_token_hash",
            unique=True,
            postgresql_where=text("activation_token_hash IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False, default="self_hosted")
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="pending")
    activation_token_hash: Mapped[str | None] = mapped_column(TEXT, default=None)
    activation_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    activated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    # §2.4 S-11: runtime_token_hash is the SINGLE source of truth for machine
    # credentials. The old runtime_token_id FK to api_tokens has been removed.
    runtime_token_hash: Mapped[str | None] = mapped_column(TEXT, default=None)
    # §2.6 P0: daemon protocol negotiation fields.
    protocol_version: Mapped[int | None] = mapped_column(Integer, default=None)
    daemon_version: Mapped[str | None] = mapped_column(TEXT, default=None)
    provider_manifest: Mapped[dict | None] = mapped_column(JSONB, default=None)
    daemon_features: Mapped[dict | None] = mapped_column(JSONB, default=None)
    capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    labels: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    hostname: Mapped[str | None] = mapped_column(TEXT, default=None)
    os: Mapped[str | None] = mapped_column(TEXT, default=None)
    cpu_cores: Mapped[int | None] = mapped_column(Integer, default=None)
    memory_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    max_concurrent: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    heartbeat_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    lease_grace_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    version: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class TaskExecution(Base):
    """One logical execution (README §6.4): trigger → exactly one row."""

    __tablename__ = "task_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agents.workspace_id", "agents.id"],
            ondelete="SET NULL (agent_id)",
            name="task_executions_workspace_id_agent_id_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "issue_id"],
            ["issues.workspace_id", "issues.id"],
            ondelete="SET NULL (issue_id)",
            name="task_executions_workspace_id_issue_id_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "cancel_requested_by"],
            ["members.workspace_id", "members.id"],
            ondelete="SET NULL (cancel_requested_by)",
            name="task_executions_workspace_id_cancel_requested_by_fkey",
        ),
        CheckConstraint(
            f"trigger IN {EXECUTION_TRIGGER_VALUES!r}", name="task_executions_trigger_check"
        ),
        CheckConstraint(
            f"status IN {EXECUTION_STATUS_VALUES!r}", name="task_executions_status_check"
        ),
        Index("uq_task_executions_ws_id", "workspace_id", "id", unique=True),
        Index(
            "uq_task_executions_idem",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "idx_executions_claimable",
            "workspace_id",
            "priority",
            "queued_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("idx_executions_agent_time", "agent_id", text("queued_at DESC")),
        Index("idx_executions_issue_time", "issue_id", text("queued_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    trigger: Mapped[str] = mapped_column(TEXT, nullable=False, default="assign")
    # Server-persisted context-injection water level (runtime.md runtime
    # context appends, MES-82): high-water seq the daemon has injected.
    context_injected_through_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="queued")
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    task_spec: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    label_requirements: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    required_capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    trigger_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    config_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # §2.1 P0: schema version for the frozen AttemptSpec snapshot.
    snapshot_schema_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=1, server_default=text("1")
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    queued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    cancel_requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    failure_reason: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class ExecutionAttempt(Base):
    """One physical try (README §6.4): lease, runtime, branch, single result."""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "execution_id"],
            ["task_executions.workspace_id", "task_executions.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "runtime_id"],
            ["runtimes.workspace_id", "runtimes.id"],
            ondelete="SET NULL (runtime_id)",
            name="execution_attempts_workspace_id_runtime_id_fkey",
        ),
        CheckConstraint(
            f"status IN {ATTEMPT_STATUS_VALUES!r}", name="execution_attempts_status_check"
        ),
        # Requeue never reuses an attempt number (audit chain, T4).
        UniqueConstraint("execution_id", "attempt_number"),
        Index("uq_attempts_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_attempts_lease_expired",
            "lease_expires_at",
            postgresql_where=text("status IN ('claimed','running','cancelling')"),
        ),
        Index(
            "idx_attempts_runtime_inflight",
            "runtime_id",
            postgresql_where=text("status IN ('claimed','running','cancelling')"),
        ),
        Index("idx_attempts_execution", "execution_id", "attempt_number"),
        # §13.1: claim request idempotency (one claim per runtime+request).
        Index(
            "uq_attempts_runtime_claim_request",
            "runtime_id",
            "claim_request_id",
            unique=True,
            postgresql_where=text("claim_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    claimed_by_runtime_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="claimed")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )
    lease_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    working_branch: Mapped[str | None] = mapped_column(TEXT, default=None)
    result: Mapped[dict | None] = mapped_column(JSONB, default=None)
    failure_reason: Mapped[str | None] = mapped_column(TEXT, default=None)
    # §2.6 P0: structured result fields for reliable verification/budget/query.
    provider: Mapped[str | None] = mapped_column(TEXT, default=None)
    provider_version: Mapped[str | None] = mapped_column(TEXT, default=None)
    provider_session_id: Mapped[str | None] = mapped_column(TEXT, default=None)
    model: Mapped[str | None] = mapped_column(TEXT, default=None)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cache_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(16, 6), default=None)
    num_turns: Mapped[int | None] = mapped_column(Integer, default=None)
    result_schema_version: Mapped[int | None] = mapped_column(Integer, default=None)
    redaction_hits: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=0, server_default=text("0")
    )
    # §13.1: claim request id for idempotent claim dedup.
    claim_request_id: Mapped[str | None] = mapped_column(TEXT, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class TaskLogSegment(Base):
    """Byte-offset index entry; log content lives in object storage (§2.3)."""

    __tablename__ = "task_log_segments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"],
            ["execution_attempts.workspace_id", "execution_attempts.id"],
            ondelete="CASCADE",
        ),
        # Offsets are contiguous and non-overlapping per attempt.
        UniqueConstraint("attempt_id", "start_offset"),
        Index("uq_task_log_segments_ws_id", "workspace_id", "id", unique=True),
        Index("idx_log_segments_attempt_offset", "attempt_id", "start_offset"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    start_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class RepoCheckout(Base):
    """One repository checkout per attempt (§6.5 per-attempt branch)."""

    __tablename__ = "repo_checkouts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"],
            ["execution_attempts.workspace_id", "execution_attempts.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"status IN {CHECKOUT_STATUS_VALUES!r}", name="repo_checkouts_status_check"
        ),
        Index("uq_repo_checkouts_ws_id", "workspace_id", "id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    repo_url: Mapped[str] = mapped_column(TEXT, nullable=False)
    base_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    working_branch: Mapped[str] = mapped_column(TEXT, nullable=False)
    commit_sha: Mapped[str | None] = mapped_column(TEXT, default=None)
    local_path: Mapped[str | None] = mapped_column(TEXT, default=None)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="cloning")
    diff_ref: Mapped[str | None] = mapped_column(TEXT, default=None)
    recycled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class RuntimeCredential(Base):
    """A workspace secret — ciphertext only, plaintext never echoed (§6.16)."""

    __tablename__ = "runtime_credentials"
    __table_args__ = (
        CheckConstraint(f"kind IN {CREDENTIAL_KIND_VALUES!r}", name="runtime_credentials_kind_check"),
        CheckConstraint(
            "env_name IS NULL OR env_name ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="runtime_credentials_env_name_check",
        ),
        Index("uq_runtime_credentials_ws_id", "workspace_id", "id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(TEXT, nullable=False)
    kind: Mapped[str] = mapped_column(TEXT, nullable=False, default="env")
    scope: Mapped[str] = mapped_column(TEXT, nullable=False, default="execution")
    encrypted_value: Mapped[str] = mapped_column(TEXT, nullable=False)
    env_name: Mapped[str | None] = mapped_column(TEXT, default=None)
    redact_in_logs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)


class ExecutionCredential(Base):
    """Per-attempt credential injection audit + one-shot envelope fencing."""

    __tablename__ = "execution_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"],
            ["execution_attempts.workspace_id", "execution_attempts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "credential_id"],
            ["runtime_credentials.workspace_id", "runtime_credentials.id"],
            ondelete="CASCADE",
        ),
        Index(
            "uq_execution_credentials_ws_attempt",
            "workspace_id",
            "attempt_id",
            "credential_id",
            unique=True,
        ),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    credential_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    envelope_ref: Mapped[str] = mapped_column(TEXT, nullable=False)
    injected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    refetch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RuntimeHeartbeat(Base):
    """Heartbeat detail window (runtime.md §2.2, optional retention)."""

    __tablename__ = "runtime_heartbeats"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "runtime_id"],
            ["runtimes.workspace_id", "runtimes.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(f"health IN {HEALTH_VALUES!r}", name="runtime_heartbeats_health_check"),
        Index("uq_runtime_heartbeats_ws_id", "workspace_id", "id", unique=True),
        Index("idx_runtime_heartbeats_runtime_time", "runtime_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    runtime_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    current_load: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    health: Mapped[str] = mapped_column(TEXT, nullable=False, default="healthy")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class Approval(Base):
    """Unified approval entity (README §6.10).

    ``tool_call`` subjects (high-risk tool confirmation) reference
    ``task_executions``; ``squad_plan`` subjects reference ``squad_tasks``
    (composite FK landed by the squad module, migration 0021) and
    ``autopilot_action`` subjects reference ``autopilot_runs`` (physical
    composite FK landed by the autopilot increment, README §6.10 R2).
    """

    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "subject_execution_id"],
            ["task_executions.workspace_id", "task_executions.id"],
            ondelete="CASCADE",
        ),
        # Deferred composite FK landed by the squad module (migration 0021,
        # README §6.10): squad_plan approvals reference squad_tasks physically.
        ForeignKeyConstraint(
            ["workspace_id", "subject_task_id"],
            ["squad_tasks.workspace_id", "squad_tasks.id"],
            ondelete="CASCADE",
            name="approvals_subject_task_id_squad_tasks",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "subject_run_id"],
            ["autopilot_runs.workspace_id", "autopilot_runs.id"],
            ondelete="CASCADE",
            name="approvals_subject_run_id_autopilot_runs",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "requested_by_member_id"],
            ["members.workspace_id", "members.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "decided_by_member_id"],
            ["members.workspace_id", "members.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"subject_type IN {APPROVAL_SUBJECT_TYPES!r}", name="approvals_subject_type_check"
        ),
        CheckConstraint(f"status IN {APPROVAL_STATUS_VALUES!r}", name="approvals_status_check"),
        Index("uq_approvals_ws_id", "workspace_id", "id", unique=True),
        Index(
            "idx_approvals_pending",
            "workspace_id",
            "requested_at",
            postgresql_where=text("status = 'pending'"),
        ),
        # One pending approval per subject (README §6.10).
        Index(
            "uq_approvals_pending_execution",
            "workspace_id",
            "subject_execution_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND subject_type = 'tool_call'"),
        ),
        Index(
            "uq_approvals_pending_run",
            "workspace_id",
            "subject_run_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND subject_type = 'autopilot_action'"),
        ),
        Index(
            "uq_approvals_pending_task",
            "workspace_id",
            "subject_task_id",
            unique=True,
            postgresql_where=text("status = 'pending' AND subject_type = 'squad_plan'"),
        ),
        Index(
            "uq_approvals_idem",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    subject_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    subject_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    subject_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    requested_by_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(TEXT, nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    decided_by_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    decision_comment: Mapped[str | None] = mapped_column(TEXT, default=None)
    idempotency_key: Mapped[str | None] = mapped_column(TEXT, default=None)


# §2.2 S-05: task token prefix (auth.md §2.5.1 registered).
TASK_TOKEN_PREFIX = "mesh_task_"


class AttemptTaskToken(Base):
    """Short-lived task token ledger (§2.2 S-05).

    One active token per attempt (partial unique index on ``attempt_id``
    WHERE ``revoked_at IS NULL``). Plaintext is delivered exactly once in
    the claim/renew response; only the SHA-256 hash is stored. Never
    reuses ``api_tokens`` or member roles (auth.md §2.5.1 R2-H2).
    """

    __tablename__ = "attempt_task_tokens"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "attempt_id"],
            ["execution_attempts.workspace_id", "execution_attempts.id"],
            ondelete="CASCADE",
            name="attempt_task_tokens_ws_attempt_fkey",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "runtime_id"],
            ["runtimes.workspace_id", "runtimes.id"],
            ondelete="CASCADE",
            name="attempt_task_tokens_ws_runtime_fkey",
        ),
        Index("uq_attempt_task_tokens_ws_id", "workspace_id", "id", unique=True),
        Index("uq_attempt_task_tokens_hash", "token_hash", unique=True),
        # Only one active (non-revoked) token per attempt.
        Index(
            "uq_attempt_task_tokens_active",
            "attempt_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("idx_attempt_task_tokens_attempt", "attempt_id", "lease_seq"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    runtime_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    lease_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    token_hash: Mapped[str] = mapped_column(TEXT, nullable=False)
    scopes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
