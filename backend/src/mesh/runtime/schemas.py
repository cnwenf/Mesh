"""Pydantic request models for the runtime module (runtime.md §3).

Responses are hand-built ``{"data": ...}`` dicts at the route layer (house
style, matches the agent module). Only REQUEST bodies live here.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

NAME_MAX = 120
# L3: daemon-supplied JSONB payloads are size-capped (storage-bloat / DoS).
MAX_JSON_FIELD_BYTES = 64 * 1024


def _bounded_json(value: object, field: str) -> object:
    if isinstance(value, dict) and len(json.dumps(value).encode("utf-8")) > MAX_JSON_FIELD_BYTES:
        raise ValueError(f"{field} exceeds {MAX_JSON_FIELD_BYTES} bytes")
    return value
LABEL_KEY_MAX = 64
LABEL_VALUE_MAX = 256
LABELS_MAX = 32
CAPABILITIES_MAX = 64
CAPABILITY_KEY_MAX = 128
LOG_LINES_MAX = 2000
LOG_LINE_BYTES_MAX = 8192

DiagnosticName = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.:-]*$"),
]
RuntimeDiagnosticReason = Literal[
    "provider_unavailable",
    "capability_missing",
    "sandbox_unavailable",
    "broker_unreachable",
    "egress_blocked",
    "budget_unavailable",
    "cleanup_failed",
    "provider_isolation_failed",
    "runtime_auth_failed",
    "sandbox_security_failed",
    "security_anomaly",
    "usage_invariant_failed",
    "usage_anomaly",
]
ISOLATION_DIAGNOSTIC_REASONS = frozenset(
    {
        "cleanup_failed",
        "provider_isolation_failed",
        "runtime_auth_failed",
        "sandbox_security_failed",
        "security_anomaly",
        "usage_invariant_failed",
        "usage_anomaly",
    }
)


class RuntimeLabelsModel(BaseModel):
    """Flat string→string label map (claim matches by JSONB containment)."""

    model_config = {"extra": "forbid"}


class CreateRuntimeRequest(BaseModel):
    """POST /workspaces/{ws}/runtimes (§3.1)."""

    name: str = Field(min_length=1, max_length=NAME_MAX)
    kind: Literal["platform_managed", "self_hosted"] = "self_hosted"
    labels: dict[str, str] = Field(default_factory=dict)
    max_concurrent: int = Field(default=1, ge=1, le=1024)


class PatchRuntimeRequest(BaseModel):
    """PATCH /workspaces/{ws}/runtimes/{id} — name / labels / max_concurrent."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    labels: dict[str, str] | None = None
    max_concurrent: int | None = Field(default=None, ge=1, le=1024)


class ActivateRuntimeRequest(BaseModel):
    """POST /daemon/runtimes:activate (§3.2).

    The activation code arrives in the body, assembled by the daemon from a
    restricted stdin / 0600 file — never a shell argument (§3.1 install safety).

    §2.6 P0: protocol_version, provider manifest, and daemon features are
    reported at activation for capability negotiation.
    """

    activation_code: str = Field(min_length=8, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    protocol_version: int = Field(default=1, ge=1)
    provider_manifest: dict[str, Any] = Field(default_factory=dict)
    daemon_features: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", "provider_manifest", "daemon_features")
    @classmethod
    def _bound_metadata(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(v, "metadata")  # type: ignore[return-value]


class ContextProgressEntry(BaseModel):
    """One heartbeat ``context_progress`` record (runtime.md §3.2, MES-82).

    Best-effort injection receipt for a single in-flight attempt: the daemon's
    local view of the highest injected ``seq`` plus the ``lease_seq`` fencing
    token it holds after claim/renew (R7-1 protocol input). The server fences on
    workspace/runtime ownership + latest valid attempt + ``claimed/running`` +
    exact ``lease_seq``; any mismatch writes 0 rows (never an error).
    """

    model_config = {"extra": "forbid"}

    attempt_id: UUID
    execution_id: UUID
    injected_through_seq: int = Field(default=0, ge=0)
    lease_seq: int = Field(default=0, ge=0)


class RuntimeDiagnosticRequest(BaseModel):
    """Safe daemon→server operational diagnostic.

    Only fixed reason codes and slug-like identifiers cross this boundary.
    Raw exception text, host paths, addresses, tokens and provider output are
    deliberately not representable. The server derives the repair command
    from ``reason_code`` instead of trusting daemon-authored shell text.
    """

    model_config = {"extra": "forbid"}

    reason_code: RuntimeDiagnosticReason
    missing_capabilities: list[DiagnosticName] = Field(default_factory=list, max_length=64)
    affected_task_types: list[DiagnosticName] = Field(default_factory=list, max_length=64)


class HeartbeatRequest(BaseModel):
    """POST /daemon/runtimes/{id}:heartbeat (§3.2).

    §2.6 P0: protocol_version reported on every heartbeat for drift detection.
    MES-82: ``context_progress`` is the best-effort context-injection receipt
    channel (runtime.md「运行期上下文追加」); absent on old daemons, which simply
    loses the dedup fast path without affecting at-least-once semantics.
    """

    model_config = {"extra": "forbid"}

    current_load: int = Field(default=0, ge=0)
    health: Literal["healthy", "degraded"] = "healthy"
    operational_state: Literal["online", "degraded", "isolated"] | None = None
    diagnostics: list[RuntimeDiagnosticRequest] = Field(default_factory=list, max_length=16)
    metrics: dict[str, Any] = Field(default_factory=dict)
    inflight: list[str] = Field(default_factory=list)
    protocol_version: int | None = Field(default=None, ge=1)
    context_progress: list[ContextProgressEntry] = Field(default_factory=list)

    @field_validator("metrics")
    @classmethod
    def _bound_metrics(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(v, "metrics")  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_operational_report(self) -> HeartbeatRequest:
        # Fields are optional as a compatibility bridge for already-installed
        # daemons. Once supplied, however, the report is internally coherent
        # and fail-closed.
        if self.operational_state is None:
            if self.diagnostics:
                raise ValueError("diagnostics require operational_state")
            return self
        if self.operational_state == "online":
            if self.health != "healthy" or self.diagnostics:
                raise ValueError("online requires healthy with no diagnostics")
            return self
        if self.health != "degraded" or not self.diagnostics:
            raise ValueError("degraded/isolated require degraded health and diagnostics")
        reasons = {item.reason_code for item in self.diagnostics}
        if self.operational_state == "isolated" and not reasons.intersection(
            ISOLATION_DIAGNOSTIC_REASONS
        ):
            raise ValueError("isolated requires a security, usage, or cleanup reason")
        if self.operational_state == "degraded" and reasons.intersection(
            ISOLATION_DIAGNOSTIC_REASONS
        ):
            raise ValueError("isolation reasons require isolated operational_state")
        return self


class ClaimRequest(BaseModel):
    """POST /daemon/runtimes/{id}/executions:claim (§3.2).

    ``diagnostics`` is advisory ONLY — server-side stored labels/capabilities/
    load decide matching and capacity (§2.5 red line). Never trusted.
    """

    diagnostics: dict[str, Any] = Field(default_factory=dict)


class AttemptTransitionRequest(BaseModel):
    """PATCH /daemon/attempts/{id} (§3.2, lease_seq fenced)."""

    lease_seq: int = Field(ge=0)
    status: Literal["running", "cancelling", "completed", "failed", "timeout", "cancelled"]
    result: dict[str, Any] | None = None
    failure_reason: str | None = Field(default=None, max_length=64)

    @field_validator("result")
    @classmethod
    def _bound_result(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        # §2.6 / L3: every daemon JSONB payload is size-capped (64 KiB → 422).
        # ``result`` was the only transition field missing the guard. None
        # passes through (``_bounded_json`` only measures dicts).
        return _bounded_json(v, "result")  # type: ignore[return-value]


class RenewLeaseRequest(BaseModel):
    """POST /daemon/attempts/{id}:renew-lease (§3.2)."""

    lease_seq: int = Field(ge=0)


class AppendLogsRequest(BaseModel):
    """POST /daemon/attempts/{id}/logs (§3.2, offset-idempotent)."""

    lease_seq: int = Field(ge=0)
    stream: Literal["stdout", "stderr"] = "stdout"
    start_offset: int = Field(ge=0)
    lines: list[str] = Field(default_factory=list, max_length=LOG_LINES_MAX)
    sealed: bool = False


class CheckoutReportRequest(BaseModel):
    """POST /daemon/attempts/{id}/checkouts (§3.2 / §2.2 H1)."""

    lease_seq: int = Field(ge=0)
    status: Literal["cloning", "ready", "diff_ready", "recycled", "failed"]
    repo_url: str | None = Field(default=None, max_length=2048)
    base_ref: str | None = Field(default=None, max_length=512)
    commit_sha: str | None = Field(default=None, max_length=128)
    local_path: str | None = Field(default=None, max_length=1024)
    diff: str | None = Field(default=None, max_length=2 * 1024 * 1024)


class RefetchCredentialsRequest(BaseModel):
    """POST /daemon/attempts/{id}/credentials:refetch (§2.2 protocol)."""

    lease_seq: int = Field(ge=0)


class ApprovalCreateRequest(BaseModel):
    """POST /daemon/executions/{id}/approvals (README §6.10)."""

    lease_seq: int = Field(ge=0)
    attempt_id: str
    action_summary: dict[str, Any]
    resume_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("action_summary", "resume_context")
    @classmethod
    def _bound_approval_json(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _bounded_json(v, "approval json")  # type: ignore[return-value]


class ApprovalDecideRequest(BaseModel):
    """POST /approvals/{id}/approve | :reject (README §6.10)."""

    comment: str | None = Field(default=None, max_length=2000)


class CreateCredentialRequest(BaseModel):
    """POST /workspaces/{ws}/credentials (§3.1 — plaintext only ever IN)."""

    name: str = Field(min_length=1, max_length=NAME_MAX)
    kind: Literal["env", "file", "repo_token", "ssh_key"] = "env"
    scope: str = Field(default="execution", max_length=64)
    value: str = Field(min_length=1, max_length=64 * 1024)
    env_name: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    redact_in_logs: bool = True
    expires_in_seconds: int | None = Field(default=None, gt=0, le=30 * 24 * 3600)
