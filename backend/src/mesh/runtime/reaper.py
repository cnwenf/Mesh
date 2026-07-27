"""Reaper — lost-runtime self-healing (runtime.md §4.8).

A single worker task sweeps, each row in its OWN transaction so one poison
row never stalls the pass:

A. **Lease expiry** — in-flight attempts past ``lease_expires_at`` become
   ``reclaimed`` (audit preserved), ``lease_seq`` advances (zombie fencing,
   T10), capacity is released idempotently, and the logical execution either
   requeues (new attempt #N+1 — never reusing rows, T4) or fails
   ``max_retries``.
B. **Heartbeat loss** — online runtimes silent past
   ``interval × multiplier`` flip to ``unavailable`` (their in-flight
   attempts are collected by pass A once leases lapse).
C. **Approval expiry** — pending approvals past ``expires_at`` expire; an
   ``awaiting_approval`` execution becomes ``cancelled(approval_expired)``
   (README §6.10).
D. **Heartbeat retention** — prune the detail window.

``awaiting_approval`` needs no special handling here: that state has NO
in-flight attempt (the current attempt was cancelled on entry), so the lease
sweep never touches it — there is no "paused lease → permanent stall" path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.runtime import (
    Approval,
    ExecutionAttempt,
    RuntimeHeartbeat,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime
from mesh.runtime.attempts import _emit_terminal_notification, _release_capacity
from mesh.runtime.credentials import revoke_attempt_envelopes

REAPER_BATCH_SIZE = 50


def _now() -> datetime:
    return datetime.now(UTC)


async def run_reaper_pass(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    heartbeat_timeout_multiplier: int = 3,
    heartbeat_retention: timedelta = timedelta(hours=1),
    batch_size: int = REAPER_BATCH_SIZE,
) -> dict:
    """One sweep across all workspaces; returns counters for observability."""
    counts = {
        "reclaimed": 0,
        "requeued": 0,
        "failed_max_retries": 0,
        "cancelled": 0,
        "offline": 0,
        "approvals_expired": 0,
        "heartbeats_pruned": 0,
    }
    counts["reclaimed"] = await _sweep_expired_leases(session_factory, batch_size, counts)
    counts["offline"] = await _mark_offline_runtimes(
        session_factory, multiplier=heartbeat_timeout_multiplier
    )
    counts["approvals_expired"] = await _expire_approvals(session_factory)
    counts["heartbeats_pruned"] = await _prune_heartbeats(session_factory, heartbeat_retention)
    return counts


async def _candidate_expired_attempts(
    session_factory: async_sessionmaker[AsyncSession], batch_size: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Candidate (attempt_id, workspace_id) pairs — re-validated under the row
    lock in their own transaction (SKIP LOCKED keeps reapers wait-free)."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, workspace_id
                      FROM execution_attempts
                     WHERE status IN ('claimed','running','cancelling')
                       AND lease_expires_at IS NOT NULL
                       AND lease_expires_at < now()
                     ORDER BY lease_expires_at ASC
                     LIMIT :batch
                    """
                ),
                {"batch": batch_size},
            )
        ).all()
    return [(row[0], row[1]) for row in rows]


async def _sweep_expired_leases(
    session_factory: async_sessionmaker[AsyncSession], batch_size: int, counts: dict
) -> int:
    candidates = await _candidate_expired_attempts(session_factory, batch_size)
    reclaimed = 0
    for attempt_id, workspace_id in candidates:
        try:
            outcome = await _reclaim_one(session_factory, attempt_id, workspace_id)
        except Exception:  # noqa: BLE001 — poison row: skip, next pass retries
            continue
        if outcome is None:
            continue
        reclaimed += 1
        if outcome == "requeued":
            counts["requeued"] += 1
        elif outcome == "failed_max_retries":
            counts["failed_max_retries"] += 1
        elif outcome == "cancelled":
            counts["cancelled"] += 1
    return reclaimed


async def _reclaim_one(
    session_factory: async_sessionmaker[AsyncSession],
    attempt_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> str | None:
    """Reclaim a single expired attempt; returns the execution outcome.

    Unified lock order — execution row first, then the attempt row — matching
    transition_attempt / cancel_execution / request_tool_approval so sweeps
    never deadlock against concurrent daemon reports (review M2).
    """
    async with session_factory() as session:
        execution_ref = (
            await session.execute(
                select(ExecutionAttempt.execution_id).where(
                    ExecutionAttempt.id == attempt_id
                )
            )
        ).scalar_one_or_none()
    if execution_ref is None:
        return None
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        locked_exec = (
            await session.execute(
                select(TaskExecution.id)
                .where(TaskExecution.id == execution_ref)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if locked_exec is None:
            return None  # execution locked elsewhere — sweep it next pass
        attempt = (
            await session.execute(
                select(ExecutionAttempt)
                .where(
                    ExecutionAttempt.id == attempt_id,
                    ExecutionAttempt.status.in_(["claimed", "running", "cancelling"]),
                    ExecutionAttempt.lease_expires_at < func.now(),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if attempt is None:
            return None  # someone else reclaimed / it transitioned away

        now = _now()
        attempt.status = "reclaimed"
        attempt.lease_seq = attempt.lease_seq + 1  # zombie fence (T10)
        attempt.finished_at = now
        attempt.failure_reason = "lease_expired"
        attempt.updated_at = now
        await _release_capacity(session, attempt.runtime_id)
        await revoke_attempt_envelopes(session, attempt_id=attempt.id, now=now)

        execution = (
            await session.execute(
                select(TaskExecution)
                .where(TaskExecution.id == attempt.execution_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if execution is None or execution.status in ("completed", "failed", "timeout", "cancelled"):
            return "reclaimed"

        attempt_count = (
            await session.execute(
                select(func.count())
                .select_from(ExecutionAttempt)
                .where(ExecutionAttempt.execution_id == execution.id)
            )
        ).scalar_one()

        if execution.status == "cancelling":
            # The daemon died mid-cancel: finish the cancellation.
            execution.status = "cancelled"
            execution.finished_at = now
            execution.updated_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}",
                event="execution.cancelled",
                data={"execution_id": str(execution.id), "failure_reason": "lease_expired"},
                idempotency_key=f"execution:{execution.id}:cancelled",
            )
            return "cancelled"

        if attempt_count < execution.max_attempts:
            execution.status = "queued"
            execution.updated_at = now
            # Audit-preserving requeue (T4): the old attempt row stays; the
            # next claim creates attempt #N+1.
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}",
                event="execution.requeued",
                data={
                    "execution_id": str(execution.id),
                    "reclaimed_attempt": str(attempt.id),
                    "attempt_count": int(attempt_count),
                },
                idempotency_key=f"reclaim:{attempt.id}:requeued",
            )
            depth = (
                await session.execute(
                    select(func.count())
                    .select_from(TaskExecution)
                    .where(
                        TaskExecution.workspace_id == workspace_id,
                        TaskExecution.status == "queued",
                    )
                )
            ).scalar_one()
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:queue",
                event="queue.depth_changed",
                data={"depth": int(depth)},
            )
            return "requeued"

        execution.status = "failed"
        execution.failure_reason = "max_retries"
        execution.finished_at = now
        execution.updated_at = now
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=f"execution:{execution.id}",
            event="execution.failed",
            data={"execution_id": str(execution.id), "failure_reason": "max_retries"},
            idempotency_key=f"execution:{execution.id}:failed",
        )
        await _emit_terminal_notification(session, workspace_id=workspace_id, execution=execution)
        return "failed_max_retries"


async def _mark_offline_runtimes(
    session_factory: async_sessionmaker[AsyncSession], *, multiplier: int
) -> int:
    """Online runtimes past THEIR OWN heartbeat staleness window → unavailable
    (staleness = per-runtime ``heartbeat_interval_seconds × multiplier``)."""
    threshold_iso = _now().isoformat()
    offline = 0
    async with session_factory() as session, session.begin():
        rows = (
            await session.execute(
                text(
                    """
                    UPDATE runtimes
                       SET status = 'unavailable', updated_at = now()
                     WHERE status = 'online'
                       AND deleted_at IS NULL
                       AND last_heartbeat_at IS NOT NULL
                       AND last_heartbeat_at <
                             now() - make_interval(
                               secs => heartbeat_interval_seconds * :multiplier)
                    RETURNING id, workspace_id, name
                    """
                ),
                {"multiplier": multiplier},
            )
        ).all()
        for row in rows:
            await set_tenant_context(session, row.workspace_id)
            await emit_realtime(
                session,
                workspace_id=row.workspace_id,
                channel=f"workspace:{row.workspace_id}:runtimes",
                event="runtime.offline",
                data={"runtime_id": str(row.id), "name": row.name},
                idempotency_key=f"runtime:{row.id}:offline:{threshold_iso}",
            )
            offline += 1
    return offline


async def _expire_approvals(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Pending approvals past expiry → expired; awaiting executions cancel."""
    expired = 0
    async with session_factory() as session:
        candidates = (
            await session.execute(
                select(Approval.id, Approval.workspace_id).where(
                    Approval.status == "pending", Approval.expires_at < func.now()
                )
            )
        ).all()
    for approval_id, workspace_id in candidates:
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            approval = (
                await session.execute(
                    select(Approval)
                    .where(Approval.id == approval_id, Approval.status == "pending")
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if approval is None:
                continue
            now = _now()
            approval.status = "expired"
            approval.decided_at = now
            execution = None
            if approval.subject_execution_id is not None:
                execution = (
                    await session.execute(
                        select(TaskExecution)
                        .where(TaskExecution.id == approval.subject_execution_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"workspace:{workspace_id}:executions",
                event="approval.decided",
                data={"approval_id": str(approval.id), "decision": "expired"},
                idempotency_key=f"approval:{approval.id}:expired",
            )
            if execution is not None and execution.status == "awaiting_approval":
                execution.status = "cancelled"
                execution.failure_reason = "approval_expired"
                execution.finished_at = now
                execution.updated_at = now
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=f"execution:{execution.id}",
                    event="execution.cancelled",
                    data={
                        "execution_id": str(execution.id),
                        "failure_reason": "approval_expired",
                    },
                    idempotency_key=f"execution:{execution.id}:cancelled",
                )
            expired += 1
    return expired


async def runtime_reaper_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings,  # Settings — loose typing keeps the worker import light
    stop,  # asyncio.Event
) -> None:
    """Worker task: sweep on the configured interval until stopped."""
    import asyncio

    while not stop.is_set():
        try:
            await run_reaper_pass(
                session_factory,
                heartbeat_timeout_multiplier=settings.runtime_heartbeat_timeout_multiplier,
                heartbeat_retention=settings.runtime_heartbeat_retention,
            )
        except Exception:  # noqa: BLE001 — one bad pass must not kill the loop
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.runtime_reaper_interval)
        except TimeoutError:
            continue


async def _prune_heartbeats(
    session_factory: async_sessionmaker[AsyncSession], retention: timedelta
) -> int:
    threshold = _now() - retention
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(RuntimeHeartbeat).where(RuntimeHeartbeat.created_at < threshold)
        )
        return result.rowcount or 0
