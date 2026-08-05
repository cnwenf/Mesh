"""Approval protocol tests — the single resume path (README §6.10, T21).

running → awaiting_approval (attempt cancelled, lease ended, capacity
released) → approve → queued → NEW attempt resumes from resume_context;
reject → cancelled(approval_rejected); one pending approval per subject.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import Approval, ExecutionAttempt, Runtime, TaskExecution
from mesh.errors import BusinessRuleError, ForbiddenError
from mesh.runtime.approvals import decide_approval, request_tool_approval
from mesh.runtime.claim import claim_execution
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    assert_execution_finished_fanout,
    make_runtime,
    seed_world,
)

pytestmark = pytest.mark.unit

APPROVAL_TTL = timedelta(hours=24)


async def _running_execution(session_factory, world):
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    from tests.unit.runtime_support import make_execution

    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    result = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert result is not None
    from mesh.runtime.attempts import transition_attempt

    await transition_attempt(
        session_factory,
        attempt_id=uuid.UUID(result.attempt["id"]),
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    return runtime, execution, result


async def test_t21_full_approval_resume_protocol(session_factory):
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    attempt_id = uuid.UUID(result.attempt["id"])

    data = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=attempt_id,
        lease_seq=1,
        action_summary={"action": "exec:shell", "capability": "exec:shell"},
        resume_context={"checkpoint_ref": "obj/ckpt-1", "completed_steps": 4},
        approval_ttl=APPROVAL_TTL,
    )
    assert data["status"] == "pending"
    assert data["execution_status"] == "awaiting_approval"
    assert data["action_summary"] == {
        "action": "exec:shell",
        "capability": "exec:shell",
    }
    assert "resume_context" not in data["action_summary"]

    async with session_factory() as session:
        attempt = await session.get(ExecutionAttempt, attempt_id)
        stored = await session.get(TaskExecution, execution.id)
        fresh = await session.get(Runtime, runtime.id)
        approval = (
            await session.execute(select(Approval).where(Approval.status == "pending"))
        ).scalar_one()
    # The ONLY protocol: attempt cancelled (audit row kept), no in-flight state.
    assert attempt.status == "cancelled"
    assert attempt.failure_reason == "awaiting_approval"
    assert attempt.finished_at is not None
    assert stored.status == "awaiting_approval"
    assert fresh.current_load == 0  # capacity released
    # resume_context frozen into the approval.
    assert approval.action_summary["resume_context"]["checkpoint_ref"] == "obj/ckpt-1"

    # Approve → back to queued; the NEXT claim builds attempt #2.
    decided = await decide_approval(
        session_factory,
        approval_id=approval.id,
        workspace_id=world["ws_id"],
        member=_member_stub(world),
        approve=True,
        comment="lgtm",
    )
    assert decided["status"] == "approved"
    assert decided["execution_status"] == "queued"

    resumed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert resumed is not None
    assert resumed.attempt["attempt_number"] == 2
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
    assert stored.status == "claimed"


async def test_approval_reject_cancels_execution(session_factory):
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    data = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=uuid.UUID(result.attempt["id"]),
        lease_seq=1,
        action_summary={"action": "exec:shell"},
        resume_context={},
        approval_ttl=APPROVAL_TTL,
    )
    decided = await decide_approval(
        session_factory,
        approval_id=uuid.UUID(data["id"]),
        workspace_id=world["ws_id"],
        member=_member_stub(world),
        approve=False,
    )
    assert decided["status"] == "rejected"
    async with session_factory() as session:
        stored = await session.get(TaskExecution, execution.id)
    assert stored.status == "cancelled"
    assert stored.failure_reason == "approval_rejected"


async def test_approval_request_only_from_running(session_factory):
    world = await seed_world(session_factory)
    runtime = await make_runtime(
        session_factory, world["ws_id"], created_by=world["member_id"]
    )
    from tests.unit.runtime_support import make_execution

    execution = await make_execution(session_factory, world["ws_id"], world["agent_id"])
    with pytest.raises(BusinessRuleError):
        await request_tool_approval(
            session_factory,
            execution_id=execution.id,
            runtime=runtime,
            attempt_id=uuid.uuid4(),
            lease_seq=1,
            action_summary={},
            resume_context={},
            approval_ttl=APPROVAL_TTL,
        )


async def test_duplicate_pending_approval_returns_existing(session_factory):
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    first = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=uuid.UUID(result.attempt["id"]),
        lease_seq=1,
        action_summary={"action": "exec:shell"},
        resume_context={},
        approval_ttl=APPROVAL_TTL,
    )
    # Execution is now awaiting_approval — a second request cannot create a
    # second pending approval (single pending per subject).
    with pytest.raises(BusinessRuleError):
        await request_tool_approval(
            session_factory,
            execution_id=execution.id,
            runtime=runtime,
            attempt_id=uuid.UUID(result.attempt["id"]),
            lease_seq=1,
            action_summary={"action": "exec:shell"},
            resume_context={},
            approval_ttl=APPROVAL_TTL,
        )
    async with session_factory() as session:
        approvals = (await session.execute(select(Approval))).scalars().all()
    assert len(approvals) == 1
    assert str(approvals[0].id) == first["id"]


async def test_agent_member_cannot_decide_and_member_permission(session_factory):
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    data = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=uuid.UUID(result.attempt["id"]),
        lease_seq=1,
        action_summary={},
        resume_context={},
        approval_ttl=APPROVAL_TTL,
    )
    # The agent's own roster row may not self-approve (anti-loop).
    from mesh.db.models.member import Member

    async with session_factory() as session:
        agent_member = await session.get(Member, world["agent_member_id"])
        session.expunge(agent_member)
    with pytest.raises(ForbiddenError):
        await decide_approval(
            session_factory,
            approval_id=uuid.UUID(data["id"]),
            workspace_id=world["ws_id"],
            member=agent_member,
            approve=True,
        )


async def test_decide_is_idempotent(session_factory):
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    data = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=uuid.UUID(result.attempt["id"]),
        lease_seq=1,
        action_summary={},
        resume_context={},
        approval_ttl=APPROVAL_TTL,
    )
    once = await decide_approval(
        session_factory,
        approval_id=uuid.UUID(data["id"]),
        workspace_id=world["ws_id"],
        member=_member_stub(world),
        approve=True,
    )
    twice = await decide_approval(
        session_factory,
        approval_id=uuid.UUID(data["id"]),
        workspace_id=world["ws_id"],
        member=_member_stub(world),
        approve=False,  # re-decide is a no-op returning the current state
    )
    assert once["status"] == twice["status"] == "approved"


def _member_stub(world):
    """Admin member stub for console-side decisions."""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=world["member_id"],
        member_type="human",
        role="admin",
        user_id=world["user_id"],
    )


async def test_finished_fanout_approval_reject_cancels_execution(session_factory):
    """MES-96 P1-2: rejecting a tool approval cancels the awaiting execution
    (approval_rejected) AND must write the execution.finished fan-out in the
    same transaction (runtime.md §3.6) — the squad relay observes this event
    alone; without it a rejected squad subtask hangs in_progress."""
    world = await seed_world(session_factory)
    runtime, execution, result = await _running_execution(session_factory, world)
    data = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=uuid.UUID(result.attempt["id"]),
        lease_seq=1,
        action_summary={"action": "exec:shell"},
        resume_context={},
        approval_ttl=APPROVAL_TTL,
    )
    decided = await decide_approval(
        session_factory,
        approval_id=uuid.UUID(data["id"]),
        workspace_id=world["ws_id"],
        member=_member_stub(world),
        approve=False,
    )
    assert decided["status"] == "rejected"
    await assert_execution_finished_fanout(
        session_factory,
        world["ws_id"],
        execution.id,
        status="cancelled",
        failure_reason="approval_rejected",
    )
