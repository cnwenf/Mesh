"""Dual-layer state machine, lease fencing, cancel / freeze — runtime.md §4.7/§4.8.

Every daemon transition carries ``lease_seq``: the reaper increments it when
reclaiming, so a zombie holder's late reports fail the fence with 409 (T10,
split-brain protection). Terminal transitions release runtime capacity
EXACTLY once — guarded by the attempt's own status transition (a repeated
terminal report is a no-op that releases nothing, ``GREATEST(load-1, 0)``
prevents negatives).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.runtime import (
    ATTEMPT_INFLIGHT_STATUSES,
    ATTEMPT_TERMINAL_STATUSES,
    EXECUTION_TERMINAL_STATUSES,
    FAILURE_REASONS,
    ExecutionAttempt,
    Runtime,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from mesh.outbox.service import emit_event, emit_realtime
from mesh.runtime.credentials import revoke_attempt_envelopes, revoke_execution_envelopes
from mesh.runtime.redaction import redact_result
from mesh.runtime.task_tokens import revoke_attempt_task_tokens

# Physical attempt machine (§4.7): source → allowed daemon-driven targets.
ATTEMPT_TRANSITIONS: dict[str, frozenset[str]] = {
    "claimed": frozenset({"running", "cancelling", "cancelled", "failed", "timeout"}),
    "running": frozenset({"cancelling", "completed", "failed", "timeout", "cancelled"}),
    "cancelling": frozenset({"cancelled", "failed", "timeout"}),
}

_TERMINAL_NOTIFICATION_KINDS = frozenset({"failed", "timeout"})


def _now() -> datetime:
    return datetime.now(UTC)


async def _load_daemon_attempt(
    session: AsyncSession,
    *,
    attempt_id: uuid.UUID,
    runtime: Runtime,
    for_update: bool = True,
) -> ExecutionAttempt:
    """Fetch an attempt the daemon may touch; 404 unknown, 403 foreign."""
    stmt = select(ExecutionAttempt).where(ExecutionAttempt.id == attempt_id)
    if for_update:
        stmt = stmt.with_for_update()
    attempt = (await session.execute(stmt)).scalar_one_or_none()
    if attempt is None or attempt.workspace_id != runtime.workspace_id:
        raise NotFoundError("attempt not found")
    if attempt.runtime_id != runtime.id and attempt.claimed_by_runtime_id != runtime.id:
        raise ForbiddenError("attempt belongs to another runtime")
    return attempt


def _assert_lease(attempt: ExecutionAttempt, lease_seq: int) -> None:
    """Fencing token check — stale holders get 409, never a silent accept."""
    if attempt.lease_seq != lease_seq:
        raise ConflictError(
            "lease sequence mismatch",
            code="lease_seq_mismatch",
            details={"expected": attempt.lease_seq},
        )


async def _release_capacity(session: AsyncSession, runtime_id: uuid.UUID | None) -> None:
    """Idempotent single release (§2.5): never below zero, never twice
    (callers only invoke this on a non-terminal → terminal transition)."""
    if runtime_id is None:
        return
    await session.execute(
        update(Runtime)
        .where(Runtime.id == runtime_id)
        .values(
            current_load=func.greatest(Runtime.current_load - 1, 0),
            updated_at=func.now(),
        )
    )


async def _emit_terminal_notification(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution: TaskExecution
) -> None:
    """§6.13 matrix: failed/timeout = critical inbox event; success/cancel are
    NOT fanned out here (success stays on the run page unless subscribed;
    the cancel initiator is never notified)."""
    if execution.status not in _TERMINAL_NOTIFICATION_KINDS:
        return
    await emit_event(
        session,
        workspace_id=workspace_id,
        event_type="notification.fanout",
        payload={
            "kind": "execution_finished",
            "priority": "critical",
            "execution_id": str(execution.id),
            "agent_id": str(execution.agent_id) if execution.agent_id else None,
            "issue_id": str(execution.issue_id) if execution.issue_id else None,
            "status": execution.status,
            "failure_reason": execution.failure_reason,
        },
        idempotency_key=f"execution:{execution.id}:notify:{execution.status}",
    )


async def _sync_execution_status(
    session: AsyncSession,
    *,
    execution: TaskExecution,
    attempt_status: str,
    result: dict | None,
    failure_reason: str | None,
) -> str:
    """Mirror an attempt transition onto the logical execution (§4.7).

    The approvals module owns the ``awaiting_approval`` branch; here a
    cancelled attempt carrying that reason leaves execution status to it.
    """
    if execution.status in EXECUTION_TERMINAL_STATUSES:
        return execution.status  # already settled (superseded, frozen…)

    now = _now()
    new_status = execution.status
    if attempt_status == "running":
        if execution.status == "claimed":
            new_status = "running"
            await emit_realtime(
                session,
                workspace_id=execution.workspace_id,
                channel=f"workspace:{execution.workspace_id}:executions",
                event="execution.started",
                data={"execution_id": str(execution.id), "agent_id": _opt(execution.agent_id)},
                idempotency_key=f"execution:{execution.id}:started",
            )
    elif attempt_status == "completed":
        new_status = "completed"
        execution.result = result
        execution.finished_at = now
        await emit_realtime(
            session,
            workspace_id=execution.workspace_id,
            channel=f"execution:{execution.id}",
            event="execution.completed",
            data={"execution_id": str(execution.id), "result": result or {}},
            idempotency_key=f"execution:{execution.id}:completed",
        )
    elif attempt_status in ("failed", "timeout"):
        new_status = attempt_status
        execution.failure_reason = failure_reason or attempt_status
        execution.finished_at = now
        event = "execution.failed" if attempt_status == "failed" else "execution.timeout"
        await emit_realtime(
            session,
            workspace_id=execution.workspace_id,
            channel=f"execution:{execution.id}",
            event=event,
            data={
                "execution_id": str(execution.id),
                "failure_reason": execution.failure_reason,
            },
            idempotency_key=f"execution:{execution.id}:{attempt_status}",
        )
    elif attempt_status == "cancelled":
        if failure_reason == "awaiting_approval":
            # approvals.py drives execution → awaiting_approval in its own txn.
            return execution.status
        new_status = "cancelled"
        execution.failure_reason = failure_reason
        execution.finished_at = now
        await emit_realtime(
            session,
            workspace_id=execution.workspace_id,
            channel=f"execution:{execution.id}",
            event="execution.cancelled",
            data={
                "execution_id": str(execution.id),
                "failure_reason": failure_reason,
            },
            idempotency_key=f"execution:{execution.id}:cancelled",
        )

    if new_status != execution.status:
        execution.status = new_status
        execution.updated_at = now
        await session.flush()
    if new_status in EXECUTION_TERMINAL_STATUSES:
        await _emit_terminal_notification(
            session, workspace_id=execution.workspace_id, execution=execution
        )
        # Domain hook (squad.md §4.4): orchestration layers observe the terminal
        # state. The squad module correlates via task_spec.squad_task_id and maps
        # completed→done / failed|timeout|cancelled→failed on its subtask.
        await emit_event(
            session,
            workspace_id=execution.workspace_id,
            event_type="execution.finished",
            payload={
                "execution_id": str(execution.id),
                "status": new_status,
                "failure_reason": execution.failure_reason,
            },
            idempotency_key=f"execution:{execution.id}:finished",
        )
    return new_status


def _opt(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


# §2.6 P0: structured result schema version.
RESULT_SCHEMA_VERSION = 1


def _extract_structured_result(attempt: ExecutionAttempt, result: dict | None) -> None:
    """Parse the versioned result schema (§2.6) into typed attempt columns.

    Extracts provider/model/usage/outcome fields for reliable verification,
    budget aggregation, and querying. ``result.output`` stays as the final
    summary; the structured columns are the queryable truth.
    """
    if not result or not isinstance(result, dict):
        return
    attempt.result_schema_version = RESULT_SCHEMA_VERSION
    provider = result.get("provider")
    if isinstance(provider, dict):
        attempt.provider = provider.get("name")
        attempt.provider_version = provider.get("version")
        attempt.provider_session_id = provider.get("session_id")
        attempt.model = provider.get("model")
    usage = result.get("usage")
    if isinstance(usage, dict):
        attempt.prompt_tokens = _safe_int(usage.get("input_tokens"))
        attempt.completion_tokens = _safe_int(usage.get("output_tokens"))
        cache_read = _safe_int(usage.get("cache_read_tokens")) or 0
        cache_create = _safe_int(usage.get("cache_creation_tokens")) or 0
        attempt.cache_tokens = cache_read + cache_create
        attempt.num_turns = _safe_int(usage.get("turns"))
        cost = usage.get("cost_usd")
        if cost is not None:
            try:
                attempt.cost_usd = float(cost)
            except (TypeError, ValueError):
                pass


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def transition_attempt(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    attempt_id: uuid.UUID,
    runtime: Runtime,
    lease_seq: int,
    new_status: str,
    result: dict | None = None,
    failure_reason: str | None = None,
    signing_secret: str = "",
) -> dict:
    """PATCH /daemon/attempts/{id} — fenced status transition.

    Idempotent on terminal re-report (same status → no-op, no double release);
    conflicting terminal re-report → 409; illegal edge → 422.
    """
    # ``awaiting_approval`` is reserved for the approvals module's internal
    # path (it moves the execution to awaiting_approval in ONE transaction).
    # A daemon supplying it here would strand the execution in ``running``
    # with no in-flight attempt — rejected outright. The vocabulary is
    # enforced too: arbitrary reasons must not reach storage/events/UI.
    if failure_reason == "awaiting_approval":
        raise BusinessRuleError(
            "reserved failure reason",
            code="reserved_failure_reason",
            details={"failure_reason": failure_reason},
        )
    if failure_reason is not None and failure_reason not in FAILURE_REASONS:
        raise BusinessRuleError(
            "unknown failure reason",
            code="invalid_failure_reason",
            details={"failure_reason": failure_reason},
        )
    workspace_id = runtime.workspace_id
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        # Unified lock order: EXECUTION row first, then the attempt row —
        # identical to cancel_execution / request_tool_approval / the reaper,
        # so concurrent daemon reports and console cancels cannot deadlock.
        peek = (
            await session.execute(
                select(ExecutionAttempt.execution_id).where(
                    ExecutionAttempt.id == attempt_id
                )
            )
        ).scalar_one_or_none()
        if peek is None:
            raise NotFoundError("attempt not found")
        execution = (
            await session.execute(
                select(TaskExecution)
                .where(TaskExecution.id == peek)
                .with_for_update()
            )
        ).scalar_one_or_none()
        attempt = await _load_daemon_attempt(session, attempt_id=attempt_id, runtime=runtime)

        if attempt.status in ATTEMPT_TERMINAL_STATUSES:
            if attempt.status == new_status:
                # Duplicate terminal report: no-op (idempotent daemon retries).
                return _attempt_response(attempt, execution_status=None)
            raise ConflictError(
                "attempt already terminal",
                code="attempt_terminal",
                details={"status": attempt.status},
            )

        _assert_lease(attempt, lease_seq)
        allowed = ATTEMPT_TRANSITIONS.get(attempt.status, frozenset())
        if new_status not in allowed:
            raise BusinessRuleError(
                "illegal attempt state transition",
                code="invalid_state_transition",
                details={"from": attempt.status, "to": new_status},
            )

        was_inflight = attempt.status in ATTEMPT_INFLIGHT_STATUSES
        now = _now()
        attempt.status = new_status
        attempt.updated_at = now
        if new_status == "running" and attempt.started_at is None:
            attempt.started_at = now
        if new_status in ATTEMPT_TERMINAL_STATUSES:
            attempt.finished_at = now
            attempt.failure_reason = failure_reason
            # §2.5 S-06: server-side fallback redaction — daemon redacts
            # first, server MUST redact again before persisting (ISO-13).
            redaction_hits = 0
            if result is not None and signing_secret:
                result, redaction_hits = await redact_result(
                    session,
                    workspace_id=workspace_id,
                    result=result,
                    signing_secret=signing_secret,
                )
            attempt.result = result
            attempt.redaction_hits = redaction_hits
            # §2.6 P0: parse structured result fields for reliable
            # verification, budget aggregation, and querying.
            _extract_structured_result(attempt, result)
            await revoke_attempt_envelopes(session, attempt_id=attempt.id, now=now)
            # §2.2 S-05: revoke task tokens same-transaction with terminal
            # state transition (fail-closed).
            await revoke_attempt_task_tokens(session, attempt_id=attempt.id, now=now)
            if was_inflight:
                await _release_capacity(session, attempt.runtime_id)

        # Execution row already locked above (unified lock order).
        execution_status = None
        if execution is not None:
            execution_status = await _sync_execution_status(
                session,
                execution=execution,
                attempt_status=new_status,
                result=result,
                failure_reason=failure_reason,
            )
        await session.flush()
        return _attempt_response(attempt, execution_status=execution_status)


def _attempt_response(attempt: ExecutionAttempt, *, execution_status: str | None) -> dict:
    return {
        "id": str(attempt.id),
        "execution_id": str(attempt.execution_id),
        "status": attempt.status,
        "execution_status": execution_status,
        "lease_seq": attempt.lease_seq,
        "finished_at": attempt.finished_at.isoformat() if attempt.finished_at else None,
    }


async def renew_lease(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    attempt_id: uuid.UUID,
    runtime: Runtime,
    lease_seq: int,
    lease_seconds: int,
) -> dict:
    """POST /daemon/attempts/{id}:renew-lease — lease_seq advances every
    claim / renew; the old value is then a zombie detector (409).

    §2.6 P0: also atomically rotates the task token — old token revoked
    same-transaction, new plaintext returned once.
    """
    from mesh.runtime.task_tokens import issue_task_token

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, runtime.workspace_id)
        attempt = await _load_daemon_attempt(session, attempt_id=attempt_id, runtime=runtime)
        if attempt.status not in ATTEMPT_INFLIGHT_STATUSES:
            raise BusinessRuleError(
                "attempt not in flight",
                code="invalid_state_transition",
                details={"status": attempt.status},
            )
        _assert_lease(attempt, lease_seq)
        now = _now()
        attempt.lease_seq = attempt.lease_seq + 1
        attempt.lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt.updated_at = now

        # §2.2 S-05: rotate task token atomically with lease renewal.
        # Old token revoked same-transaction; new plaintext returned once.
        execution = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.id == attempt.execution_id)
            )
        ).scalar_one_or_none()
        task_token_plaintext = None
        task_token_expires = None
        if execution is not None:
            from mesh.runtime.task_tokens import squad_role_of_task_spec

            task_token_plaintext, token_row = await issue_task_token(
                session,
                workspace_id=runtime.workspace_id,
                attempt_id=attempt.id,
                runtime_id=runtime.id,
                lease_seq=attempt.lease_seq,
                lease_expires_at=attempt.lease_expires_at,
                issue_id=execution.issue_id,
                agent_id=execution.agent_id,
                squad_role=squad_role_of_task_spec(execution.task_spec),
            )
            task_token_expires = token_row.expires_at.isoformat()

        await session.flush()
        response: dict = {
            "lease_expires_at": attempt.lease_expires_at.isoformat(),
            "lease_seq": attempt.lease_seq,
        }
        if task_token_plaintext is not None:
            response["task_token"] = task_token_plaintext
            response["task_token_expires_at"] = task_token_expires
        return response


async def cancel_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    member_id: uuid.UUID | None = None,
    failure_reason: str | None = None,
) -> dict:
    """POST /executions/{id}:cancel — two-phase, idempotent (§4.7/§4.10).

    queued → cancelled immediately (nothing to drain); claimed/running →
    ``cancelling`` with the downlink cancel command queued on the heartbeat
    channel (the daemon SIGTERMs, then SIGKILLs after the grace period, and
    PATCHes the attempt to ``cancelled``); awaiting_approval → cancelled with
    the pending approval closed.
    """
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution)
                .where(TaskExecution.id == execution_id, TaskExecution.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError("execution not found")

        now = _now()
        if execution.status in EXECUTION_TERMINAL_STATUSES:
            return _cancel_response(execution)  # idempotent no-op
        if execution.status == "cancelling":
            return _cancel_response(execution)

        execution.cancel_requested_by = member_id
        execution.cancel_requested_at = now
        execution.updated_at = now

        if execution.status == "queued":
            execution.status = "cancelled"
            execution.failure_reason = failure_reason
            execution.finished_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}",
                event="execution.cancelled",
                data={"execution_id": str(execution.id), "failure_reason": failure_reason},
                idempotency_key=f"execution:{execution.id}:cancelled",
            )
        elif execution.status == "awaiting_approval":
            from mesh.runtime.approvals import cancel_pending_approvals

            await cancel_pending_approvals(
                session, workspace_id=workspace_id, execution_id=execution_id, now=now
            )
            execution.status = "cancelled"
            execution.failure_reason = failure_reason
            execution.finished_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}",
                event="execution.cancelled",
                data={"execution_id": str(execution.id), "failure_reason": failure_reason},
                idempotency_key=f"execution:{execution.id}:cancelled",
            )
        else:  # claimed / running: two-phase via daemon downlink
            execution.status = "cancelling"
            inflight = (
                await session.execute(
                    select(ExecutionAttempt).where(
                        ExecutionAttempt.execution_id == execution_id,
                        ExecutionAttempt.status.in_(sorted(ATTEMPT_INFLIGHT_STATUSES)),
                    )
                )
            ).scalars().all()
            for attempt in inflight:
                attempt.status = "cancelling"
                attempt.updated_at = now
        await session.flush()
        return _cancel_response(execution)


def _cancel_response(execution: TaskExecution) -> dict:
    return {
        "id": str(execution.id),
        "status": execution.status,
        "cancel_requested_at": (
            execution.cancel_requested_at.isoformat() if execution.cancel_requested_at else None
        ),
    }


async def freeze_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
) -> dict:
    """POST /executions/{id}:freeze — revoke every envelope NOW, keep the
    scene (workdir + logs) for forensics; critical security notification."""
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution)
                .where(TaskExecution.id == execution_id, TaskExecution.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError("execution not found")
        revoked = await revoke_execution_envelopes(session, execution_id=execution_id)
        # §2.2 S-05: revoke task tokens for ALL attempts of this execution
        # (freeze is a security action — every token must die immediately).

        attempt_ids = (
            await session.execute(
                select(ExecutionAttempt.id).where(
                    ExecutionAttempt.execution_id == execution_id,
                )
            )
        ).scalars().all()
        for aid in attempt_ids:
            await revoke_attempt_task_tokens(session, attempt_id=aid)
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type="notification.fanout",
            payload={
                "kind": "security_freeze",
                "priority": "critical",
                "execution_id": str(execution.id),
                "agent_id": _opt(execution.agent_id),
            },
            idempotency_key=f"execution:{execution.id}:freeze",
        )
        return {"id": str(execution.id), "status": execution.status, "revoked_envelopes": revoked}


async def cancel_in_flight_for_agent(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    issue_id: uuid.UUID | None,
    failure_reason: str,
) -> int:
    """Supersede / agent-pause path (README §6.9): cancel the agent's
    queued/claimed/running executions for the issue. Runs inside the caller's
    transaction (outbox relay savepoint)."""
    stmt = (
        select(TaskExecution)
        .where(
            TaskExecution.workspace_id == workspace_id,
            TaskExecution.agent_id == agent_id,
            TaskExecution.status.in_(["queued", "claimed", "running", "cancelling"]),
        )
        .with_for_update()
    )
    if issue_id is not None:
        stmt = stmt.where(TaskExecution.issue_id == issue_id)
    executions = (await session.execute(stmt)).scalars().all()
    now = _now()
    for execution in executions:
        if execution.status == "queued":
            execution.status = "cancelled"
            execution.failure_reason = failure_reason
            execution.finished_at = now
            execution.updated_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=f"execution:{execution.id}",
                event="execution.cancelled",
                data={"execution_id": str(execution.id), "failure_reason": failure_reason},
                idempotency_key=f"execution:{execution.id}:cancelled",
            )
        else:
            execution.status = "cancelling"
            execution.cancel_requested_at = now
            execution.updated_at = now
            attempts = (
                await session.execute(
                    select(ExecutionAttempt).where(
                        ExecutionAttempt.execution_id == execution.id,
                        ExecutionAttempt.status.in_(sorted(ATTEMPT_INFLIGHT_STATUSES)),
                    )
                )
            ).scalars().all()
            for attempt in attempts:
                attempt.status = "cancelling"
                attempt.updated_at = now
    return len(executions)
