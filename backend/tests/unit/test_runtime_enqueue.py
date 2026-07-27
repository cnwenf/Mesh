"""execution.enqueue outbox consumer tests (MES-60 contract closure).

Idempotent insert by §6.5 key, list→map label normalization, capability
string filtering, and the cancel_in_flight supersede path (README §6.9).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE, enqueue_execution_handler, queue_depth

from tests.unit.runtime_support import make_execution, seed_world

pytestmark = pytest.mark.unit


def _idempotency_key(agent_id, issue_id, trigger_event_id) -> str:
    return hashlib.sha256(f"{agent_id}|{issue_id}|{trigger_event_id}".encode()).hexdigest()


async def _emit(world, payload, key=None) -> OutboxEvent:
    return OutboxEvent(
        workspace_id=world["ws_id"],
        event_type=ENQUEUE_EVENT_TYPE,
        payload=payload,
        idempotency_key=key,
        status="pending",
    )


async def test_enqueue_inserts_execution_with_frozen_snapshot(session_factory):
    world = await seed_world(session_factory)
    trigger_event_id = uuid.uuid4()
    key = _idempotency_key(world["agent_id"], "issue", trigger_event_id)
    event = await _emit(
        world,
        {
            "intent": "enqueue",
            "agent_id": str(world["agent_id"]),
            "issue_id": None,
            "trigger": "assign",
            "trigger_event_id": str(trigger_event_id),
            "idempotency_key": key,
            "config_snapshot": {"agent_config_version_id": None, "capability_grants": []},
            "required_capabilities": ["python"],
            "label_requirements": [],  # agent module emits a LIST — normalize to {}
            "task_spec": {"kind": "issue_assignment"},
        },
        key=key,
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)

    async with session_factory() as session:
        execution = (await session.execute(select(TaskExecution))).scalar_one()
        depth = await queue_depth(session, world["ws_id"])
    assert execution.status == "queued"
    assert execution.agent_id == world["agent_id"]
    assert execution.trigger == "assign"
    assert execution.idempotency_key == key
    assert execution.label_requirements == {}  # normalized
    assert execution.required_capabilities == ["python"]
    assert execution.config_snapshot["capability_grants"] == []
    assert depth == 1


async def test_enqueue_duplicate_idempotency_key_is_noop(session_factory):
    world = await seed_world(session_factory)
    existing = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        idempotency_key="dup-key-1",
    )
    event = await _emit(
        world,
        {
            "intent": "enqueue",
            "agent_id": str(world["agent_id"]),
            "idempotency_key": "dup-key-1",
        },
        key="dup-key-1",
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)

    async with session_factory() as session:
        rows = (await session.execute(select(TaskExecution))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == existing.id


async def test_enqueue_redelivery_under_different_outbox_key_is_noop(session_factory):
    """The handler-level existence check dedupes even when the outbox row
    itself arrives under a different key (at-least-once redelivery)."""
    world = await seed_world(session_factory)
    event_a = await _emit(
        world,
        {"intent": "enqueue", "agent_id": str(world["agent_id"]), "idempotency_key": "race-1"},
        key="outbox-key-a",
    )
    event_b = await _emit(
        world,
        {"intent": "enqueue", "agent_id": str(world["agent_id"]), "idempotency_key": "race-1"},
        key="outbox-key-b",
    )
    async with session_factory() as session, session.begin():
        session.add(event_a)
        await session.flush()
        await enqueue_execution_handler(session, event_a)
    async with session_factory() as session, session.begin():
        session.add(event_b)
        await session.flush()
        await enqueue_execution_handler(session, event_b)
    async with session_factory() as session:
        rows = (await session.execute(select(TaskExecution))).scalars().all()
    assert len(rows) == 1


async def test_idempotency_conflict_detector_matches_only_idem_index():
    """The IntegrityError discriminator: only uq_task_executions_idem is
    swallowed; every other constraint violation must surface."""
    from mesh.runtime.enqueue import _is_idempotency_conflict

    class FakeOrig:
        def __init__(self, name):
            self.constraint_name = name

        def __str__(self):
            return f'violates unique constraint "{self.constraint_name}"'

    from sqlalchemy.exc import IntegrityError

    def make(name):
        return IntegrityError("stmt", {}, FakeOrig(name))

    assert _is_idempotency_conflict(make("uq_task_executions_idem"))
    assert not _is_idempotency_conflict(make("task_executions_workspace_id_fkey"))


async def test_cancel_in_flight_supersedes_queued_and_running(session_factory):
    """README §6.9: reassigning cancels the previous agent's in-flight runs."""
    world = await seed_world(session_factory)
    from mesh.runtime.claim import claim_execution

    from tests.unit.runtime_support import TEST_JWT_SECRET, make_runtime
    from datetime import timedelta

    runtime = await make_runtime(session_factory, world["ws_id"])
    await make_execution(session_factory, world["ws_id"], world["agent_id"])  # stays queued
    await make_execution(session_factory, world["ws_id"], world["agent_id"])
    # FIFO claim picks the oldest queued execution; drive it to running.
    claimed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert claimed is not None
    from mesh.runtime.attempts import transition_attempt

    await transition_attempt(
        session_factory,
        attempt_id=uuid.UUID(claimed.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )

    event = await _emit(
        world,
        {
            "intent": "cancel_in_flight",
            "failure_reason": "superseded",
            "agent_id": str(world["agent_id"]),
            "issue_id": None,  # cancel all of the agent's in-flight runs
        },
        key="cancel-1",
    )
    async with session_factory() as session, session.begin():
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)

    async with session_factory() as session:
        statuses = {
            e.status
            for e in (await session.execute(select(TaskExecution))).scalars().all()
        }
        cancelled_rows = (
            await session.execute(
                select(TaskExecution).where(TaskExecution.status == "cancelled")
            )
        ).scalars().all()
    # One was queued (→ cancelled immediately) and one was running
    # (→ cancelling, daemon completes it): exactly this pair of outcomes.
    assert statuses == {"cancelled", "cancelling"}
    assert cancelled_rows[0].failure_reason == "superseded"
