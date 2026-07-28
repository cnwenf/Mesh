"""Edge-branch tests: duplicate membership, leader-departure-with-replacement,
dependency-not-satisfied dispatch, malformed dependency refs, get_status."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.squad import SquadTask
from mesh.errors import BusinessRuleError, ConflictError
from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput
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

    return uuid.UUID(
        (
            await svc.assign_issue_to_squad(
                actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body()
            )
        )["id"]
    )


async def test_add_duplicate_member_conflict(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    svc, _ = build_services(session_factory)
    from mesh.squad.schemas import AddMembersRequest, SquadMemberInput

    with pytest.raises(ConflictError):
        await svc.add_members(
            actor=leader, workspace_id=ws.id, squad_id=squad.id,
            body=AddMembersRequest(members=[SquadMemberInput(member_id=str(coder.id), role="member")]),
        )


async def test_leader_departure_with_replacement_rotates(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader1 = await make_agent_member(session_factory, ws, name="l1")
    _, leader2 = await make_agent_member(session_factory, ws, name="l2")
    squad = await make_squad(session_factory, ws, leader_member=leader1)
    await add_member(session_factory, ws, squad, leader2, role="leader")
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, tsvc = build_services(session_factory)
    await _assign(tsvc, ws, squad, issue, actor)
    # Removing leader1 leaves leader2 → primary leader rotates, root NOT blocked.
    await svc.remove_member(actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=leader1.id)
    async with session_factory() as session:
        from mesh.db.models.squad import Squad

        sq = await session.scalar(select(Squad).where(Squad.id == squad.id))
    assert sq.primary_leader_id == leader2.id


async def test_dispatch_skips_unsatisfied_dependencies(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    res = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[SubtaskInput(title="a", stage=1), SubtaskInput(title="b", stage=2, depends_on=["a"])]
        ),
    )
    b_id = uuid.UUID([s for s in res["created_subtasks"] if s["title"] == "b"][0]["id"])
    # b is pending (dep a not done). Dispatch again → b still not dispatched.
    result = await svc.dispatch_task(actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    assert result["task_id"] == str(root_id)
    async with session_factory() as session:
        b = await session.scalar(select(SquadTask).where(SquadTask.id == b_id))
    assert b.status == "pending"


async def test_malformed_dependency_ref_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    # A dangling depends_on reference is request validation (§3.3
    # validation_error), NOT a cycle.
    from mesh.errors import ValidationError

    with pytest.raises(ValidationError) as exc:
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
            body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="a", depends_on=["not-a-real-ref"])]),
        )
    assert exc.value.code == "validation_error"


async def test_self_dependency_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    with pytest.raises(BusinessRuleError):
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
            body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="a", depends_on=["a"])]),
        )


async def test_get_status_service(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    status = await svc.get_status(workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    assert status["task_id"] == str(root_id)
    assert status["status"] == "pending"


async def test_assign_missing_issue_404(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    actor = await make_human_member(session_factory, ws, role="admin")
    _, svc = build_services(session_factory)
    from mesh.errors import NotFoundError

    class Body:
        issue_id = str(uuid.uuid4())

    with pytest.raises(NotFoundError):
        await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())


async def test_create_subtasks_wrong_squad_404(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    other = await make_squad(session_factory, ws, leader_member=leader, name="other")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    from mesh.errors import NotFoundError

    with pytest.raises(NotFoundError):
        await svc.create_subtasks(
            actor=leader, workspace_id=ws.id, squad_id=other.id, task_id=root_id,
            body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")]),
        )


async def test_deps_satisfied_same_stage_blocks_dispatch(session_factory, workspace_factory):
    """Same-stage dependency: dispatch_ready must consult _deps_satisfied (the
    not-done count branch) rather than only the stage gate."""
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    res = await svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="a", stage=1),
                SubtaskInput(title="b", stage=1, depends_on=["a"]),
            ]
        ),
    )
    b_id = uuid.UUID([s for s in res["created_subtasks"] if s["title"] == "b"][0]["id"])
    # Both stage 1, but b depends on a (not done) → b stays pending.
    async with session_factory() as session:
        b = await session.scalar(select(SquadTask).where(SquadTask.id == b_id))
    assert b.status == "pending"
    # Dispatch again — still blocked by the unsatisfied dependency.
    await svc.dispatch_task(actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    async with session_factory() as session:
        b2 = await session.scalar(select(SquadTask).where(SquadTask.id == b_id))
    assert b2.status == "pending"


async def test_public_wrappers_and_wrong_squad_404(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    other = await make_squad(session_factory, ws, leader_member=leader, name="other2")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(svc, ws, squad, issue, leader)
    from mesh.errors import NotFoundError

    # get_task / get_tree / get_status against the WRONG squad → 404.
    with pytest.raises(NotFoundError):
        await svc.get_task(workspace_id=ws.id, squad_id=other.id, task_id=root_id)
    with pytest.raises(NotFoundError):
        await svc.get_tree(workspace_id=ws.id, squad_id=other.id, task_id=root_id)
    # Public observe wrapper (delegates to the _tx helper).
    await svc.observe_execution_finished(
        workspace_id=ws.id, execution_id=uuid.uuid4(), status="completed", failure_reason=None
    )


async def test_list_squads_pagination_cursor(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    for i in range(3):
        await make_squad(session_factory, ws, leader_member=leader, name=f"page-{i}")
    svc, _ = build_services(session_factory)
    page1 = await svc.list_squads(workspace_id=ws.id, limit=2)
    assert len(page1["data"]) == 2
    assert page1["next_cursor"] is not None
    page2 = await svc.list_squads(workspace_id=ws.id, limit=2, cursor=page1["next_cursor"])
    assert len(page2["data"]) == 1
    assert page2["next_cursor"] is None


async def test_change_role_success_promotes_member(session_factory, workspace_factory):
    """A real role change (with another leader present) exercises the success
    path: reconcile + activity + realtime broadcast."""
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    data = await svc.change_role(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=coder.id, role="leader"
    )
    # Now two leaders; the promoted member appears among leaders.
    assert any(ldr["member_id"] == str(coder.id) for ldr in data["leaders"])


async def test_list_squads_status_kind_filters(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    await make_squad(session_factory, ws, leader_member=leader, name="filt-active")
    adhoc = await make_squad(session_factory, ws, leader_member=leader, name="filt-adhoc")
    # Flip adhoc to archived + kind via update to exercise filters.
    from mesh.db.models.squad import Squad

    async with session_factory() as session, session.begin():
        row = await session.scalar(select(Squad).where(Squad.id == adhoc.id))
        row.status = "archived"
        row.kind = "adhoc"
    active = await svc.list_squads(workspace_id=ws.id, status="active")
    assert all(s["status"] == "active" for s in active["data"])
    adhoc_list = await svc.list_squads(workspace_id=ws.id, kind="adhoc")
    assert all(s["kind"] == "adhoc" for s in adhoc_list["data"])
    archived = await svc.list_squads(workspace_id=ws.id, status="archived")
    assert any(s["id"] == str(adhoc.id) for s in archived["data"])


async def test_relay_handlers_malformed_ids(session_factory, workspace_factory):
    from types import SimpleNamespace

    from mesh.squad.relay import (
        squad_execution_finished_handler,
        squad_plan_decided_handler,
    )

    ws = await workspace_factory()
    # Non-UUID task id / execution id → handlers return without raising.
    async with session_factory() as session, session.begin():
        await squad_plan_decided_handler(
            session,
            SimpleNamespace(
                workspace_id=ws.id,
                payload={"subject_task_id": "zzz", "decision": "approved"},
            ),
        )
    async with session_factory() as session, session.begin():
        await squad_execution_finished_handler(
            session,
            SimpleNamespace(
                workspace_id=ws.id,
                payload={"execution_id": "zzz", "status": "completed"},
            ),
        )
