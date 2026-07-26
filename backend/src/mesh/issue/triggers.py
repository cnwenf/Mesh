"""Agent-assignment trigger hook (issue.md §3.7, README §6.9 触发矩阵).

This increment lands the NO-OP diff semantics and the event emission logic;
real agent execution arrives with the agent.md increment. The semantics that
ARE enforced here:

* assignee value unchanged → no event (the service's empty-diff no-op);
* assignee changes TO an agent member → one ``issue.assigned`` outbox event
  (``trigger='assign'``) in the SAME business transaction, idempotency key per
  README §6.5 (``sha256(agent_id | issue_id | trigger_event_id)``);
* assignee changes AWAY from an agent member (reassignment / unassignment) →
  one ``issue.assigned`` event carrying ``supersede`` intent so the future
  orchestrator cancels the previous agent's queued/claimed/running execution
  (``failure_reason='superseded'``, §6.9) before enqueueing the new one.

The relay handler :func:`assign_trigger_handler` is registered by
``workers/main.py`` as the single dispatch point for ``issue.assigned``; it
marks the event published and records the pending orchestration handoff until
the unified agent entry point (agent.md) consumes these events.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.issue.triggers")

ASSIGN_EVENT_TYPE = "issue.assigned"


def assign_idempotency_key(
    *, agent_key: uuid.UUID, issue_id: uuid.UUID, trigger_event_id: uuid.UUID
) -> str:
    """README §6.5: sha256(agent_id | issue_id | trigger_event_id)."""
    return hashlib.sha256(
        f"{agent_key}|{issue_id}|{trigger_event_id}".encode()
    ).hexdigest()


async def _member_is_agent(session: AsyncSession, member_id: uuid.UUID | None) -> Member | None:
    if member_id is None:
        return None
    member = await session.scalar(select(Member).where(Member.id == member_id))
    if member is not None and member.member_type == "agent":
        return member
    return None


async def apply_assign_triggers(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    issue: Issue,
    previous_assignee_id: uuid.UUID | None,
    trigger_event_id: uuid.UUID,
) -> None:
    """Emit §6.9 assign-trigger events for a committed assignee diff.

    Runs inside the business transaction (transactional outbox, §6.6): the
    event rows commit atomically with the issue change, so "business
    committed but trigger lost" is impossible.
    """
    new_agent = await _member_is_agent(session, issue.assignee_id)
    old_agent = await _member_is_agent(session, previous_assignee_id)

    if old_agent is not None and old_agent.id != (new_agent.id if new_agent else None):
        # Reassignment away from an agent: the orchestrator must cancel the
        # previous agent's live executions (superseded, §6.9) before anything
        # new is enqueued.
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type=ASSIGN_EVENT_TYPE,
            payload={
                "issue_id": str(issue.id),
                "agent_member_id": str(old_agent.id),
                "agent_id": str(old_agent.agent_id) if old_agent.agent_id else None,
                "trigger": "assign",
                "action": "supersede",
                "previous_assignee_id": str(old_agent.id),
            },
            idempotency_key=assign_idempotency_key(
                agent_key=old_agent.agent_id or old_agent.id,
                issue_id=issue.id,
                trigger_event_id=trigger_event_id,
            ),
        )
    if new_agent is not None and new_agent.id != previous_assignee_id:
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type=ASSIGN_EVENT_TYPE,
            payload={
                "issue_id": str(issue.id),
                "agent_member_id": str(new_agent.id),
                "agent_id": str(new_agent.agent_id) if new_agent.agent_id else None,
                "trigger": "assign",
                "action": "enqueue",
                "previous_assignee_id": str(previous_assignee_id)
                if previous_assignee_id is not None
                else None,
            },
            idempotency_key=assign_idempotency_key(
                agent_key=new_agent.agent_id or new_agent.id,
                issue_id=issue.id,
                trigger_event_id=trigger_event_id,
            ),
        )


async def assign_trigger_handler(session: AsyncSession, event: OutboxEvent) -> None:
    """Relay handler bridging to the agent.md increment.

    Until the unified agent orchestration entry point exists, this consumes
    ``issue.assigned`` events (marking them published so the relay stays
    healthy) and logs the pending handoff. When agent.md lands, this handler
    creates ``task_executions`` (trigger='assign') with the §6.11 snapshot and
    cancels superseded executions per §6.9 — the producing side above already
    carries every field the orchestrator needs.
    """
    payload = event.payload or {}
    logger.info(
        "issue.assigned received (agent orchestration pending agent.md increment): "
        "issue=%s agent_member=%s action=%s",
        payload.get("issue_id"),
        payload.get("agent_member_id"),
        payload.get("action"),
    )
    return None


__all__ = ["ASSIGN_EVENT_TYPE", "apply_assign_triggers", "assign_idempotency_key", "assign_trigger_handler"]
