"""Squad orchestration unit tests — assignment identity (T23), decomposition DAG,
plan approval (§6.10), aggregation and execution observation. Real PostgreSQL.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import Approval, TaskExecution
from mesh.db.models.squad import IssueSquadAssignment, SquadTask
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError
from mesh.squad import tasks as squad_tasks
from mesh.squad.tasks import (
    apply_plan_decision_tx,
    change_primary_leader_tx,
    handle_leader_departure_tx,
    observe_execution_finished_tx,
    on_issue_assignee_changed_tx,
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


async def _active_assignment(session_factory, issue_id):
    async with session_factory() as session:
        return await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.issue_id == issue_id,
                IssueSquadAssignment.status == "active",
            )
        )


async def _load_task(session_factory, task_id):
    async with session_factory() as session:
        return await session.scalar(select(SquadTask).where(SquadTask.id == task_id))


async def _root_task(session_factory, assignment):
    return await _load_task(session_factory, assignment.root_task_id)


async def test_assign_creates_active_assignment_and_root(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="leader")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws, title="Orders")
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    result = await svc.assign_issue_to_squad(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body()
    )
    assert result["assignment_id"]
    assert result["status"] == "pending"
    assert result["issue_assignee_id"] == str(leader.id)
    assert result["noop"] is False

    assignment = await _active_assignment(session_factory, issue.id)
    assert assignment is not None
    assert assignment.squad_id == squad.id
    root = await _root_task(session_factory, assignment)
    assert root.parent_task_id is None
    assert root.root_task_id == root.id
    assert root.depth == 0
    assert assignment.root_task_id == root.id
    # Issue assignee = squad leader (exclusive assignee model).
    from mesh.db.models.issue import Issue

    async with session_factory() as session:
        refreshed = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert refreshed.assignee_id == leader.id


async def test_assign_without_leader_raises_squad_no_leader(session_factory, workspace_factory):
    ws = await workspace_factory()
    leader = await make_human_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    # Clear the primary leader to simulate a leaderless squad.
    from mesh.db.models.squad import Squad

    async with session_factory() as session, session.begin():
        row = await session.scalar(select(Squad).where(Squad.id == squad.id))
        row.primary_leader_id = None
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    with pytest.raises(BusinessRuleError) as exc:
        await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    assert exc.value.code == "squad_no_leader"
    assert await _active_assignment(session_factory, issue.id) is None


async def test_duplicate_assign_same_squad_is_noop(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    first = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    second = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    assert second["noop"] is True
    assert second["assignment_id"] == first["assignment_id"]
    assert second["id"] == first["id"]


async def test_same_leader_cross_squad_reassign_is_not_noop(session_factory, workspace_factory):
    """T23: one leader leads S1 and S2; issue S1→S2 must cancel S1's root even
    though the assignee value (the shared leader) is unchanged."""
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="shared-leader")
    s1 = await make_squad(session_factory, ws, leader_member=leader, name="s1")
    s2 = await make_squad(session_factory, ws, leader_member=leader, name="s2")
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r1 = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=s1.id, body=Body())
    root1_id = r1["id"]
    r2 = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=s2.id, body=Body())
    assert r2["noop"] is False
    assert r2["superseded_assignment_id"] == r1["assignment_id"]
    assert r2["issue_assignee_id"] == str(leader.id)  # assignee value unchanged

    # S1 root cancelled, S2 root active.
    root1 = await _load_task(session_factory, uuid.UUID(root1_id))
    assert root1.status == "cancelled"
    active = await _active_assignment(session_factory, issue.id)
    assert active.squad_id == s2.id
    root2 = await _root_task(session_factory, active)
    assert root2.status == "pending"
    # Exactly one active assignment (partial unique index).
    async with session_factory() as session:
        count = len(
            (
                await session.execute(
                    select(IssueSquadAssignment).where(
                        IssueSquadAssignment.issue_id == issue.id,
                        IssueSquadAssignment.status == "active",
                    )
                )
            ).scalars().all()
        )
    assert count == 1


async def test_leader_change_propagates_same_txn(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader1 = await make_agent_member(session_factory, ws, name="l1")
    _, leader2 = await make_agent_member(session_factory, ws, name="l2")
    squad = await make_squad(session_factory, ws, leader_member=leader1)
    await add_member(session_factory, ws, squad, leader2, role="leader")
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    assignment = await _active_assignment(session_factory, issue.id)
    root_before = await _root_task(session_factory, assignment)

    from datetime import UTC, datetime

    from mesh.db.models.squad import Squad

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        sq = await session.scalar(select(Squad).where(Squad.id == squad.id).with_for_update())
        await change_primary_leader_tx(
            session, workspace_id=ws.id, squad=sq, new_leader_id=leader2.id, actor_id=actor.id,
            now=datetime.now(UTC),
        )

    assignment2 = await _active_assignment(session_factory, issue.id)
    assert assignment2.leader_member_id == leader2.id
    from mesh.db.models.issue import Issue

    async with session_factory() as session:
        refreshed = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert refreshed.assignee_id == leader2.id
    # Root NOT cancelled (squad unchanged, only the leader rotated).
    root_after = await _load_task(session_factory, root_before.id)
    assert root_after.status == "pending"


async def test_leader_departure_no_replacement_blocks_root(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    assignment = await _active_assignment(session_factory, issue.id)
    root_id = assignment.root_task_id

    from datetime import UTC, datetime

    from mesh.db.models.squad import Squad, SquadMember

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        # Mirror remove_member: the leader's membership is soft-deleted first.
        row = await session.scalar(
            select(SquadMember)
            .where(SquadMember.squad_id == squad.id, SquadMember.member_id == leader.id)
            .with_for_update()
        )
        row.left_at = datetime.now(UTC)
        sq = await session.scalar(select(Squad).where(Squad.id == squad.id).with_for_update())
        await handle_leader_departure_tx(
            session, workspace_id=ws.id, squad=sq, departed_member_id=leader.id, actor_id=actor.id,
            now=datetime.now(UTC),
        )

    root = await _load_task(session_factory, root_id)
    assert root.status == "blocked"
    assert root.failure_reason == "leader_lost"
    # Assignment preserved (not cancelled).
    assert (await _active_assignment(session_factory, issue.id)) is not None


async def test_issue_reassigned_watcher_cancels_assignment(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    assignment = await _active_assignment(session_factory, issue.id)
    root_id = assignment.root_task_id
    outsider = await make_human_member(session_factory, ws)

    from datetime import UTC, datetime

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        await on_issue_assignee_changed_tx(
            session, workspace_id=ws.id, issue_id=issue.id, new_assignee_id=outsider.id,
            now=datetime.now(UTC),
        )

    assert await _active_assignment(session_factory, issue.id) is None
    root = await _load_task(session_factory, root_id)
    assert root.status == "cancelled"


async def test_watcher_noop_when_assignee_still_leader(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    from datetime import UTC, datetime

    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, ws.id)
        await on_issue_assignee_changed_tx(
            session, workspace_id=ws.id, issue_id=issue.id, new_assignee_id=leader.id,
            now=datetime.now(UTC),
        )
    assert await _active_assignment(session_factory, issue.id) is not None


async def _assign_and_decompose(session_factory, ws, svc, squad, issue, actor, *, subtasks, approval=False):
    from tests.unit.squad_support import build_services  # noqa: F401

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])

    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(
        plan_markdown="plan",
        subtasks=[SubtaskInput(**s) for s in subtasks],
    )
    result = await svc.create_subtasks(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
    )
    return root_id, result


async def test_create_subtasks_and_dispatch(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=False)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    actor = leader  # orchestrator
    _, svc = build_services(session_factory)

    root_id, result = await _assign_and_decompose(
        session_factory, ws, svc, squad, issue, actor,
        subtasks=[
            {"title": "t1", "assignee": {"member_id": str(coder.id)}, "stage": 1},
            {"title": "t2", "assignee": {"member_id": str(coder.id)}, "stage": 2, "depends_on": ["t1"]},
        ],
    )
    assert result["awaiting_approval"] is False
    assert len(result["created_subtasks"]) == 2
    # Stage-1 dispatched (in_progress), stage-2 still pending (dep not done).
    statuses = {s["title"]: s["status"] for s in result["created_subtasks"]}
    assert statuses["t1"] == "in_progress"
    assert statuses["t2"] == "pending"
    assert len(result["dependencies"]) == 1


async def test_decompose_cycle_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])

    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(
        subtasks=[
            SubtaskInput(title="a", depends_on=["b"]),
            SubtaskInput(title="b", depends_on=["a"]),
        ]
    )
    with pytest.raises(BusinessRuleError) as exc:
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
        )
    assert exc.value.code == "dependency_cycle"


async def test_decompose_depth_exceeded(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader, max_decompose_depth=1)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    actor = await make_human_member(session_factory, ws, role="admin")
    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])

    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    # root is depth 0; max_decompose_depth=1 allows depth-1 children. Create one,
    # then try to decompose THAT (depth 2 > 1).
    req1 = CreateSubtasksRequest(subtasks=[SubtaskInput(title="child")])
    res1 = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req1
    )
    child_id = uuid.UUID(res1["created_subtasks"][0]["id"])
    req2 = CreateSubtasksRequest(subtasks=[SubtaskInput(title="grandchild")])
    with pytest.raises(BusinessRuleError) as exc:
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=child_id, body=req2
        )
    assert exc.value.code == "decompose_depth_exceeded"


async def test_assignee_not_member_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, outsider = await make_agent_member(session_factory, ws, name="outsider")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x", assignee={"member_id": str(outsider.id)})])
    with pytest.raises(BusinessRuleError) as exc:
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
        )
    assert exc.value.code == "assignee_not_member"


async def test_plan_approval_approve_dispatches(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=True)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x", assignee={"member_id": str(coder.id)})])
    result = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
    )
    assert result["awaiting_approval"] is True
    root = await _load_task(session_factory, root_id)
    assert root.status == "awaiting_plan_approval"
    approval_id = uuid.UUID(result["approval"]["id"])

    # Approve via the shared decide_approval (thin wrapper target).
    from mesh.runtime.approvals import decide_approval

    await decide_approval(
        session_factory, approval_id=approval_id, workspace_id=ws.id, member=actor, approve=True
    )
    # The relay handler applies the decision; invoke it directly here.
    async with session_factory() as session:
        ap = await session.scalar(select(Approval).where(Approval.id == approval_id))
    assert ap.status == "approved"
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id).with_for_update())
        await apply_plan_decision_tx(
            session, workspace_id=ws.id, task=task, decision="approved", now=datetime.now(UTC)
        )
    root2 = await _load_task(session_factory, root_id)
    assert root2.status in ("dispatching", "in_progress")


async def test_plan_approval_reject_returns_to_decomposing(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=True)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")])
    await svc.create_subtasks(actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req)
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id).with_for_update())
        await apply_plan_decision_tx(
            session, workspace_id=ws.id, task=task, decision="rejected", now=datetime.now(UTC)
        )
    assert (await _load_task(session_factory, root_id)).status == "decomposing"


async def test_plan_approval_expired_fails_root(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=True)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")])
    await svc.create_subtasks(actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req)
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id).with_for_update())
        await apply_plan_decision_tx(
            session, workspace_id=ws.id, task=task, decision="expired", now=datetime.now(UTC)
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "failed"
    assert root.failure_reason == "approval_expired"


async def test_execution_terminal_maps_and_aggregates(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=False)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(
        subtasks=[SubtaskInput(title="only", assignee={"member_id": str(coder.id)}, stage=1)]
    )
    result = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
    )
    sub_id = uuid.UUID(result["created_subtasks"][0]["id"])
    sub = await _load_task(session_factory, sub_id)
    assert sub.status == "in_progress"

    # Create the execution row that the dispatch enqueue would have produced.
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=ws.id,
                agent_id=coder.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status="running",
                task_spec={"squad_task_id": str(sub_id), "squad_role": "executor"},
            )
        )

    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session, workspace_id=ws.id, execution_id=exec_id, status="completed", failure_reason=None,
            now=datetime.now(UTC),
        )
    sub2 = await _load_task(session_factory, sub_id)
    assert sub2.status == "done"
    # §S8 / B10: with an AGENT leader the root parks in ``aggregating`` and
    # the leader is woken for a summary run (squad_role='aggregator').
    root = await _load_task(session_factory, root_id)
    assert root.status == "aggregating"
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        assigned_events = list(
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == ws.id,
                        OutboxEvent.event_type == "issue.assigned",
                    )
                )
            ).scalars()
        )
    agg_events = [e for e in assigned_events if e.payload.get("squad_role") == "aggregator"]
    assert len(agg_events) == 1
    assert agg_events[0].payload.get("squad_task_id") == str(root_id)

    # Complete the aggregator execution → root settles done with the
    # concatenated child summaries and the assignment completes.
    agg_exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=agg_exec_id,
                workspace_id=ws.id,
                agent_id=leader.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status="running",
                task_spec={"squad_task_id": str(root_id), "squad_role": "aggregator"},
            )
        )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session, workspace_id=ws.id, execution_id=agg_exec_id, status="completed",
            failure_reason=None, now=datetime.now(UTC),
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "done"
    assert root.result_summary == "completed"
    assert await _active_assignment(session_factory, issue.id) is None


async def test_cancel_task_cascades(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    actor = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    result = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="a"), SubtaskInput(title="b")]),
    )
    await svc.cancel_task(actor=actor, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, reason="stop")
    root = await _load_task(session_factory, root_id)
    assert root.status == "cancelled"
    for s in result["created_subtasks"]:
        assert (await _load_task(session_factory, uuid.UUID(s["id"]))).status == "cancelled"


def test_assert_transition_illegal_raises():
    with pytest.raises(ConflictError):
        squad_tasks.assert_transition("done", "in_progress", is_root=True)
    with pytest.raises(ConflictError):
        squad_tasks.assert_transition("pending", "done", is_root=False)
    # legal edges do not raise
    squad_tasks.assert_transition("pending", "decomposing", is_root=True)
    squad_tasks.assert_transition("in_progress", "done", is_root=False)


# -- B3: orchestrator authorization (membership alone is insufficient) --------


async def test_orchestrator_authz_membership_insufficient(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch")
    member = await make_human_member(session_factory, ws, role="member")
    admin = await make_human_member(session_factory, ws, role="admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    # Prove squad membership ALONE does not grant orchestration rights.
    await add_member(session_factory, ws, squad, member, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")])
    with pytest.raises(ForbiddenError):
        await svc.create_subtasks(
            actor=member, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
        )
    with pytest.raises(ForbiddenError):
        await svc.dispatch_task(actor=member, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    # A workspace admin is allowed and gets a rendered result dict.
    out = await svc.dispatch_task(actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    assert isinstance(out, dict)
    assert out["task_id"] == str(root_id)


# -- B5: state-machine conflict guards + legal manual move --------------------


async def test_state_machine_conflicts_and_legal_move(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch")
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    admin = await make_human_member(session_factory, ws, role="admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    res = await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="s1", assignee={"member_id": str(coder.id)}, stage=1),
                SubtaskInput(title="s2", stage=2),
            ]
        ),
    )
    s1_id = uuid.UUID([s for s in res["created_subtasks"] if s["title"] == "s1"][0]["id"])
    s2_id = uuid.UUID([s for s in res["created_subtasks"] if s["title"] == "s2"][0]["id"])
    # s1 (stage 1) dispatched → in_progress; s2 (stage 2) gated → pending.
    assert (await _load_task(session_factory, s1_id)).status == "in_progress"
    assert (await _load_task(session_factory, s2_id)).status == "pending"

    # (c) illegal edge: pending -> aggregating is not allowed for a pending task.
    with pytest.raises(ConflictError):
        await svc.move_task_status(
            actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=s2_id, status="aggregating"
        )

    # (d) legal edge: in_progress -> done sets finished_at and renders done.
    moved = await svc.move_task_status(
        actor=admin,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=s1_id,
        status="done",
        result_summary=None,
    )
    assert moved["status"] == "done"
    s1 = await _load_task(session_factory, s1_id)
    assert s1.status == "done"
    assert s1.finished_at is not None

    # Force the root terminal to exercise the conflict guards.
    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        root = await session.scalar(
            select(SquadTask).where(SquadTask.id == root_id).with_for_update()
        )
        root.status = "done"

    # (a) dispatching a terminal tree → conflict.
    with pytest.raises(ConflictError):
        await svc.dispatch_task(actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    # (b) cancelling an already-done task → conflict.
    with pytest.raises(ConflictError):
        await svc.cancel_task(
            actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, reason="late"
        )


# -- B7: manual status move requires membership / admin -----------------------


async def test_move_task_status_non_member_forbidden(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    admin = await make_human_member(session_factory, ws, role="admin")
    outsider = await make_human_member(session_factory, ws, role="member")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    # A workspace member who is neither a squad member nor an admin → 403.
    with pytest.raises(ForbiddenError):
        await svc.move_task_status(
            actor=outsider, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, status="in_progress"
        )


# -- B15c/d: leader evaluation closed loop (no subtasks) ----------------------


async def test_leader_evaluated_no_action(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    admin = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=ws.id,
                agent_id=leader.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status="running",
                task_spec={"squad_task_id": str(root_id), "squad_role": "orchestrator"},
            )
        )
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session,
            workspace_id=ws.id,
            execution_id=exec_id,
            status="completed",
            failure_reason=None,
            now=datetime.now(UTC),
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "done"
    from mesh.db.models.squad import SquadActivity

    async with session_factory() as session:
        evaluated = list(
            (
                await session.execute(
                    select(SquadActivity).where(
                        SquadActivity.workspace_id == ws.id,
                        SquadActivity.action == "leader_evaluated",
                    )
                )
            ).scalars()
        )
    assert len(evaluated) == 1
    assert evaluated[0].payload["result"] == "no_action"


async def test_leader_evaluated_failed(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    admin = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=ws.id,
                agent_id=leader.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status="running",
                task_spec={"squad_task_id": str(root_id), "squad_role": "orchestrator"},
            )
        )
    from datetime import UTC, datetime

    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session,
            workspace_id=ws.id,
            execution_id=exec_id,
            status="failed",
            failure_reason=None,
            now=datetime.now(UTC),
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "failed"
    from mesh.db.models.squad import SquadActivity

    async with session_factory() as session:
        evaluated = list(
            (
                await session.execute(
                    select(SquadActivity).where(
                        SquadActivity.workspace_id == ws.id,
                        SquadActivity.action == "leader_evaluated",
                    )
                )
            ).scalars()
        )
    assert len(evaluated) == 1
    assert evaluated[0].payload["result"] == "failed"
