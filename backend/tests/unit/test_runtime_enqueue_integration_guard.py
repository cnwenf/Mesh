"""execution.enqueue consumer — integration queue-item guard (§3.9 R5-2).

T39-17 at service level: original (row key K) and derived rearm (row key K2,
payload still K) events consumed in any order create EXACTLY ONE execution;
the queue item binds once; no orphan executions.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.integrations.dispatcher import rearm_row_key
from mesh.integrations.message_queue import execution_idempotency_key
from mesh.outbox.service import scope_idempotency_key
from mesh.runtime.enqueue import enqueue_execution_handler
from tests.unit.test_integration_dispatcher import (
    _event_msg_id,
    _item,
    _seed_item,
    _seed_world,
)

pytestmark = pytest.mark.unit


def _enqueue_payload(world, *, key: str, item_id: uuid.UUID, trigger: str = "integration"):
    return {
        "intent": "enqueue",
        "agent_id": str(world["agent"]),
        "issue_id": None,
        "trigger": trigger,
        "trigger_event_id": None,
        "idempotency_key": key,
        "config_snapshot": {},
        "required_capabilities": [],
        "label_requirements": {},
        "task_spec": {},
        "queue_item_id": str(item_id),
    }


async def _consume(session_factory, event: OutboxEvent):
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, event.workspace_id)
        return await enqueue_execution_handler(session, event)


async def _execution_count(session_factory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(TaskExecution)) or 0


class TestIntegrationGuard:
    async def test_creates_execution_and_binds_item(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(session_factory, world, seq=1, state="dispatching")
        msg_id = await _event_msg_id(session_factory, event_id)
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id=msg_id
        )
        event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(world["ws"], k),
        )
        await _consume(session_factory, event)

        item = await _item(session_factory, item_id)
        assert item.state == "processing"  # dispatching → processing single transition
        assert item.execution_id is not None
        assert item.started_at is not None
        assert item.lease_expires_at is not None
        async with session_factory() as session:
            execution = await session.get(TaskExecution, item.execution_id)
        assert execution.idempotency_key == k
        assert execution.trigger == "integration"

    async def test_dual_consumer_any_order_exactly_one_execution(self, session_factory):
        """T39-17: row key K and derived K2 (payload keeps K) → one execution,
        one binding, no orphan."""
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(session_factory, world, seq=1, state="dispatching")
        msg_id = await _event_msg_id(session_factory, event_id)
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id=msg_id
        )
        k2 = rearm_row_key(original_key=k, item_id=item_id)
        original = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(world["ws"], k),
        )
        derived = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(world["ws"], k2),
        )

        # order A: original then derived
        await _consume(session_factory, original)
        await _consume(session_factory, derived)

        assert await _execution_count(session_factory) == 1
        item = await _item(session_factory, item_id)
        assert item.state == "processing"
        assert item.execution_id is not None

    async def test_dual_consumer_reverse_order(self, session_factory):
        world = await _seed_world(session_factory)
        item_id, event_id = await _seed_item(session_factory, world, seq=1, state="dispatching")
        msg_id = await _event_msg_id(session_factory, event_id)
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id=msg_id
        )
        derived = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(
                world["ws"], rearm_row_key(original_key=k, item_id=item_id)
            ),
        )
        original = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(world["ws"], k),
        )
        await _consume(session_factory, derived)
        await _consume(session_factory, original)
        assert await _execution_count(session_factory) == 1

    async def test_guard_item_already_bound_creates_no_orphan(self, session_factory):
        """Item past dispatching (bound elsewhere) + execution row absent for
        this payload key → guard refuses, no orphan execution."""
        world = await _seed_world(session_factory)
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="processing")
        k = execution_idempotency_key(
            agent_id=world["agent"], binding_id=world["binding"], external_event_id="OTHER-event"
        )
        event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=_enqueue_payload(world, key=k, item_id=item_id),
            idempotency_key=scope_idempotency_key(world["ws"], k),
        )
        result = await _consume(session_factory, event)
        assert result is None
        assert await _execution_count(session_factory) == 0  # no orphan

    async def test_missing_queue_item_id_raises(self, session_factory):
        """Producer contract: integration trigger MUST carry queue_item_id."""
        world = await _seed_world(session_factory)
        item_id, _ = await _seed_item(session_factory, world, seq=1, state="dispatching")
        payload = _enqueue_payload(world, key="k1", item_id=item_id)
        del payload["queue_item_id"]
        event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=payload,
            idempotency_key=scope_idempotency_key(world["ws"], "k1"),
        )
        with pytest.raises(ValueError):
            await _consume(session_factory, event)

    async def test_non_integration_trigger_contract_unchanged(self, session_factory):
        """R5-2 zero-regression: assign trigger needs no queue_item_id, no
        guard, no queue binding (existing mention/assign path intact)."""
        world = await _seed_world(session_factory)
        payload = {
            "intent": "enqueue",
            "agent_id": str(world["agent"]),
            "issue_id": None,
            "trigger": "assign",
            "idempotency_key": "assign-key-1",
            "config_snapshot": {},
            "required_capabilities": [],
            "label_requirements": {},
            "task_spec": {},
        }
        event = OutboxEvent(
            workspace_id=world["ws"],
            event_type="execution.enqueue",
            payload=payload,
            idempotency_key=scope_idempotency_key(world["ws"], "assign-key-1"),
        )
        await _consume(session_factory, event)
        assert await _execution_count(session_factory) == 1
