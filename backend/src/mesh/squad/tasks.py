"""Squad orchestration core — assignment, decomposition DAG, dispatch, state
machine, aggregation, plan approval and execution observation (squad.md §2.4,
§2.5, §4.4, §6.9, §6.10).

The transaction-owning public methods live on :class:`SquadTaskService`. The
``*_tx`` helpers take an open session so they can run inside a caller's
transaction (``service.py`` membership changes, the outbox relay handlers) —
this keeps leader change / departure atomic with the membership write and keeps
plan-decision / execution-terminal effects on the relay's savepoint.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.comment_inbox.notifications import emit_notification_fanout
from mesh.db.constraints import violates
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.squad import (
    IssueSquadAssignment,
    Squad,
    SquadMember,
    SquadTask,
    SquadTaskDependency,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from mesh.issue.triggers import ASSIGN_EVENT_TYPE, assign_event_idempotency_key
from mesh.outbox.service import emit_event, emit_realtime
from mesh.squad.common import (
    SQUAD_CHANNEL,
    emit_task_status,
    emit_task_stream_frame,
    now_utc,
    record_squad_activity,
)

logger = logging.getLogger(__name__)

# Default time a plan approval stays pending before the reaper expires it.
PLAN_APPROVAL_TTL = timedelta(hours=24)

EXECUTION_TERMINAL = frozenset({"completed", "failed", "timeout", "cancelled"})

# Server-side state machines (squad.md §4.4). Illegal edge → 409 conflict.
ROOT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"decomposing", "cancelled", "blocked", "failed"}),
    "decomposing": frozenset(
        # "done" covers the leader evaluation outcome "no action needed"
        # (leader run completed without producing any subtasks, §5.1).
        {"awaiting_plan_approval", "dispatching", "done", "cancelled", "failed", "blocked"}
    ),
    "awaiting_plan_approval": frozenset({"dispatching", "decomposing", "failed", "cancelled"}),
    # "aggregating" from dispatching: every child may reach a terminal state
    # while the parent is still dispatching (no explicit in_progress hop).
    "dispatching": frozenset({"in_progress", "aggregating", "cancelled", "failed", "blocked"}),
    "in_progress": frozenset({"aggregating", "blocked", "cancelled", "failed", "done"}),
    "blocked": frozenset({"in_progress", "dispatching", "cancelled", "failed"}),
    "aggregating": frozenset({"done", "failed"}),
    "done": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

# Mid-tree nodes (subtasks that can themselves be decomposed by a sub-leader,
# §S7) pass through the same orchestration states as roots; pure leaves use
# the simpler subset. One superset table keeps assert_transition total.
SUBTASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"decomposing", "dispatching", "cancelled", "failed"}),
    "decomposing": frozenset(
        {"awaiting_plan_approval", "dispatching", "done", "cancelled", "failed", "blocked"}
    ),
    "awaiting_plan_approval": frozenset({"dispatching", "decomposing", "failed", "cancelled"}),
    "dispatching": frozenset({"in_progress", "aggregating", "cancelled", "failed", "blocked"}),
    "in_progress": frozenset({"done", "failed", "blocked", "cancelled", "aggregating"}),
    "blocked": frozenset({"in_progress", "dispatching", "cancelled", "failed"}),
    "aggregating": frozenset({"done", "failed"}),
    "done": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def assert_transition(current: str, target: str, *, is_root: bool) -> None:
    table = ROOT_TRANSITIONS if is_root else SUBTASK_TRANSITIONS
    allowed = table.get(current, frozenset())
    if target not in allowed:
        raise ConflictError(
            f"illegal task status transition {current} -> {target}",
            code="conflict",
            details={"from": current, "to": target},
        )


def _is_root(task: SquadTask) -> bool:
    return task.parent_task_id is None


# -- low-level loads ----------------------------------------------------------


async def load_task(
    session: AsyncSession, *, workspace_id: uuid.UUID, task_id: uuid.UUID, for_update: bool = False
) -> SquadTask:
    stmt = select(SquadTask).where(
        SquadTask.workspace_id == workspace_id, SquadTask.id == task_id
    )
    if for_update:
        stmt = stmt.with_for_update()
    task = (await session.execute(stmt)).scalar_one_or_none()
    if task is None:
        raise NotFoundError("task not found")
    return task


async def _children(session: AsyncSession, *, workspace_id: uuid.UUID, task_id: uuid.UUID) -> list[SquadTask]:
    return list(
        (
            await session.execute(
                select(SquadTask).where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.parent_task_id == task_id,
                )
            )
        ).scalars()
    )


# -- execution enqueue / cancel ----------------------------------------------


async def _enqueue_agent_run(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member: Member,
    issue_id: uuid.UUID,
    task: SquadTask,
    role: str,
) -> None:
    """Wake an agent member via the unified assign orchestration entry.

    Emits ``issue.assigned`` (action='enqueue'); the agent orchestration handler
    runs guardrails, freezes the §6.11 snapshot and writes ``execution.enqueue``.
    ``squad_task_id`` / ``squad_role`` ride along so the terminal observer can
    correlate the execution back to this task.
    """
    # The wake key must be unique PER WAKE, not per task: the same task is
    # woken several times over its life (initial orchestrator run, aggregator
    # summary run, post-unblock re-wake). A per-task key would de-duplicate
    # the later wakes against the first and silently drop their enqueue.
    rt = await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=task.squad_id),
        event="squad_task.status_changed",
        data={
            "task_id": str(task.id),
            "squad_id": str(task.squad_id),
            "old_status": task.status,
            "new_status": task.status,
            "dispatch": True,
        },
        idempotency_key=f"squad-task:{task.id}:dispatch-trigger:{role}:{task.status}",
    )
    await emit_event(
        session,
        workspace_id=workspace_id,
        event_type=ASSIGN_EVENT_TYPE,
        payload={
            "issue_id": str(issue_id),
            "agent_member_id": str(member.id),
            "agent_id": str(member.agent_id) if member.agent_id else None,
            "trigger": "assign",
            "action": "enqueue",
            "trigger_event_id": str(rt.id),
            "squad_task_id": str(task.id),
            "squad_role": role,
        },
        idempotency_key=assign_event_idempotency_key(
            agent_key=member.agent_id or member.id,
            issue_id=issue_id,
            trigger_event_id=rt.id,
        ),
    )


async def _notify_human_assigned(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member: Member,
    issue_id: uuid.UUID,
    task: SquadTask,
) -> None:
    await emit_notification_fanout(
        session,
        workspace_id=workspace_id,
        notification_type="assigned",
        issue_id=issue_id,
        recipient_ids=[member.id],
        title=task.title_snapshot,
        idempotency_key=f"squad-task:{task.id}:assign-notify:{member.id}",
    )


async def _dispatch_one(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task: SquadTask,
    now,
) -> None:
    """Dispatch a single ready subtask to its assignee."""
    # §4.4 member machine: pending ──deps satisfied / dispatched──► dispatching
    # ──member takes the run──► in_progress. Both edges are guard-checked.
    assert_transition(task.status, "dispatching", is_root=False)
    assert_transition("dispatching", "in_progress", is_root=False)
    old = task.status
    task.status = "in_progress"
    task.dispatched_at = now
    task.started_at = now
    task.updated_at = now
    await session.flush()
    await emit_task_status(
        session,
        workspace_id=workspace_id,
        task_id=task.id,
        squad_id=task.squad_id,
        old_status=old,
        new_status="in_progress",
        idempotency_key=f"squad-task:{task.id}:status:in_progress:{now.isoformat()}",
    )
    if task.assignee_id is None:
        return
    member = await session.scalar(
        select(Member).where(Member.workspace_id == workspace_id, Member.id == task.assignee_id)
    )
    if member is None:
        return
    await emit_task_stream_frame(
        session,
        workspace_id=workspace_id,
        task_id=task.id,
        event="subtask.assigned",
        data={
            "task_id": str(task.id),
            "title": task.title_snapshot,
            "assignee_id": str(task.assignee_id),
        },
        idempotency_key=f"squad-task:{task.id}:sse:assigned:{now.isoformat()}",
    )
    if member.member_type == "agent" and member.agent_id is not None:
        await _enqueue_agent_run(
            session,
            workspace_id=workspace_id,
            member=member,
            issue_id=task.issue_id,
            task=task,
            role="executor",
        )
    else:
        await _notify_human_assigned(
            session, workspace_id=workspace_id, member=member, issue_id=task.issue_id, task=task
        )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=task.squad_id,
        action="task_dispatched",
        actor_id=task.orchestrator_id,
        task_id=task.id,
        target_type="task",
        target_id=task.id,
        payload={"assignee_id": str(task.assignee_id)},
    )


async def _deps_satisfied(
    session: AsyncSession, *, workspace_id: uuid.UUID, task: SquadTask
) -> bool:
    """True when every dependency of ``task`` is ``done``."""
    deps = (
        await session.execute(
            select(SquadTaskDependency.depends_on_task_id).where(
                SquadTaskDependency.workspace_id == workspace_id,
                SquadTaskDependency.task_id == task.id,
            )
        )
    ).scalars().all()
    if not deps:
        return True
    not_done = await session.scalar(
        select(func.count())
        .select_from(SquadTask)
        .where(
            SquadTask.workspace_id == workspace_id,
            SquadTask.id.in_(deps),
            SquadTask.status != "done",
        )
    )
    return not not_done


async def dispatch_ready(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    root_task_id: uuid.UUID,
    now,
) -> int:
    """Dispatch every pending subtask in the tree whose deps are all done and
    whose stage allows (no lower stage still unfinished)."""
    tree = list(
        (
            await session.execute(
                select(SquadTask).where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.root_task_id == root_task_id,
                )
            )
        ).scalars()
    )
    dispatched = 0
    # Stage gating: a stage may start only once all lower stages are terminal.
    stages = sorted({t.stage for t in tree if t.stage is not None})
    blocked_stages: set[int] = set()
    for stage in stages:
        lower_unfinished = any(
            t.stage is not None
            and t.stage < stage
            and t.status not in ("done", "failed", "cancelled")
            for t in tree
        )
        if lower_unfinished:
            blocked_stages.add(stage)
    for task in tree:
        if task.status != "pending" or task.parent_task_id is None:
            continue
        if task.stage is not None and task.stage in blocked_stages:
            continue
        if not await _deps_satisfied(session, workspace_id=workspace_id, task=task):
            continue
        await _dispatch_one(session, workspace_id=workspace_id, task=task, now=now)
        dispatched += 1
    # §4.4: dispatching ──subtasks dispatched──► in_progress. The root itself
    # never gets _dispatch_one'd, so hop it explicitly once anything moved.
    if dispatched:
        root = next((t for t in tree if t.id == root_task_id), None)
        if root is not None and root.status == "dispatching":
            assert_transition(root.status, "in_progress", is_root=True)
            old = root.status
            root.status = "in_progress"
            root.updated_at = now
            await session.flush()
            await emit_task_status(
                session,
                workspace_id=workspace_id,
                task_id=root.id,
                squad_id=root.squad_id,
                old_status=old,
                new_status="in_progress",
                idempotency_key=f"squad-task:{root.id}:status:in_progress:dispatched:{now.isoformat()}",
            )
    return dispatched


# -- cancel cascade -----------------------------------------------------------


async def _cancel_execution(
    session: AsyncSession, *, workspace_id: uuid.UUID, execution_id: uuid.UUID, reason: str, now
) -> None:
    execution = (
        await session.execute(
            select(TaskExecution)
            .where(TaskExecution.workspace_id == workspace_id, TaskExecution.id == execution_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if execution is None or execution.status in EXECUTION_TERMINAL:
        return
    if execution.status == "queued":
        execution.status = "cancelled"
        execution.failure_reason = reason
        execution.finished_at = now
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=f"execution:{execution.id}",
            event="execution.cancelled",
            data={"execution_id": str(execution.id), "failure_reason": reason},
            idempotency_key=f"execution:{execution.id}:cancelled",
        )
    else:
        # claimed / running / cancelling → two-phase cancel (daemon finalizes).
        execution.status = "cancelling"
    execution.updated_at = now


async def cascade_cancel_task(
    session: AsyncSession, *, workspace_id: uuid.UUID, task: SquadTask, reason: str, now
) -> None:
    """Cancel ``task`` and all unfinished descendants; keep finished results."""
    if task.status in ("done", "failed", "cancelled"):
        return
    old = task.status
    if task.execution_id is not None:
        await _cancel_execution(
            session, workspace_id=workspace_id, execution_id=task.execution_id, reason=reason, now=now
        )
    task.status = "cancelled"
    task.failure_reason = reason
    task.finished_at = now
    task.updated_at = now
    await session.flush()
    await emit_task_status(
        session,
        workspace_id=workspace_id,
        task_id=task.id,
        squad_id=task.squad_id,
        old_status=old,
        new_status="cancelled",
        idempotency_key=f"squad-task:{task.id}:status:cancelled:{now.isoformat()}",
    )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=task.squad_id,
        action="task_cancelled",
        actor_id=None,
        task_id=task.id,
        target_type="task",
        target_id=task.id,
        payload={"reason": reason},
    )
    for child in await _children(session, workspace_id=workspace_id, task_id=task.id):
        await cascade_cancel_task(session, workspace_id=workspace_id, task=child, reason=reason, now=now)


async def cancel_assignment(
    session: AsyncSession, *, workspace_id: uuid.UUID, assignment: IssueSquadAssignment, reason: str, now
) -> None:
    """Cancel an active assignment + cascade-cancel its root tree (§2.5)."""
    if assignment.status != "active":
        return
    assignment.status = "cancelled"
    assignment.cancel_reason = reason
    assignment.cancelled_at = now
    assignment.updated_at = now
    if assignment.root_task_id is not None:
        root = (
            await session.execute(
                select(SquadTask)
                .where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.id == assignment.root_task_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if root is not None:
            await cascade_cancel_task(
                session, workspace_id=workspace_id, task=root, reason=reason, now=now
            )
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=assignment.squad_id),
        event="squad_assignment.changed",
        data={
            "issue_id": str(assignment.issue_id),
            "squad_id": str(assignment.squad_id),
            "assignment_id": str(assignment.id),
            "status": "cancelled",
            "cancel_reason": reason,
        },
        idempotency_key=f"squad-assignment:{assignment.id}:cancelled:{now.isoformat()}",
    )


# -- leader change / departure (§2.5) -----------------------------------------


async def change_primary_leader_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad: Squad,
    new_leader_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    now,
) -> None:
    """Rotate the primary leader; propagate to all active assignments + issues
    in the SAME transaction. Root tasks are NOT cancelled (squad unchanged)."""
    leader_row = await session.scalar(
        select(SquadMember).where(
            SquadMember.squad_id == squad.id,
            SquadMember.member_id == new_leader_id,
            SquadMember.role == "leader",
            SquadMember.left_at.is_(None),
        )
    )
    if leader_row is None:
        raise BusinessRuleError("new leader is not an active leader member", code="no_leader")
    if squad.primary_leader_id == new_leader_id:
        return
    squad.primary_leader_id = new_leader_id
    squad.updated_at = now
    assignments = (
        await session.execute(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.workspace_id == workspace_id,
                IssueSquadAssignment.squad_id == squad.id,
                IssueSquadAssignment.status == "active",
            )
        )
    ).scalars().all()
    for assignment in assignments:
        assignment.leader_member_id = new_leader_id
        assignment.updated_at = now
        await session.execute(
            update(Issue)
            .where(Issue.workspace_id == workspace_id, Issue.id == assignment.issue_id)
            .values(assignee_id=new_leader_id, updated_at=now)
        )
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=SQUAD_CHANNEL.format(squad_id=squad.id),
            event="squad_assignment.changed",
            data={
                "issue_id": str(assignment.issue_id),
                "squad_id": str(squad.id),
                "assignment_id": str(assignment.id),
                "status": "active",
                "leader_member_id": str(new_leader_id),
            },
            idempotency_key=f"squad-assignment:{assignment.id}:leader:{now.isoformat()}",
        )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=squad.id,
        action="role_changed",
        actor_id=actor_id,
        target_type="squad",
        target_id=squad.id,
        payload={"primary_leader_id": str(new_leader_id)},
    )
    # §2.5 / §5.1⑤: a replacement leader unblocks roots that were parked with
    # failure_reason='leader_lost' (same transaction as the propagation above).
    await unblock_leader_lost_roots_tx(
        session,
        workspace_id=workspace_id,
        squad=squad,
        assignments=assignments,
        new_leader_id=new_leader_id,
        now=now,
    )


async def unblock_leader_lost_roots_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad: Squad,
    assignments: list[IssueSquadAssignment],
    new_leader_id: uuid.UUID,
    now,
) -> None:
    """Resume roots blocked by ``leader_lost`` now that a leader is back."""
    for assignment in assignments:
        if assignment.root_task_id is None:
            continue
        root = (
            await session.execute(
                select(SquadTask)
                .where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.id == assignment.root_task_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if root is None or root.status != "blocked" or root.failure_reason != "leader_lost":
            continue
        assert_transition(root.status, "in_progress", is_root=True)
        root.status = "in_progress"
        root.failure_reason = None
        if root.orchestrator_id is None:
            root.orchestrator_id = new_leader_id
        root.updated_at = now
        await session.flush()
        await emit_task_status(
            session,
            workspace_id=workspace_id,
            task_id=root.id,
            squad_id=squad.id,
            old_status="blocked",
            new_status="in_progress",
            idempotency_key=f"squad-task:{root.id}:status:in_progress:unblocked:{now.isoformat()}",
        )
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=squad.id,
            action="task_started",
            actor_id=None,
            task_id=root.id,
            target_type="task",
            target_id=root.id,
            payload={"unblocked": True, "leader_member_id": str(new_leader_id)},
        )
        # Re-wake the leader and resume any ready subtasks.
        leader_member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.id == new_leader_id
            )
        )
        if leader_member is not None and leader_member.member_type == "agent":
            await _enqueue_agent_run(
                session,
                workspace_id=workspace_id,
                member=leader_member,
                issue_id=root.issue_id,
                task=root,
                role="orchestrator",
            )
        await dispatch_ready(session, workspace_id=workspace_id, root_task_id=root.id, now=now)
        # Children may all have finished while the root was parked — settle
        # aggregation that was deferred during the blocked window.
        await try_aggregate_parent_tx(session, workspace_id=workspace_id, parent=root, now=now)


async def handle_leader_departure_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad: Squad,
    departed_member_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    now,
) -> None:
    """Leader left: if others remain, rotate primary; else block active roots."""
    if squad.primary_leader_id != departed_member_id:
        return
    remaining = (
        await session.execute(
            select(SquadMember.member_id).where(
                SquadMember.squad_id == squad.id,
                SquadMember.role == "leader",
                SquadMember.left_at.is_(None),
            )
        )
    ).scalars().all()
    if remaining:
        await change_primary_leader_tx(
            session,
            workspace_id=workspace_id,
            squad=squad,
            new_leader_id=remaining[0],
            actor_id=actor_id,
            now=now,
        )
        return
    # No replacement: keep assignments, block their roots (squad.md §2.5).
    squad.primary_leader_id = None
    squad.updated_at = now
    assignments = (
        await session.execute(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.workspace_id == workspace_id,
                IssueSquadAssignment.squad_id == squad.id,
                IssueSquadAssignment.status == "active",
            )
        )
    ).scalars().all()
    for assignment in assignments:
        if assignment.root_task_id is None:
            continue
        root = (
            await session.execute(
                select(SquadTask)
                .where(
                    SquadTask.workspace_id == workspace_id,
                    SquadTask.id == assignment.root_task_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if root is None or root.status in ("done", "failed", "cancelled", "blocked"):
            continue
        old = root.status
        root.status = "blocked"
        root.failure_reason = "leader_lost"
        root.updated_at = now
        await session.flush()
        await emit_task_status(
            session,
            workspace_id=workspace_id,
            task_id=root.id,
            squad_id=squad.id,
            old_status=old,
            new_status="blocked",
            idempotency_key=f"squad-task:{root.id}:status:blocked:{now.isoformat()}",
        )
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=squad.id,
            action="task_blocked",
            actor_id=None,
            task_id=root.id,
            target_type="task",
            target_id=root.id,
            payload={"failure_reason": "leader_lost"},
        )
        # Notify the issue reporter (initiator) + admins.
        reporter = await session.scalar(
            select(Issue.reporter_id).where(
                Issue.workspace_id == workspace_id, Issue.id == assignment.issue_id
            )
        )
        admins = (
            await session.execute(
                select(Member.id).where(
                    Member.workspace_id == workspace_id,
                    Member.role.in_(["admin", "owner"]),
                    Member.status == "active",
                    Member.member_type == "human",
                )
            )
        ).scalars().all()
        recipients = [r for r in {reporter, *admins} if r is not None]
        if recipients:
            await emit_notification_fanout(
                session,
                workspace_id=workspace_id,
                notification_type="execution_finished",
                issue_id=assignment.issue_id,
                recipient_ids=recipients,
                execution_status="failed",
                title=root.title_snapshot,
                idempotency_key=f"squad-task:{root.id}:leader-lost-notify",
            )


# -- issue-reassigned watcher (§2.5) ------------------------------------------


async def on_issue_assignee_changed_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue_id: uuid.UUID,
    new_assignee_id: uuid.UUID | None,
    now,
) -> None:
    """If the issue moved away from its active squad leader, cancel that
    assignment (``cancel_reason='issue_reassigned'``) + cascade the root."""
    assignment = (
        await session.execute(
            select(IssueSquadAssignment)
            .where(
                IssueSquadAssignment.workspace_id == workspace_id,
                IssueSquadAssignment.issue_id == issue_id,
                IssueSquadAssignment.status == "active",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if assignment is None:
        return
    if new_assignee_id is not None and new_assignee_id == assignment.leader_member_id:
        return  # still the squad's leader — no change
    await cancel_assignment(
        session,
        workspace_id=workspace_id,
        assignment=assignment,
        reason="issue_reassigned",
        now=now,
    )


def make_issue_assignee_watcher(
    clock: Callable[[], object] | None = None,
):
    """Return the callable IssueService invokes on assignee change (same txn)."""

    async def watcher(
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        issue_id: uuid.UUID,
        previous_assignee_id: uuid.UUID | None,
        new_assignee_id: uuid.UUID | None,
    ) -> None:
        await on_issue_assignee_changed_tx(
            session,
            workspace_id=workspace_id,
            issue_id=issue_id,
            new_assignee_id=new_assignee_id,
            now=now_utc(clock),
        )

    return watcher


# -- instruction → run (loop-suppressed) ----------------------------------------


async def maybe_trigger_instruction_run(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    squad: Squad,
    message,
    sender: Member,
    recipient_id: uuid.UUID,
    task_id: uuid.UUID | None,
) -> None:
    """A leader→agent instruction triggers the recipient's run; any other
    direction is notification-only (leader↔member loop suppression, §5.3)."""
    sender_is_leader = await session.scalar(
        select(SquadMember.id).where(
            SquadMember.squad_id == squad.id,
            SquadMember.member_id == sender.id,
            SquadMember.role == "leader",
            SquadMember.left_at.is_(None),
        )
    )
    if sender_is_leader is None:
        return
    recipient = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id, Member.id == recipient_id
        )
    )
    if recipient is None or recipient.member_type != "agent" or recipient.agent_id is None:
        return
    # Never re-wake the sender (loop guard).
    if recipient.id == sender.id:
        return
    issue_id = None
    if task_id is not None:
        issue_id = await session.scalar(
            select(SquadTask.issue_id).where(
                SquadTask.workspace_id == workspace_id, SquadTask.id == task_id
            )
        )
    rt = await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=squad.id),
        event="squad_message.created",
        data={"squad_id": str(squad.id), "message_id": str(message.id), "trigger": True},
        idempotency_key=f"squad-message:{message.id}:instruction-trigger",
    )
    await emit_event(
        session,
        workspace_id=workspace_id,
        event_type=ASSIGN_EVENT_TYPE,
        payload={
            "issue_id": str(issue_id) if issue_id else None,
            "agent_member_id": str(recipient.id),
            "agent_id": str(recipient.agent_id),
            "trigger": "mention",
            "action": "enqueue",
            "trigger_event_id": str(rt.id),
        },
        idempotency_key=assign_event_idempotency_key(
            agent_key=recipient.agent_id,
            issue_id=issue_id or squad.id,
            trigger_event_id=rt.id,
        ),
    )


# -- plan approval (§6.10) ----------------------------------------------------


async def create_plan_approval_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    root_task: SquadTask,
    requested_by_member_id: uuid.UUID,
    plan_markdown: str | None,
    subtask_count: int,
    now,
) -> Approval:
    """Create (or return existing) pending squad_plan approval for a root task."""
    existing = (
        await session.execute(
            select(Approval).where(
                Approval.workspace_id == workspace_id,
                Approval.subject_type == "squad_plan",
                Approval.subject_task_id == root_task.id,
                Approval.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    expires_at = now + PLAN_APPROVAL_TTL
    digest = (plan_markdown or "")[:280]
    approval = Approval(
        workspace_id=workspace_id,
        subject_type="squad_plan",
        subject_task_id=root_task.id,
        requested_by_member_id=requested_by_member_id,
        action_summary={
            "plan_digest": digest,
            "impact_scope": f"issue {root_task.issue_id} subtree",
            "subtask_count": subtask_count,
            "expires_at": expires_at.isoformat(),
        },
        status="pending",
        requested_at=now,
        expires_at=expires_at,
    )
    session.add(approval)
    await session.flush()
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=SQUAD_CHANNEL.format(squad_id=root_task.squad_id),
        event="approval.created",
        data={"approval_id": str(approval.id), "subject_task_id": str(root_task.id)},
        idempotency_key=f"approval:{approval.id}:created",
    )
    return approval


async def apply_plan_decision_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task: SquadTask,
    decision: str,
    now,
) -> None:
    """Relay-side effect of a squad_plan approval decision on the root task."""
    if decision == "approved":
        if task.status != "awaiting_plan_approval":
            return
        old = task.status
        task.status = "dispatching"
        task.updated_at = now
        await session.flush()
        await emit_task_status(
            session,
            workspace_id=workspace_id,
            task_id=task.id,
            squad_id=task.squad_id,
            old_status=old,
            new_status="dispatching",
            idempotency_key=f"squad-task:{task.id}:status:dispatching:approved:{now.isoformat()}",
        )
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=task.squad_id,
            action="plan_approved",
            actor_id=None,
            task_id=task.id,
            target_type="task",
            target_id=task.id,
        )
        await dispatch_ready(session, workspace_id=workspace_id, root_task_id=task.id, now=now)
    elif decision == "rejected":
        if task.status != "awaiting_plan_approval":
            return
        old = task.status
        task.status = "decomposing"
        task.updated_at = now
        await session.flush()
        await emit_task_status(
            session,
            workspace_id=workspace_id,
            task_id=task.id,
            squad_id=task.squad_id,
            old_status=old,
            new_status="decomposing",
            idempotency_key=f"squad-task:{task.id}:status:decomposing:rejected:{now.isoformat()}",
        )
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=task.squad_id,
            action="plan_rejected",
            actor_id=None,
            task_id=task.id,
            target_type="task",
            target_id=task.id,
        )
    elif decision == "expired":
        if task.status in ("done", "failed", "cancelled"):
            return
        old = task.status
        task.status = "failed"
        task.failure_reason = "approval_expired"
        task.finished_at = now
        task.updated_at = now
        await session.flush()
        await emit_task_status(
            session,
            workspace_id=workspace_id,
            task_id=task.id,
            squad_id=task.squad_id,
            old_status=old,
            new_status="failed",
            idempotency_key=f"squad-task:{task.id}:status:failed:expired:{now.isoformat()}",
        )
        await record_squad_activity(
            session,
            workspace_id=workspace_id,
            squad_id=task.squad_id,
            action="task_failed",
            actor_id=None,
            task_id=task.id,
            target_type="task",
            target_id=task.id,
            payload={"failure_reason": "approval_expired"},
        )


# -- aggregation --------------------------------------------------------------


async def _maybe_aggregate_tx(
    session: AsyncSession, *, workspace_id: uuid.UUID, task: SquadTask, now
) -> None:
    """When all of ``task``'s parent's children are terminal, aggregate upward."""
    parent_id = task.parent_task_id
    if parent_id is None:
        return
    parent = (
        await session.execute(
            select(SquadTask)
            .where(SquadTask.workspace_id == workspace_id, SquadTask.id == parent_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if parent is None:
        return
    await try_aggregate_parent_tx(session, workspace_id=workspace_id, parent=parent, now=now)


async def try_aggregate_parent_tx(
    session: AsyncSession, *, workspace_id: uuid.UUID, parent: SquadTask, now
) -> None:
    """Aggregate ``parent`` when every direct child has reached a terminal
    state (§S8 / §4.3-7). An AGENT orchestrator gets a summary run first
    (``squad_role='aggregator'``); human leaders resolve synchronously with a
    concatenated summary."""
    if parent.status in ("done", "failed", "cancelled", "aggregating", "blocked"):
        return
    siblings = await _children(session, workspace_id=workspace_id, task_id=parent.id)
    if not siblings:
        return
    if any(s.status not in ("done", "failed", "cancelled") for s in siblings):
        return
    assert_transition(parent.status, "aggregating", is_root=_is_root(parent))
    old = parent.status
    parent.status = "aggregating"
    parent.updated_at = now
    await session.flush()
    await emit_task_status(
        session,
        workspace_id=workspace_id,
        task_id=parent.id,
        squad_id=parent.squad_id,
        old_status=old,
        new_status="aggregating",
        idempotency_key=f"squad-task:{parent.id}:status:aggregating:{now.isoformat()}",
    )
    leader = None
    if parent.orchestrator_id is not None:
        leader = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id, Member.id == parent.orchestrator_id
            )
        )
    if leader is not None and leader.member_type == "agent" and leader.agent_id is not None:
        # Wake the leader to produce the summary; the execution-terminal
        # observer resolves aggregation (role='aggregator').
        await _enqueue_agent_run(
            session,
            workspace_id=workspace_id,
            member=leader,
            issue_id=parent.issue_id,
            task=parent,
            role="aggregator",
        )
        return
    await resolve_aggregation_tx(session, workspace_id=workspace_id, parent=parent, now=now)


async def resolve_aggregation_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    parent: SquadTask,
    now,
    aggregator_failed: bool = False,
) -> None:
    """Resolve an ``aggregating`` parent: concatenate child summaries, settle
    done/failed and write the aggregate back up the tree / to the issue."""
    if parent.status != "aggregating":
        return
    siblings = await _children(session, workspace_id=workspace_id, task_id=parent.id)
    summaries = [s.result_summary for s in siblings if s.result_summary]
    any_failed = any(s.status in ("failed", "cancelled") for s in siblings)
    target = "failed" if any_failed else "done"
    assert_transition(parent.status, target, is_root=_is_root(parent))
    old = parent.status
    parent.status = target
    parent.result_summary = " | ".join(summaries) if summaries else None
    parent.finished_at = now
    parent.updated_at = now
    await session.flush()
    await emit_task_status(
        session,
        workspace_id=workspace_id,
        task_id=parent.id,
        squad_id=parent.squad_id,
        old_status=old,
        new_status=parent.status,
        idempotency_key=f"squad-task:{parent.id}:status:{parent.status}:agg:{now.isoformat()}",
    )
    await emit_task_stream_frame(
        session,
        workspace_id=workspace_id,
        task_id=parent.id,
        event="task.aggregated",
        data={
            "task_id": str(parent.id),
            "status": parent.status,
            "result_summary": parent.result_summary,
        },
        idempotency_key=f"squad-task:{parent.id}:sse:aggregated:{now.isoformat()}",
    )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=parent.squad_id,
        action="task_aggregated",
        actor_id=parent.orchestrator_id,
        task_id=parent.id,
        target_type="task",
        target_id=parent.id,
        payload={"status": parent.status, "aggregator_failed": aggregator_failed},
    )
    if _is_root(parent):
        await _finalize_root_tx(session, workspace_id=workspace_id, root=parent, now=now)
    else:
        await _maybe_aggregate_tx(session, workspace_id=workspace_id, task=parent, now=now)


async def _finalize_root_tx(
    session: AsyncSession, *, workspace_id: uuid.UUID, root: SquadTask, now
) -> None:
    """Root reached a terminal state → complete its assignment + notify initiator."""
    assignment = (
        await session.execute(
            select(IssueSquadAssignment)
            .where(
                IssueSquadAssignment.workspace_id == workspace_id,
                IssueSquadAssignment.root_task_id == root.id,
                IssueSquadAssignment.status == "active",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if assignment is not None:
        assignment.status = "completed"
        assignment.cancel_reason = "done"
        assignment.cancelled_at = now
        assignment.updated_at = now
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            channel=SQUAD_CHANNEL.format(squad_id=root.squad_id),
            event="squad_assignment.changed",
            data={
                "issue_id": str(assignment.issue_id),
                "squad_id": str(assignment.squad_id),
                "assignment_id": str(assignment.id),
                "status": "completed",
            },
            idempotency_key=f"squad-assignment:{assignment.id}:completed:{now.isoformat()}",
        )
    reporter = await session.scalar(
        select(Issue.reporter_id).where(Issue.workspace_id == workspace_id, Issue.id == root.issue_id)
    )
    if reporter is not None:
        await emit_notification_fanout(
            session,
            workspace_id=workspace_id,
            notification_type="execution_finished",
            issue_id=root.issue_id,
            recipient_ids=[reporter],
            execution_status="failed" if root.status == "failed" else "completed",
            title=root.title_snapshot,
            preview=root.result_summary,
            idempotency_key=f"squad-task:{root.id}:finished-notify",
        )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=root.squad_id,
        action="task_finished" if root.status == "done" else "task_failed",
        actor_id=root.orchestrator_id,
        task_id=root.id,
        target_type="task",
        target_id=root.id,
        payload={"status": root.status},
    )


async def observe_execution_finished_tx(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    execution_id: uuid.UUID,
    status: str,
    failure_reason: str | None,
    now,
) -> None:
    """Session-level execution-terminal observation (runs on the relay savepoint
    or inside the service transaction). Correlates via ``task_spec.squad_task_id``."""
    execution = await session.scalar(
        select(TaskExecution).where(
            TaskExecution.workspace_id == workspace_id, TaskExecution.id == execution_id
        )
    )
    if execution is None:
        return
    spec = execution.task_spec or {}
    task_id_raw = spec.get("squad_task_id")
    if not task_id_raw:
        return  # not a squad-spawned execution
    role = spec.get("squad_role", "executor")
    task = (
        await session.execute(
            select(SquadTask)
            .where(SquadTask.workspace_id == workspace_id, SquadTask.id == uuid.UUID(task_id_raw))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None or task.status in ("done", "failed", "cancelled"):
        return
    if task.execution_id is None:
        task.execution_id = execution.id
    if role == "aggregator":
        # The leader's summary run terminated: settle the aggregating parent.
        # An aggregator-run failure must not strand the tree — resolve with the
        # concatenated child summaries either way (audit via activity payload).
        if task.status == "aggregating":
            await resolve_aggregation_tx(
                session,
                workspace_id=workspace_id,
                parent=task,
                now=now,
                aggregator_failed=status != "completed",
            )
        return
    if role == "orchestrator":
        # The leader's decompose/evaluate run terminated. Closed loop (§5.1):
        # completed + subtasks   → result 'action'
        # completed + no output  → result 'no_action', close the task done
        # failed    + no output  → result 'failed', task failed
        child_count = await session.scalar(
            select(func.count())
            .select_from(SquadTask)
            .where(
                SquadTask.workspace_id == workspace_id,
                SquadTask.parent_task_id == task.id,
            )
        )
        if status == "completed":
            if child_count:
                await record_squad_activity(
                    session,
                    workspace_id=workspace_id,
                    squad_id=task.squad_id,
                    action="leader_evaluated",
                    actor_id=task.orchestrator_id,
                    task_id=task.id,
                    target_type="task",
                    target_id=task.id,
                    payload={"result": "action", "subtask_count": int(child_count)},
                )
            elif task.status in ("pending", "decomposing"):
                if task.status == "pending":
                    assert_transition(task.status, "decomposing", is_root=_is_root(task))
                    old = task.status
                    task.status = "decomposing"
                    task.updated_at = now
                    await session.flush()
                    await emit_task_status(
                        session,
                        workspace_id=workspace_id,
                        task_id=task.id,
                        squad_id=task.squad_id,
                        old_status=old,
                        new_status="decomposing",
                        idempotency_key=f"squad-task:{task.id}:status:decomposing:noaction:{now.isoformat()}",
                    )
                assert_transition(task.status, "done", is_root=_is_root(task))
                old = task.status
                task.status = "done"
                task.result_summary = task.result_summary or "leader evaluation: no action required"
                task.finished_at = now
                task.updated_at = now
                await session.flush()
                await emit_task_status(
                    session,
                    workspace_id=workspace_id,
                    task_id=task.id,
                    squad_id=task.squad_id,
                    old_status=old,
                    new_status="done",
                    idempotency_key=f"squad-task:{task.id}:status:done:noaction:{now.isoformat()}",
                )
                await record_squad_activity(
                    session,
                    workspace_id=workspace_id,
                    squad_id=task.squad_id,
                    action="leader_evaluated",
                    actor_id=task.orchestrator_id,
                    task_id=task.id,
                    target_type="task",
                    target_id=task.id,
                    payload={"result": "no_action"},
                )
                await record_squad_activity(
                    session,
                    workspace_id=workspace_id,
                    squad_id=task.squad_id,
                    action="task_finished",
                    actor_id=task.orchestrator_id,
                    task_id=task.id,
                    target_type="task",
                    target_id=task.id,
                    payload={"status": "done", "evaluation": "no_action"},
                )
                if _is_root(task):
                    await _finalize_root_tx(session, workspace_id=workspace_id, root=task, now=now)
                else:
                    await _maybe_aggregate_tx(session, workspace_id=workspace_id, task=task, now=now)
        elif not child_count and task.status in ("pending", "decomposing"):
            old = task.status
            task.status = "failed"
            task.failure_reason = failure_reason or "orchestrator_failed"
            task.finished_at = now
            task.updated_at = now
            await session.flush()
            await emit_task_status(
                session,
                workspace_id=workspace_id,
                task_id=task.id,
                squad_id=task.squad_id,
                old_status=old,
                new_status="failed",
                idempotency_key=f"squad-task:{task.id}:status:failed:orch:{now.isoformat()}",
            )
            await record_squad_activity(
                session,
                workspace_id=workspace_id,
                squad_id=task.squad_id,
                action="leader_evaluated",
                actor_id=task.orchestrator_id,
                task_id=task.id,
                target_type="task",
                target_id=task.id,
                payload={"result": "failed", "failure_reason": task.failure_reason},
            )
        return
    # Executor terminal mapping.
    old = task.status
    if status == "completed":
        task.status = "done"
        task.result_summary = task.result_summary or "completed"
    else:
        task.status = "failed"
        task.failure_reason = failure_reason or status
    task.finished_at = now
    task.updated_at = now
    await session.flush()
    await emit_task_status(
        session,
        workspace_id=workspace_id,
        task_id=task.id,
        squad_id=task.squad_id,
        old_status=old,
        new_status=task.status,
        idempotency_key=f"squad-task:{task.id}:status:{task.status}:exec:{now.isoformat()}",
    )
    await record_squad_activity(
        session,
        workspace_id=workspace_id,
        squad_id=task.squad_id,
        action="task_finished" if task.status == "done" else "task_failed",
        actor_id=None,
        task_id=task.id,
        target_type="task",
        target_id=task.id,
        payload={"execution_status": status},
    )
    # Unlock dependents + aggregate upward.
    root_id = task.root_task_id or task.id
    await dispatch_ready(session, workspace_id=workspace_id, root_task_id=root_id, now=now)
    if _is_root(task):
        if task.status in ("done", "failed"):
            await _finalize_root_tx(session, workspace_id=workspace_id, root=task, now=now)
    else:
        await _maybe_aggregate_tx(session, workspace_id=workspace_id, task=task, now=now)


class SquadTaskService:
    """Transaction-owning orchestration entry points."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], object] | None = None,
        comment_service=None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock
        # §S8/§4.3-7 parent-issue summary writeback for the synchronous
        # (human-leader) aggregation path; None degrades to no writeback.
        self._comment_service = comment_service

    # -- assignment (§2.5 / T23) ----------------------------------------------

    async def assign_issue_to_squad(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, body
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = now_utc(self._clock)
            squad = (
                await session.execute(
                    select(Squad)
                    .where(
                        Squad.workspace_id == workspace_id,
                        Squad.id == squad_id,
                        Squad.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if squad is None:
                raise NotFoundError("squad not found")
            leader_id = squad.primary_leader_id
            if leader_id is None:
                raise BusinessRuleError(
                    "squad has no leader; cannot accept assignment", code="squad_no_leader"
                )
            issue = (
                await session.execute(
                    select(Issue)
                    .where(Issue.workspace_id == workspace_id, Issue.id == uuid.UUID(body.issue_id))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if issue is None:
                raise NotFoundError("issue not found")

            active = (
                await session.execute(
                    select(IssueSquadAssignment)
                    .where(
                        IssueSquadAssignment.workspace_id == workspace_id,
                        IssueSquadAssignment.issue_id == issue.id,
                        IssueSquadAssignment.status == "active",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()

            # Duplicate dispatch to the SAME squad → no-op (return existing).
            if active is not None and active.squad_id == squad.id:
                return await self._render_assignment(session, active, noop=True)

            superseded_id = None
            superseded_root = None
            if active is not None:
                # Same-leader-cross-squad OR different squad → NEVER a no-op.
                superseded_id = str(active.id)
                superseded_root = str(active.root_task_id) if active.root_task_id else None
                await cancel_assignment(
                    session, workspace_id=workspace_id, assignment=active, reason="reassigned", now=now
                )

            assignment = IssueSquadAssignment(
                workspace_id=workspace_id,
                issue_id=issue.id,
                squad_id=squad.id,
                leader_member_id=leader_id,
                status="active",
                assigned_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(assignment)
            root = SquadTask(
                workspace_id=workspace_id,
                squad_id=squad.id,
                issue_id=issue.id,
                parent_task_id=None,
                depth=0,
                title_snapshot=issue.title,
                status="pending",
                orchestrator_id=leader_id,
                created_at=now,
                updated_at=now,
            )
            session.add(root)
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_issue_squad_active"):
                    raise ConflictError(
                        "issue already has an active squad assignment", code="conflict"
                    ) from exc
                raise
            # Two-way backfill (assignment ↔ root task).
            root.root_task_id = root.id
            assignment.root_task_id = root.id
            # Exclusive assignee model: the leader becomes the issue's assignee.
            issue.assignee_id = leader_id
            issue.updated_at = now
            await session.flush()

            await record_squad_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="task_received",
                actor_id=actor.id,
                task_id=root.id,
                target_type="task",
                target_id=root.id,
                payload={"issue_id": str(issue.id)},
            )
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                event="squad_assignment.changed",
                data={
                    "issue_id": str(issue.id),
                    "squad_id": str(squad.id),
                    "assignment_id": str(assignment.id),
                    "status": "active",
                },
                idempotency_key=f"squad-assignment:{assignment.id}:active",
            )
            # Wake the leader (orchestrator run). Human leaders get a notification.
            leader_member = await session.scalar(
                select(Member).where(Member.workspace_id == workspace_id, Member.id == leader_id)
            )
            if leader_member is not None:
                if leader_member.member_type == "agent" and leader_member.agent_id is not None:
                    await _enqueue_agent_run(
                        session,
                        workspace_id=workspace_id,
                        member=leader_member,
                        issue_id=issue.id,
                        task=root,
                        role="orchestrator",
                    )
                else:
                    await _notify_human_assigned(
                        session,
                        workspace_id=workspace_id,
                        member=leader_member,
                        issue_id=issue.id,
                        task=root,
                    )
            rendered = await self._render_assignment(session, assignment, noop=False)
            rendered["superseded_assignment_id"] = superseded_id
            rendered["superseded_root_task_id"] = superseded_root
            return rendered

    async def _render_assignment(
        self, session: AsyncSession, assignment: IssueSquadAssignment, *, noop: bool
    ) -> dict:
        root = None
        if assignment.root_task_id is not None:
            root = (
                await session.execute(
                    select(SquadTask).where(
                        SquadTask.workspace_id == assignment.workspace_id,
                        SquadTask.id == assignment.root_task_id,
                    )
                )
            ).scalar_one_or_none()
        ws = assignment.workspace_id
        base = f"/api/v1/workspaces/{ws}/squads/{assignment.squad_id}"
        return {
            "assignment_id": str(assignment.id),
            "id": str(root.id) if root else None,
            "squad_id": str(assignment.squad_id),
            "issue_id": str(assignment.issue_id),
            "parent_task_id": None,
            "root_task_id": str(root.id) if root else None,
            "depth": 0,
            "title_snapshot": root.title_snapshot if root else None,
            "status": root.status if root else None,
            "orchestrator_id": str(root.orchestrator_id) if root and root.orchestrator_id else None,
            "issue_assignee_id": str(assignment.leader_member_id),
            "noop": noop,
            "status_url": f"{base}/tasks/{root.id}/status" if root else None,
            "stream_url": f"{base}/tasks/{root.id}/stream" if root else None,
            "created_at": assignment.created_at.isoformat(),
        }

    # -- decomposition + DAG (§2.4 / §2.6) ------------------------------------

    async def create_subtasks(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, task_id: uuid.UUID, body
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = now_utc(self._clock)
            parent = await load_task(
                session, workspace_id=workspace_id, task_id=task_id, for_update=True
            )
            if parent.squad_id != squad_id:
                raise NotFoundError("task not found")
            # §3.4 / §5.3: only the task's orchestrator (leader agent via its
            # API token) or a workspace admin/owner may decompose.
            await self._assert_can_orchestrate(
                session, workspace_id=workspace_id, actor=actor, task=parent
            )
            squad = (
                await session.execute(
                    select(Squad)
                    .where(Squad.workspace_id == workspace_id, Squad.id == squad_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if squad is None:
                raise NotFoundError("squad not found")
            # Serialize dependency writes within this tree (T12 concurrent cycle).
            root_id = parent.root_task_id or parent.id
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:r))"), {"r": str(root_id)}
            )
            new_depth = parent.depth + 1
            if new_depth > squad.max_decompose_depth:
                raise BusinessRuleError(
                    "decompose depth exceeded", code="decompose_depth_exceeded"
                )
            # §4.4 state machine: the leader taking over hops the task
            # pending → decomposing before it can submit a plan / dispatch.
            target = (
                "awaiting_plan_approval" if squad.require_plan_approval else "dispatching"
            )
            if parent.status == "pending":
                assert_transition(parent.status, "decomposing", is_root=_is_root(parent))
                parent.status = "decomposing"
                parent.updated_at = now
                await session.flush()
                await emit_task_status(
                    session,
                    workspace_id=workspace_id,
                    task_id=parent.id,
                    squad_id=squad.id,
                    old_status="pending",
                    new_status="decomposing",
                    idempotency_key=f"squad-task:{parent.id}:status:decomposing:takeover:{now.isoformat()}",
                )
                await record_squad_activity(
                    session,
                    workspace_id=workspace_id,
                    squad_id=squad.id,
                    action="decompose_started",
                    actor_id=actor.id,
                    task_id=parent.id,
                    target_type="task",
                    target_id=parent.id,
                )
            assert_transition(parent.status, target, is_root=_is_root(parent))
            # Create subtasks.
            created: list[SquadTask] = []
            title_to_id: dict[str, uuid.UUID] = {}
            for index, sub in enumerate(body.subtasks):
                assignee_id = None
                if sub.assignee is not None:
                    assignee_id = uuid.UUID(sub.assignee.member_id)
                    is_member = await session.scalar(
                        select(SquadMember.id).where(
                            SquadMember.squad_id == squad.id,
                            SquadMember.member_id == assignee_id,
                            SquadMember.left_at.is_(None),
                        )
                    )
                    if is_member is None:
                        raise BusinessRuleError(
                            "assignee is not a squad member", code="assignee_not_member"
                        )
                child = SquadTask(
                    workspace_id=workspace_id,
                    squad_id=squad.id,
                    issue_id=parent.issue_id,
                    parent_task_id=parent.id,
                    root_task_id=root_id,
                    depth=new_depth,
                    title_snapshot=sub.title,
                    status="pending",
                    orchestrator_id=parent.orchestrator_id,
                    assignee_id=assignee_id,
                    stage=sub.stage,
                    created_at=now,
                    updated_at=now,
                )
                session.add(child)
                await session.flush()
                created.append(child)
                title_to_id[sub.title] = child.id
                title_to_id[f"temp_{index}"] = child.id
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=SQUAD_CHANNEL.format(squad_id=squad.id),
                    event="squad_task.status_changed",
                    data={
                        "task_id": str(child.id),
                        "squad_id": str(squad.id),
                        "old_status": None,
                        "new_status": "pending",
                        "subtask_created": True,
                        "title": child.title_snapshot,
                    },
                    idempotency_key=f"squad-task:{child.id}:created",
                )
                await emit_task_stream_frame(
                    session,
                    workspace_id=workspace_id,
                    task_id=child.id,
                    event="subtask.created",
                    data={
                        "task_id": str(child.id),
                        "title": child.title_snapshot,
                        "assignee_id": str(assignee_id) if assignee_id else None,
                    },
                    idempotency_key=f"squad-task:{child.id}:sse:created",
                )
            # Dependencies (title / temp_ref / task_id).
            dep_pairs = []
            for sub in body.subtasks:
                task_row = title_to_id[sub.title]
                for ref in sub.depends_on:
                    dep_id = title_to_id.get(ref)
                    if dep_id is None:
                        try:
                            dep_id = uuid.UUID(ref)
                        except ValueError as exc:
                            # A dangling reference is a request validation
                            # failure (§3.3 validation_error), NOT a cycle.
                            raise ValidationError(
                                "unknown dependency reference", details={"ref": ref[:120]}
                            ) from exc
                    if dep_id == task_row:
                        raise BusinessRuleError("self dependency", code="dependency_cycle")
                    await self._assert_no_cycle(
                        session, workspace_id=workspace_id, task_id=task_row, dep_id=dep_id
                    )
                    session.add(
                        SquadTaskDependency(
                            workspace_id=workspace_id,
                            task_id=task_row,
                            depends_on_task_id=dep_id,
                            created_at=now,
                        )
                    )
                    # Flush each edge so the NEXT cycle check sees it (the app
                    # session factory runs with autoflush off — a deferred flush
                    # would let a two-edge cycle slip through).
                    await session.flush()
                    dep_pairs.append({"task_id": str(task_row), "depends_on_task_id": str(dep_id)})

            # Plan submission + approval gate.
            parent.plan_markdown = body.plan_markdown or parent.plan_markdown
            parent.updated_at = now
            awaiting = bool(squad.require_plan_approval)
            approval_resp = None
            if awaiting:
                old = parent.status
                parent.status = "awaiting_plan_approval"
                await session.flush()
                await emit_task_status(
                    session,
                    workspace_id=workspace_id,
                    task_id=parent.id,
                    squad_id=squad.id,
                    old_status=old,
                    new_status="awaiting_plan_approval",
                    idempotency_key=f"squad-task:{parent.id}:status:awaiting:{now.isoformat()}",
                )
                root_task = parent if _is_root(parent) else await load_task(
                    session, workspace_id=workspace_id, task_id=parent.root_task_id
                )
                approval = await create_plan_approval_tx(
                    session,
                    workspace_id=workspace_id,
                    root_task=root_task,
                    requested_by_member_id=parent.orchestrator_id or actor.id,
                    plan_markdown=body.plan_markdown,
                    subtask_count=len(created),
                    now=now,
                )
                approval_resp = {
                    "id": str(approval.id),
                    "subject_type": approval.subject_type,
                    "subject_task_id": str(approval.subject_task_id),
                    "action_summary": approval.action_summary,
                    "status": approval.status,
                }
                await emit_task_stream_frame(
                    session,
                    workspace_id=workspace_id,
                    task_id=root_task.id,
                    event="plan.submitted",
                    data={
                        "task_id": str(root_task.id),
                        "approval_id": str(approval.id),
                        "subtask_count": len(created),
                    },
                    idempotency_key=f"squad-task:{root_task.id}:sse:plan-submitted:{approval.id}",
                )
            else:
                old = parent.status
                parent.status = "dispatching"
                await session.flush()
                await emit_task_status(
                    session,
                    workspace_id=workspace_id,
                    task_id=parent.id,
                    squad_id=squad.id,
                    old_status=old,
                    new_status="dispatching",
                    idempotency_key=f"squad-task:{parent.id}:status:dispatching:{now.isoformat()}",
                )
                await dispatch_ready(session, workspace_id=workspace_id, root_task_id=root_id, now=now)
            await record_squad_activity(
                session,
                workspace_id=workspace_id,
                squad_id=squad.id,
                action="task_decomposed",
                actor_id=actor.id,
                task_id=parent.id,
                target_type="task",
                target_id=parent.id,
                payload={"subtask_count": len(created), "awaiting_approval": awaiting},
            )
            created_resp = []
            for child in created:
                snap = None
                if child.assignee_id:
                    from mesh.squad.common import load_member_snapshot

                    snap = await load_member_snapshot(
                        session, workspace_id=workspace_id, member_id=child.assignee_id
                    )
                created_resp.append(
                    {
                        "id": str(child.id),
                        "title": child.title_snapshot,
                        "assignee_id": str(child.assignee_id) if child.assignee_id else None,
                        "assignee_type": snap["member_type"] if snap else None,
                        "stage": child.stage,
                        "depth": child.depth,
                        "status": child.status,
                    }
                )
            return {
                "root_task_id": str(root_id),
                "root_status": parent.status,
                "created_subtasks": created_resp,
                "dependencies": dep_pairs,
                "awaiting_approval": awaiting,
                "approval": approval_resp,
            }

    async def _assert_no_cycle(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, task_id: uuid.UUID, dep_id: uuid.UUID
    ) -> None:
        """Recursive-CTE reachability: adding ``task_id → dep_id`` is a cycle if
        ``dep_id`` already reaches ``task_id`` through existing edges."""
        # Dependency must live in the same tree / workspace.
        dep_task = await session.scalar(
            select(SquadTask.id).where(
                SquadTask.workspace_id == workspace_id, SquadTask.id == dep_id
            )
        )
        if dep_task is None:
            # Referencing a nonexistent task is a validation failure (§3.3
            # validation_error), not a cycle.
            raise ValidationError(
                "dependency task not found", details={"depends_on_task_id": str(dep_id)}
            )
        row = (
            await session.execute(
                text(
                    """
                    WITH RECURSIVE reach(cur) AS (
                        SELECT depends_on_task_id FROM squad_task_dependencies
                        WHERE task_id = :dep AND workspace_id = :ws
                        UNION
                        SELECT d.depends_on_task_id FROM squad_task_dependencies d
                        JOIN reach r ON d.task_id = r.cur
                    )
                    SELECT 1 FROM reach WHERE cur = :task LIMIT 1
                    """
                ),
                {"dep": dep_id, "ws": workspace_id, "task": task_id},
            )
        ).first()
        if row is not None:
            raise BusinessRuleError("dependency cycle detected", code="dependency_cycle")

    # -- authorization (§3.4 / §5.3) -------------------------------------------

    async def _assert_can_orchestrate(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, actor: Member, task: SquadTask
    ) -> None:
        """Decompose / dispatch gates: the caller must be this task's
        orchestrator (the leader agent calling via its API token) or a
        workspace admin/owner. Any other member — even a squad member — gets
        403, because ``agent:trigger`` RBAC alone is workspace-wide."""
        if actor.role in ("admin", "owner"):
            return
        if task.orchestrator_id is not None and actor.id == task.orchestrator_id:
            return
        raise ForbiddenError("caller is not this task's orchestrator")

    # -- manual dispatch / status / cancel ------------------------------------

    async def dispatch_task(
        self, *, actor: Member, workspace_id: uuid.UUID, squad_id: uuid.UUID, task_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = now_utc(self._clock)
            task = await load_task(session, workspace_id=workspace_id, task_id=task_id, for_update=True)
            if task.squad_id != squad_id:
                raise NotFoundError("task not found")
            root_id = task.root_task_id or task.id
            root = task if task.id == root_id else await load_task(
                session, workspace_id=workspace_id, task_id=root_id, for_update=True
            )
            await self._assert_can_orchestrate(
                session, workspace_id=workspace_id, actor=actor, task=root
            )
            # §3.3 conflict: dispatching a terminal tree is an illegal move.
            if root.status in ("done", "failed", "cancelled"):
                raise ConflictError(
                    "cannot dispatch a terminal task",
                    code="conflict",
                    details={"status": root.status},
                )
            count = await dispatch_ready(session, workspace_id=workspace_id, root_task_id=root_id, now=now)
            return {"dispatched": count, "task_id": str(task.id)}

    async def cancel_task(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID,
        task_id: uuid.UUID,
        reason: str | None,
    ) -> dict:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = now_utc(self._clock)
            task = await load_task(session, workspace_id=workspace_id, task_id=task_id, for_update=True)
            if task.squad_id != squad_id:
                raise NotFoundError("task not found")
            # §4.4 state machine: cancelling a terminal task → 409 conflict.
            assert_transition(task.status, "cancelled", is_root=_is_root(task))
            await cascade_cancel_task(
                session,
                workspace_id=workspace_id,
                task=task,
                reason=reason or "cancelled_by_user",
                now=now,
            )
            return {"cancelled": True, "task_id": str(task.id)}

    async def move_task_status(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        squad_id: uuid.UUID,
        task_id: uuid.UUID,
        status: str,
        result_summary: str | None = None,
    ) -> dict:
        """Manual status move — the kanban drag target (§4.2: humans may
        change subtask status; agent tasks flow automatically)."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = now_utc(self._clock)
            task = await load_task(session, workspace_id=workspace_id, task_id=task_id, for_update=True)
            if task.squad_id != squad_id:
                raise NotFoundError("task not found")
            root_id = task.root_task_id or task.id
            # Humans on the squad (any role incl. observer) or admin/owner may
            # move cards; the orchestrator agent may too (via API token).
            if actor.role not in ("admin", "owner"):
                allowed = actor.id == task.orchestrator_id
                if not allowed:
                    row = await session.scalar(
                        select(SquadMember.id).where(
                            SquadMember.squad_id == squad_id,
                            SquadMember.member_id == actor.id,
                            SquadMember.left_at.is_(None),
                        )
                    )
                    allowed = row is not None
                if not allowed:
                    raise ForbiddenError("not a member of this squad")
            if task.status == "pending" and status == "in_progress":
                # Kanban "start" drag: mirror _dispatch_one's two-edge hop
                # (pending → dispatching → in_progress), both guard-checked.
                assert_transition(task.status, "dispatching", is_root=_is_root(task))
                assert_transition("dispatching", "in_progress", is_root=_is_root(task))
                if task.dispatched_at is None:
                    task.dispatched_at = now
            else:
                assert_transition(task.status, status, is_root=_is_root(task))
            old = task.status
            task.status = status
            if status in ("done", "failed", "cancelled"):
                task.finished_at = now
                if result_summary is not None:
                    task.result_summary = result_summary
                if status == "cancelled" and task.failure_reason is None:
                    task.failure_reason = "cancelled_by_user"
            if old == "blocked":
                task.failure_reason = None
            if status == "in_progress" and task.started_at is None:
                task.started_at = now
            task.updated_at = now
            await session.flush()
            await emit_task_status(
                session,
                workspace_id=workspace_id,
                task_id=task.id,
                squad_id=task.squad_id,
                old_status=old,
                new_status=status,
                idempotency_key=f"squad-task:{task.id}:status:{status}:manual:{now.isoformat()}",
            )
            await record_squad_activity(
                session,
                workspace_id=workspace_id,
                squad_id=task.squad_id,
                action=(
                    "task_finished" if status == "done"
                    else "task_failed" if status == "failed"
                    else "task_cancelled" if status == "cancelled"
                    else "task_started"
                ),
                actor_id=actor.id,
                task_id=task.id,
                target_type="task",
                target_id=task.id,
                payload={"from": old, "to": status, "manual": True},
            )
            if status == "done":
                await dispatch_ready(session, workspace_id=workspace_id, root_task_id=root_id, now=now)
                if _is_root(task):
                    await _finalize_root_tx(session, workspace_id=workspace_id, root=task, now=now)
                else:
                    await _maybe_aggregate_tx(session, workspace_id=workspace_id, task=task, now=now)
            elif status in ("failed", "cancelled") and not _is_root(task):
                await _maybe_aggregate_tx(session, workspace_id=workspace_id, task=task, now=now)
            rendered = await self._render_task(session, task)
        # Post-commit §S8/§4.3-7 writeback: the synchronous (human-leader)
        # aggregation path resolves the root inside the transaction above but
        # has no execution.finished relay to post the parent-issue summary —
        # do it here, best-effort, with the SAME idempotency key as the relay
        # path so agent/human completions converge without duplicates.
        if status == "done":
            await self._writeback_summary_if_due(workspace_id, root_id)
        return rendered

    async def _writeback_summary_if_due(
        self, workspace_id: uuid.UUID, root_id: uuid.UUID
    ) -> None:
        """Post the leader's aggregate summary to the parent issue when the
        squad-assigned root is ``done`` with a summary. Re-checks all preconds
        on committed state; failures are logged, never raised."""
        if self._comment_service is None:
            return
        from mesh.squad.relay import summary_writeback_body, summary_writeback_key

        try:
            async with self._factory() as session:
                await set_tenant_context(session, workspace_id)
                root = await session.scalar(
                    select(SquadTask).where(
                        SquadTask.workspace_id == workspace_id, SquadTask.id == root_id
                    )
                )
                if root is None or root.status != "done" or not root.result_summary:
                    return
                assigned = await session.scalar(
                    select(IssueSquadAssignment.id).where(
                        IssueSquadAssignment.workspace_id == workspace_id,
                        IssueSquadAssignment.root_task_id == root.id,
                    )
                )
                if assigned is None:
                    return  # not a squad-assigned root
                if root.orchestrator_id is None:
                    return
                author = await session.scalar(
                    select(Member).where(
                        Member.workspace_id == workspace_id,
                        Member.id == root.orchestrator_id,
                    )
                )
                if author is None:
                    return
                await self._comment_service.create_comment(
                    workspace_id=workspace_id,
                    issue_id=root.issue_id,
                    author_member=author,
                    body_markdown=summary_writeback_body(root.title_snapshot, root.result_summary),
                    suppress_triggers=True,
                    idempotency_key=summary_writeback_key(root.id),
                )
        except Exception:  # noqa: BLE001 — best-effort side effect (§S8); a
            logger.exception(  # writeback failure must not fail the move
                "squad summary writeback failed for root %s", root_id
            )

    # -- execution terminal observation (§6.4) --------------------------------

    async def observe_execution_finished(
        self, *, workspace_id: uuid.UUID, execution_id: uuid.UUID, status: str, failure_reason: str | None
    ) -> None:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await observe_execution_finished_tx(
                session,
                workspace_id=workspace_id,
                execution_id=execution_id,
                status=status,
                failure_reason=failure_reason,
                now=now_utc(self._clock),
            )

    # -- reads ----------------------------------------------------------------

    async def get_task(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            task = await load_task(session, workspace_id=workspace_id, task_id=task_id)
            if task.squad_id != squad_id:
                raise NotFoundError("task not found")
            return await self._render_task(session, task)

    async def _render_task(self, session: AsyncSession, task: SquadTask) -> dict:
        from mesh.squad.common import load_member_snapshot

        assignee = await load_member_snapshot(
            session, workspace_id=task.workspace_id, member_id=task.assignee_id
        )
        deps = (
            await session.execute(
                select(SquadTaskDependency.depends_on_task_id).where(
                    SquadTaskDependency.workspace_id == task.workspace_id,
                    SquadTaskDependency.task_id == task.id,
                )
            )
        ).scalars().all()
        blocked_by = []
        if deps:
            rows = (
                await session.execute(
                    select(SquadTask.id, SquadTask.status).where(
                        SquadTask.workspace_id == task.workspace_id,
                        SquadTask.id.in_(deps),
                        SquadTask.status != "done",
                    )
                )
            ).all()
            blocked_by = [str(r[0]) for r in rows]
        return {
            "id": str(task.id),
            "squad_id": str(task.squad_id),
            "issue_id": str(task.issue_id),
            "parent_task_id": str(task.parent_task_id) if task.parent_task_id else None,
            "root_task_id": str(task.root_task_id) if task.root_task_id else None,
            "depth": task.depth,
            "title_snapshot": task.title_snapshot,
            "status": task.status,
            "assignee": assignee,
            "stage": task.stage,
            "execution_id": str(task.execution_id) if task.execution_id else None,
            "plan_markdown": task.plan_markdown,
            "result_summary": task.result_summary,
            "failure_reason": task.failure_reason,
            "depends_on": [str(d) for d in deps],
            "blocked_by": blocked_by,
            "dispatched_at": task.dispatched_at.isoformat() if task.dispatched_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at else None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    async def get_tree(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            root = await load_task(session, workspace_id=workspace_id, task_id=task_id)
            if root.squad_id != squad_id:
                raise NotFoundError("task not found")
            tree = list(
                (
                    await session.execute(
                        select(SquadTask).where(
                            SquadTask.workspace_id == workspace_id,
                            SquadTask.root_task_id == (root.root_task_id or root.id),
                        )
                    )
                ).scalars()
            )
            by_parent: dict[uuid.UUID | None, list[SquadTask]] = {}
            for t in tree:
                by_parent.setdefault(t.parent_task_id, []).append(t)
            total = len([t for t in tree if t.parent_task_id is not None])
            progress = {
                "total": total,
                "done": len([t for t in tree if t.status == "done"]),
                "in_progress": len([t for t in tree if t.status == "in_progress"]),
                "pending": len([t for t in tree if t.status == "pending"]),
                "failed": len([t for t in tree if t.status in ("failed", "cancelled")]),
            }

            async def build(node: SquadTask) -> dict:
                rendered = await self._render_task(session, node)
                kids = by_parent.get(node.id, [])
                rendered["children"] = [await build(k) for k in kids]
                return rendered

            data = await build(root)
            data["progress"] = progress
            return data

    async def get_status(self, *, workspace_id: uuid.UUID, squad_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        task = await self.get_task(workspace_id=workspace_id, squad_id=squad_id, task_id=task_id)
        return {"task_id": task["id"], "status": task["status"], "result_summary": task["result_summary"]}


__all__ = [
    "SquadTaskService",
    "make_issue_assignee_watcher",
    "apply_plan_decision_tx",
    "change_primary_leader_tx",
    "unblock_leader_lost_roots_tx",
    "handle_leader_departure_tx",
    "maybe_trigger_instruction_run",
    "on_issue_assignee_changed_tx",
    "observe_execution_finished_tx",
    "try_aggregate_parent_tx",
    "resolve_aggregation_tx",
    "assert_transition",
    "ROOT_TRANSITIONS",
    "SUBTASK_TRANSITIONS",
]
