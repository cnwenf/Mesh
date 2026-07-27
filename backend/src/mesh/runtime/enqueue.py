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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
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
                select(TaskExecution.id).where(
                    TaskExecution.workspace_id == event.workspace_id,
                    TaskExecution.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None  # at-least-once redelivery: first result wins

    trigger = payload.get("trigger", "assign")
    if trigger not in VALID_TRIGGERS:
        trigger = "assign"
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
    except IntegrityError:
        # Concurrent delivery of the same trigger: the unique idempotency key
        # already won elsewhere — treat as handled.
        return None
    await _emit_queue_depth(session, workspace_id=event.workspace_id)
    return None


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
