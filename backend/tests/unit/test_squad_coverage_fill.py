"""Targeted coverage for defensive / edge branches in the squad module
(relay guard rails, SSE error path, move_task_status sub-paths, leader
rotation rejections). Real PostgreSQL.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import TaskExecution
from mesh.db.models.squad import IssueSquadAssignment, SquadTask
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ValidationError
from mesh.squad.relay import make_squad_execution_finished_handler
from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput, UpdateSquadRequest
from mesh.squad.sse import task_stream_response
from tests.unit.squad_support import (
    add_member,
    build_services,
    make_agent_member,
    make_human_member,
    make_squad,
    seed_issue,
)

pytestmark = pytest.mark.unit


class _Body:
    def __init__(self, issue_id):
        self.issue_id = str(issue_id)


async def _assign(svc, ws, squad, issue, actor):
    result = await svc.assign_issue_to_squad(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, body=_Body(issue.id)
    )
    return uuid.UUID(result["id"])


# -- relay guard rails (B10 writeback defensive branches) ------------------------


async def test_writeback_missing_execution_is_noop(session_factory, workspace_factory):
    ws = await workspace_factory()
    handler = make_squad_execution_finished_handler(object())
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(uuid.uuid4()), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)  # no execution row → silent no-op


async def test_writeback_no_orchestrator_is_noop(session_factory, workspace_factory):
    from mesh.db.models.comment import Comment

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="wb-orch")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    # Clear the orchestrator, force the root done with a summary.
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
        task.orchestrator_id = None
        task.status = "done"
        task.result_summary = "summary without author"
        session.add(
            TaskExecution(
                id=exec_id, workspace_id=ws.id, agent_id=leader.agent_id, issue_id=issue.id,
                trigger="assign", status="completed",
                task_spec={"squad_task_id": str(root_id), "squad_role": "executor"},
            )
        )
    from mesh.comment_inbox.service import CommentService

    handler = make_squad_execution_finished_handler(
        CommentService(session_factory, signing_secret="s")
    )
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(exec_id), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)
    async with session_factory() as session:
        count = len(
            (await session.execute(select(Comment).where(Comment.issue_id == issue.id))).scalars().all()
        )
    assert count == 0  # no author → no writeback


async def test_writeback_failure_does_not_break_observation(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="wb-fail")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
        task.status = "done"
        task.result_summary = "summary"
        session.add(
            TaskExecution(
                id=exec_id, workspace_id=ws.id, agent_id=leader.agent_id, issue_id=issue.id,
                trigger="assign", status="completed",
                task_spec={"squad_task_id": str(root_id), "squad_role": "executor"},
            )
        )

    class _BrokenComments:
        async def create_comment(self, **kwargs):
            raise RuntimeError("comment backend down")

    handler = make_squad_execution_finished_handler(_BrokenComments())
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(exec_id), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)  # logged, NOT raised


# -- SSE error path --------------------------------------------------------------


async def test_sse_stream_emits_error_when_task_missing(session_factory, workspace_factory):
    ws = await workspace_factory()
    resp = task_stream_response(
        session_factory,
        workspace_id=ws.id,
        squad_id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        last_event_id=0,
    )
    frames = []
    async for chunk in resp.body_iterator:
        frames.append(chunk)
        break
    assert "event: error" in frames[0]


# -- move_task_status sub-paths ---------------------------------------------------


async def test_move_pending_to_in_progress_double_hop(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="mv-leader")
    _, coder = await make_agent_member(session_factory, ws, name="mv-coder")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    # Two subtasks: the second depends on the first → stays pending.
    result = await tsvc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[
            SubtaskInput(title="first", assignee={"member_id": str(coder.id)}, stage=1),
            SubtaskInput(title="second", assignee={"member_id": str(coder.id)}, stage=1,
                         depends_on=["first"]),
        ]),
    )
    second_id = uuid.UUID(result["created_subtasks"][1]["id"])
    async with session_factory() as session:
        second = await session.scalar(select(SquadTask).where(SquadTask.id == second_id))
        assert second.status == "pending"
    rendered = await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=second_id,
        status="in_progress",
    )
    assert rendered["status"] == "in_progress"
    async with session_factory() as session:
        second = await session.scalar(select(SquadTask).where(SquadTask.id == second_id))
        assert second.dispatched_at is not None  # double hop stamps dispatch


async def test_move_blocked_clears_failure_reason(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="mv-blk")
    _, coder = await make_agent_member(session_factory, ws, name="mv-blk-coder")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    result = await tsvc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[
            SubtaskInput(title="w", assignee={"member_id": str(coder.id)}, stage=1),
        ]),
    )
    sub_id = uuid.UUID(result["created_subtasks"][0]["id"])
    async with session_factory() as session, session.begin():
        task = await session.scalar(select(SquadTask).where(SquadTask.id == sub_id))
        task.status = "blocked"
        task.failure_reason = "stuck"
    rendered = await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=sub_id, status="in_progress"
    )
    assert rendered["status"] == "in_progress"
    async with session_factory() as session:
        task = await session.scalar(select(SquadTask).where(SquadTask.id == sub_id))
        assert task.failure_reason is None


async def test_move_root_to_done_finalizes_assignment(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="mv-done")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    # pending → decomposing → done (no-action evaluation edges, manual).
    await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, status="decomposing"
    )
    rendered = await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, status="done"
    )
    assert rendered["status"] == "done"
    async with session_factory() as session:
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.issue_id == issue.id,
            )
        )
    assert assignment.status == "completed"


async def test_move_done_child_aggregates_parent(session_factory, workspace_factory):
    ws = await workspace_factory()
    leader = await make_human_member(session_factory, ws, role="member", name="HL")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    result = await tsvc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[
            SubtaskInput(title="solo", assignee={"member_id": str(leader.id)}, stage=1),
        ]),
    )
    sub_id = uuid.UUID(result["created_subtasks"][0]["id"])
    await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=sub_id, status="done",
        result_summary="child result",
    )
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert root.status == "done"


async def test_move_wrong_squad_task_not_found(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="mv-404")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await tsvc.move_task_status(
            actor=admin, workspace_id=ws.id, squad_id=uuid.uuid4(), task_id=root_id,
            status="cancelled",
        )


# -- dependency validation + leader rotation rejections ---------------------------


async def test_dependency_on_missing_task_is_validation_error(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="dep-missing")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(tsvc, ws, squad, issue, admin)
    ghost = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        await tsvc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
            body=CreateSubtasksRequest(subtasks=[
                SubtaskInput(title="a", depends_on=[ghost]),
            ]),
        )


async def test_change_primary_leader_rejects_non_leader(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="pl-rej")
    worker = await make_human_member(session_factory, ws, role="member", name="W")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, worker, role="member")
    svc, _ = build_services(session_factory)
    with pytest.raises(BusinessRuleError) as exc:
        await svc.update_squad(
            actor=admin, workspace_id=ws.id, squad_id=squad.id,
            body=UpdateSquadRequest(primary_leader_id=str(worker.id)),
        )
    assert exc.value.code == "no_leader"
