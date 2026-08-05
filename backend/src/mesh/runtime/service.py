"""Runtime lifecycle service — console side (runtime.md §3.1 / §5.1).

Covers: three-stage registration (shadow row + one-time activation code +
daemon activation), heartbeat / health, pause / resume / decommission with
token revocation linkage (NEW-L2), token rotation, credential management
(plaintext IN only), executions & queue-depth observability.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import Settings
from mesh.db.models.api_token import DISPLAY_PREFIX_LEN, RUNTIME_TOKEN_PREFIX
from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import (
    Approval,
    ExecutionAttempt,
    ExecutionCredential,
    RepoCheckout,
    Runtime,
    RuntimeCredential,
    RuntimeHeartbeat,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    GoneError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from mesh.issue.service import IssueService
from mesh.outbox.service import emit_realtime
from mesh.runtime.context_appends import (
    ack_context_progress,
    compute_inject_commands,
)
from mesh.runtime.credentials import encrypt_credential_value

logger = logging.getLogger(__name__)

# Activation code alphabet without ambiguous glyphs (0/O, 1/I/L).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_GROUP_COUNT = 3
CODE_GROUP_LEN = 4

_RUNTIME_NOT_FOUND = "runtime not found"
_EXECUTION_NOT_FOUND = "execution not found"
_CREDENTIAL_NOT_FOUND = "credential not found"

# Terminal / offline transitions that revoke the daemon token (NEW-L2).
_TOKEN_REVOKING_STATUSES = frozenset({"paused", "decommissioned"})

MAX_LABEL_KEY = 64
MAX_LABEL_VALUE = 256
MAX_LABELS = 32
MAX_CAPABILITIES = 64

_DIAGNOSTIC_NAME = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_AUDIT_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_AUDIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,511}$")
_DECIMAL_AMOUNT = re.compile(r"^\d+(?:\.\d+)?$")
_REPOSITORY_LABEL = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_INVENTORY_HASH = re.compile(r"^sha256:[a-f0-9]{64}$")

_DIAGNOSTIC_REPAIR_COMMANDS = {
    "provider_unavailable": "mesh-runtime doctor --config <config-file>",
    "capability_missing": "mesh-runtime doctor --config <config-file>",
    "sandbox_unavailable": "mesh-runtime doctor --config <config-file>",
    "broker_unreachable": "mesh-runtime doctor --config <config-file>",
    "egress_blocked": "mesh-runtime doctor --config <config-file>",
    "budget_unavailable": "mesh-runtime doctor --config <config-file>",
    "cleanup_failed": "mesh-runtime doctor --config <config-file>",
    "security_anomaly": "mesh-runtime doctor --config <config-file>",
    "usage_anomaly": "mesh-runtime doctor --config <config-file>",
    "activation_pending": "mesh-runtime activate --config <config-file> --activation-code-stdin",
    "heartbeat_unavailable": "mesh-runtime doctor --config <config-file>",
    "runtime_draining": "mesh-runtime doctor --config <config-file>",
}
_DAEMON_DIAGNOSTIC_REASONS = frozenset(_DIAGNOSTIC_REPAIR_COMMANDS) - {
    "activation_pending",
    "heartbeat_unavailable",
    "runtime_draining",
}
_SAFE_NUMERIC_HEARTBEAT_METRICS = frozenset(
    {
        "cpu_percent",
        "memory_percent",
        "disk_percent",
        "queue_depth",
        "inflight_reported",
    }
)
_FROZEN_BUDGET_KEYS = (
    "max_cost_usd",
    "max_tokens",
    "max_turns",
    "max_wall_time_seconds",
    "max_idle_time_seconds",
)
_APPROVAL_FIELD_KEYS = frozenset(
    {
        "repository",
        "branch",
        "operation",
        "resource",
        "scope",
        "method",
        "target_type",
        "target_id",
    }
)


def _now() -> datetime:
    return datetime.now(UTC)


def generate_activation_code() -> str:
    """``ACT-XXXX-XXXX-XXXX`` — shown once; only its hash is stored."""
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUP_COUNT)
    ]
    return "ACT-" + "-".join(groups)


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def _validate_labels(labels: dict | None) -> dict:
    if labels is None:
        return {}
    if not isinstance(labels, dict):
        raise BusinessRuleError("labels must be an object", code="invalid_labels")
    if len(labels) > MAX_LABELS:
        raise BusinessRuleError("too many labels", code="invalid_labels")
    for key, value in labels.items():
        if not isinstance(key, str) or not (1 <= len(key) <= MAX_LABEL_KEY):
            raise BusinessRuleError("invalid label key", code="invalid_labels")
        if not isinstance(value, str) or len(value) > MAX_LABEL_VALUE:
            raise BusinessRuleError("invalid label value", code="invalid_labels")
    return dict(labels)


def _safe_heartbeat_metrics(metrics: dict | None) -> dict:
    """Keep only bounded operational counters and the opaque inventory hash."""
    raw = metrics if isinstance(metrics, dict) else {}
    safe: dict = {}
    inventory_hash = raw.get("inventory_hash")
    if isinstance(inventory_hash, str) and _INVENTORY_HASH.fullmatch(inventory_hash):
        safe["inventory_hash"] = inventory_hash
    for key in _SAFE_NUMERIC_HEARTBEAT_METRICS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            safe[key] = value
    return safe


def _safe_diagnostic_names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            item
            for item in value[:64]
            if isinstance(item, str) and _DIAGNOSTIC_NAME.fullmatch(item)
        }
    )


def _render_diagnostics(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    rendered: list[dict] = []
    for raw in value[:16]:
        if not isinstance(raw, dict):
            continue
        reason = raw.get("reason_code")
        if reason not in _DIAGNOSTIC_REPAIR_COMMANDS:
            continue
        rendered.append(
            {
                "reason_code": reason,
                "missing_capabilities": _safe_diagnostic_names(
                    raw.get("missing_capabilities")
                ),
                "affected_task_types": _safe_diagnostic_names(
                    raw.get("affected_task_types")
                ),
                "repair_command": _DIAGNOSTIC_REPAIR_COMMANDS[reason],
            }
        )
    return rendered


def _synthetic_diagnostic(reason_code: str) -> dict:
    return {
        "reason_code": reason_code,
        "missing_capabilities": [],
        "affected_task_types": ["all"],
        "repair_command": _DIAGNOSTIC_REPAIR_COMMANDS[reason_code],
    }


def _runtime_operational_view(
    runtime: Runtime, latest_heartbeat: RuntimeHeartbeat | None
) -> tuple[str, list[dict]]:
    # Paused is an administrator-controlled lifecycle state and wins over a
    # stale heartbeat report. Other lifecycle states remain separately exposed
    # in ``status``; this projection answers only whether work is actionable.
    if runtime.status == "paused":
        return "paused", []
    metrics = latest_heartbeat.metrics if latest_heartbeat is not None else {}
    if isinstance(metrics, dict):
        reported_state = metrics.get("operational_state")
        diagnostics = _render_diagnostics(metrics.get("diagnostics"))
        if reported_state == "isolated" and diagnostics:
            return "isolated", diagnostics
        if reported_state == "degraded" and diagnostics:
            return "degraded", diagnostics
        if reported_state == "online" and runtime.status == "online":
            return "online", []
    if runtime.status == "online":
        return "online", []
    if runtime.status == "pending":
        return "degraded", [_synthetic_diagnostic("activation_pending")]
    if runtime.status == "draining":
        return "degraded", [_synthetic_diagnostic("runtime_draining")]
    return "degraded", [_synthetic_diagnostic("heartbeat_unavailable")]


def _render_runtime(
    runtime: Runtime,
    *,
    include_activation: dict | None = None,
    latest_heartbeat: RuntimeHeartbeat | None = None,
) -> dict:
    operational_state, diagnostics = _runtime_operational_view(
        runtime, latest_heartbeat
    )
    data = {
        "id": str(runtime.id),
        "workspace_id": str(runtime.workspace_id),
        "name": runtime.name,
        "kind": runtime.kind,
        "status": runtime.status,
        "operational_state": operational_state,
        "diagnostics": diagnostics,
        "capabilities": runtime.capabilities,
        "labels": runtime.labels,
        "hostname": runtime.hostname,
        "os": runtime.os,
        "cpu_cores": runtime.cpu_cores,
        "memory_mb": runtime.memory_mb,
        "max_concurrent": runtime.max_concurrent,
        "current_load": runtime.current_load,
        "last_heartbeat_at": (
            runtime.last_heartbeat_at.isoformat() if runtime.last_heartbeat_at else None
        ),
        "heartbeat_interval_seconds": runtime.heartbeat_interval_seconds,
        "lease_grace_seconds": runtime.lease_grace_seconds,
        "version": runtime.version,
        "activated_at": runtime.activated_at.isoformat()
        if runtime.activated_at
        else None,
        "created_at": runtime.created_at.isoformat() if runtime.created_at else None,
    }
    if include_activation is not None:
        data["activation"] = include_activation
    return data


def _frozen_budget(config_snapshot: dict | None) -> dict:
    raw = (config_snapshot or {}).get("budget")
    if not isinstance(raw, dict):
        return {}
    budget: dict = {}
    for key in _FROZEN_BUDGET_KEYS:
        value = raw.get(key)
        if key == "max_cost_usd":
            if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value):
                budget[key] = value
        elif (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value >= 0
        ):
            budget[key] = value
    return budget


def _safe_result(result: dict | None) -> dict | None:
    """Project the redacted result schema into a strict public DTO.

    The persisted document already passed server-side secret redaction and
    schema validation. This second allowlist still rejects arbitrary legacy
    keys, prompts, paths and provider session identifiers while retaining the
    fields users need to audit a run: provider/model, usage, redacted summary,
    opaque artifact refs and redaction metadata.
    """
    if not isinstance(result, dict):
        return None
    safe: dict = {}
    version = result.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        safe["schema_version"] = version
    provider = result.get("provider")
    if isinstance(provider, dict):
        safe_provider = {
            key: value
            for key in ("name", "version", "model")
            if isinstance((value := provider.get(key)), str)
            and _AUDIT_LABEL.fullmatch(value)
        }
        # The opaque provider session id is useful internally for diagnosis,
        # but is not a browser/API credential or artifact. Publish presence,
        # never its value.
        safe_provider["session_recorded"] = bool(
            isinstance(provider.get("session_id"), str) and provider["session_id"]
        )
        safe["provider"] = safe_provider

    usage = result.get("usage")
    if isinstance(usage, dict):
        safe_usage: dict = {}
        for key in (
            "input_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
            "output_tokens",
            "total_tokens",
            "turns",
        ):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_usage[key] = value
        cost = usage.get("cost_usd")
        if isinstance(cost, str) and _DECIMAL_AMOUNT.fullmatch(cost):
            safe_usage["cost_usd"] = cost
        if safe_usage:
            safe["usage"] = safe_usage

    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        safe_outcome: dict = {}
        if isinstance(outcome.get("exit_code"), int) and not isinstance(
            outcome.get("exit_code"), bool
        ):
            safe_outcome["exit_code"] = outcome["exit_code"]
        termination = outcome.get("termination")
        if isinstance(termination, str) and _AUDIT_LABEL.fullmatch(termination):
            safe_outcome["termination"] = termination
        summary = outcome.get("summary")
        if isinstance(summary, str):
            # Preserve readable newlines/tabs, replace other controls, and
            # bound the public representation independently of the JSON cap.
            safe_outcome["summary"] = "".join(
                character
                if character in {"\n", "\r", "\t"} or ord(character) >= 32
                else " "
                for character in summary[:8192]
            )
        if safe_outcome:
            safe["outcome"] = safe_outcome

    artifacts = result.get("artifacts")
    if isinstance(artifacts, dict):
        safe_artifacts: dict = {}
        for key in ("checkout_id", "diff_ref"):
            value = artifacts.get(key)
            if value is None:
                safe_artifacts[key] = None
            elif isinstance(value, str) and _AUDIT_REF.fullmatch(value):
                safe_artifacts[key] = value
        if safe_artifacts:
            safe["artifacts"] = safe_artifacts

    redaction = result.get("redaction")
    if isinstance(redaction, dict):
        safe_redaction: dict = {}
        rule_version = redaction.get("rule_version")
        if isinstance(rule_version, str) and _AUDIT_LABEL.fullmatch(rule_version):
            safe_redaction["rule_version"] = rule_version
        hit_count = redaction.get("hit_count")
        if (
            isinstance(hit_count, int)
            and not isinstance(hit_count, bool)
            and hit_count >= 0
        ):
            safe_redaction["hit_count"] = hit_count
        if safe_redaction:
            safe["redaction"] = safe_redaction
    return safe or None


def _actual_usage(attempt: ExecutionAttempt) -> dict | None:
    values = (
        attempt.prompt_tokens,
        attempt.completion_tokens,
        attempt.cache_tokens,
        attempt.cost_usd,
        attempt.num_turns,
    )
    if all(value is None for value in values):
        return None
    prompt = attempt.prompt_tokens or 0
    completion = attempt.completion_tokens or 0
    cache = attempt.cache_tokens or 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_tokens": cache,
        "total_tokens": prompt + completion + cache,
        "cost_usd": format(attempt.cost_usd, "f")
        if attempt.cost_usd is not None
        else None,
        "turns": attempt.num_turns,
    }


def _safe_audit_label(value: str | None) -> str | None:
    return value if isinstance(value, str) and _AUDIT_LABEL.fullmatch(value) else None


def _safe_approval_request(action_summary: dict | None) -> dict:
    summary = action_summary if isinstance(action_summary, dict) else {}
    action = _safe_audit_label(summary.get("action")) or "unknown"
    params = summary.get("params")
    fields: dict = {}
    if isinstance(params, dict):
        for key in sorted(_APPROVAL_FIELD_KEYS):
            value = params.get(key)
            if isinstance(value, bool) or isinstance(value, (int, float)):
                fields[key] = value
            elif not isinstance(value, str):
                continue
            elif key == "repository" and _REPOSITORY_LABEL.fullmatch(value):
                fields[key] = value
            elif key == "branch" and _BRANCH_LABEL.fullmatch(value):
                fields[key] = value
            elif key not in {"repository", "branch"} and _AUDIT_LABEL.fullmatch(value):
                fields[key] = value
    return {"action": action, "fields": fields}


def _source_attempt_for_approval(
    approval: Approval, attempts: list[ExecutionAttempt]
) -> ExecutionAttempt | None:
    candidates = [
        attempt
        for attempt in attempts
        if attempt.failure_reason == "awaiting_approval"
        and attempt.finished_at is not None
    ]
    if not candidates:
        return None
    before = [
        attempt
        for attempt in candidates
        if attempt.finished_at <= approval.requested_at
    ]
    return max(before or candidates, key=lambda attempt: attempt.finished_at)


def _approval_audits(
    approvals: list[Approval], attempts: list[ExecutionAttempt]
) -> tuple[list[dict], dict[uuid.UUID, list[dict]]]:
    audits: list[dict] = []
    timeline_events: dict[uuid.UUID, list[dict]] = {}
    for approval in approvals:
        source = _source_attempt_for_approval(approval, attempts)
        request = _safe_approval_request(approval.action_summary)
        next_attempt = None
        if source is not None:
            next_attempt = next(
                (
                    attempt
                    for attempt in attempts
                    if attempt.attempt_number > source.attempt_number
                ),
                None,
            )
            events = timeline_events.setdefault(source.id, [])
            events.append(
                {"event": "approval_requested", "at": approval.requested_at.isoformat()}
            )
            if approval.decided_at is not None:
                events.append(
                    {
                        "event": f"approval_{approval.status}",
                        "at": approval.decided_at.isoformat(),
                    }
                )
            if (
                next_attempt is not None
                and approval.status == "approved"
                and approval.decided_at is not None
            ):
                timeline_events.setdefault(next_attempt.id, []).append(
                    {
                        "event": "requeued",
                        "at": approval.decided_at.isoformat(),
                        "reason_code": "awaiting_approval",
                    }
                )
        result = None
        if next_attempt is not None and next_attempt.status in {
            "completed",
            "failed",
            "timeout",
            "cancelled",
            "reclaimed",
        }:
            outcome = (
                next_attempt.result.get("outcome")
                if isinstance(next_attempt.result, dict)
                else {}
            )
            termination = (
                outcome.get("termination") if isinstance(outcome, dict) else None
            )
            result = {
                "attempt_id": str(next_attempt.id),
                "status": next_attempt.status,
                "termination": _safe_audit_label(termination),
            }
        decision = {
            "status": approval.status,
            "decided_by_member_id": (
                str(approval.decided_by_member_id)
                if approval.decided_by_member_id
                else None
            ),
            "decided_at": approval.decided_at.isoformat()
            if approval.decided_at
            else None,
        }
        audits.append(
            {
                "id": str(approval.id),
                "source_attempt_id": str(source.id) if source else None,
                "request": request,
                "requested_by_member_id": str(approval.requested_by_member_id),
                "requested_at": approval.requested_at.isoformat(),
                "decision": decision,
                # A decision authorizes a transition; it is not itself a
                # separately persisted capability grant. Never copy the
                # request and present it as grant evidence.
                "grant": None,
                "result": result,
            }
        )
    return audits, timeline_events


def _render_attempt(
    attempt: ExecutionAttempt,
    runtime_name: str | None = None,
    *,
    frozen_budget: dict | None = None,
    approval_events: list[dict] | None = None,
) -> dict:
    timeline: list[dict] = []
    if attempt.claimed_at is not None:
        timeline.append({"event": "claimed", "at": attempt.claimed_at.isoformat()})
    if attempt.started_at is not None:
        timeline.append({"event": "running", "at": attempt.started_at.isoformat()})
    timeline.extend(approval_events or [])
    if attempt.finished_at is not None:
        timeline.append(
            {
                "event": "terminal",
                "at": attempt.finished_at.isoformat(),
                "status": attempt.status,
                "reason_code": attempt.failure_reason,
            }
        )
    # Approval decisions can happen after the source attempt has already
    # become terminal. Preserve a true chronology instead of grouping event
    # kinds, while Python's stable sort keeps requeue→claim ties deterministic.
    timeline.sort(key=lambda event: event["at"])
    return {
        "id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "runtime_id": str(attempt.runtime_id) if attempt.runtime_id else None,
        "runtime_name": runtime_name,
        "status": attempt.status,
        "lease_seq": attempt.lease_seq,
        "claimed_at": attempt.claimed_at.isoformat() if attempt.claimed_at else None,
        "started_at": attempt.started_at.isoformat() if attempt.started_at else None,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
        "working_branch": attempt.working_branch,
        "failure_reason": attempt.failure_reason,
        "provider": _safe_audit_label(attempt.provider),
        "provider_version": _safe_audit_label(attempt.provider_version),
        "model": _safe_audit_label(attempt.model),
        "actual_usage": _actual_usage(attempt),
        "frozen_budget": frozen_budget or {},
        "timeline": timeline,
        "redaction_hits": attempt.redaction_hits or 0,
        "redacted": bool(attempt.redaction_hits),
        "security_alert": "result_redacted" if attempt.redaction_hits else None,
        "result": _safe_result(attempt.result),
    }


def _render_execution(
    execution: TaskExecution, attempts: list[dict] | None = None
) -> dict:
    budget = _frozen_budget(execution.config_snapshot)
    data = {
        "id": str(execution.id),
        "workspace_id": str(execution.workspace_id),
        "agent_id": str(execution.agent_id) if execution.agent_id else None,
        "issue_id": str(execution.issue_id) if execution.issue_id else None,
        "trigger": execution.trigger,
        "status": execution.status,
        "priority": execution.priority,
        "label_requirements": execution.label_requirements,
        "required_capabilities": execution.required_capabilities,
        "frozen_budget": budget,
        "max_attempts": execution.max_attempts,
        "queued_at": execution.queued_at.isoformat() if execution.queued_at else None,
        "finished_at": execution.finished_at.isoformat()
        if execution.finished_at
        else None,
        "timeout_seconds": execution.timeout_seconds,
        "failure_reason": execution.failure_reason,
        "result": _safe_result(execution.result),
        "cancel_requested_at": (
            execution.cancel_requested_at.isoformat()
            if execution.cancel_requested_at
            else None
        ),
    }
    if attempts is not None:
        data["attempts"] = attempts
    return data


class RuntimeService:
    """Stateless orchestrator bound to a session factory (house pattern)."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ):
        self._sf = session_factory
        self._settings = settings

    # -- registration & activation -------------------------------------------

    async def create_runtime(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        name: str,
        kind: str = "self_hosted",
        labels: dict | None = None,
        max_concurrent: int = 1,
    ) -> dict:
        code = generate_activation_code()
        now = _now()
        activation = {
            "code": code,
            "expires_at": (now + self._settings.runtime_activation_ttl).isoformat(),
            "release": {
                "version": self._settings.runtime_release_version,
                "artifact_url": self._settings.runtime_release_artifact_url,
                "sha256": self._settings.runtime_release_sha256,
                "signature_url": self._settings.runtime_release_signature_url,
                "signing_key_url": self._settings.runtime_release_signing_key_url,
            },
            "activate_hint": (
                "mesh-runtime activate --activation-file ./activation.txt   "
                "# activation code via restricted file/stdin, never a CLI argument"
            ),
        }
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = Runtime(
                workspace_id=workspace_id,
                name=name.strip(),
                kind=kind,
                status="pending",
                labels=_validate_labels(labels),
                max_concurrent=max_concurrent,
                activation_token_hash=hash_activation_code(code),
                activation_expires_at=now + self._settings.runtime_activation_ttl,
                created_by=member.id,
            )
            session.add(runtime)
            await session.flush()
            return _render_runtime(runtime, include_activation=activation)

    async def activate_runtime(
        self,
        *,
        activation_code: str,
        metadata: dict,
        protocol_version: int = 1,
        provider_manifest: dict | None = None,
        daemon_features: dict | None = None,
    ) -> dict:
        """Daemon: exchange the one-time code for the long-lived runtime token.

        Plaintext token appears ONLY in this response; the server stores the
        hash. Expired / already-used codes → 410 (create a fresh runtime).
        """
        code_hash = hash_activation_code(activation_code)
        async with self._sf() as session, session.begin():
            # SECURITY DEFINER bootstrap read (workspace unknown until here).
            from sqlalchemy import text

            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, workspace_id, status, activation_expires_at, "
                            "activated_at, deleted_at "
                            "FROM mesh_runtime_by_activation_hash(:h)"
                        ),
                        {"h": code_hash},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise UnauthorizedError("invalid activation code")
            if row["deleted_at"] is not None:
                raise GoneError("activation expired", code="activation_expired")
            now = _now()
            if row["activated_at"] is not None or (
                row["activation_expires_at"] is not None
                and row["activation_expires_at"] < now
            ):
                raise GoneError("activation expired", code="activation_expired")

            await set_tenant_context(session, row["workspace_id"])
            runtime = await session.get(Runtime, row["id"])
            if runtime is None or runtime.status != "pending":
                raise GoneError("activation expired", code="activation_expired")

            meta = metadata or {}
            capabilities = meta.get("capabilities") or []
            if not isinstance(capabilities, list):
                capabilities = []
            capabilities = [str(c) for c in capabilities if isinstance(c, str)][
                :MAX_CAPABILITIES
            ]
            labels = _validate_labels(meta.get("labels") or {})
            merged_labels = {**runtime.labels, **labels}

            runtime.hostname = str(meta.get("hostname") or runtime.hostname)
            runtime.os = str(meta.get("os") or runtime.os)
            runtime.cpu_cores = (
                _as_positive_int(meta.get("cpu_cores")) or runtime.cpu_cores
            )
            runtime.memory_mb = (
                _as_positive_int(meta.get("memory_mb")) or runtime.memory_mb
            )
            runtime.capabilities = capabilities
            runtime.labels = merged_labels
            runtime.version = str(meta.get("version") or runtime.version)
            # §2.6 P0: persist protocol negotiation fields.
            runtime.protocol_version = protocol_version
            runtime.daemon_version = str(meta.get("version") or runtime.version)
            runtime.provider_manifest = provider_manifest or {}
            runtime.daemon_features = daemon_features or {}
            runtime.status = "online"
            runtime.activated_at = now  # non-null = code consumed (replay → 410)
            runtime.last_heartbeat_at = now
            # The hash stays (used codes resolve to a 410, §5.1; plaintext is
            # never stored and the row can no longer be activated).
            runtime.updated_at = now

            # §2.4 S-11: issue the long-lived daemon token — hash stored
            # ONLY in runtimes.runtime_token_hash (single source of truth).
            # No api_tokens row is created (R2-H2: runtime is not a roster
            # member; owner_member_id NOT NULL cannot host it).
            if runtime.created_by is None:
                raise BusinessRuleError(
                    "runtime has no registered owner for token issuance",
                    code="runtime_owner_missing",
                )
            plaintext = RUNTIME_TOKEN_PREFIX + secrets.token_urlsafe(32)
            runtime.runtime_token_hash = hashlib.sha256(
                plaintext.encode("utf-8")
            ).hexdigest()

            await emit_realtime(
                session,
                workspace_id=runtime.workspace_id,
                channel=f"workspace:{runtime.workspace_id}:runtimes",
                event="runtime.activated",
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:activated",
            )
            await emit_realtime(
                session,
                workspace_id=runtime.workspace_id,
                channel=f"workspace:{runtime.workspace_id}:runtimes",
                event="runtime.online",
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:online:{now.isoformat()}",
            )
            return {
                "runtime_id": str(runtime.id),
                "runtime_token": plaintext,
                "heartbeat_interval_seconds": runtime.heartbeat_interval_seconds,
            }

    # -- heartbeat ------------------------------------------------------------

    async def heartbeat(
        self,
        *,
        runtime: Runtime,
        current_load: int,
        health: str,
        metrics: dict,
        inflight: list[str],
        protocol_version: int | None = None,
        context_progress: list[dict] | None = None,
        operational_state: str | None = None,
        diagnostics: list[dict] | None = None,
    ) -> dict:
        # F8: daemon-reported inflight is DIAGNOSTIC (server-side attempt
        # rows stay the capacity authority) — but validated and persisted in
        # the heartbeat detail row for drift auditing, never ignored.
        for entry in inflight:
            try:
                uuid.UUID(str(entry))
            except (ValueError, TypeError):
                raise ValidationError(
                    "inflight entries must be attempt UUIDs",
                    code="invalid_request",
                    details={"inflight": entry},
                ) from None
        now = _now()
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, runtime.workspace_id)
            row = await session.get(Runtime, runtime.id)
            if row is None or row.deleted_at is not None:
                raise UnauthorizedError("invalid runtime token")
            previous = row.status
            row.last_heartbeat_at = now
            row.updated_at = now
            # §2.6 P0: track protocol version drift on heartbeat.
            if protocol_version is not None:
                row.protocol_version = protocol_version
            reported_state = operational_state or (
                "degraded" if health == "degraded" else "online"
            )
            if health == "healthy":
                reported_state = "online"
            elif reported_state not in {"degraded", "isolated"}:
                reported_state = "degraded"
            rendered_diagnostics = _render_diagnostics(diagnostics or [])
            if reported_state != "online" and not rendered_diagnostics:
                rendered_diagnostics = [_synthetic_diagnostic("heartbeat_unavailable")]
            if reported_state in {"degraded", "isolated"}:
                # Alive process, broken environment: stop dispatch, keep the
                # troubleshooting window (§5.1).
                row.status = "unavailable"
                event = (
                    "runtime.isolated"
                    if reported_state == "isolated"
                    else "runtime.degraded"
                )
                await emit_realtime(
                    session,
                    workspace_id=row.workspace_id,
                    channel=f"workspace:{row.workspace_id}:runtimes",
                    event=event,
                    data={"runtime_id": str(row.id), "name": row.name},
                    idempotency_key=f"runtime:{row.id}:{reported_state}:{now.isoformat()}",
                )
            elif previous == "unavailable":
                row.status = "online"
                await emit_realtime(
                    session,
                    workspace_id=row.workspace_id,
                    channel=f"workspace:{row.workspace_id}:runtimes",
                    event="runtime.online",
                    data={"runtime_id": str(row.id), "name": row.name},
                    idempotency_key=f"runtime:{row.id}:online:{now.isoformat()}",
                )
            session.add(
                RuntimeHeartbeat(
                    workspace_id=row.workspace_id,
                    runtime_id=row.id,
                    current_load=current_load,
                    metrics={
                        **_safe_heartbeat_metrics(metrics),
                        "inflight_reported": len(inflight),
                        "operational_state": reported_state,
                        "diagnostics": rendered_diagnostics,
                    },
                    health=health,
                )
            )

            # MES-82: best-effort context-injection receipts (runtime.md
            # 「运行期上下文追加」). A lost/mis-fenced report only widens the
            # duplicate window — it must NEVER fail the heartbeat, so it runs in
            # a SAVEPOINT and any unexpected error is logged and swallowed.
            if context_progress:
                try:
                    async with session.begin_nested():
                        await ack_context_progress(
                            session,
                            workspace_id=row.workspace_id,
                            runtime_id=row.id,
                            entries=context_progress,
                        )
                except Exception:  # noqa: BLE001 — best-effort by contract
                    logger.exception(
                        "context_progress ack failed for runtime %s; heartbeat continues",
                        row.id,
                    )

            # Downlink commands ride the heartbeat response (§4.8: ≤15s).
            commands: list[dict] = []
            cancelling = (
                (
                    await session.execute(
                        select(ExecutionAttempt)
                        .where(
                            ExecutionAttempt.runtime_id == row.id,
                            ExecutionAttempt.status == "cancelling",
                        )
                        .order_by(ExecutionAttempt.updated_at.desc())
                        .limit(20)
                    )
                )
                .scalars()
                .all()
            )
            for attempt in cancelling:
                commands.append(
                    {
                        "type": "cancel_execution",
                        "execution_id": str(attempt.execution_id),
                        "attempt_id": str(attempt.id),
                        "grace_seconds": 15,
                    }
                )

            # MES-82: inject_context downlink for in-flight attempts whose
            # execution has new append rows beyond the server watermark
            # (from_seq = server watermark, never the daemon-reported value).
            in_flight = (
                (
                    await session.execute(
                        select(ExecutionAttempt).where(
                            ExecutionAttempt.workspace_id == row.workspace_id,
                            or_(
                                ExecutionAttempt.runtime_id == row.id,
                                ExecutionAttempt.claimed_by_runtime_id == row.id,
                            ),
                            ExecutionAttempt.status.in_(["claimed", "running"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            commands.extend(
                await compute_inject_commands(
                    session,
                    workspace_id=row.workspace_id,
                    runtime_id=row.id,
                    attempt_rows=in_flight,
                )
            )
            return {
                "server_time": now.isoformat(),
                "commands": commands,
                "operational_state": reported_state,
            }

    # -- listing / detail ------------------------------------------------------

    async def list_runtimes(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str | None = None,
        kind: str | None = None,
        labels: dict | None = None,
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict:
        limit = max(1, min(limit, 200))
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(Runtime).where(
                Runtime.workspace_id == workspace_id, Runtime.deleted_at.is_(None)
            )
            if status:
                stmt = stmt.where(Runtime.status == status)
            if kind:
                stmt = stmt.where(Runtime.kind == kind)
            if labels:
                # H3 (§3.1): runtime must carry ALL requested labels (JSONB
                # containment — the same operator claim matching uses).
                stmt = stmt.where(Runtime.labels.op("@>")(labels))
            if search:
                # Escape LIKE metacharacters (review L1): user search terms
                # must match literally, not as wildcards.
                escaped = (
                    search.strip()
                    .replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                stmt = stmt.where(Runtime.name.ilike(f"%{escaped}%", escape="\\"))
            if cursor:
                decoded = _decode_cursor(cursor)
                if decoded is not None:
                    stmt = stmt.where(
                        or_(
                            Runtime.created_at < decoded["created_at"],
                            (Runtime.created_at == decoded["created_at"])
                            & (Runtime.id < decoded["id"]),
                        )
                    )
            stmt = stmt.order_by(Runtime.created_at.desc(), Runtime.id.desc()).limit(
                limit + 1
            )
            rows = (await session.execute(stmt)).scalars().all()
            has_more = len(rows) > limit
            items = rows[:limit]
            latest_heartbeats: dict[uuid.UUID, RuntimeHeartbeat] = {}
            runtime_ids = [item.id for item in items]
            if runtime_ids:
                heartbeat_rows = (
                    (
                        await session.execute(
                            select(RuntimeHeartbeat)
                            .where(RuntimeHeartbeat.runtime_id.in_(runtime_ids))
                            .order_by(
                                RuntimeHeartbeat.runtime_id,
                                RuntimeHeartbeat.created_at.desc(),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for heartbeat in heartbeat_rows:
                    latest_heartbeats.setdefault(heartbeat.runtime_id, heartbeat)
            next_cursor = _encode_cursor(items[-1]) if has_more and items else None
            return {
                "data": [
                    _render_runtime(r, latest_heartbeat=latest_heartbeats.get(r.id))
                    for r in items
                ],
                "next_cursor": next_cursor,
            }

    async def get_runtime(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            heartbeats = (
                (
                    await session.execute(
                        select(RuntimeHeartbeat)
                        .where(RuntimeHeartbeat.runtime_id == runtime_id)
                        .order_by(RuntimeHeartbeat.created_at.desc())
                        .limit(120)
                    )
                )
                .scalars()
                .all()
            )
            latest_heartbeat = heartbeats[0] if heartbeats else None
            data = _render_runtime(runtime, latest_heartbeat=latest_heartbeat)
            data["recent_heartbeats"] = [
                {
                    "created_at": h.created_at.isoformat(),
                    "health": h.health,
                    "current_load": h.current_load,
                    "metrics": _safe_heartbeat_metrics(h.metrics),
                }
                for h in heartbeats
            ]
            return data

    async def patch_runtime(
        self,
        *,
        workspace_id: uuid.UUID,
        runtime_id: uuid.UUID,
        name: str | None,
        labels: dict | None,
        max_concurrent: int | None,
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            if name is not None:
                runtime.name = name.strip()
            if labels is not None:
                runtime.labels = _validate_labels(labels)
            if max_concurrent is not None:
                runtime.max_concurrent = max_concurrent
            runtime.updated_at = _now()
            await session.flush()
            return _render_runtime(runtime)

    async def pause_runtime(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        return await self._lifecycle(workspace_id, runtime_id, target="paused")

    async def resume_runtime(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        return await self._lifecycle(workspace_id, runtime_id, target="online")

    async def decommission_runtime(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            runtime.status = "decommissioned"
            runtime.deleted_at = _now()
            runtime.updated_at = _now()
            await self._revoke_runtime_token(session, runtime)
            return _render_runtime(runtime)

    async def rotate_runtime_token(
        self, *, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> dict:
        """Issue a new daemon token; plaintext shown ONLY here; old hash
        overwritten (§2.4 S-11: single source — no api_tokens row)."""
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            if runtime.created_by is None:
                raise BusinessRuleError(
                    "runtime has no registered owner for token issuance",
                    code="runtime_owner_missing",
                )
            # §2.4 S-11: overwrite the hash in-place; the old token
            # immediately gets 401 on next daemon_auth lookup.
            plaintext = RUNTIME_TOKEN_PREFIX + secrets.token_urlsafe(32)
            runtime.runtime_token_hash = hashlib.sha256(
                plaintext.encode("utf-8")
            ).hexdigest()
            runtime.updated_at = _now()
            return {
                "runtime_id": str(runtime.id),
                "runtime_token": plaintext,
                "prefix": plaintext[:DISPLAY_PREFIX_LEN],
            }

    async def _lifecycle(
        self, workspace_id: uuid.UUID, runtime_id: uuid.UUID, *, target: str
    ) -> dict:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            runtime = await self._get_runtime_row(session, workspace_id, runtime_id)
            runtime.status = target
            runtime.updated_at = _now()
            if target in _TOKEN_REVOKING_STATUSES:
                await self._revoke_runtime_token(session, runtime)
            event = "runtime.paused" if target == "paused" else "runtime.online"
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:runtimes",
                event=event,
                data={"runtime_id": str(runtime.id), "name": runtime.name},
                idempotency_key=f"runtime:{runtime.id}:{target}:{_now().isoformat()}",
            )
            return _render_runtime(runtime)

    async def _revoke_runtime_token(
        self, session: AsyncSession, runtime: Runtime
    ) -> None:
        """§2.4 S-11: clear the hash — the old token immediately gets 401.

        No api_tokens row to revoke (single source of truth).
        """
        runtime.runtime_token_hash = None

    async def _get_runtime_row(
        self, session: AsyncSession, workspace_id: uuid.UUID, runtime_id: uuid.UUID
    ) -> Runtime:
        runtime = (
            await session.execute(
                select(Runtime).where(
                    Runtime.id == runtime_id,
                    Runtime.workspace_id == workspace_id,
                    Runtime.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if runtime is None:
            raise NotFoundError(_RUNTIME_NOT_FOUND)
        return runtime

    # -- executions observability ----------------------------------------------

    async def _assert_issue_visible(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        viewer: Member,
    ) -> None:
        issue = (
            await session.execute(
                select(Issue).where(
                    Issue.id == issue_id,
                    Issue.workspace_id == workspace_id,
                    Issue.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if issue is None:
            raise NotFoundError("issue not found")
        await IssueService(self._sf).assert_can_view_issue(
            session, viewer=viewer, issue=issue
        )

    def _execution_visibility_clause(self, *, workspace_id: uuid.UUID, viewer: Member):
        issue_visibility = IssueService(self._sf)._base_visibility_clause(
            viewer, workspace_id
        )
        if issue_visibility is None:
            return None
        visible_issue_ids = select(Issue.id).where(
            Issue.workspace_id == workspace_id,
            Issue.deleted_at.is_(None),
            issue_visibility,
        )
        # Executions without an issue are workspace-scoped operational data;
        # issue-bound rows inherit the issue/project read boundary.
        return or_(
            TaskExecution.issue_id.is_(None),
            TaskExecution.issue_id.in_(visible_issue_ids),
        )

    async def _assert_execution_visible(
        self, session: AsyncSession, *, execution: TaskExecution, viewer: Member | None
    ) -> None:
        if viewer is None or execution.issue_id is None:
            return
        await self._assert_issue_visible(
            session,
            workspace_id=execution.workspace_id,
            issue_id=execution.issue_id,
            viewer=viewer,
        )

    async def _render_execution_rows(
        self, session: AsyncSession, executions: list[TaskExecution]
    ) -> list[dict]:
        if not executions:
            return []
        execution_ids = [execution.id for execution in executions]
        attempts = (
            (
                await session.execute(
                    select(ExecutionAttempt)
                    .where(ExecutionAttempt.execution_id.in_(execution_ids))
                    .order_by(
                        ExecutionAttempt.execution_id,
                        ExecutionAttempt.attempt_number.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        approvals = (
            (
                await session.execute(
                    select(Approval)
                    .where(
                        Approval.subject_type == "tool_call",
                        Approval.subject_execution_id.in_(execution_ids),
                    )
                    .order_by(Approval.requested_at.asc(), Approval.id.asc())
                )
            )
            .scalars()
            .all()
        )
        requeue_events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == executions[0].workspace_id,
                        OutboxEvent.event_type == "realtime.publish",
                        OutboxEvent.payload["event"].astext == "execution.requeued",
                        OutboxEvent.payload["data"]["execution_id"].astext.in_(
                            [str(execution_id) for execution_id in execution_ids]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        requeue_by_reclaimed_attempt: dict[str, dict] = {}
        for event in requeue_events:
            data = (
                event.payload.get("data") if isinstance(event.payload, dict) else None
            )
            reclaimed_attempt = (
                data.get("reclaimed_attempt") if isinstance(data, dict) else None
            )
            if isinstance(reclaimed_attempt, str):
                requeue_by_reclaimed_attempt[reclaimed_attempt] = {
                    "event": "requeued",
                    "at": event.created_at.isoformat(),
                    "reason_code": "lease_expired",
                }
        output_review_rows = (
            (
                await session.execute(
                    select(IssueActivity)
                    .where(
                        IssueActivity.workspace_id == executions[0].workspace_id,
                        IssueActivity.field == "execution_output_review",
                        IssueActivity.new_value["execution_id"].astext.in_(
                            [str(execution_id) for execution_id in execution_ids]
                        ),
                    )
                    .order_by(IssueActivity.created_at.desc(), IssueActivity.id.desc())
                )
            )
            .scalars()
            .all()
        )
        output_review_by_execution: dict[str, dict] = {}
        for review in output_review_rows:
            value = review.new_value if isinstance(review.new_value, dict) else {}
            execution_id = value.get("execution_id")
            decision = value.get("decision")
            if (
                isinstance(execution_id, str)
                and execution_id not in output_review_by_execution
                and decision in {"approved", "rejected"}
            ):
                output_review_by_execution[execution_id] = {
                    "decision": decision,
                    "decided_by_member_id": (
                        str(review.actor_member_id) if review.actor_member_id else None
                    ),
                    "decided_at": review.created_at.isoformat(),
                }
        runtime_ids = {attempt.runtime_id for attempt in attempts if attempt.runtime_id}
        runtime_names = (
            dict(
                (
                    await session.execute(
                        select(Runtime.id, Runtime.name).where(
                            Runtime.id.in_(runtime_ids)
                        )
                    )
                ).all()
            )
            if runtime_ids
            else {}
        )
        attempts_by_execution: dict[uuid.UUID, list[ExecutionAttempt]] = {}
        for attempt in attempts:
            attempts_by_execution.setdefault(attempt.execution_id, []).append(attempt)
        approvals_by_execution: dict[uuid.UUID, list[Approval]] = {}
        for approval in approvals:
            if approval.subject_execution_id is not None:
                approvals_by_execution.setdefault(
                    approval.subject_execution_id, []
                ).append(approval)

        rendered: list[dict] = []
        for execution in executions:
            execution_attempts = attempts_by_execution.get(execution.id, [])
            approval_audits, approval_events = _approval_audits(
                approvals_by_execution.get(execution.id, []), execution_attempts
            )
            budget = _frozen_budget(execution.config_snapshot)
            rendered_attempts = [
                _render_attempt(
                    attempt,
                    runtime_names.get(attempt.runtime_id)
                    if attempt.runtime_id
                    else None,
                    frozen_budget=budget,
                    approval_events=[
                        *(
                            [
                                requeue_by_reclaimed_attempt[
                                    str(execution_attempts[index - 1].id)
                                ]
                            ]
                            if index > 0
                            and str(execution_attempts[index - 1].id)
                            in requeue_by_reclaimed_attempt
                            else []
                        ),
                        *(approval_events.get(attempt.id) or []),
                    ],
                )
                for index, attempt in enumerate(execution_attempts)
            ]
            item = _render_execution(execution, attempts=rendered_attempts)
            item["retry_count"] = max(len(execution_attempts) - 1, 0)
            item["approval_audits"] = approval_audits
            item["output_review"] = output_review_by_execution.get(str(execution.id))
            rendered.append(item)
        return rendered

    async def list_executions(
        self,
        *,
        workspace_id: uuid.UUID,
        runtime_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        issue_id: uuid.UUID | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        viewer: Member | None = None,
    ) -> dict:
        limit = max(1, min(limit, 200))
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(TaskExecution).where(
                TaskExecution.workspace_id == workspace_id
            )
            if viewer is not None:
                visibility = self._execution_visibility_clause(
                    workspace_id=workspace_id, viewer=viewer
                )
                if visibility is not None:
                    stmt = stmt.where(visibility)
            if runtime_id is not None:
                stmt = stmt.where(
                    TaskExecution.id.in_(
                        select(ExecutionAttempt.execution_id).where(
                            ExecutionAttempt.runtime_id == runtime_id
                        )
                    )
                )
            if agent_id is not None:
                stmt = stmt.where(TaskExecution.agent_id == agent_id)
            if issue_id is not None:
                if viewer is not None:
                    await self._assert_issue_visible(
                        session,
                        workspace_id=workspace_id,
                        issue_id=issue_id,
                        viewer=viewer,
                    )
                stmt = stmt.where(TaskExecution.issue_id == issue_id)
            if status:
                stmt = stmt.where(TaskExecution.status == status)
            if cursor:
                decoded = _decode_cursor(cursor)
                if decoded is not None:
                    stmt = stmt.where(
                        or_(
                            TaskExecution.queued_at < decoded["created_at"],
                            (TaskExecution.queued_at == decoded["created_at"])
                            & (TaskExecution.id < decoded["id"]),
                        )
                    )
            stmt = stmt.order_by(
                TaskExecution.queued_at.desc(), TaskExecution.id.desc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()
            has_more = len(rows) > limit
            items = rows[:limit]
            next_cursor = (
                _encode_cursor_execution(items[-1]) if has_more and items else None
            )
            return {
                "data": await self._render_execution_rows(session, items),
                "next_cursor": next_cursor,
            }

    async def get_execution(
        self,
        *,
        workspace_id: uuid.UUID,
        execution_id: uuid.UUID,
        viewer: Member | None = None,
    ) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            execution = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.id == execution_id,
                        TaskExecution.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
            if execution is None:
                raise NotFoundError(_EXECUTION_NOT_FOUND)
            await self._assert_execution_visible(
                session, execution=execution, viewer=viewer
            )
            data = (await self._render_execution_rows(session, [execution]))[0]
            attempts = (
                (
                    await session.execute(
                        select(ExecutionAttempt)
                        .where(ExecutionAttempt.execution_id == execution_id)
                        .order_by(ExecutionAttempt.attempt_number.asc())
                    )
                )
                .scalars()
                .all()
            )
            # Credential metadata — values are NEVER rendered (*** forever).
            if attempts:
                creds = (
                    await session.execute(
                        select(ExecutionCredential, RuntimeCredential)
                        .join(
                            RuntimeCredential,
                            RuntimeCredential.id == ExecutionCredential.credential_id,
                        )
                        .where(
                            ExecutionCredential.attempt_id.in_([a.id for a in attempts])
                        )
                    )
                ).all()
                data["credentials"] = [
                    {
                        "id": str(ec.credential_id),
                        "name": rc.name,
                        "kind": rc.kind,
                        "attempt_id": str(ec.attempt_id),
                        "injected_at": ec.injected_at.isoformat(),
                        "revoked_at": ec.revoked_at.isoformat()
                        if ec.revoked_at
                        else None,
                        "value": "***",
                    }
                    for ec, rc in creds
                ]
                checkout = (
                    await session.execute(
                        select(RepoCheckout).where(
                            RepoCheckout.attempt_id == attempts[-1].id
                        )
                    )
                ).scalar_one_or_none()
                if checkout is not None:
                    data["checkout"] = {
                        "repo_url": checkout.repo_url,
                        "base_ref": checkout.base_ref,
                        "working_branch": checkout.working_branch,
                        "commit_sha": checkout.commit_sha,
                        "status": checkout.status,
                        "diff_ref": checkout.diff_ref,
                    }
            return data

    # -- credentials ------------------------------------------------------------

    async def create_credential(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        kind: str,
        scope: str,
        value: str,
        env_name: str | None,
        redact_in_logs: bool,
        expires_in_seconds: int | None,
    ) -> dict:
        if env_name is not None:
            # NEW-M1: reserved loader/runtime names are rejected at the gate.
            from mesh.runtime.daemon_auth import validate_env_name

            validate_env_name(env_name)
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            credential = RuntimeCredential(
                workspace_id=workspace_id,
                name=name.strip(),
                kind=kind,
                scope=scope or "execution",
                encrypted_value=encrypt_credential_value(
                    value, self._settings.jwt_secret
                ),
                env_name=env_name,
                redact_in_logs=redact_in_logs,
                expires_at=(
                    _now() + timedelta(seconds=expires_in_seconds)
                    if expires_in_seconds
                    else None
                ),
            )
            session.add(credential)
            await session.flush()
            return self._render_credential(credential)

    async def list_credentials(self, *, workspace_id: uuid.UUID) -> dict:
        async with self._sf() as session:
            await set_tenant_context(session, workspace_id)
            rows = (
                (
                    await session.execute(
                        select(RuntimeCredential)
                        .where(
                            RuntimeCredential.workspace_id == workspace_id,
                            RuntimeCredential.deleted_at.is_(None),
                        )
                        .order_by(RuntimeCredential.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
            return {
                "data": [self._render_credential(c) for c in rows],
                "next_cursor": None,
            }

    async def delete_credential(
        self, *, workspace_id: uuid.UUID, credential_id: uuid.UUID
    ) -> None:
        async with self._sf() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            credential = (
                await session.execute(
                    select(RuntimeCredential).where(
                        RuntimeCredential.id == credential_id,
                        RuntimeCredential.workspace_id == workspace_id,
                        RuntimeCredential.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if credential is None:
                raise NotFoundError(_CREDENTIAL_NOT_FOUND)
            credential.deleted_at = _now()
            credential.updated_at = _now()

    @staticmethod
    def _render_credential(credential: RuntimeCredential) -> dict:
        return {
            "id": str(credential.id),
            "name": credential.name,
            "kind": credential.kind,
            "scope": credential.scope,
            "env_name": credential.env_name,
            "redact_in_logs": credential.redact_in_logs,
            "expires_at": credential.expires_at.isoformat()
            if credential.expires_at
            else None,
            "created_at": credential.created_at.isoformat()
            if credential.created_at
            else None,
            # Plaintext never leaves the server (§6.16).
            "value": "***",
        }


def _as_positive_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


# --- opaque cursors (base64url JSON, house style) ---------------------------

import base64  # noqa: E402
import json  # noqa: E402


def _encode_cursor(runtime: Runtime) -> str:
    payload = {"created_at": runtime.created_at.isoformat(), "id": str(runtime.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _encode_cursor_execution(execution: TaskExecution) -> str:
    payload = {"created_at": execution.queued_at.isoformat(), "id": str(execution.id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_cursor(cursor: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        payload = json.loads(raw)
        return {
            "created_at": datetime.fromisoformat(payload["created_at"]),
            "id": uuid.UUID(payload["id"]),
        }
    except (ValueError, KeyError, TypeError):
        return None
