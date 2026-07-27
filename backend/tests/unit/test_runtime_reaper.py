"""Reaper self-healing tests (runtime.md §4.8, T4 audit preservation).

Expired-lease reclaim → requeue with attempt #N+1 (old row untouched) or
failed(max_retries); cancelling executions finish cancelled; heartbeat-lost
runtimes flip unavailable; pending approvals expire their executions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta

import pytest
from sqlalchemy import select, text

from mesh.db.models.runtime import Approval, ExecutionAttempt, Runtime, TaskExecution
from mesh.runtime.reaper import run_reaper_pass
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
)

pytestmark = pytest.mark.unit


async def _seed_expired_attempt(
    session_factory, ws, execution, runtime, *, number=1, status="running", seq=1
) -> uuid.UUID:
    attempt_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO execution_attempts "
                "(id, workspace_id, execution_id, attempt_number, runtime_id, "
                " claimed_by_runtime_id, status, lease_expires_at, lease_seq, claimed_at) "
                "VALUES (:id, :ws, :e, :n, :r, :r, :status, now() - interval '1 minute', "
                ":seq, now() - interval '5 minutes')"
            ),
            {
                "id": attempt_id,
                "ws": ws,
                "e": execution.id,
                "n": number,
                "r": runtime.id,
                "status": status,
                "seq": seq,
            },
        )
        # The attempt occupies a capacity slot.
        await session.execute(
            text("UPDATE runtimes SET current_load = current_load + 1 WHERE id = :r"),
            {"r": runtime.id},
        )
        await session.execute(
            text("UPDATE task_executions SET status = :s WHERE id = :e"),
            {"s": status, "e": execution.id},
        )
    return attempt_id


async def test_t4_reclaim_requeues_preserving_audit_and_advancing_seq(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="running", max_attempts=3
    )
    attempt_id = await _seed_expired_attempt(
        session_factory, world["ws_id"], execution, runtime
    )

    counts = await run_reaper_pass(session_factory)

    assert counts["reclaimed"] == 1
    assert counts["requeued"] == 1
    async with session_factory() as session:
        attempt = await session.get(ExecutionAttempt, attempt_id)
        stored_exec = await session.get(TaskExecution, execution.id)
        fresh_runtime = await session.get(Runtime, runtime.id)
    # Audit row preserved + reclaimed, lease_seq advanced (zombie fence).
    assert attempt.status == "reclaimed"
    assert attempt.failure_reason == "lease_expired"
    assert attempt.runtime_id == runtime.id  # original runtime kept
    assert attempt.claimed_at is not None  # original claim time kept
    assert attempt.lease_seq == 2
    assert attempt.finished_at is not None
    # Execution back to queued for attempt #2.
    assert stored_exec.status == "queued"
    # Capacity released exactly once.
    assert fresh_runtime.current_load == 0

    # The next claim builds attempt #2; attempt #1 stays intact.
    from mesh.runtime.claim import claim_execution

    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    assert result.attempt["attempt_number"] == 2
    async with session_factory() as session:
        attempts = (
            await session.execute(
                select(ExecutionAttempt).order_by(ExecutionAttempt.attempt_number)
            )
        ).scalars().all()
    assert [a.attempt_number for a in attempts] == [1, 2]
    assert attempts[0].status == "reclaimed"  # untouched by requeue


async def test_reaper_fails_execution_at_max_attempts(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="running", max_attempts=2
    )
    # Two prior attempts already exist (attempt #1 failed, #2 expired).
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO execution_attempts "
                "(workspace_id, execution_id, attempt_number, runtime_id, status, "
                " failure_reason, finished_at, claimed_at) "
                "VALUES (:ws, :e, 1, :r, 'failed', 'nonzero_exit', now(), now())"
            ),
            {"ws": world["ws_id"], "e": execution.id, "r": runtime.id},
        )
    await _seed_expired_attempt(
        session_factory, world["ws_id"], execution, runtime, number=2
    )

    counts = await run_reaper_pass(session_factory)

    assert counts["failed_max_retries"] == 1
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
    assert stored.status == "failed"
    assert stored.failure_reason == "max_retries"
    assert stored.finished_at is not None


async def test_reaper_completes_cancelling_execution_as_cancelled(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="cancelling"
    )
    await _seed_expired_attempt(
        session_factory, world["ws_id"], execution, runtime, status="cancelling"
    )

    counts = await run_reaper_pass(session_factory)

    assert counts["cancelled"] == 1
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
    assert stored.status == "cancelled"


async def test_reaper_marks_stale_heartbeats_offline(session_factory):
    world = await seed_world(session_factory)
    from datetime import datetime

    stale = await make_runtime(
        session_factory,
        world["ws_id"],
        name="stale",
        last_heartbeat_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    fresh = await make_runtime(session_factory, world["ws_id"], name="fresh")

    counts = await run_reaper_pass(session_factory)

    assert counts["offline"] == 1
    async with session_factory() as session:
        stale_row = await session.get(Runtime, stale.id)
        fresh_row = await session.get(Runtime, fresh.id)
    assert stale_row.status == "unavailable"
    assert fresh_row.status == "online"


async def test_reaper_expires_pending_approvals_and_cancels_execution(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="awaiting_approval"
    )
    approval_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO approvals (id, workspace_id, subject_type, subject_execution_id, "
                "requested_by_member_id, action_summary, expires_at, status) "
                "VALUES (:id, :ws, 'tool_call', :e, :m, '{}'::jsonb, "
                "now() - interval '1 minute', 'pending')"
            ),
            {
                "id": approval_id,
                "ws": world["ws_id"],
                "e": execution.id,
                "m": world["member_id"],
            },
        )

    counts = await run_reaper_pass(session_factory)

    assert counts["approvals_expired"] == 1
    async with session_factory() as session:
        approval = await session.get(Approval, approval_id)
        stored = await session.get(TaskExecution, execution.id)
    assert approval.status == "expired"
    assert stored.status == "cancelled"
    assert stored.failure_reason == "approval_expired"
