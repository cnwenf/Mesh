"""Run lifecycle — creation, status transitions, artifacts (autopilot.md §2.3 / §4.4).

Shared by every trigger path (scheduler / event matcher / inbound webhook /
manual test-run) and by the executor. Status transitions are validated
against the §4.4 machine and ALWAYS emit ``autopilot_runs.status_changed``
through the outbox (the unique realtime write path, README §6.6/§6.7) so
list/detail views refresh live (§3.5) and relay redelivery stays idempotent
(per-transition idempotency keys).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from mesh.autopilot.guardrails import autopilot_channel, autopilots_channel
from mesh.db.models.autopilot import (
    Autopilot,
    AutopilotArtifact,
    AutopilotRun,
    AutopilotRunAttempt,
)
from mesh.outbox.service import emit_realtime

# autopilot.md §4.4 run state machine edges.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "waiting_approval", "cancelled", "succeeded", "failed"}),
    "waiting_approval": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "retrying"}),
    "retrying": frozenset({"running", "failed", "cancelled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

# Error classification (autopilot.md §4.4): retryable failures back off and
# retry; non-retryable ones fail the run immediately.
RETRYABLE_ERROR_CODES = frozenset(
    {"timeout", "rate_limited", "executor_busy", "transient", "execution_failed_retryable"}
)


def is_retryable(error: dict[str, Any] | None) -> bool:
    """Whether an error record allows a retry (§4.4 可重试/不可重试)."""
    if not error:
        return False
    if "retryable" in error:
        return bool(error["retryable"])
    return str(error.get("code") or "") in RETRYABLE_ERROR_CODES


async def create_run(
    session: AsyncSession,
    *,
    rule: Autopilot,
    trigger_snapshot: dict[str, Any],
    webhook_event_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    cascade_depth: int = 0,
    triggered_by: uuid.UUID | None = None,
    is_test: bool = False,
    now: datetime | None = None,
) -> AutopilotRun:
    """Insert a ``pending`` run inside the caller's transaction.

    The trigger snapshot is frozen at creation (replayable, §5.1). The
    caller is responsible for having passed the guardrail gate first.
    """
    moment = now if now is not None else datetime.now(UTC)
    run = AutopilotRun(
        workspace_id=rule.workspace_id,
        autopilot_id=rule.id,
        trigger_type=rule.trigger_type,
        trigger_snapshot=trigger_snapshot,
        webhook_event_id=webhook_event_id,
        parent_run_id=parent_run_id,
        cascade_depth=cascade_depth,
        status="pending",
        triggered_by=triggered_by,
        is_test=is_test,
        created_at=moment,
        updated_at=moment,
    )
    session.add(run)
    await session.flush()
    rule.last_run_at = moment
    rule.updated_at = moment
    await emit_run_status_changed(
        session, run=run, old_status=None, new_status="pending", now=moment
    )
    return run


async def emit_run_status_changed(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    old_status: str | None,
    new_status: str,
    now: datetime | None = None,
) -> None:
    """autopilot_runs.status_changed on BOTH the workspace and rule channels."""
    moment = now if now is not None else datetime.now(UTC)
    data = {
        "run_id": str(run.id),
        "autopilot_id": str(run.autopilot_id),
        "old_status": old_status,
        "new_status": new_status,
    }
    idem = f"run:{run.id}:status:{new_status}:{int(moment.timestamp() * 1000)}"
    await emit_realtime(
        session,
        workspace_id=run.workspace_id,
        channel=autopilots_channel(run.workspace_id),
        event="autopilot_runs.status_changed",
        data=data,
        idempotency_key=idem,
    )
    await emit_realtime(
        session,
        workspace_id=run.workspace_id,
        channel=autopilot_channel(run.autopilot_id),
        event="autopilot_runs.status_changed",
        data=data,
        idempotency_key=f"{idem}:rule-channel",
    )


async def transition_run(
    session: AsyncSession,
    run: AutopilotRun,
    new_status: str,
    *,
    error: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> bool:
    """Validate + apply a §4.4 transition; emit the realtime event.

    Returns False (no-op) when the transition is illegal or the run is
    already in the target state — idempotent callers (relay redelivery,
    reconciler re-passes) must never crash on a stale view.
    """
    moment = now if now is not None else datetime.now(UTC)
    old_status = run.status
    if new_status == old_status:
        return False
    if new_status not in _ALLOWED_TRANSITIONS.get(old_status, frozenset()):
        return False
    run.status = new_status
    run.updated_at = moment
    if error is not None:
        run.error = error
    if new_status == "running" and run.started_at is None:
        run.started_at = moment
    if new_status in TERMINAL_STATUSES:
        run.finished_at = moment
        if run.started_at is not None:
            run.duration_ms = max(0, int((moment - run.started_at).total_seconds() * 1000))
    await emit_run_status_changed(
        session, run=run, old_status=old_status, new_status=new_status, now=moment
    )
    return True


async def new_attempt(
    session: AsyncSession,
    run: AutopilotRun,
    *,
    now: datetime | None = None,
) -> AutopilotRunAttempt:
    """Append attempt #(retry_count + 1) — numbers are never reused (§2.4)."""
    moment = now if now is not None else datetime.now(UTC)
    attempt = AutopilotRunAttempt(
        workspace_id=run.workspace_id,
        run_id=run.id,
        attempt_number=run.retry_count + 1,
        status="running",
        started_at=moment,
        created_at=moment,
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def record_artifact(
    session: AsyncSession,
    run: AutopilotRun,
    *,
    artifact_type: str,
    ref_table: str,
    ref_id: uuid.UUID,
    summary: str | None = None,
    now: datetime | None = None,
) -> AutopilotArtifact:
    """Decouple a run product reference (§2.4)."""
    artifact = AutopilotArtifact(
        workspace_id=run.workspace_id,
        run_id=run.id,
        artifact_type=artifact_type,
        ref_table=ref_table,
        ref_id=ref_id,
        summary=summary,
        created_at=now if now is not None else datetime.now(UTC),
    )
    session.add(artifact)
    await session.flush()
    return artifact


__all__ = [
    "TERMINAL_STATUSES",
    "create_run",
    "emit_run_status_changed",
    "is_retryable",
    "new_attempt",
    "record_artifact",
    "transition_run",
]
