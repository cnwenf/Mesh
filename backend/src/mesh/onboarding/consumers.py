"""Outbox relay consumers — domain events → checklist auto-completion.

onboarding.md §3.6 (README §6.6 唯一权威). Business modules write
``realtime.publish`` outbox rows in their own transactions; this consumer is
chained onto the relay's ``realtime.publish`` handler AFTER the realtime
projector and autopilot matching (same single-claim composition as
``mesh.autopilot.matcher``). Relay redelivery re-runs the consumer; every
completion goes through the §3.5 conditional-UPDATE guard, so at-least-once
delivery never double-completes a step or re-emits progress events.

Event → step mapping:

* ``member.added`` → ``invite_member_or_add_agent`` (workspace-level: agent
  member appeared, or ≥ 2 humans — batch-complete the workspace's pending
  checklists);
* ``issue.created`` → ``create_first_issue`` (workspace-first issue batches;
  the reporter's own checklist completes on ANY of their issues);
* ``execution.queued`` → ``dispatch_or_mention_agent`` — STRICT trigger
  ownership (R4): only the triggering member's OWN checklist completes
  (never batched on "workspace first execution"; never fabricated);
* ``notification.read`` → ``see_agent_reply_in_inbox`` — the aha moment:
  only when the member reads a notification whose chain (agent-authored
  comment → completed execution → triggered by that member) validates;
  unread notifications never complete anything.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue
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
from mesh.db.tenant import set_tenant_context
from mesh.onboarding.attribution import (
    evaluate_agent_reply_notification,
    resolve_execution_trigger_member,
)
from mesh.onboarding.completion import complete_step
from mesh.onboarding.reconcile import reconcile_state
from mesh.onboarding.service import ensure_seeded

logger = logging.getLogger("mesh.onboarding.consumers")

_EVENT_MEMBER_ADDED = "member.added"
_EVENT_ISSUE_CREATED = "issue.created"
_EVENT_EXECUTION_QUEUED = "execution.queued"
_EVENT_NOTIFICATION_READ = "notification.read"

_HANDLED_EVENTS = frozenset(
    {
        _EVENT_MEMBER_ADDED,
        _EVENT_ISSUE_CREATED,
        _EVENT_EXECUTION_QUEUED,
        _EVENT_NOTIFICATION_READ,
    }
)

_TRIGGER_TRIGGERS = ("assign", "mention")


def _uuid_or_none(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, AttributeError, TypeError):
        return None


async def _pending_states(
    session: AsyncSession, *, workspace_id: uuid.UUID, step_key: str
) -> list[OnboardingState]:
    """Checklists in this workspace whose ``step_key`` row is still pending.

    Uses the ``idx_onboarding_steps_pending`` partial index (§5.2).
    """
    from mesh.db.models.onboarding import STEP_STATUS_PENDING, OnboardingStateStep

    rows = (
        (
            await session.execute(
                select(OnboardingState)
                .join(
                    OnboardingStateStep,
                    (OnboardingStateStep.state_id == OnboardingState.id)
                    & (OnboardingStateStep.workspace_id == OnboardingState.workspace_id),
                )
                .where(
                    OnboardingStateStep.workspace_id == workspace_id,
                    OnboardingStateStep.step_key == step_key,
                    OnboardingStateStep.status == STEP_STATUS_PENDING,
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def _complete_member_step(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    step_key: str,
    evidence: dict,
) -> None:
    """Complete one member's step (seeding lazily for pre-feature members)."""
    state, created = await ensure_seeded(
        session, workspace_id=workspace_id, member_id=member_id
    )
    if created:
        # A brand-new checklist reconciles from full history — the single
        # event evidence would understate a mature workspace (§3.5 entry 2).
        await reconcile_state(session, workspace_id=workspace_id, state=state)
        return
    await complete_step(
        session,
        workspace_id=workspace_id,
        state=state,
        step_key=step_key,
        via=COMPLETED_VIA_AUTO,
        evidence=evidence,
    )


# --- per-event handlers ---------------------------------------------------------


async def _handle_member_added(
    session: AsyncSession, *, workspace_id: uuid.UUID, data: dict
) -> None:
    """Step 2: agent member appeared, or human count reached 2."""
    member_id = _uuid_or_none(data.get("member_id"))
    member_type = str(data.get("member_type") or "")
    condition_hit = False
    evidence: dict = {}
    if member_type == "agent":
        condition_hit = member_id is not None
        evidence = {"member_added_id": str(member_id)} if member_id else {}
    elif member_type == "human":
        human_count = await session.scalar(
            select(func.count(Member.id)).where(
                Member.workspace_id == workspace_id,
                Member.member_type == "human",
                Member.status == "active",
            )
        )
        condition_hit = (human_count or 0) >= 2
        if condition_hit:
            evidence = {"member_added_id": str(member_id)} if member_id else {}
    if not condition_hit:
        return
    for state in await _pending_states(
        session, workspace_id=workspace_id, step_key=STEP_INVITE_MEMBER_OR_ADD_AGENT
    ):
        await complete_step(
            session,
            workspace_id=workspace_id,
            state=state,
            step_key=STEP_INVITE_MEMBER_OR_ADD_AGENT,
            via=COMPLETED_VIA_AUTO,
            evidence=dict(evidence),
        )


async def _handle_issue_created(
    session: AsyncSession, *, workspace_id: uuid.UUID, data: dict
) -> None:
    """Step 3: workspace-first issue batches; the reporter completes on any issue."""
    nested = data.get("issue")
    issue_id = _uuid_or_none(nested.get("id")) if isinstance(nested, dict) else None
    if issue_id is None:
        return
    issue = await session.scalar(
        select(Issue).where(Issue.workspace_id == workspace_id, Issue.id == issue_id)
    )
    if issue is None:
        return
    issue_count = await session.scalar(
        select(func.count(Issue.id)).where(Issue.workspace_id == workspace_id)
    )
    is_first = (issue_count or 0) <= 1
    if is_first:
        for state in await _pending_states(
            session, workspace_id=workspace_id, step_key=STEP_CREATE_FIRST_ISSUE
        ):
            evidence = {"issue_id": str(issue.id)}
            if issue.reporter_id is not None and issue.reporter_id == state.member_id:
                evidence["reporter_member_id"] = str(state.member_id)
            await complete_step(
                session,
                workspace_id=workspace_id,
                state=state,
                step_key=STEP_CREATE_FIRST_ISSUE,
                via=COMPLETED_VIA_AUTO,
                evidence=evidence,
            )
    elif issue.reporter_id is not None:
        await _complete_member_step(
            session,
            workspace_id=workspace_id,
            member_id=issue.reporter_id,
            step_key=STEP_CREATE_FIRST_ISSUE,
            evidence={"issue_id": str(issue.id), "reporter_member_id": str(issue.reporter_id)},
        )


async def _handle_execution_queued(
    session: AsyncSession, *, workspace_id: uuid.UUID, data: dict
) -> None:
    """Step 4: STRICT trigger ownership — only the trigger member's checklist.

    Only the unified enqueue writer's shape carries a resolvable
    ``execution_id`` (the agent-trigger / mention writers broadcast skeleton
    payloads — the outbox event id — which never resolve to an execution
    row, so they are skipped; the materialized execution's own
    ``execution.queued`` covers them exactly once). R4: never batch-complete
    other members on "workspace first execution".
    """
    execution_id = _uuid_or_none(data.get("execution_id"))
    if execution_id is None:
        return
    execution = await session.scalar(
        select(TaskExecution).where(
            TaskExecution.workspace_id == workspace_id,
            TaskExecution.id == execution_id,
        )
    )
    if execution is None or execution.trigger not in _TRIGGER_TRIGGERS:
        return
    trigger_member = await resolve_execution_trigger_member(
        session, workspace_id=workspace_id, execution=execution
    )
    if trigger_member is None:
        return
    await _complete_member_step(
        session,
        workspace_id=workspace_id,
        member_id=trigger_member,
        step_key=STEP_DISPATCH_OR_MENTION_AGENT,
        evidence={"execution_id": str(execution.id), "trigger_member_id": str(trigger_member)},
    )


async def _handle_notification_read(
    session: AsyncSession, *, workspace_id: uuid.UUID, data: dict
) -> None:
    """Step 5 (aha): reading a qualifying agent-reply notification.

    The member must have actually READ it (read_at non-null) AND the chain
    must validate: agent-authored comment → completed execution → triggered
    by this same member (R4). Reading someone else's triggered execution's
    reply never completes the reader's final step.
    """
    notification_id = _uuid_or_none(data.get("id"))
    if notification_id is None:
        return
    notification = await session.scalar(
        select(Notification).where(
            Notification.workspace_id == workspace_id,
            Notification.id == notification_id,
        )
    )
    if notification is None or notification.read_at is None:
        return  # unread (or unread event) — the final step stays pending
    member_id = notification.recipient_id
    evidence = await evaluate_agent_reply_notification(
        session, workspace_id=workspace_id, notification=notification, member_id=member_id
    )
    if evidence is None:
        return
    await _complete_member_step(
        session,
        workspace_id=workspace_id,
        member_id=member_id,
        step_key=STEP_SEE_AGENT_REPLY_IN_INBOX,
        evidence=evidence,
    )


# --- relay entry -----------------------------------------------------------------


async def consume_realtime_event(session: AsyncSession, event: OutboxEvent) -> None:
    """Relay handler body: advance onboarding checklists from one domain event.

    Runs in the relay's savepoint after projection; completions commit
    atomically with the outbox row's ``published`` mark (at-least-once +
    completion guards = no double completion on redelivery). Errors are
    contained by the caller's composition (projection must not break).
    """
    payload = event.payload or {}
    event_name = str(payload.get("event") or "")
    if event_name not in _HANDLED_EVENTS:
        return
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return
    await set_tenant_context(session, event.workspace_id)
    if event_name == _EVENT_MEMBER_ADDED:
        await _handle_member_added(session, workspace_id=event.workspace_id, data=data)
    elif event_name == _EVENT_ISSUE_CREATED:
        await _handle_issue_created(session, workspace_id=event.workspace_id, data=data)
    elif event_name == _EVENT_EXECUTION_QUEUED:
        await _handle_execution_queued(session, workspace_id=event.workspace_id, data=data)
    elif event_name == _EVENT_NOTIFICATION_READ:
        await _handle_notification_read(session, workspace_id=event.workspace_id, data=data)
