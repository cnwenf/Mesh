"""Extra branch coverage for squad orchestration: human dispatch + notify,
execution cancel (queued/running), orchestrator-failure mapping, leader change
through the service, instruction-trigger guards, and read filters."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.squad import SquadTask
from mesh.squad.schemas import (
    CreateSubtasksRequest,
    SendMessageRequest,
    SquadMemberInput,
    SubtaskInput,
    UpdateSquadRequest,
)
from tests.unit.squad_support import (
    add_member,
    build_services,
    make_agent_member,
    make_human_member,
    make_squad,
    seed_issue,
)

pytestmark = pytest.mark.unit


async def _assign(svc, ws, squad, issue, actor):
    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    return uuid.UUID(r["id"])


async def test_dispatch_human_assignee_notifies(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    human = await make_human_member(session_factory, ws, role="member")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, human, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    res = await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="h", assignee={"member_id": str(human.id)}, stage=1)
            ]
        ),
    )
    # Human assignee → in_progress + a notification.fanout outbox event.
    sub_id = uuid.UUID(res["created_subtasks"][0]["id"])
    async with session_factory() as session:
        sub = await session.scalar(select(SquadTask).where(SquadTask.id == sub_id))
        fanouts = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == ws.id, OutboxEvent.event_type == "notification.fanout"
                )
            )
        ).scalars().all()
    assert sub.status == "in_progress"
    assert len(fanouts) >= 1


async def test_cancel_task_cancels_queued_and_running_executions(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    res = await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="a"), SubtaskInput(title="b")]),
    )
    ids = [uuid.UUID(s["id"]) for s in res["created_subtasks"]]
    q_id, r_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(id=q_id, workspace_id=ws.id, trigger="assign", status="queued", task_spec={})
        )
        session.add(
            TaskExecution(id=r_id, workspace_id=ws.id, trigger="assign", status="running", task_spec={})
        )
        t0 = await session.scalar(select(SquadTask).where(SquadTask.id == ids[0]))
        t1 = await session.scalar(select(SquadTask).where(SquadTask.id == ids[1]))
        t0.execution_id = q_id
        t1.execution_id = r_id
    await svc.cancel_task(actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, reason="stop")
    async with session_factory() as session:
        q = await session.scalar(select(TaskExecution).where(TaskExecution.id == q_id))
        r = await session.scalar(select(TaskExecution).where(TaskExecution.id == r_id))
    assert q.status == "cancelled"
    assert r.status == "cancelling"


async def test_orchestrator_failure_marks_root_failed(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    # The leader's orchestrator execution fails before producing subtasks.
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id, workspace_id=ws.id, agent_id=leader.agent_id, issue_id=issue.id,
                trigger="assign", status="running",
                task_spec={"squad_task_id": str(root_id), "squad_role": "orchestrator"},
            )
        )
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context
    from mesh.squad.tasks import observe_execution_finished_tx

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session, workspace_id=ws.id, execution_id=exec_id, status="failed",
            failure_reason="crashed", now=datetime.now(UTC),
        )
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert root.status == "failed"
    assert root.failure_reason == "crashed"


async def test_update_squad_leader_change_propagates(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader1 = await make_agent_member(session_factory, ws, name="l1")
    _, leader2 = await make_agent_member(session_factory, ws, name="l2")
    squad = await make_squad(session_factory, ws, leader_member=leader1)
    await add_member(session_factory, ws, squad, leader2, role="leader")
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, tsvc = build_services(session_factory)
    await _assign(tsvc, ws, squad, issue, actor)
    updated = await svc.update_squad(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=UpdateSquadRequest(primary_leader_id=str(leader2.id)),
    )
    assert updated["primary_leader_id"] == str(leader2.id)
    from mesh.db.models.issue import Issue

    async with session_factory() as session:
        refreshed = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert refreshed.assignee_id == leader2.id


async def test_update_squad_duplicate_name_conflict(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    await make_squad(session_factory, ws, leader_member=leader, name="NameA")
    s2 = await make_squad(session_factory, ws, leader_member=leader, name="NameB")
    from mesh.errors import ConflictError

    with pytest.raises(ConflictError) as exc:
        await svc.update_squad(
            actor=actor, workspace_id=ws.id, squad_id=s2.id, body=UpdateSquadRequest(name="NameA")
        )
    assert exc.value.code == "squad_name_taken"


async def test_instruction_from_non_leader_no_trigger(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    member = await make_human_member(session_factory, ws, role="member")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, member, role="member")
    await add_member(session_factory, ws, squad, coder, role="member")
    svc, _ = build_services(session_factory)
    # A non-leader member sends an "instruction" → must NOT trigger a run.
    await svc.send_message(
        actor=member, workspace_id=ws.id, squad_id=squad.id,
        body=SendMessageRequest(
            kind="instruction", recipient=SquadMemberInput(member_id=str(coder.id)), body_markdown="hey"
        ),
    )
    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == ws.id, OutboxEvent.event_type == "issue.assigned"
                )
            )
        ).scalars().all()
    assert len(events) == 0


async def test_list_messages_filters(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, actor, role="observer")
    svc, _ = build_services(session_factory)
    await svc.send_message(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=SendMessageRequest(kind="report", body_markdown="a report"),
    )
    await svc.send_message(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=SendMessageRequest(kind="chat", body_markdown="a chat"),
    )
    reports = await svc.list_messages(workspace_id=ws.id, squad_id=squad.id, kind="report")
    assert len(reports["data"]) == 1
    assert reports["data"][0]["kind"] == "report"


async def test_get_tree_blocked_by(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="a", stage=1),
                SubtaskInput(title="b", stage=2, depends_on=["a"]),
            ]
        ),
    )
    tree = await svc.get_tree(workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    assert tree["progress"]["total"] == 2
    by_title = {c["title_snapshot"]: c for c in tree["children"]}
    assert by_title["b"]["blocked_by"]  # b is blocked by a (a not done)
