"""High-risk action approval gate (autopilot.md §6.10 / §4.4 / §5.3).

A run hits the human-confirmation gate when the rule sets
``require_approval=true`` OR any of its action types appears in
``guardrails.approval_required_actions`` (outbound HTTP / issue creation by
default). The gate creates ONE row in the unified ``approvals`` entity
(``subject_type='autopilot_action'``, linked through the physical composite
FK ``approvals.subject_run_id → autopilot_runs``) and parks the run at
``waiting_approval``. Approve/reject are collected at the unified inbox
endpoints (``POST /api/v1/approvals/{id}/approve|reject``); the
autopilot-facing ``runs/{run_id}/approve|reject`` routes are thin wrappers
over the SAME decision logic (§3.1).

Invariants (README §6.10): at most one pending approval per run (partial
unique index ``uq_approvals_pending_run``); repeated requests return the
existing pending row; agents can never approve; expiry → run
``cancelled(approval_expired)`` (the reaper sweeps).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.autopilot.guardrails import autopilot_channel, autopilots_channel
from mesh.autopilot.runs import emit_run_status_changed
from mesh.db.models.autopilot import Autopilot, AutopilotRun
from mesh.db.models.runtime import Approval
from mesh.errors import BusinessRuleError, NotFoundError
from mesh.outbox.service import emit_event, emit_realtime


def requires_approval(rule: Autopilot) -> tuple[bool, list[str]]:
    """Whether the rule's dispatch must pass the human gate, and why.

    Returns ``(required, matched_action_types)`` — matched types are the
    action entries covered by ``approval_required_actions`` (empty when the
    whole-rule ``require_approval`` flag is the sole reason).
    """
    gated_types = [
        str(action_type)
        for action_type in (rule.guardrails or {}).get("approval_required_actions") or []
    ]
    matched = [
        str(action.get("type") or "")
        for action in (rule.action_config or [])
        if isinstance(action, dict) and str(action.get("type") or "") in gated_types
    ]
    return (rule.require_approval or bool(matched)), matched


async def find_pending_run_approval(
    session: AsyncSession, *, workspace_id: uuid.UUID, run_id: uuid.UUID
) -> Approval | None:
    """The single pending approval for a run (README §6.10)."""
    return await session.scalar(
        select(Approval).where(
            Approval.workspace_id == workspace_id,
            Approval.subject_type == "autopilot_action",
            Approval.subject_run_id == run_id,
            Approval.status == "pending",
        )
    )


async def request_run_approval(
    session: AsyncSession,
    *,
    run: AutopilotRun,
    rule: Autopilot,
    requested_by_member_id: uuid.UUID,
    action_summary: dict,
    ttl: timedelta,
    now: datetime | None = None,
) -> Approval:
    """Create (or return) the pending approval and park the run.

    Idempotent: a repeat while one is pending returns the existing row —
    the partial unique index is the last-resort guard, this query is the
    fast path.
    """
    moment = now if now is not None else datetime.now(UTC)
    existing = await find_pending_run_approval(
        session, workspace_id=rule.workspace_id, run_id=run.id
    )
    if existing is not None:
        return existing

    summary = dict(action_summary or {})
    summary.setdefault("expires_at", (moment + ttl).isoformat())
    approval = Approval(
        workspace_id=rule.workspace_id,
        subject_type="autopilot_action",
        subject_run_id=run.id,
        requested_by_member_id=requested_by_member_id,
        action_summary=summary,
        status="pending",
        requested_at=moment,
        expires_at=moment + ttl,
        idempotency_key=f"approval:autopilot-run:{run.id}",
    )
    session.add(approval)
    await session.flush()

    # Run parks at waiting_approval (§4.4).
    old_status = run.status
    run.status = "waiting_approval"
    run.updated_at = moment
    await emit_run_status_changed(
        session, run=run, old_status=old_status, new_status="waiting_approval", now=moment
    )
    await emit_realtime(
        session,
        workspace_id=rule.workspace_id,
        channel=autopilot_channel(rule.id),
        event="autopilot_runs.approval_required",
        data={"run_id": str(run.id), "autopilot_id": str(rule.id)},
        idempotency_key=f"run:{run.id}:approval-required",
    )
    await emit_realtime(
        session,
        workspace_id=rule.workspace_id,
        channel=autopilots_channel(rule.workspace_id),
        event="approval.created",
        data={
            "approval_id": str(approval.id),
            "subject_type": "autopilot_action",
            "run_id": str(run.id),
            "expires_at": approval.expires_at.isoformat(),
        },
        idempotency_key=f"approval:{approval.id}:created",
    )
    # §6.13 matrix: approval request = critical (unified 待我审批 inbox,
    # pierce quiet hours, reset group unread).
    await emit_event(
        session,
        workspace_id=rule.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "review_requested",
            "recipient_ids": [str(requested_by_member_id), str(rule.created_by)],
            "group_key": f"autopilot:{rule.id}:approval",
            "autopilot_id": str(rule.id),
            "run_id": str(run.id),
            "approval_id": str(approval.id),
        },
        idempotency_key=f"approval:{approval.id}:notify",
    )
    return approval


async def apply_approval_decision(
    session: AsyncSession,
    *,
    approval: Approval,
    approve: bool,
    now: datetime | None = None,
) -> str | None:
    """Run-side effect of an approval decision (called by decide_approval).

    Approve → ``waiting_approval`` → ``running`` (the executor dispatches);
    reject → ``cancelled(approval_rejected)``. Returns the new run status,
    or None when the run no longer exists / is not awaiting.
    """
    moment = now if now is not None else datetime.now(UTC)
    if approval.subject_run_id is None:
        return None
    run = await session.scalar(
        select(AutopilotRun)
        .where(
            AutopilotRun.id == approval.subject_run_id,
            AutopilotRun.workspace_id == approval.workspace_id,
        )
        .with_for_update()
    )
    if run is None or run.status != "waiting_approval":
        return None
    old_status = run.status
    if approve:
        run.status = "running"
        run.error = None
        run.updated_at = moment
        await emit_run_status_changed(
            session, run=run, old_status=old_status, new_status="running", now=moment
        )
    else:
        run.status = "cancelled"
        run.error = {"code": "approval_rejected", "message": "approval rejected", "retryable": False}
        run.finished_at = moment
        run.updated_at = moment
        if run.started_at is not None:
            run.duration_ms = max(0, int((moment - run.started_at).total_seconds() * 1000))
        await emit_run_status_changed(
            session, run=run, old_status=old_status, new_status="cancelled", now=moment
        )
    return run.status


async def expire_run_approval(
    session: AsyncSession, *, approval: Approval, now: datetime | None = None
) -> None:
    """Reaper hook: expiry → run ``cancelled(approval_expired)`` + notify."""
    moment = now if now is not None else datetime.now(UTC)
    if approval.subject_run_id is None:
        return
    run = await session.scalar(
        select(AutopilotRun)
        .where(
            AutopilotRun.id == approval.subject_run_id,
            AutopilotRun.workspace_id == approval.workspace_id,
        )
        .with_for_update()
    )
    if run is None or run.status != "waiting_approval":
        return
    old_status = run.status
    run.status = "cancelled"
    run.error = {"code": "approval_expired", "message": "approval expired", "retryable": False}
    run.finished_at = moment
    run.updated_at = moment
    if run.started_at is not None:
        run.duration_ms = max(0, int((moment - run.started_at).total_seconds() * 1000))
    await emit_run_status_changed(
        session, run=run, old_status=old_status, new_status="cancelled", now=moment
    )
    await emit_event(
        session,
        workspace_id=approval.workspace_id,
        event_type="notification.fanout",
        payload={
            "type": "autopilot_notice",
            "recipient_ids": [str(approval.requested_by_member_id)],
            "group_key": f"autopilot-run:{run.id}:expired",
            "run_id": str(run.id),
        },
        idempotency_key=f"approval:{approval.id}:expired-notify",
    )


async def decide_run_approval(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
) -> Approval:
    """Resolve the run's pending approval for the thin wrapper routes.

    Raises 404 when the run or a pending approval does not exist, 422 when
    the run has no pending gate (wrong state).
    """
    approval = await find_pending_run_approval(
        session, workspace_id=workspace_id, run_id=run_id
    )
    if approval is None:
        run_exists = await session.scalar(
            select(AutopilotRun.id).where(
                AutopilotRun.id == run_id, AutopilotRun.workspace_id == workspace_id
            )
        )
        if run_exists is None:
            raise NotFoundError("run not found")
        raise BusinessRuleError(
            "run has no pending approval",
            code="invalid_state_transition",
            details={"run_id": str(run_id)},
        )
    return approval


__all__ = [
    "apply_approval_decision",
    "decide_run_approval",
    "expire_run_approval",
    "find_pending_run_approval",
    "request_run_approval",
    "requires_approval",
]
