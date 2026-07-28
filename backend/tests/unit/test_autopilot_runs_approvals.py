"""autopilot.runs state machine + autopilot.approvals gate (§4.4 / §6.10)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.autopilot import approvals as approvals_mod
from mesh.autopilot import runs as runs_mod
from mesh.db.models.autopilot import AutopilotRun
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import Approval
from mesh.errors import BusinessRuleError, NotFoundError
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.runtime_support import seed_world


async def _load_run(session_factory, run_id) -> AutopilotRun:
    async with session_factory() as session:
        return await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run_id))


async def test_transition_allowed_and_idempotent(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        assert await runs_mod.transition_run(session, row, "running") is True
        assert row.started_at is not None
        # same transition again → no-op
        assert await runs_mod.transition_run(session, row, "running") is False
        assert await runs_mod.transition_run(session, row, "succeeded") is True
        assert row.finished_at is not None and row.duration_ms is not None
        # terminal states accept no transitions
        assert await runs_mod.transition_run(session, row, "running") is False


async def test_transition_illegal_edge(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        # pending cannot jump straight to retrying
        assert await runs_mod.transition_run(session, row, "retrying") is False


async def test_new_attempt_numbers_never_reused(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="running")
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        a1 = await runs_mod.new_attempt(session, row)
        row.retry_count = 1
        a2 = await runs_mod.new_attempt(session, row)
    assert a1.attempt_number == 1
    assert a2.attempt_number == 2


async def test_artifact_recording(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="running")
    ref = uuid.uuid4()
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        artifact = await runs_mod.record_artifact(
            session, row, artifact_type="comment", ref_table="comments", ref_id=ref, summary="ok"
        )
    assert artifact.ref_id == ref


def test_is_retryable_classification() -> None:
    assert runs_mod.is_retryable({"code": "timeout"}) is True
    assert runs_mod.is_retryable({"code": "executor_busy"}) is True
    assert runs_mod.is_retryable({"code": "invalid_request"}) is False
    assert runs_mod.is_retryable({"retryable": True, "code": "anything"}) is True
    assert runs_mod.is_retryable(None) is False


async def test_create_run_emits_status_changed(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    async with session_factory() as session, session.begin():
        await runs_mod.create_run(session, rule=rule, trigger_snapshot={"event_id": "e1"})
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "realtime.publish")
                )
            )
            .scalars()
            .all()
        )
    names = {e.payload["event"] for e in events}
    assert names == {"autopilot_runs.status_changed"}
    # both channels: workspace autopilots + rule detail
    channels = {e.payload["channel"] for e in events}
    assert len(channels) == 2
    assert any(channel.startswith("autopilot:") for channel in channels)


# ---------------------------------------------------------------------------
# approvals gate
# ---------------------------------------------------------------------------


def test_requires_approval_flag_and_action_types() -> None:
    class _Rule:
        require_approval = False
        guardrails = {"approval_required_actions": ["http_request", "create_issue"]}
        action_config = [{"type": "run_agent_prompt"}, {"type": "http_request", "url": "x"}]

    required, matched = approvals_mod.requires_approval(_Rule())
    assert required is True
    assert matched == ["http_request"]
    _Rule.action_config = [{"type": "run_agent_prompt"}]
    required2, matched2 = approvals_mod.requires_approval(_Rule())
    assert required2 is False and matched2 == []
    _Rule.require_approval = True
    required3, _ = approvals_mod.requires_approval(_Rule())
    assert required3 is True


async def test_request_approval_parks_run_and_is_idempotent(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        approval = await approvals_mod.request_run_approval(
            session,
            run=row,
            rule=rule,
            requested_by_member_id=world["member_id"],
            action_summary={"action": "autopilot_run"},
            ttl=timedelta(hours=24),
        )
        assert row.status == "waiting_approval"
    assert approval.subject_type == "autopilot_action"
    # repeat request returns the existing pending row (single pending per run)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        again = await approvals_mod.request_run_approval(
            session,
            run=row,
            rule=rule,
            requested_by_member_id=world["member_id"],
            action_summary={},
            ttl=timedelta(hours=24),
        )
    assert again.id == approval.id
    async with session_factory() as session:
        count = len(
            (await session.execute(select(Approval).where(Approval.status == "pending")))
            .scalars()
            .all()
        )
    assert count == 1
    # realtime + notification emitted
    async with session_factory() as session:
        events = (await session.execute(select(OutboxEvent))).scalars().all()
    published = {e.payload.get("event") for e in events if e.event_type == "realtime.publish"}
    assert "autopilot_runs.approval_required" in published
    assert "approval.created" in published
    fanouts = [e for e in events if e.event_type == "notification.fanout"]
    assert fanouts and fanouts[0].payload["type"] == "review_requested"


async def test_apply_decision_approve_and_reject(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="waiting_approval")
    async with session_factory() as session, session.begin():
        approval = Approval(
            workspace_id=world["ws_id"],
            subject_type="autopilot_action",
            subject_run_id=run.id,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(approval)
        await session.flush()
        new_status = await approvals_mod.apply_approval_decision(session, approval=approval, approve=True)
    assert new_status == "running"
    assert (await _load_run(session_factory, run.id)).status == "running"

    # reject path on a second run
    run2 = await make_run(session_factory, rule, status="waiting_approval")
    async with session_factory() as session, session.begin():
        approval2 = Approval(
            workspace_id=world["ws_id"],
            subject_type="autopilot_action",
            subject_run_id=run2.id,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(approval2)
        await session.flush()
        rejected = await approvals_mod.apply_approval_decision(session, approval=approval2, approve=False)
    assert rejected == "cancelled"
    row2 = await _load_run(session_factory, run2.id)
    assert row2.error["code"] == "approval_rejected"


async def test_apply_decision_requires_waiting_state(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="succeeded")
    async with session_factory() as session, session.begin():
        approval = Approval(
            workspace_id=world["ws_id"],
            subject_type="autopilot_action",
            subject_run_id=run.id,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="approved",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(approval)
        await session.flush()
        assert await approvals_mod.apply_approval_decision(session, approval=approval, approve=True) is None


async def test_expire_run_approval(session_factory) -> None:
    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="waiting_approval")
    async with session_factory() as session, session.begin():
        approval = Approval(
            workspace_id=world["ws_id"],
            subject_type="autopilot_action",
            subject_run_id=run.id,
            requested_by_member_id=world["member_id"],
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(approval)
        await session.flush()
        await approvals_mod.expire_run_approval(session, approval=approval)
    row = await _load_run(session_factory, run.id)
    assert row.status == "cancelled"
    assert row.error["code"] == "approval_expired"
    # notified
    async with session_factory() as session:
        fanouts = (
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
                )
            )
            .scalars()
            .all()
        )
    assert fanouts and fanouts[0].payload["type"] == "autopilot_notice"


async def test_decide_run_approval_lookup_errors(session_factory) -> None:
    world = await seed_world(session_factory)
    with pytest.raises(NotFoundError):
        async with session_factory() as session:
            await approvals_mod.decide_run_approval(
                session, workspace_id=world["ws_id"], run_id=uuid.uuid4()
            )
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="running")  # no pending approval
    with pytest.raises(BusinessRuleError) as excinfo:
        async with session_factory() as session:
            await approvals_mod.decide_run_approval(
                session, workspace_id=world["ws_id"], run_id=run.id
            )
    assert excinfo.value.code == "invalid_state_transition"


async def test_unified_decide_approval_resumes_run(session_factory) -> None:
    """End-to-end through runtime.approvals.decide_approval (the §6.10收口)."""
    from mesh.runtime.approvals import decide_approval

    world = await seed_world(session_factory)
    rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        executor_agent_id=world["agent_id"],
    )
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session:
        member = await session.scalar(select(Member).where(Member.id == world["member_id"]))
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        approval = await approvals_mod.request_run_approval(
            session, run=row, rule=rule, requested_by_member_id=world["member_id"],
            action_summary={}, ttl=timedelta(hours=24),
        )
    decision = await decide_approval(
        session_factory,
        approval_id=approval.id,
        workspace_id=world["ws_id"],
        member=member,
        approve=True,
    )
    assert decision["status"] == "approved"
    row = await _load_run(session_factory, run.id)
    assert row.status == "running"

    # idempotent re-decide returns current state
    again = await decide_approval(
        session_factory,
        approval_id=approval.id,
        workspace_id=world["ws_id"],
        member=member,
        approve=False,
    )
    assert again["status"] == "approved"


async def test_agent_cannot_approve(session_factory) -> None:
    from mesh.errors import ForbiddenError
    from mesh.runtime.approvals import decide_approval

    world = await seed_world(session_factory)
    rule = await make_rule(session_factory, world["ws_id"], created_by=world["member_id"])
    run = await make_run(session_factory, rule, status="pending")
    async with session_factory() as session:
        agent_member = await session.scalar(
            select(Member).where(Member.id == world["agent_member_id"])
        )
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(AutopilotRun).where(AutopilotRun.id == run.id))
        approval = await approvals_mod.request_run_approval(
            session, run=row, rule=rule, requested_by_member_id=world["member_id"],
            action_summary={}, ttl=timedelta(hours=24),
        )
    with pytest.raises(ForbiddenError):
        await decide_approval(
            session_factory,
            approval_id=approval.id,
            workspace_id=world["ws_id"],
            member=agent_member,
            approve=True,
        )
