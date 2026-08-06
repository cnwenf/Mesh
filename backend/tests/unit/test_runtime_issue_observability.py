"""Issue-facing execution lifecycle projection (agent.md §4.7 / runtime.md §4.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from mesh.agent.triggers import assign_orchestration_handler
from mesh.db.models.issue import Issue, IssueActivity
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.db.models.runtime import TaskExecution
from mesh.issue.execution_observability import record_issue_execution_phase
from mesh.issue.schemas import CreateIssueRequest
from mesh.issue.service import IssueService
from mesh.issue.statuses import seed_default_statuses
from mesh.runtime.approvals import decide_approval, request_tool_approval
from mesh.runtime.attempts import transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.enqueue import enqueue_execution_handler
from tests.unit.runtime_support import (
    TEST_JWT_SECRET,
    make_execution,
    make_runtime,
    seed_world,
    valid_result_v1,
)


async def _world_with_issue(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=world["ws_id"])
        actor = await session.get(Member, world["member_id"])
        session.expunge(actor)
    created = await IssueService(session_factory).create_issue(
        actor=actor,
        workspace_id=world["ws_id"],
        body=CreateIssueRequest(
            title="Run this issue",
            assignee_id=str(world["agent_member_id"]),
        ),
    )
    execution = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        issue_id=uuid.UUID(created["id"]),
    )
    return world, created, execution


async def _materialize_assignment(session_factory):
    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=world["ws_id"])
        actor = await session.get(Member, world["member_id"])
        session.expunge(actor)
    created = await IssueService(session_factory).create_issue(
        actor=actor,
        workspace_id=world["ws_id"],
        body=CreateIssueRequest(
            title="Observe the whole run",
            assignee_id=str(world["agent_member_id"]),
        ),
    )
    async with session_factory() as session, session.begin():
        assigned_event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "issue.assigned")
            )
        ).scalar_one()
        await assign_orchestration_handler(session, assigned_event)
    async with session_factory() as session, session.begin():
        enqueue_event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "execution.enqueue")
            )
        ).scalar_one()
        await enqueue_execution_handler(session, enqueue_event)
    async with session_factory() as session:
        execution = (await session.execute(select(TaskExecution))).scalar_one()
    return world, created, execution


async def _execution_frames(session_factory, execution_id: uuid.UUID):
    async with session_factory() as session:
        events = list(
            (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.event_type == "realtime.publish")
                    .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
                )
            )
            .scalars()
            .all()
        )
    return [
        event.payload
        for event in events
        if (event.payload.get("data") or {}).get("execution_id") == str(execution_id)
    ]


async def test_queued_projection_uses_final_execution_id_and_moves_issue_in_progress(
    session_factory,
):
    world, created, execution = await _world_with_issue(session_factory)

    async with session_factory() as session, session.begin():
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=uuid.UUID(created["id"]),
            execution_id=execution.id,
            agent_id=world["agent_id"],
            phase="queued",
        )

    async with session_factory() as session:
        issue = await session.get(Issue, uuid.UUID(created["id"]))
        activity = list(
            (
                await session.execute(
                    select(IssueActivity).where(IssueActivity.issue_id == issue.id)
                )
            )
            .scalars()
            .all()
        )
    assert issue.state_category == "in_progress"
    assert any(
        row.field == "execution"
        and row.new_value == {"state": "started", "execution_id": str(execution.id)}
        for row in activity
    )

    frames = await _execution_frames(session_factory, execution.id)
    queued = next(frame for frame in frames if frame["event"] == "execution.queued")
    assert queued["channel"] == f"issue:{created['id']}"
    assert queued["data"]["execution_id"] == str(execution.id)
    assert queued["data"]["agent_member_id"] == str(world["agent_member_id"])


async def test_private_issue_execution_never_enters_workspace_execution_stream(
    session_factory,
):
    world, created, _existing = await _world_with_issue(session_factory)
    issue_id = uuid.UUID(created["id"])
    key = f"private-execution-{uuid.uuid4()}"
    async with session_factory() as session, session.begin():
        project = Project(
            workspace_id=world["ws_id"],
            name=f"Private {uuid.uuid4().hex[:8]}",
            key=f"P{uuid.uuid4().hex[:7].upper()}",
            visibility="private",
        )
        session.add(project)
        await session.flush()
        issue = await session.get(Issue, issue_id)
        assert issue is not None
        issue.project_id = project.id
        event = OutboxEvent(
            workspace_id=world["ws_id"],
            event_type="execution.enqueue",
            payload={
                "intent": "enqueue",
                "agent_id": str(world["agent_id"]),
                "issue_id": str(issue_id),
                "trigger": "mention",
                "idempotency_key": key,
                "comment_id": str(uuid.uuid4()),
            },
            idempotency_key=key,
            status="pending",
        )
        session.add(event)
        await session.flush()
        await enqueue_execution_handler(session, event)

    async with session_factory() as session:
        execution = await session.scalar(
            select(TaskExecution).where(TaskExecution.idempotency_key == key)
        )
        assert execution is not None
    frames = await _execution_frames(session_factory, execution.id)
    assert any(frame["channel"] == f"issue:{issue_id}" for frame in frames)
    assert all(
        frame["channel"] != f"workspace:{world['ws_id']}:executions"
        for frame in frames
    )


async def test_claimed_and_started_projection_carries_runtime_and_completed_moves_to_review(
    session_factory,
):
    world, created, execution = await _world_with_issue(session_factory)
    attempt_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=uuid.UUID(created["id"]),
            execution_id=execution.id,
            agent_id=world["agent_id"],
            phase="claimed",
            attempt_id=attempt_id,
            runtime_id=uuid.uuid4(),
            runtime_name="runner-east",
        )
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=uuid.UUID(created["id"]),
            execution_id=execution.id,
            agent_id=world["agent_id"],
            phase="started",
            attempt_id=attempt_id,
            runtime_name="runner-east",
        )
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=uuid.UUID(created["id"]),
            execution_id=execution.id,
            agent_id=world["agent_id"],
            phase="completed",
            attempt_id=attempt_id,
        )

    async with session_factory() as session:
        issue = await session.get(Issue, uuid.UUID(created["id"]))
    assert issue.state_category == "in_review"

    frames = await _execution_frames(session_factory, execution.id)
    claimed = next(frame for frame in frames if frame["event"] == "execution.claimed")
    assert claimed["data"]["runtime_name"] == "runner-east"
    assert claimed["data"]["attempt_id"] == str(attempt_id)
    assert {frame["event"] for frame in frames} >= {
        "execution.claimed",
        "execution.started",
        "execution.completed",
    }


async def test_older_completion_cannot_move_issue_with_newer_run_to_review(
    session_factory,
):
    world, created, older = await _world_with_issue(session_factory)
    issue_id = uuid.UUID(created["id"])
    newer = await make_execution(
        session_factory,
        world["ws_id"],
        world["agent_id"],
        issue_id=issue_id,
        status="running",
    )
    async with session_factory() as session, session.begin():
        stored_older = await session.get(TaskExecution, older.id)
        stored_newer = await session.get(TaskExecution, newer.id)
        stored_older.queued_at = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
        stored_newer.queued_at = datetime(2026, 8, 5, 8, 1, tzinfo=UTC)

    async with session_factory() as session, session.begin():
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=issue_id,
            execution_id=newer.id,
            agent_id=world["agent_id"],
            phase="queued",
        )
        await record_issue_execution_phase(
            session,
            workspace_id=world["ws_id"],
            issue_id=issue_id,
            execution_id=older.id,
            agent_id=world["agent_id"],
            phase="completed",
        )

    async with session_factory() as session:
        issue = await session.get(Issue, issue_id)
    assert issue.state_category == "in_progress"


async def test_assignment_materialize_claim_start_terminal_projects_issue_and_presence(
    session_factory,
):
    world, created, execution = await _materialize_assignment(session_factory)
    runtime = await make_runtime(session_factory, world["ws_id"], name="runner-east")

    claimed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert claimed is not None
    attempt_id = uuid.UUID(claimed.attempt["id"])
    first_attempt_id = attempt_id
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    approval = await request_tool_approval(
        session_factory,
        execution_id=execution.id,
        runtime=runtime,
        attempt_id=attempt_id,
        lease_seq=1,
        action_summary={"action": "exec:shell"},
        resume_context={"checkpoint_ref": "checkpoint/1"},
        approval_ttl=timedelta(hours=1),
    )
    await decide_approval(
        session_factory,
        approval_id=uuid.UUID(approval["id"]),
        workspace_id=world["ws_id"],
        member=SimpleNamespace(
            id=world["member_id"],
            member_type="human",
            role="admin",
            user_id=world["user_id"],
        ),
        approve=True,
    )
    resumed = await claim_execution(
        session_factory,
        runtime=runtime,
        lease_seconds=120,
        signing_secret=TEST_JWT_SECRET,
        envelope_ttl=timedelta(hours=2),
    )
    assert resumed is not None
    attempt_id = uuid.UUID(resumed.attempt["id"])
    assert resumed.attempt["attempt_number"] == 2
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="running",
    )
    await transition_attempt(
        session_factory,
        attempt_id=attempt_id,
        runtime=runtime,
        lease_seq=1,
        new_status="completed",
        result=valid_result_v1(),
    )

    async with session_factory() as session:
        issue = await session.get(Issue, uuid.UUID(created["id"]))
        events = list(
            (
                    await session.execute(
                        select(OutboxEvent)
                        .where(OutboxEvent.event_type == "realtime.publish")
                        .order_by(OutboxEvent.created_at.asc(), OutboxEvent.id.asc())
                    )
            )
            .scalars()
            .all()
        )

    issue_phases = [
        event.payload
        for event in events
        if event.payload["channel"] == f"issue:{created['id']}"
        and event.payload["event"].startswith("execution.")
    ]
    assert [event["event"] for event in issue_phases] == [
        "execution.queued",
        "execution.claimed",
        "execution.started",
        "execution.awaiting_approval",
        "execution.requeued",
        "execution.claimed",
        "execution.started",
        "execution.completed",
    ]
    assert all(
        event["data"]["execution_id"] == str(execution.id) for event in issue_phases
    )
    claimed_frames = [event for event in issue_phases if event["event"] == "execution.claimed"]
    assert all(event["data"]["runtime_name"] == "runner-east" for event in claimed_frames)
    assert [event["data"]["attempt_id"] for event in claimed_frames] == [
        str(first_attempt_id),
        str(attempt_id),
    ]
    presence = [
        event.payload["data"]
        for event in events
        if event.payload["event"] == "agent.presence"
    ]
    assert presence[0] == {
        "agent_id": str(world["agent_id"]),
        "running": 0,
        "queued": 1,
        "awaiting_approval": 0,
    }
    assert any(snapshot["running"] == 1 for snapshot in presence)
    assert any(snapshot["awaiting_approval"] == 1 for snapshot in presence)
    assert presence[-1] == {
        "agent_id": str(world["agent_id"]),
        "running": 0,
        "queued": 0,
        "awaiting_approval": 0,
    }
    assert issue.state_category == "in_review"
