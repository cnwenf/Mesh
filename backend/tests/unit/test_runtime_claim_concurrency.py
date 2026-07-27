"""Claim concurrency red lines (runtime.md §5.2 — real PostgreSQL races).

T2: N runtimes racing the same queue — every task claimed EXACTLY once,
zero lock waits (SKIP LOCKED). T3: capacity never overshoots max_concurrent
under contention, and terminal transitions return the counter to zero.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from mesh.db.models.runtime import ExecutionAttempt, Runtime, TaskExecution
from mesh.runtime.attempts import transition_attempt
from mesh.runtime.claim import claim_execution

from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
)

pytestmark = pytest.mark.unit

LEASE_SECONDS = 120


async def _claim(session_factory, runtime):
    return await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=LEASE_SECONDS,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )


async def test_t2_three_runtimes_race_one_task_exactly_one_winner(session_factory):
    world = await seed_world(session_factory)
    runtimes = [
        await make_runtime(session_factory, world["ws_id"], name=f"r{i}", max_concurrent=4)
        for i in range(3)
    ]
    await make_execution(session_factory, world["ws_id"], world["agent_id"])

    results = await asyncio.gather(*[_claim(session_factory, rt) for rt in runtimes])

    winners = [r for r in results if r is not None]
    assert len(winners) == 1  # exactly one grab, zero duplicates
    async with session_factory() as session:
        attempts = (await session.execute(select(ExecutionAttempt))).scalars().all()
        claimed = (
            await session.execute(
                select(func.count())
                .select_from(TaskExecution)
                .where(TaskExecution.status == "claimed")
            )
        ).scalar_one()
    assert len(attempts) == 1
    assert claimed == 1


async def test_t2_ten_tasks_five_runtimes_no_duplicates(session_factory):
    world = await seed_world(session_factory)
    runtimes = [
        await make_runtime(session_factory, world["ws_id"], name=f"r{i}", max_concurrent=10)
        for i in range(5)
    ]
    for _ in range(10):
        await make_execution(session_factory, world["ws_id"], world["agent_id"])

    async def drain(runtime):
        wins = []
        for _ in range(10):
            result = await _claim(session_factory, runtime)
            if result is None:
                break
            wins.append(result)
        return wins

    all_wins = [w for batch in await asyncio.gather(*(drain(rt) for rt in runtimes)) for w in batch]

    claimed_ids = [w.execution["id"] for w in all_wins]
    assert len(claimed_ids) == 10
    assert len(set(claimed_ids)) == 10  # every task exactly once


async def test_t3_five_claims_vs_capacity_two_exactly_two_succeed(session_factory):
    """Concurrent claims against max_concurrent=2: never more than 2 slots."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], max_concurrent=2
    )
    # Five separate runtimes share nothing; contention is on the runtime row
    # lock + capacity check for ONE runtime claiming against its own limit.
    for _ in range(5):
        await make_execution(session_factory, world["ws_id"], world["agent_id"])

    results = await asyncio.gather(*[_claim(session_factory, runtime) for _ in range(5)])

    winners = [r for r in results if r is not None]
    assert len(winners) == 2
    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.current_load == 2


async def test_t3_terminal_transitions_return_load_to_zero(session_factory):
    """After both attempts finish, capacity is fully released (idempotent)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], max_concurrent=2)
    for _ in range(2):
        await make_execution(session_factory, world["ws_id"], world["agent_id"])
    first = await _claim(session_factory, runtime)
    second = await _claim(session_factory, runtime)
    assert first is not None and second is not None

    for result, status in ((first, "completed"), (second, "failed")):
        await transition_attempt(
            session_factory,
            attempt_id=_uuid(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,
            new_status="running",
        )
        await transition_attempt(
            session_factory,
            attempt_id=_uuid(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,
            new_status=status,
            result={"exit_code": 0} if status == "completed" else None,
            failure_reason=None if status == "completed" else "nonzero_exit",
        )

    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.current_load == 0


async def test_t3_duplicate_terminal_report_no_double_release(session_factory):
    """Re-reporting the same terminal state is a no-op: load never goes
    negative, second release is skipped (idempotent release guard)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], max_concurrent=1)
    await make_execution(session_factory, world["ws_id"], world["agent_id"])
    result = await _claim(session_factory, runtime)
    assert result is not None

    await transition_attempt(
        session_factory,
        attempt_id=_uuid(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    for _ in range(2):
        # Second report of the SAME terminal state is an idempotent no-op.
        await transition_attempt(
            session_factory,
            attempt_id=_uuid(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,
            new_status="completed",
            result={"exit_code": 0},
        )

    async with session_factory() as session:
        fresh = await session.get(Runtime, runtime.id)
    assert fresh.current_load == 0  # exactly one release


def _uuid(value: str):
    import uuid

    return uuid.UUID(value)
