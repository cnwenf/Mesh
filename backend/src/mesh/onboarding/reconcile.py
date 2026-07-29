"""建状态全量历史 reconcile(onboarding.md §3.5 R3/R4,T34②)。

播种后同事务回查历史事实:步骤 2/3 按工作区事实带证据完成;步骤 4 仅按成员
自身触发历史完成(未触发过的成员保持 pending——不批量补齐、不伪造证据);
步骤 5 仅历史已读且满足末步条件的通知完成。完成一律经 completion 守卫。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.comment import Comment, CommentMention
from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification
from mesh.db.models.onboarding import (
    COMPLETED_VIA_AUTO,
    STEP_CREATE_FIRST_ISSUE,
    STEP_DISPATCH_OR_MENTION_AGENT,
    STEP_INVITE_MEMBER_OR_ADD_AGENT,
    STEP_SEE_AGENT_REPLY_IN_INBOX,
    OnboardingState,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.onboarding.attribution import evaluate_agent_reply_notification
from mesh.onboarding.completion import complete_step

Clock = Callable[[], datetime]

_IDEMPOTENCY_PREFIX = "ws"
_ASSIGN_FIELD = "assignee_id"
_TRIGGER_TRIGGERS = ("assign", "mention")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


# --- historical reconcile (R3) --------------------------------------------------


async def _reconcile_step_invite(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    agent = await session.scalar(
        select(Member)
        .where(
            Member.workspace_id == workspace_id,
            Member.member_type == "agent",
            Member.status == "active",
        )
        .order_by(Member.created_at.asc())
        .limit(1)
    )
    evidence: dict | None = None
    if agent is not None:
        evidence = {"member_added_id": str(agent.id)}
    else:
        humans = (
            (
                await session.execute(
                    select(Member)
                    .where(
                        Member.workspace_id == workspace_id,
                        Member.member_type == "human",
                        Member.status == "active",
                    )
                    .order_by(Member.created_at.asc())
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if len(humans) >= 2:
            evidence = {"member_added_id": str(humans[1].id)}
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_INVITE_MEMBER_OR_ADD_AGENT,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _reconcile_step_first_issue(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    reported = await session.scalar(
        select(Issue)
        .where(
            Issue.workspace_id == workspace_id,
            Issue.reporter_id == state.member_id,
        )
        .order_by(Issue.created_at.asc())
        .limit(1)
    )
    evidence: dict | None = None
    if reported is not None:
        evidence = {"issue_id": str(reported.id), "reporter_member_id": str(state.member_id)}
    else:
        first = await session.scalar(
            select(Issue)
            .where(Issue.workspace_id == workspace_id)
            .order_by(Issue.created_at.asc(), Issue.id.asc())
            .limit(1)
        )
        if first is not None:
            evidence = {"issue_id": str(first.id)}
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_CREATE_FIRST_ISSUE,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _reconcile_step_dispatch(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    """Step 4 by the member's OWN history only (R4 — never batch from others')."""
    evidence = await _historical_mention_evidence(
        session, workspace_id=workspace_id, member_id=state.member_id
    )
    if evidence is None:
        evidence = await _historical_assign_evidence(
            session, workspace_id=workspace_id, member_id=state.member_id
        )
    if evidence is not None:
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_DISPATCH_OR_MENTION_AGENT,
            via=COMPLETED_VIA_AUTO,
            evidence=evidence,
            clock=clock,
        )


async def _historical_mention_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> dict | None:
    """Earliest mention-triggered execution this member personally authored."""
    rows = (
        (
            await session.execute(
                select(CommentMention.triggered_execution_id)
                .join(
                    Comment,
                    and_(
                        Comment.id == CommentMention.comment_id,
                        Comment.workspace_id == CommentMention.workspace_id,
                    ),
                )
                .where(
                    CommentMention.workspace_id == workspace_id,
                    CommentMention.triggered_execution_id.is_not(None),
                    CommentMention.deleted_at.is_(None),
                    Comment.author_id == member_id,
                )
                .order_by(CommentMention.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for outbox_id in rows:
        scoped = await session.scalar(
            select(OutboxEvent.idempotency_key).where(
                OutboxEvent.workspace_id == workspace_id,
                OutboxEvent.id == outbox_id,
            )
        )
        if scoped is None:
            continue
        prefix = f"{_IDEMPOTENCY_PREFIX}:{workspace_id}:"
        if not scoped.startswith(prefix):
            continue
        execution = await session.scalar(
            select(TaskExecution).where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.idempotency_key == scoped[len(prefix) :],
            )
        )
        if execution is not None and execution.trigger in _TRIGGER_TRIGGERS:
            return {"execution_id": str(execution.id), "trigger_member_id": str(member_id)}
    return None


async def _historical_assign_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, member_id: uuid.UUID
) -> dict | None:
    """Earliest assign-triggered execution this member personally dispatched."""
    activities = (
        (
            await session.execute(
                select(IssueActivity)
                .where(
                    IssueActivity.workspace_id == workspace_id,
                    IssueActivity.actor_member_id == member_id,
                    IssueActivity.field == _ASSIGN_FIELD,
                    IssueActivity.new_value.is_not(None),
                )
                .order_by(IssueActivity.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for activity in activities:
        assigned = activity.new_value if isinstance(activity.new_value, str) else None
        assigned_id = _parse_uuid(assigned)
        if assigned_id is None:
            continue
        agent_member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == assigned_id,
                Member.member_type == "agent",
            )
        )
        if agent_member is None or agent_member.agent_id is None:
            continue
        execution = await session.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.issue_id == activity.issue_id,
                TaskExecution.agent_id == agent_member.agent_id,
                TaskExecution.trigger == "assign",
                TaskExecution.queued_at >= activity.created_at,
            )
            .order_by(TaskExecution.queued_at.asc())
            .limit(1)
        )
        if execution is not None:
            return {"execution_id": str(execution.id), "trigger_member_id": str(member_id)}
    # Creation-time assignment leaves no activity trail: issues the member
    # REPORTED with an agent assignee (and no assignee edits at all) dispatched
    # their assign-triggered executions at creation — the reporter is the
    # dispatching member (mirror of _assign_trigger_member's fallback).
    reported = (
        (
            await session.execute(
                select(Issue)
                .where(
                    Issue.workspace_id == workspace_id,
                    Issue.reporter_id == member_id,
                    Issue.assignee_id.is_not(None),
                )
                .order_by(Issue.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for issue in reported:
        edits = await session.scalar(
            select(func.count(IssueActivity.id)).where(
                IssueActivity.workspace_id == workspace_id,
                IssueActivity.issue_id == issue.id,
                IssueActivity.field == _ASSIGN_FIELD,
            )
        )
        if edits:
            continue  # assignee changed post-creation — attribution is ambiguous
        assignee = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == issue.assignee_id,
                Member.member_type == "agent",
            )
        )
        if assignee is None or assignee.agent_id is None:
            continue
        execution = await session.scalar(
            select(TaskExecution)
            .where(
                TaskExecution.workspace_id == workspace_id,
                TaskExecution.issue_id == issue.id,
                TaskExecution.agent_id == assignee.agent_id,
                TaskExecution.trigger == "assign",
            )
            .order_by(TaskExecution.queued_at.asc())
            .limit(1)
        )
        if execution is not None:
            return {"execution_id": str(execution.id), "trigger_member_id": str(member_id)}
    return None


async def _reconcile_step_aha(
    session: AsyncSession, *, workspace_id: uuid.UUID, state: OnboardingState, clock: Clock
) -> None:
    """Step 5 from history: a previously READ qualifying notification only."""
    notifications = (
        (
            await session.execute(
                select(Notification)
                .where(
                    Notification.workspace_id == workspace_id,
                    Notification.recipient_id == state.member_id,
                    Notification.read_at.is_not(None),
                    Notification.comment_id.is_not(None),
                )
                .order_by(Notification.read_at.asc())
            )
        )
        .scalars()
        .all()
    )
    for notification in notifications:
        evidence = await evaluate_agent_reply_notification(
            session,
            workspace_id=workspace_id,
            notification=notification,
            member_id=state.member_id,
        )
        if evidence is not None:
            await complete_step(
                session,
                workspace_id=workspace_id,
                state=state,
                step_key=STEP_SEE_AGENT_REPLY_IN_INBOX,
                via=COMPLETED_VIA_AUTO,
                evidence=evidence,
                clock=clock,
            )
            return


async def reconcile_state(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    state: OnboardingState,
    clock: Clock = _utcnow,
) -> None:
    """Full historical reconcile (§3.5 R3) — idempotent via completion guards.

    Steps complete from the member's OWN history with real evidence; a
    member who never triggered an execution keeps step 4 pending (R4 — no
    batch completion from workspace-first-execution, no fabricated
    evidence); step 5 stays pending until a qualifying notification is
    actually read.
    """
    await _reconcile_step_invite(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_first_issue(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_dispatch(session, workspace_id=workspace_id, state=state, clock=clock)
    await _reconcile_step_aha(session, workspace_id=workspace_id, state=state, clock=clock)


