"""Squad service unit tests — CRUD, membership guards, messages, activity.
Real PostgreSQL, no mocks."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.squad import SquadTask
from mesh.errors import BusinessRuleError, ConflictError, ForbiddenError
from mesh.squad.schemas import (
    AddMembersRequest,
    CreateSquadRequest,
    SendMessageRequest,
    SquadMemberInput,
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


async def test_create_squad_with_members_and_leader(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="lead")
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)

    body = CreateSquadRequest(
        name="Payments Squad",
        description="payment refactor",
        require_plan_approval=True,
        members=[
            SquadMemberInput(member_id=str(leader.id), role="leader"),
            SquadMemberInput(member_id=str(coder.id), role="member"),
        ],
    )
    data = await svc.create_squad(actor=actor, workspace_id=ws.id, body=body)
    assert data["name"] == "Payments Squad"
    assert data["primary_leader_id"] == str(leader.id)
    assert data["member_count"] == 2
    assert data["require_plan_approval"] is True
    assert any(ldr["member_id"] == str(leader.id) for ldr in data["leaders"])


async def test_create_squad_duplicate_name_conflict(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    body = CreateSquadRequest(
        name="Dup Squad", members=[SquadMemberInput(member_id=str(leader.id), role="leader")]
    )
    await svc.create_squad(actor=actor, workspace_id=ws.id, body=body)
    with pytest.raises(ConflictError) as exc:
        await svc.create_squad(actor=actor, workspace_id=ws.id, body=body)
    assert exc.value.code == "squad_name_taken"


async def test_update_squad_fields(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader, name="orig")
    data = await svc.update_squad(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=UpdateSquadRequest(name="renamed", max_decompose_depth=3),
    )
    assert data["name"] == "renamed"
    assert data["max_decompose_depth"] == 3


async def test_archive_blocked_by_running_task(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    # A running task exists.
    async with session_factory() as session, session.begin():
        session.add(
            SquadTask(
                workspace_id=ws.id, squad_id=squad.id, issue_id=issue.id, depth=0,
                title_snapshot="t", status="in_progress",
            )
        )
    with pytest.raises(ConflictError):
        await svc.archive_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id)


async def test_archive_and_restore(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    archived = await svc.archive_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id)
    assert archived["status"] == "archived"
    restored = await svc.restore_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id)
    assert restored["status"] == "active"


async def test_list_squads_filter(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    await make_squad(session_factory, ws, leader_member=leader, name="alpha")
    await make_squad(session_factory, ws, leader_member=leader, name="beta")
    result = await svc.list_squads(workspace_id=ws.id, q="alph")
    assert len(result["data"]) == 1
    assert result["data"][0]["name"] == "alpha"


async def test_change_role_last_leader_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    with pytest.raises(BusinessRuleError) as exc:
        await svc.change_role(
            actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=leader.id, role="member"
        )
    assert exc.value.code == "no_leader"


async def test_remove_member_with_active_task_rejected(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    async with session_factory() as session, session.begin():
        session.add(
            SquadTask(
                workspace_id=ws.id, squad_id=squad.id, issue_id=issue.id, depth=0,
                title_snapshot="t", status="in_progress", assignee_id=coder.id,
            )
        )
    with pytest.raises(BusinessRuleError) as exc:
        await svc.remove_member(actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=coder.id)
    assert exc.value.code == "member_has_active_task"


async def test_agent_cannot_modify_membership(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    # Actor is an agent → forbidden.
    with pytest.raises(ForbiddenError):
        await svc.remove_member(actor=leader, workspace_id=ws.id, squad_id=squad.id, member_id=coder.id)


async def test_add_members_batch(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    data = await svc.add_members(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=AddMembersRequest(members=[SquadMemberInput(member_id=str(coder.id), role="member")]),
    )
    assert data["member_count"] == 2


async def test_send_and_list_messages(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, actor, role="observer")
    msg = await svc.send_message(
        actor=actor, workspace_id=ws.id, squad_id=squad.id,
        body=SendMessageRequest(kind="chat", body_markdown="hello team"),
    )
    assert msg["body_markdown"] == "hello team"
    assert msg["sender"]["member_id"] == str(actor.id)
    listed = await svc.list_messages(workspace_id=ws.id, squad_id=squad.id)
    assert len(listed["data"]) == 1


async def test_send_message_non_member_forbidden(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    outsider = await make_human_member(session_factory, ws)
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    with pytest.raises(ForbiddenError):
        await svc.send_message(
            actor=outsider, workspace_id=ws.id, squad_id=squad.id,
            body=SendMessageRequest(kind="chat", body_markdown="hi"),
        )


async def test_instruction_to_agent_triggers_run(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="lead")
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    svc, _ = build_services(session_factory)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, coder, role="member")
    await svc.send_message(
        actor=leader, workspace_id=ws.id, squad_id=squad.id,
        body=SendMessageRequest(
            kind="instruction",
            recipient=SquadMemberInput(member_id=str(coder.id)),
            body_markdown="do the thing",
        ),
    )
    # An issue.assigned outbox event should have been written (leader→agent).
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.workspace_id == ws.id, OutboxEvent.event_type == "issue.assigned"
                )
            )
        ).scalars().all()
    assert len(events) == 1


async def test_list_activity_filter(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    body = CreateSquadRequest(
        name="Act Squad", members=[SquadMemberInput(member_id=str(leader.id), role="leader")]
    )
    await svc.create_squad(actor=actor, workspace_id=ws.id, body=body)
    listed = await svc.list_squads(workspace_id=ws.id, q="Act Squad")
    sid = listed["data"][0]["id"]
    import uuid as _uuid

    activity = await svc.list_activity(workspace_id=ws.id, squad_id=_uuid.UUID(sid), action="squad_created")
    assert len(activity["data"]) >= 1
    assert activity["data"][0]["action"] == "squad_created"


# -- B4: leader_lost blocks the root; a replacement leader unblocks it --------


async def test_leader_lost_blocks_then_new_leader_unblocks(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="leadA")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, tsvc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await tsvc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])

    # Removing the ONLY leader parks the active root (failure_reason=leader_lost).
    await svc.remove_member(actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=leader.id)
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert root.status == "blocked"
    assert root.failure_reason == "leader_lost"

    # A replacement agent leader unblocks the root and takes over the assignment.
    _, leader_b = await make_agent_member(session_factory, ws, name="leadB")
    await svc.add_members(
        actor=actor,
        workspace_id=ws.id,
        squad_id=squad.id,
        body=AddMembersRequest(members=[SquadMemberInput(member_id=str(leader_b.id), role="leader")]),
    )
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert root.status == "in_progress"
    assert root.failure_reason is None
    from mesh.db.models.issue import Issue
    from mesh.db.models.squad import IssueSquadAssignment

    async with session_factory() as session:
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.workspace_id == ws.id,
                IssueSquadAssignment.status == "active",
            )
        )
        refreshed_issue = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert assignment.leader_member_id == leader_b.id
    assert refreshed_issue.assignee_id == leader_b.id


# -- B11: change_role-driven leader rotation propagates to the assignment ------


async def test_change_role_rotates_primary_and_propagates(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader_a = await make_agent_member(session_factory, ws, name="lA")
    _, leader_b = await make_agent_member(session_factory, ws, name="lB")
    squad = await make_squad(session_factory, ws, leader_member=leader_a)
    await add_member(session_factory, ws, squad, leader_b, role="leader")
    issue = await seed_issue(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, tsvc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    await tsvc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())

    # Demote A → primary rotates to B AND the active assignment + issue assignee
    # repoint to B (the B11 propagation fix, same path as an explicit PATCH).
    out = await svc.change_role(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, member_id=leader_a.id, role="member"
    )
    assert out["primary_leader_id"] == str(leader_b.id)
    from mesh.db.models.issue import Issue
    from mesh.db.models.squad import IssueSquadAssignment

    async with session_factory() as session:
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.workspace_id == ws.id,
                IssueSquadAssignment.status == "active",
            )
        )
        refreshed_issue = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert assignment.leader_member_id == leader_b.id
    assert refreshed_issue.assignee_id == leader_b.id


# -- B12a: user-supplied LIKE wildcards are escaped (match literally) ---------


async def test_list_squads_wildcard_escaped(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    await make_squad(session_factory, ws, leader_member=leader, name="alpha")
    await make_squad(session_factory, ws, leader_member=leader, name="beta")
    svc, _ = build_services(session_factory)
    # A raw "%" would match everything if unescaped; escaped it matches nothing.
    wild = await svc.list_squads(workspace_id=ws.id, q="%")
    assert len(wild["data"]) == 0
    sub = await svc.list_squads(workspace_id=ws.id, q="alph")
    assert len(sub["data"]) == 1
    assert sub["data"][0]["name"] == "alpha"


# -- B12b: cursor pagination for messages and activity ------------------------


async def test_message_and_activity_cursor_pagination(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    sender = await make_human_member(session_factory, ws, role="member")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, sender, role="member")
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)

    for i in range(4):
        await svc.send_message(
            actor=sender,
            workspace_id=ws.id,
            squad_id=squad.id,
            body=SendMessageRequest(kind="chat", body_markdown=f"msg {i}"),
        )
    page1 = await svc.list_messages(workspace_id=ws.id, squad_id=squad.id, limit=2)
    assert len(page1["data"]) == 2
    assert page1["next_cursor"] is not None
    page2 = await svc.list_messages(
        workspace_id=ws.id, squad_id=squad.id, limit=2, cursor=page1["next_cursor"]
    )
    assert len(page2["data"]) == 2
    ids1 = {m["id"] for m in page1["data"]}
    ids2 = {m["id"] for m in page2["data"]}
    assert ids1.isdisjoint(ids2)

    # Activity accumulates from service writes (squad_created + 3 updates = 4).
    s2 = await svc.create_squad(
        actor=actor,
        workspace_id=ws.id,
        body=CreateSquadRequest(
            name="ActPage", members=[SquadMemberInput(member_id=str(leader.id), role="leader")]
        ),
    )
    s2_id = uuid.UUID(s2["id"])
    for new_name in ("ActPage2", "ActPage3", "ActPage4"):
        await svc.update_squad(
            actor=actor, workspace_id=ws.id, squad_id=s2_id, body=UpdateSquadRequest(name=new_name)
        )
    apage1 = await svc.list_activity(workspace_id=ws.id, squad_id=s2_id, limit=2)
    assert len(apage1["data"]) == 2
    assert apage1["next_cursor"] is not None
    apage2 = await svc.list_activity(
        workspace_id=ws.id, squad_id=s2_id, limit=2, cursor=apage1["next_cursor"]
    )
    assert len(apage2["data"]) == 2
    aids1 = {a["id"] for a in apage1["data"]}
    aids2 = {a["id"] for a in apage2["data"]}
    assert aids1.isdisjoint(aids2)


# -- B15a/b: instructions round-trip and member_preview cap --------------------


async def test_squad_instructions_create_and_update(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    actor = await make_human_member(session_factory, ws, role="admin")
    svc, _ = build_services(session_factory)
    data = await svc.create_squad(
        actor=actor,
        workspace_id=ws.id,
        body=CreateSquadRequest(
            name="InstrSquad",
            instructions="standing orders",
            members=[SquadMemberInput(member_id=str(leader.id), role="leader")],
        ),
    )
    assert data["instructions"] == "standing orders"
    upd = await svc.update_squad(
        actor=actor,
        workspace_id=ws.id,
        squad_id=uuid.UUID(data["id"]),
        body=UpdateSquadRequest(instructions="new orders"),
    )
    assert upd["instructions"] == "new orders"


async def test_member_preview_capped_at_eight(session_factory, workspace_factory):
    ws = await workspace_factory()
    actor = await make_human_member(session_factory, ws, role="admin")
    _, leader = await make_agent_member(session_factory, ws, name="caplead")
    members = [SquadMemberInput(member_id=str(leader.id), role="leader")]
    for i in range(9):
        extra = await make_human_member(session_factory, ws, role="member", name=f"m{i}")
        members.append(SquadMemberInput(member_id=str(extra.id), role="member"))
    svc, _ = build_services(session_factory)
    data = await svc.create_squad(
        actor=actor, workspace_id=ws.id, body=CreateSquadRequest(name="CapSquad", members=members)
    )
    assert data["member_count"] == 10
    assert len(data["member_preview"]) == 8
    entry = data["member_preview"][0]
    assert {"member_id", "member_type", "name", "role"} <= set(entry)
