"""Agent-assignment trigger hook (issue.md §3.7, README §6.9 触发矩阵).

The NO-OP diff semantics and the event emission logic for the assign
trigger path. Semantics enforced here:

* assignee value unchanged → no event (the service's empty-diff no-op);
* assignee changes TO an agent member → one ``issue.assigned`` outbox event
  (``trigger='assign'``, ``action='enqueue'``) in the SAME business
  transaction; the event carries ``trigger_event_id`` so the agent
  orchestration entry (agent.md §3.3) can derive the README §6.5
  idempotency key ``sha256(agent_id | issue_id | trigger_event_id)``;
* assignee changes AWAY from an agent member (reassignment / unassignment) →
  one ``issue.assigned`` event carrying ``action='supersede'`` so the
  orchestrator cancels the previous agent's queued/claimed/running
  execution (``failure_reason='superseded'``, §6.9) before enqueueing the
  new one.

The domain event rows carry a purpose-tagged idempotency key
(``…|issue-assigned``) so they never collide with the pure §6.5 enqueue
key the orchestration handler writes later (``outbox_events.idempotency_key``
is globally unique). The relay handler that consumes these events is
``mesh.agent.triggers.assign_orchestration_handler`` (registered by
``workers/main.py``).
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.outbox.service import emit_event

ASSIGN_EVENT_TYPE = "issue.assigned"


def assign_event_idempotency_key(
    *, agent_key: uuid.UUID, issue_id: uuid.UUID, trigger_event_id: uuid.UUID
) -> str:
    """Purpose-tagged key for the ``issue.assigned`` DOMAIN event row.

    The untagged ``sha256(agent_id | issue_id | trigger_event_id)`` formula
    is reserved by README §6.5 for the execution ENQUEUE side effect (written
    by the agent orchestration handler); the tag keeps the two outbox rows
    apart under the global ``UNIQUE(idempotency_key)``.
    """
    return hashlib.sha256(
        f"{agent_key}|{issue_id}|{trigger_event_id}|issue-assigned".encode()
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
    actor: Member,
) -> None:
    """Emit §6.9 assign-trigger events for a committed assignee diff.

    Runs inside the business transaction (transactional outbox, §6.6): the
    event rows commit atomically with the issue change, so "business
    committed but trigger lost" is impossible. ``trigger_event_id`` (the
    realtime outbox event id of the issue change) is embedded in the
    payload — it anchors the §6.5 enqueue idempotency key and the §6.11
    snapshot.

    ``actor`` is the member performing the assignment; the enqueue payload
    embeds ``actor_user_id`` so the relay guardrail can enforce the agent.md
    §3.5 owner-only rule for private agents (TD-3). Agent-authored assigns
    carry ``user_id=None`` and fail that check closed.
    """
    new_agent = await _member_is_agent(session, issue.assignee_id)
    old_agent = await _member_is_agent(session, previous_assignee_id)

    if old_agent is not None and old_agent.id != (new_agent.id if new_agent else None):
        # Reassignment away from an agent: the orchestrator cancels the
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
                "trigger_event_id": str(trigger_event_id),
            },
            idempotency_key=assign_event_idempotency_key(
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
                "actor_user_id": str(actor.user_id) if actor.user_id is not None else None,
                "previous_assignee_id": str(previous_assignee_id)
                if previous_assignee_id is not None
                else None,
                "trigger_event_id": str(trigger_event_id),
            },
            idempotency_key=assign_event_idempotency_key(
                agent_key=new_agent.agent_id or new_agent.id,
                issue_id=issue.id,
                trigger_event_id=trigger_event_id,
            ),
        )


__all__ = ["ASSIGN_EVENT_TYPE", "apply_assign_triggers", "assign_event_idempotency_key"]
