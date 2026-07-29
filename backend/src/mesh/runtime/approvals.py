"""Unified approvals — the single resume protocol (README §6.10, T21).

A tool call hitting ``confirm_required`` goes through ONE path only:

1. Daemon POSTs the approval from a RUNNING attempt;
2. the current attempt is cancelled with ``failure_reason='awaiting_approval'``
   — it keeps NO in-flight state (audit row preserved, lease ended, capacity
   released); the execution becomes ``awaiting_approval``;
3. approve → execution back to ``queued``; the NEXT claim builds attempt
   #N+1 and resumes from the frozen ``resume_context``;
4. reject → ``cancelled(approval_rejected)``; expiry is the reaper's job
   (``approval_expired``).

There is deliberately no "pause the lease" variant — no in-flight attempt
means no zombie path and nothing for the reaper to special-case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.autopilot.approvals import apply_approval_decision
from mesh.db.models.agent import Agent
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.runtime import (
    Approval,
    Runtime,
    TaskExecution,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
)
from mesh.outbox.service import emit_event, emit_realtime
from mesh.runtime.attempts import (
    _assert_lease,
    _emit_finished_event,
    _load_daemon_attempt,
    _release_capacity,
)
from mesh.runtime.context_appends import reset_context_receipts_tx
from mesh.runtime.credentials import revoke_attempt_envelopes

# Internal outbox event_type the squad module consumes to apply a squad_plan
# decision onto its root task (squad.md §6.10). Declared here so the approvals
# entity stays the single decision entry while the effect is relay-side.
SQUAD_PLAN_DECIDED_EVENT_TYPE = "squad.plan_decided"


def _now() -> datetime:
    return datetime.now(UTC)


async def request_tool_approval(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    execution_id: uuid.UUID,
    runtime: Runtime,
    attempt_id: uuid.UUID,
    lease_seq: int,
    action_summary: dict,
    resume_context: dict,
    approval_ttl: timedelta,
) -> dict:
    """Daemon: suspend the running execution on a high-risk tool call."""
    workspace_id = runtime.workspace_id
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution)
                .where(
                    TaskExecution.id == execution_id,
                    TaskExecution.workspace_id == workspace_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError("execution not found")
        if execution.status != "running":
            # The ONLY entry edge is running (tool calls happen mid-run).
            raise BusinessRuleError(
                "approval can only be requested from a running execution",
                code="invalid_state_transition",
                details={"status": execution.status},
            )
        attempt = await _load_daemon_attempt(
            session, attempt_id=attempt_id, runtime=runtime
        )
        if attempt.execution_id != execution.id:
            raise BusinessRuleError(
                "attempt does not belong to this execution",
                code="invalid_state_transition",
            )
        if attempt.status != "running":
            raise BusinessRuleError(
                "attempt not running",
                code="invalid_state_transition",
                details={"status": attempt.status},
            )
        _assert_lease(attempt, lease_seq)

        # Idempotent re-request: one pending approval per subject (partial
        # unique index) — return the existing one instead of raising.
        existing = (
            await session.execute(
                select(Approval).where(
                    Approval.workspace_id == workspace_id,
                    Approval.subject_type == "tool_call",
                    Approval.subject_execution_id == execution.id,
                    Approval.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _approval_response(existing, execution_status="awaiting_approval")

        # The requester is the agent's roster member row (agents ARE members,
        # README §6.1); resolved server-side — never trusted from the daemon.
        requester = None
        if execution.agent_id is not None:
            requester = (
                await session.execute(
                    select(Member).where(
                        Member.workspace_id == workspace_id,
                        Member.agent_id == execution.agent_id,
                    )
                )
            ).scalars().first()
        if requester is None:
            raise BusinessRuleError(
                "execution has no agent roster member to request approval",
                code="approval_requester_missing",
            )

        now = _now()
        # 1) Cancel the current attempt — audit row preserved, lease ended,
        #    capacity released. No in-flight state survives.
        attempt.status = "cancelled"
        attempt.failure_reason = "awaiting_approval"
        attempt.finished_at = now
        attempt.updated_at = now
        await revoke_attempt_envelopes(session, attempt_id=attempt.id, now=now)
        await _release_capacity(session, attempt.runtime_id)

        # 2) Execution → awaiting_approval.
        execution.status = "awaiting_approval"
        execution.failure_reason = "awaiting_approval"
        execution.updated_at = now

        # 3) The unified approval row with a FROZEN resume_context.
        summary = dict(action_summary or {})
        summary["resume_context"] = resume_context or {}
        approval = Approval(
            workspace_id=workspace_id,
            subject_type="tool_call",
            subject_execution_id=execution.id,
            requested_by_member_id=requester.id,
            action_summary=summary,
            status="pending",
            expires_at=now + approval_ttl,
            idempotency_key=f"approval:execution:{execution.id}",
        )
        session.add(approval)
        await session.flush()

        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=f"workspace:{workspace_id}:executions",
            event="execution.awaiting_approval",
            data={"execution_id": str(execution.id), "approval_id": str(approval.id)},
            idempotency_key=f"execution:{execution.id}:awaiting-approval",
        )
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=f"workspace:{workspace_id}:executions",
            event="approval.created",
            data={
                "approval_id": str(approval.id),
                "subject_type": "tool_call",
                "execution_id": str(execution.id),
                "expires_at": approval.expires_at.isoformat(),
            },
            idempotency_key=f"approval:{approval.id}:created",
        )
        # §6.13 matrix: approval request = critical inbox event.
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type="notification.fanout",
            payload={
                "type": "review_requested",
                "approval_id": str(approval.id),
                "execution_id": str(execution.id),
                "agent_id": str(execution.agent_id) if execution.agent_id else None,
                "group_key": f"execution:{execution.id}:approval",
            },
            idempotency_key=f"approval:{approval.id}:notify",
        )
        return _approval_response(approval, execution_status="awaiting_approval")


async def decide_approval(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    approval_id: uuid.UUID,
    workspace_id: uuid.UUID,
    member: Member,
    approve: bool,
    comment: str | None = None,
) -> dict:
    """Console: approve / reject. Idempotent — re-deciding returns the state.

    Permission (README §6.10): HUMANS only (agents cannot self-approve) and
    either workspace admin/owner or the agent's owner.
    """
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        approval = (
            await session.execute(
                select(Approval)
                .where(Approval.id == approval_id, Approval.workspace_id == workspace_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if approval is None:
            raise NotFoundError("approval not found")

        if approval.status != "pending":
            return _approval_response(approval, execution_status=None)  # idempotent

        if member.member_type != "human":
            raise ForbiddenError("agents cannot approve")
        await _assert_may_decide(session, approval=approval, member=member)

        now = _now()
        approval.status = "approved" if approve else "rejected"
        approval.decided_by_member_id = member.id
        approval.decided_at = now
        approval.decision_comment = comment

        execution_status = None
        if approval.subject_execution_id is not None:
            execution = (
                await session.execute(
                    select(TaskExecution)
                    .where(TaskExecution.id == approval.subject_execution_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if execution is not None and execution.status == "awaiting_approval":
                if approve:
                    # Back to queued: the next claim builds attempt #N+1 and
                    # resumes from action_summary.resume_context.
                    execution.status = "queued"
                    execution.failure_reason = None
                    execution.queued_at = now
                    execution.updated_at = now
                    # R7-2/T39-19: approval resume shares the lost-contact
                    # requeue path — clear every append receipt + reset the
                    # watermark atomically under the same execution row lock
                    # so the new attempt re-receives all seqs (at-least-once;
                    # resume_context is layered on top by the claim path).
                    await reset_context_receipts_tx(session, execution_id=execution.id)
                    await emit_realtime(
                        session,
                        workspace_id=workspace_id,
                        channel=f"workspace:{workspace_id}:executions",
                        event="execution.queued",
                        data={"execution_id": str(execution.id), "resumed": True},
                        idempotency_key=f"execution:{execution.id}:resume-queued:{approval.id}",
                    )
                else:
                    execution.status = "cancelled"
                    execution.failure_reason = "approval_rejected"
                    execution.finished_at = now
                    execution.updated_at = now
                    await emit_realtime(
                        session,
                        workspace_id=workspace_id,
                        channel=f"execution:{execution.id}",
                        event="execution.cancelled",
                        data={
                            "execution_id": str(execution.id),
                            "failure_reason": "approval_rejected",
                        },
                        idempotency_key=f"execution:{execution.id}:cancelled",
                    )
                    # Terminal single fan-out (§4.8): drives the integration
                    # queue-item terminal write-back for this execution.
                    await _emit_finished_event(session, execution=execution)
                execution_status = execution.status

        run_status = None
        if approval.subject_run_id is not None:
            # autopilot_action subject (README §6.10): approve resumes the
            # parked run (waiting_approval → running, executor dispatches);
            # reject cancels it.
            run_status = await apply_approval_decision(
                session, approval=approval, approve=approve, now=now
            )

        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=f"workspace:{workspace_id}:executions",
            event="approval.decided",
            data={
                "approval_id": str(approval.id),
                "decision": approval.status,
                "execution_id": (
                    str(approval.subject_execution_id)
                    if approval.subject_execution_id
                    else None
                ),
                "run_id": (
                    str(approval.subject_run_id) if approval.subject_run_id else None
                ),
                "run_status": run_status,
            },
            idempotency_key=f"approval:{approval.id}:decided",
        )
        # squad_plan subjects: the squad module applies the decision onto its
        # root task via the outbox relay (keeps runtime decoupled from squad).
        if approval.subject_type == "squad_plan" and approval.subject_task_id is not None:
            await emit_event(
                session,
                workspace_id=workspace_id,
                event_type=SQUAD_PLAN_DECIDED_EVENT_TYPE,
                payload={
                    "approval_id": str(approval.id),
                    "subject_task_id": str(approval.subject_task_id),
                    "decision": approval.status,
                    "decided_by_member_id": str(approval.decided_by_member_id)
                    if approval.decided_by_member_id
                    else None,
                },
                idempotency_key=f"approval:{approval.id}:squad-plan-decided",
            )
        return _approval_response(approval, execution_status=execution_status)


async def _assert_may_decide(session: AsyncSession, *, approval: Approval, member: Member) -> None:
    """§6.10 permission: human member AND (workspace admin/owner, the subject
    execution's trigger/dispatcher, or the agent's owner). H4: for an
    issue-triggered execution the trigger is the issue REPORTER (the member
    who filed the work item the agent was dispatched on — the persistent
    trigger signal in the data model)."""
    if member.role in ("admin", "owner"):
        return
    if approval.subject_type == "squad_plan":
        # squad.md §3.4: any human member / observer / admin may review a plan.
        # (The humans-only gate is enforced by the caller before this point.)
        return
    if approval.subject_execution_id is not None:
        execution = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.id == approval.subject_execution_id)
            )
        ).scalar_one_or_none()
        if execution is not None:
            # Trigger/dispatcher path: reporter of the triggering issue.
            if execution.issue_id is not None:
                reporter = (
                    await session.execute(
                        select(Issue.reporter_id).where(
                            Issue.id == execution.issue_id,
                            Issue.workspace_id == approval.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
                if reporter is not None and reporter == member.id:
                    return
            # Agent owner path.
            if execution.agent_id is not None:
                agent = (
                    await session.execute(
                        select(Agent).where(
                            Agent.id == execution.agent_id,
                            Agent.workspace_id == approval.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
                if agent is not None and agent.owner_user_id == member.user_id:
                    return
    if approval.subject_run_id is not None:
        from mesh.db.models.autopilot import Autopilot, AutopilotRun

        run = (
            await session.execute(
                select(AutopilotRun).where(AutopilotRun.id == approval.subject_run_id)
            )
        ).scalar_one_or_none()
        if run is not None:
            # Trigger path: the member who manually triggered the run, or the
            # rule creator (the persistent ownership signal).
            if run.triggered_by is not None and run.triggered_by == member.id:
                return
            rule = (
                await session.execute(
                    select(Autopilot).where(Autopilot.id == run.autopilot_id)
                )
            ).scalar_one_or_none()
            if rule is not None:
                if rule.created_by == member.id:
                    return
                # Executor agent owner path.
                if rule.executor_agent_id is not None:
                    agent = (
                        await session.execute(
                            select(Agent).where(
                                Agent.id == rule.executor_agent_id,
                                Agent.workspace_id == approval.workspace_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if agent is not None and agent.owner_user_id == member.user_id:
                        return
    raise ForbiddenError("not permitted to decide this approval")


async def cancel_pending_approvals(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution_id: uuid.UUID, now: datetime
) -> int:
    """Execution cancelled while awaiting approval → close the pending row."""
    pending = (
        await session.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.subject_type == "tool_call",
                Approval.subject_execution_id == execution_id,
                Approval.status == "pending",
            )
        )
    ).scalars().all()
    for approval in pending:
        approval.status = "cancelled"
        approval.decided_at = now
    return len(pending)


def _approval_response(approval: Approval, *, execution_status: str | None) -> dict:
    return {
        "id": str(approval.id),
        "subject_type": approval.subject_type,
        "subject_execution_id": (
            str(approval.subject_execution_id) if approval.subject_execution_id else None
        ),
        "subject_task_id": (
            str(approval.subject_task_id) if approval.subject_task_id else None
        ),
        "status": approval.status,
        "action_summary": approval.action_summary,
        "requested_at": approval.requested_at.isoformat(),
        "expires_at": approval.expires_at.isoformat(),
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "decision_comment": approval.decision_comment,
        "execution_status": execution_status,
    }
