"""Consumer side of the MES-60 ``execution.enqueue`` outbox contract.

The agent module (``mesh.agent.triggers``) writes ``execution.enqueue``
events when a dispatch triggers a run; this handler turns them into
``task_executions`` rows (README §6.4 logical layer). Idempotent by the
payload's ``idempotency_key`` (README §6.5): redelivery after a crash finds
the existing row and no-ops.

Payload shapes (frozen by agent/guardrails.py):
- ``{"intent": "enqueue", agent_id, issue_id, trigger, trigger_event_id,
    idempotency_key, config_snapshot, required_capabilities,
    label_requirements, task_spec}``
- ``{"intent": "cancel_in_flight", failure_reason, agent_id, issue_id,
    trigger, trigger_event_id}`` — supersede path (README §6.9).
"""

from __future__ import annotations

import uuid
from datetime import UTC

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.agent import Agent
from mesh.db.models.comment import CommentMention
from mesh.db.models.integration import IntegrationMessageQueue
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.issue.execution_observability import (
    emit_workspace_execution_event,
    record_issue_execution_phase,
)
from mesh.runtime.agent_presence import emit_agent_presence
from mesh.runtime.attempts import cancel_in_flight_for_agent
from mesh.runtime.claim import _emit_queue_depth

ENQUEUE_EVENT_TYPE = "execution.enqueue"

VALID_TRIGGERS = frozenset({"assign", "mention", "autopilot", "manual", "chat", "integration"})


def _parse_uuid(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


async def _bind_mention_execution(
    session: AsyncSession,
    *,
    event: OutboxEvent,
    execution: TaskExecution,
    payload: dict,
    agent_member_id: uuid.UUID | None = None,
) -> None:
    """Replace one pending outbox correlation with its logical execution id."""
    if payload.get("trigger") != "mention":
        return
    comment_id = _parse_uuid(payload.get("comment_id") or payload.get("trigger_comment_id"))
    member_id = agent_member_id or _parse_uuid(payload.get("agent_member_id"))
    if member_id is None and execution.agent_id is not None:
        member_id = await session.scalar(
            select(Member.id)
            .where(
                Member.workspace_id == event.workspace_id,
                Member.agent_id == execution.agent_id,
            )
            .limit(1)
        )
    if comment_id is None or member_id is None:
        return
    await session.execute(
        update(CommentMention)
        .where(
            CommentMention.workspace_id == event.workspace_id,
            CommentMention.comment_id == comment_id,
            CommentMention.mentioned_id == member_id,
            CommentMention.pending_trigger_event_id == event.id,
            CommentMention.deleted_at.is_(None),
        )
        .values(
            triggered_execution_id=execution.id,
            pending_trigger_event_id=None,
        )
    )


async def enqueue_execution_handler(
    session: AsyncSession, event: OutboxEvent
) -> list[tuple[str, dict]] | None:
    """Relay handler — runs in the relay's savepoint; tenant GUC first."""
    await set_tenant_context(session, event.workspace_id)
    payload = event.payload or {}
    intent = payload.get("intent", "enqueue")

    if intent == "cancel_in_flight":
        agent_id = _parse_uuid(payload.get("agent_id"))
        if agent_id is None:
            return None  # malformed: nothing to cancel
        await cancel_in_flight_for_agent(
            session,
            workspace_id=event.workspace_id,
            agent_id=agent_id,
            issue_id=_parse_uuid(payload.get("issue_id")),
            failure_reason=str(payload.get("failure_reason") or "superseded"),
        )
        return None

    # Agent-dispatch payloads carry the §6.5 key inside the payload; the
    # comment-inbox mention path carries it at the outbox event level.
    idempotency_key = payload.get("idempotency_key") or event.idempotency_key
    if idempotency_key:
        existing = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.workspace_id == event.workspace_id,
                    TaskExecution.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            await _bind_mention_execution(
                session,
                event=event,
                execution=existing,
                payload=payload,
            )
            return None  # at-least-once redelivery: first result wins

    trigger = payload.get("trigger", "assign")
    if trigger not in VALID_TRIGGERS:
        trigger = "assign"

    # integrations.md §3.9 (R5-2, integration-scoped addition — other triggers
    # keep the existing contract untouched): the consumer locks the queue item
    # FIRST and guards its state, so the original event (row key K) and a
    # derived rearm event (row key K2, payload still carrying K) consumed in
    # any order / concurrently create EXACTLY ONE execution and bind it once —
    # the losing consumer rolls back with no orphan execution (T39-17).
    queue_item: IntegrationMessageQueue | None = None
    if trigger == "integration":
        queue_item_id = _parse_uuid(payload.get("queue_item_id"))
        if queue_item_id is None:
            # Contract violation by the producer — surface it (relay → failed).
            raise ValueError("integration enqueue payload missing queue_item_id")
        queue_item = (
            await session.execute(
                select(IntegrationMessageQueue)
                .where(
                    IntegrationMessageQueue.workspace_id == event.workspace_id,
                    IntegrationMessageQueue.id == queue_item_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            queue_item is None
            or queue_item.state != "dispatching"
            or queue_item.execution_id is not None
        ):
            # Guard failure: item already bound by the other consumer, already
            # terminal, or gone — never create an orphan execution.
            return None

    label_requirements = payload.get("label_requirements") or {}
    if not isinstance(label_requirements, dict):
        # Producer contract is a string map; the agent module currently emits
        # an empty LIST — normalize to the schema-required shape.
        label_requirements = {}
    required_capabilities = payload.get("required_capabilities") or []
    if not isinstance(required_capabilities, list):
        required_capabilities = []
    required_capabilities = sorted({str(c) for c in required_capabilities if isinstance(c, str)})
    config_snapshot = payload.get("config_snapshot") or {}
    if not isinstance(config_snapshot, dict):
        config_snapshot = {}
    task_spec = payload.get("task_spec") or {}
    if not isinstance(task_spec, dict):
        task_spec = {}

    execution = TaskExecution(
        workspace_id=event.workspace_id,
        agent_id=_parse_uuid(payload.get("agent_id")),
        issue_id=_parse_uuid(payload.get("issue_id")),
        trigger=trigger,
        status="queued",
        idempotency_key=idempotency_key,
        priority=int(payload.get("priority", 100)),
        task_spec=task_spec,
        label_requirements=label_requirements,
        required_capabilities=required_capabilities,
        trigger_event_id=_parse_uuid(payload.get("trigger_event_id")),
        config_snapshot=config_snapshot,
        max_attempts=int(payload.get("max_attempts", 3)),
        timeout_seconds=int(payload.get("timeout_seconds", 1800)),
    )
    session.add(execution)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Concurrent delivery of the same trigger: the unique idempotency key
        # already won elsewhere — treat as handled. Any OTHER constraint
        # violation is a real defect and must surface (relay → failed + alert).
        if idempotency_key and _is_idempotency_conflict(exc):
            return None
        raise
    if queue_item is not None:
        # Execution-association write-back (§3.9, both dispatch modes share
        # this): dispatching → processing single transition + execution bind
        # in the SAME transaction as execution creation. The queue panel's
        # "processing item → execution deep link" holds in both modes.
        from datetime import datetime, timedelta

        queue_item.execution_id = execution.id
        queue_item.state = "processing"
        queue_item.started_at = datetime.now(UTC)
        queue_item.lease_expires_at = datetime.now(UTC) + timedelta(
            seconds=execution.timeout_seconds + 300
        )
        await session.flush()

    comment_id = _parse_uuid(payload.get("comment_id") or payload.get("trigger_comment_id"))
    agent_member_id = _parse_uuid(payload.get("agent_member_id"))
    agent_name: str | None = None
    if execution.agent_id is not None:
        member_snapshot = (
            await session.execute(
                select(Member.id, Member.display_override, Agent.name)
                .join(Agent, Agent.id == Member.agent_id)
                .where(
                    Member.workspace_id == event.workspace_id,
                    Member.agent_id == execution.agent_id,
                )
                .limit(1)
            )
        ).one_or_none()
        if member_snapshot is not None:
            agent_member_id = member_snapshot.id
            agent_name = member_snapshot.display_override or member_snapshot.name

    # An older delivery cannot overwrite a newer remove/re-add trigger because
    # the pending event id is part of the update predicate.
    await _bind_mention_execution(
        session,
        event=event,
        execution=execution,
        payload=payload,
        agent_member_id=agent_member_id,
    )
    # §3.6: every enqueue is observable on the workspace executions channel
    # (F10 — agent triggers additionally emit on issue:{id}:runs; integration
    # / manual / issue-less triggers are covered here). Chat runs are the
    # exception: private to the session owner, never a workspace-wide frame
    # (issue_id=None alone would NOT suppress it — skip explicitly).
    if trigger != "chat":
        await emit_workspace_execution_event(
            session,
            workspace_id=event.workspace_id,
            issue_id=execution.issue_id,
            event="execution.queued",
            data={
                "execution_id": str(execution.id),
                "agent_id": str(execution.agent_id) if execution.agent_id else None,
                "agent_member_id": str(agent_member_id) if agent_member_id else None,
                "agent_name": agent_name,
                "issue_id": str(execution.issue_id) if execution.issue_id else None,
                "trigger": execution.trigger,
                "comment_id": str(comment_id) if comment_id else None,
            },
            idempotency_key=f"enqueue:{execution.id}:execution-queued",
        )
    await record_issue_execution_phase(
        session,
        workspace_id=event.workspace_id,
        issue_id=execution.issue_id,
        execution_id=execution.id,
        agent_id=execution.agent_id,
        phase="queued",
        comment_id=comment_id,
        agent_name=agent_name,
    )
    if execution.agent_id is not None:
        await emit_agent_presence(
            session,
            workspace_id=event.workspace_id,
            agent_id=execution.agent_id,
            idempotency_key=f"execution:{execution.id}:presence:queued",
        )
    await _emit_queue_depth(session, workspace_id=event.workspace_id)
    return None


def _is_idempotency_conflict(exc: IntegrityError) -> bool:
    """True only for the ``uq_task_executions_idem`` partial unique index
    (unique-index violations carry no constraint name — scan the text)."""
    from mesh.db.constraints import constraint_name

    name = constraint_name(exc)
    if name == "uq_task_executions_idem":
        return True
    text = str(getattr(exc.orig, "constraint_name", "") or "")
    return "uq_task_executions_idem" in (text or str(exc.orig))


async def queue_depth(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Console-facing back-pressure signal (runtime.md R8/§4.1)."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(TaskExecution)
                .where(
                    TaskExecution.workspace_id == workspace_id,
                    TaskExecution.status == "queued",
                )
            )
        ).scalar_one()
    )
