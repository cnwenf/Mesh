"""execution.enqueue consumer contract tests — R5-2 integration scope (MES-87).

§6.6 authoritative paragraph / §3.9: `trigger='integration'` payloads carry
``queue_item_id``; the consumer locks the queue item FOR UPDATE and guards
`state='dispatching' AND execution_id IS NULL` BEFORE creating the
execution, binding it in the same transaction (the single dispatching →
processing transition). Any original/rearm event consumption order yields
exactly one execution, bound once. OTHER triggers (assign/mention/…) keep
the existing contract — zero regression.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from mesh.db.models.integration import IntegrationMessageQueue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.runtime.enqueue import enqueue_execution_handler
from tests.unit.integrations_support import (
    make_dingtalk_binding,
    seed_dingtalk_world,
)

pytestmark = pytest.mark.unit


def _enqueue_event(workspace_id: uuid.UUID, agent_id: uuid.UUID, *, trigger: str,
                   idem_key: str, queue_item_id: uuid.UUID | None = None) -> OutboxEvent:
    payload = {
        "intent": "enqueue",
        "agent_id": str(agent_id),
        "issue_id": None,
        "trigger": trigger,
        "trigger_event_id": None,
        "idempotency_key": idem_key,
        "config_snapshot": {},
        "required_capabilities": [],
        "label_requirements": {},
        "task_spec": {"kind": "integration_event"},
    }
    if queue_item_id is not None:
        payload["queue_item_id"] = str(queue_item_id)
    return OutboxEvent(
        workspace_id=workspace_id,
        event_type="execution.enqueue",
        payload=payload,
    )


async def _add_queue_item(session_factory, world, binding, *, state: str,
                          execution_id: uuid.UUID | None = None) -> uuid.UUID:
    item_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        if execution_id is not None:
            # Satisfy fk_imq_execution with a real bound execution row.
            session.add(TaskExecution(
                id=execution_id,
                workspace_id=world["ws"],
                agent_id=world["agent"],
                trigger="integration",
                status="running",
                idempotency_key=f"preexisting-{execution_id.hex}",
            ))
            await session.flush()
        session.add(IntegrationMessageQueue(
            id=item_id,
            workspace_id=world["ws"],
            integration_id=world["integ_dingtalk"],
            binding_id=binding.id,
            conversation_key=f"dingtalk:dingcorp0001:{binding.external_ref}",
            seq=1,
            dispatch_mode="serial_conversation",
            state=state,
            execution_id=execution_id,
            sender_identity_key="dingtalk:dingcorp0001:014728255240768602",
        ))
    return item_id


async def _executions_count(session_factory, world) -> int:
    async with session_factory() as session:
        return int((await session.execute(
            select(func.count()).select_from(TaskExecution).where(
                TaskExecution.workspace_id == world["ws"]
            )
        )).scalar_one())


async def test_integration_trigger_binds_queue_item_and_transitions(session_factory):
    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    item_id = await _add_queue_item(session_factory, world, binding, state="dispatching")

    event = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-bind-1", queue_item_id=item_id,
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)

    assert await _executions_count(session_factory, world) == 1
    async with session_factory() as session:
        item = await session.get(IntegrationMessageQueue, item_id)
        execution = (await session.execute(select(TaskExecution))).scalar_one()
    assert item.state == "processing"
    assert item.execution_id == execution.id
    assert item.started_at is not None
    assert item.lease_expires_at is not None
    assert execution.trigger == "integration"


async def test_guard_refuses_already_bound_item_no_second_execution(session_factory):
    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    existing_exec_id = uuid.uuid4()
    item_id = await _add_queue_item(
        session_factory, world, binding, state="processing",
        execution_id=existing_exec_id,
    )

    event = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-bound-1", queue_item_id=item_id,
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        result = await enqueue_execution_handler(session, event)

    assert result is None  # no-op publish — bound elsewhere
    # Only the pre-existing (FK-satisfying) execution — none created by the handler.
    assert await _executions_count(session_factory, world) == 1
    async with session_factory() as session:
        created = (await session.execute(
            select(TaskExecution).where(TaskExecution.idempotency_key == "k-bound-1")
        )).scalars().all()
    assert created == []


async def test_guard_refuses_missing_item(session_factory):
    world = await seed_dingtalk_world(session_factory)
    await make_dingtalk_binding(session_factory, world=world)
    event = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-missing-1", queue_item_id=uuid.uuid4(),
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        result = await enqueue_execution_handler(session, event)
    assert result is None
    assert await _executions_count(session_factory, world) == 0


async def test_guard_refuses_pending_item(session_factory):
    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    item_id = await _add_queue_item(session_factory, world, binding, state="pending")
    event = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-pending-1", queue_item_id=item_id,
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        result = await enqueue_execution_handler(session, event)
    assert result is None
    assert await _executions_count(session_factory, world) == 0


async def test_non_integration_trigger_keeps_existing_contract(session_factory):
    """R5-2 zero regression: mention (no queue_item_id) materializes the
    execution exactly as before — no queue-item lookup in its path."""
    world = await seed_dingtalk_world(session_factory)
    event = _enqueue_event(
        world["ws"], world["agent"], trigger="mention", idem_key="k-mention-1"
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)
    assert await _executions_count(session_factory, world) == 1
    async with session_factory() as session:
        execution = (await session.execute(select(TaskExecution))).scalar_one()
    assert execution.trigger == "mention"


async def test_idempotent_redelivery_no_ops(session_factory):
    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    item_id = await _add_queue_item(session_factory, world, binding, state="dispatching")

    first = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-redeliver", queue_item_id=item_id,
    )
    async with session_factory() as session, session.begin():
        session.add(first)
        await session.flush()
        await enqueue_execution_handler(session, first)

    # Redelivery of the same idempotency key (crash-after-commit recovery):
    # the existing execution wins; the item (now processing) also guards.
    second = _enqueue_event(
        world["ws"], world["agent"], trigger="integration",
        idem_key="k-redeliver", queue_item_id=item_id,
    )
    async with session_factory() as session, session.begin():
        session.add(second)
        await session.flush()
        result = await enqueue_execution_handler(session, second)
    assert result is None
    assert await _executions_count(session_factory, world) == 1
