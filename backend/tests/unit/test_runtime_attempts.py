"""Dual state machine + lease fencing tests (runtime.md §4.7 / §4.8).

Legal/illegal edges, terminal idempotency, T10 zombie fencing (409),
renew-lease lease_seq advance, and logical-execution mirroring.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.agent import Agent
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import ExecutionAttempt, Runtime, TaskExecution
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError, NotFoundError
from mesh.runtime.attempts import (
    cancel_execution,
    cancel_in_flight_for_agent,
    freeze_execution,
    renew_lease,
    transition_attempt,
)
from mesh.runtime.claim import claim_execution
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    assert_execution_finished_fanout,
    make_execution,
    make_runtime,
    seed_world,
    valid_result_v1,
)

pytestmark = pytest.mark.unit


async def _claim_one(session_factory, runtime) -> dict:
    # F7: claim requires an executor (INNER JOIN agents) — resolve the
    # workspace's agent seeded by seed_world.
    async with session_factory() as session:
        agent_id = (
            await session.execute(
                select(Agent.id).where(Agent.workspace_id == runtime.workspace_id).limit(1)
            )
        ).scalar_one()
    await make_execution(session_factory, runtime.workspace_id, agent_id)
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    return result


async def _transition(session_factory, runtime, claim_result, **kwargs):
    return await transition_attempt(
        session_factory,
        attempt_id=uuid.UUID(claim_result.attempt["id"]),
        runtime=runtime,
        lease_seq=kwargs.pop("lease_seq", 1),
        new_status=kwargs.pop("new_status"),
        **kwargs,
    )


async def test_claimed_to_running_to_completed_mirrors_execution(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)

    await _transition(session_factory, runtime, result, new_status="running")
    async with session_factory() as session:
        execution = (
            await session.execute(select(TaskExecution))
        ).scalar_one()
        assert execution.status == "running"

    response = await _transition(
        session_factory, runtime, result, new_status="completed", result=valid_result_v1()
    )
    assert response["status"] == "completed"
    assert response["execution_status"] == "completed"
    async with session_factory() as session:
        execution = (await session.execute(select(TaskExecution))).scalar_one()
        attempt = (await session.execute(select(ExecutionAttempt))).scalar_one()
        fresh = await session.get(Runtime, runtime.id)
    assert execution.status == "completed"
    assert execution.result["schema_version"] == 1
    assert execution.finished_at is not None
    assert attempt.status == "completed"
    assert fresh.current_load == 0


async def test_illegal_transition_rejected_422(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    with pytest.raises(BusinessRuleError) as exc:
        await _transition(session_factory, runtime, result, new_status="claimed")
    assert exc.value.code == "invalid_state_transition"


async def test_t10_stale_lease_seq_rejected_409(session_factory):
    """A zombie holder with an old lease_seq can never write over the new
    owner (split-brain protection): 409 on every stale report."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    renewed = await renew_lease(
        session_factory,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        lease_seconds=120,
    )
    assert renewed["lease_seq"] == 2

    with pytest.raises(ConflictError) as exc:
        await _transition(
            session_factory, runtime, result, lease_seq=1, new_status="completed"
        )
    assert exc.value.code == "lease_seq_mismatch"


async def test_terminal_report_idempotent_conflicting_terminal_409(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")
    await _transition(session_factory, runtime, result, new_status="completed")
    # Same status again → no-op.
    again = await _transition(session_factory, runtime, result, new_status="completed")
    assert again["status"] == "completed"
    # Different terminal → 409.
    with pytest.raises(ConflictError):
        await _transition(session_factory, runtime, result, new_status="failed")


async def test_renew_lease_requires_inflight_and_correct_seq(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    renewed = await renew_lease(
        session_factory,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        lease_seconds=300,
    )
    assert renewed["lease_seq"] == 2
    with pytest.raises(ConflictError):
        await renew_lease(
            session_factory,
            attempt_id=uuid.UUID(result.attempt["id"]),
            runtime=runtime,
            lease_seq=1,  # stale
            lease_seconds=300,
        )
    await _transition(session_factory, runtime, result, lease_seq=2, new_status="running")
    await _transition(session_factory, runtime, result, lease_seq=2, new_status="completed")
    with pytest.raises(BusinessRuleError):
        await renew_lease(
            session_factory,
            attempt_id=uuid.UUID(result.attempt["id"]),
            runtime=runtime,
            lease_seq=2,
            lease_seconds=300,
        )


async def test_daemon_cannot_touch_foreign_runtime_attempts(session_factory):
    world = await seed_world(session_factory)
    owner = await make_runtime(session_factory, world["ws_id"], name="owner")
    stranger = await make_runtime(session_factory, world["ws_id"], name="stranger")
    result = await _claim_one(session_factory, owner)
    with pytest.raises(ForbiddenError):
        await _transition(session_factory, stranger, result, new_status="completed")
    with pytest.raises(NotFoundError):
        await transition_attempt(
            session_factory,
            attempt_id=uuid.uuid4(),
            runtime=owner,
            lease_seq=1,
            new_status="completed",
        )


async def test_cancel_queued_execution_goes_straight_to_cancelled(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        member_id=world["member_id"],
    )
    assert data["status"] == "cancelled"
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
        presence = (
            await session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "realtime.publish",
                    OutboxEvent.payload["event"].astext == "agent.presence",
                )
                .order_by(OutboxEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    assert stored.status == "cancelled"
    assert stored.finished_at is not None
    assert presence.payload["data"] == {
        "agent_id": str(world["agent_id"]),
        "running": 0,
        "queued": 0,
        "awaiting_approval": 0,
    }


async def test_cancel_running_execution_enters_cancelling(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")
    execution_id = uuid.UUID(result.execution["id"])

    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution_id,
        member_id=world["member_id"],
        failure_reason="cancelled_by_command",
    )
    assert data["status"] == "cancelling"
    async with session_factory() as session:
        attempt = (
            await session.execute(select(ExecutionAttempt))
        ).scalar_one()
    assert attempt.status == "cancelling"  # downlink + daemon completes

    # Daemon SIGTERMs the process and PATCHes cancelled.
    await transition_attempt(
        session_factory,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        new_status="cancelled",
        failure_reason=None,
    )
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution_id)
        fresh = await session.get(Runtime, runtime.id)
    assert stored.status == "cancelled"
    assert stored.failure_reason == "cancelled_by_command"
    assert fresh.current_load == 0


async def test_agent_pause_cancel_preserves_reason_after_daemon_ack(session_factory):
    """The daemon only acknowledges cancellation; the server owns its cause."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")
    execution_id = uuid.UUID(result.execution["id"])

    async with session_factory() as session, session.begin():
        affected = await cancel_in_flight_for_agent(
            session,
            workspace_id=world["ws_id"],
            agent_id=world["agent_id"],
            issue_id=None,
            failure_reason="agent_paused",
        )
    assert affected == 1
    async with session_factory() as session:
        cancelling = await session.get(TaskExecution, execution_id)
    assert cancelling.status == "cancelling"
    assert cancelling.failure_reason == "agent_paused"

    await transition_attempt(
        session_factory,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        new_status="cancelled",
        failure_reason=None,
    )
    async with session_factory() as session:
        cancelled = await session.get(TaskExecution, execution_id)
    assert cancelled.status == "cancelled"
    assert cancelled.failure_reason == "agent_paused"
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        execution_id,
        status="cancelled",
        failure_reason="agent_paused",
    )


async def test_cancel_is_idempotent_on_terminal(session_factory):
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory, world["ws_id"], world["agent_id"], status="completed"
    )
    data = await cancel_execution(
        session_factory, workspace_id=world["ws_id"], execution_id=execution.id
    )
    assert data["status"] == "completed"  # untouched no-op


async def test_freeze_revokes_envelopes(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    from mesh.db.models.runtime import ExecutionCredential, RuntimeCredential
    from mesh.runtime.credentials import encrypt_credential_value

    cred = RuntimeCredential(
        workspace_id=world["ws_id"],
        name="SECRET",
        encrypted_value=encrypt_credential_value("s3cret", TEST_JWT_SECRET),
    )
    async with session_factory() as session, session.begin():
        session.add(cred)
        await session.flush()
        cred_id = cred.id
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        task_spec={"credential_ids": [str(cred_id)]},
    )
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None and result.attempt["credentials"]

    data = await freeze_execution(
        session_factory, workspace_id=world["ws_id"], execution_id=execution.id
    )
    assert data["revoked_envelopes"] == 1
    async with session_factory() as session:
        binding = (
            await session.execute(select(ExecutionCredential))
        ).scalar_one()
    assert binding.revoked_at is not None


async def test_daemon_cannot_inject_awaiting_approval_reason(session_factory):
    """Review H1: ``awaiting_approval`` is approvals-module-internal. A daemon
    injecting it via PATCH must be rejected — otherwise the attempt goes
    terminal while the execution stays ``running`` forever (no in-flight
    attempt left for the reaper to sweep)."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")

    with pytest.raises(BusinessRuleError) as exc:
        await _transition(
            session_factory,
            runtime,
            result,
            new_status="cancelled",
            failure_reason="awaiting_approval",
        )
    assert exc.value.code == "reserved_failure_reason"

    # Execution is untouched: still running, still completable.
    async with session_factory() as session:
        executions = (
            await session.execute(select(TaskExecution))
        ).scalars().all()
    assert executions[0].status == "running"
    await _transition(
        session_factory, runtime, result, new_status="completed", result=valid_result_v1()
    )
    async with session_factory() as session:
        executions = (
            await session.execute(select(TaskExecution))
        ).scalars().all()
    assert executions[0].status == "completed"


async def test_daemon_failure_reason_vocabulary_enforced(session_factory):
    """Review M4: arbitrary failure reasons never reach storage/events/UI."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")
    with pytest.raises(BusinessRuleError) as exc:
        await _transition(
            session_factory,
            runtime,
            result,
            new_status="failed",
            failure_reason="made_up_reason",
        )
    assert exc.value.code == "invalid_failure_reason"
    # A valid reason passes.
    await _transition(
        session_factory,
        runtime,
        result,
        new_status="failed",
        failure_reason="sandbox_violation",
    )


# ---------------------------------------------------------------------------
# MES-96 P1-2 — execution.finished is the SINGLE terminal fan-out source of
# truth (runtime.md §3.6). Every terminal transition must write the outbox
# event in the same transaction with the FULL five-field payload
# {execution_id, workspace_id, status, failure_reason, finished_at}; the squad
# relay and result sink subscribe to this event alone, with no compensating
# sweep. These regressions pin the daemon-patch path AND every console /
# supersede cancel path that previously skipped (or under-filled) the event.
# ---------------------------------------------------------------------------


async def test_finished_fanout_daemon_completed_full_payload(session_factory):
    """The daemon PATCH terminal path carries all five §3.6 payload fields."""
    world = await seed_world(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"])
    result = await _claim_one(session_factory, runtime)
    await _transition(session_factory, runtime, result, new_status="running")
    await _transition(
        session_factory, runtime, result, new_status="completed", result=valid_result_v1()
    )
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        uuid.UUID(result.execution["id"]),
        status="completed",
        failure_reason=None,
    )


async def test_finished_fanout_cancel_queued_execution(session_factory):
    """Console cancel of a QUEUED execution emits the terminal fan-out (§3.6)."""
    world = await seed_world(session_factory)
    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        member_id=world["member_id"],
    )
    assert data["status"] == "cancelled"
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        execution.id,
        status="cancelled",
        failure_reason=None,
    )


async def test_finished_fanout_cancel_awaiting_approval_execution(session_factory):
    """Console cancel of an AWAITING_APPROVAL execution emits the fan-out."""
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        status="awaiting_approval",
    )
    data = await cancel_execution(
        session_factory,
        workspace_id=world["ws_id"],
        execution_id=execution.id,
        member_id=world["member_id"],
    )
    assert data["status"] == "cancelled"
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        execution.id,
        status="cancelled",
        failure_reason=None,
    )


async def test_finished_fanout_supersede_cancel_in_flight_queued(session_factory):
    """Supersede / agent-pause (README §6.9): cancelling a QUEUED execution for
    the agent must emit the fan-out — otherwise a squad subtask whose execution
    is superseded hangs in_progress forever (no relay event, no sweep)."""
    world = await seed_world(session_factory)
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        status="queued",
        issue_id=None,
    )
    async with session_factory() as session, session.begin():
        cancelled = await cancel_in_flight_for_agent(
            session,
            workspace_id=world["ws_id"],
            agent_id=world["agent_id"],
            issue_id=None,
            failure_reason="superseded",
        )
    assert cancelled == 1
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        execution.id,
        status="cancelled",
        failure_reason="superseded",
    )
