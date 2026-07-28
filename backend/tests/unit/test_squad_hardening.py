"""Squad acceptance-rework hardening tests (MES-65 B3–B15).

Covers the behaviors the acceptance review required: orchestrator
authorization (B3), leader-readd unblock (B4), state-machine guard (B5),
summary writeback relay path (B10), leader-change propagation (B11),
wildcard escaping + cursor pagination (B12), optional approval body (B13),
instructions / member_preview / leader evaluation loop (B15). Real PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from mesh.db.models.comment import Comment
from mesh.db.models.issue import Issue
from mesh.db.models.runtime import TaskExecution
from mesh.db.models.squad import IssueSquadAssignment, SquadActivity, SquadTask
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, ForbiddenError
from mesh.squad.relay import make_squad_execution_finished_handler
from mesh.squad.schemas import (
    AddMembersRequest,
    CreateSubtasksRequest,
    SquadMemberInput,
    SubtaskInput,
)
from mesh.squad.tasks import observe_execution_finished_tx
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


async def _assign(session_factory, svc, ws, squad, issue, actor):
    result = await svc.assign_issue_to_squad(
        actor=actor, workspace_id=ws.id, squad_id=squad.id, body=_Body(issue.id)
    )
    return uuid.UUID(result["id"])


async def _load_task(session_factory, task_id):
    async with session_factory() as session:
        return await session.scalar(select(SquadTask).where(SquadTask.id == task_id))


async def _set_task_fields(session_factory, task_id, **fields):
    async with session_factory() as session, session.begin():
        task = await session.scalar(select(SquadTask).where(SquadTask.id == task_id))
        for key, value in fields.items():
            setattr(task, key, value)


async def _make_execution(session_factory, ws, *, member, issue, task_id, role, status="running"):
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=ws.id,
                agent_id=member.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status=status,
                task_spec={"squad_task_id": str(task_id), "squad_role": role},
            )
        )
    return exec_id


# -- B3: orchestrator authorization -------------------------------------------


async def test_subtasks_requires_orchestrator_or_admin(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="orch-leader")
    plain = await make_human_member(session_factory, ws, role="member", name="Plain")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, plain, role="member")
    issue = await seed_issue(session_factory, ws)
    _, task_svc = build_services(session_factory)
    root_id = await _assign(session_factory, task_svc, ws, squad, issue, admin)
    req = CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")])

    # A squad member who is NOT the orchestrator → 403 (RBAC alone is not enough).
    with pytest.raises(ForbiddenError):
        await task_svc.create_subtasks(
            actor=plain, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
        )
    # The orchestrator passes.
    ok = await task_svc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, body=req
    )
    assert ok["created_subtasks"]
    # Admin passes too — on a fresh root (the first is already dispatching).
    issue2 = await seed_issue(session_factory, ws, title="Second")
    root2 = await _assign(session_factory, task_svc, ws, squad, issue2, admin)
    ok2 = await task_svc.create_subtasks(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root2, body=req
    )
    assert ok2["created_subtasks"]


async def test_dispatch_requires_orchestrator_or_admin(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="disp-leader")
    plain = await make_human_member(session_factory, ws, role="member", name="Plain")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, plain, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)

    with pytest.raises(ForbiddenError):
        await svc.dispatch_task(actor=plain, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    ok = await svc.dispatch_task(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id
    )
    assert ok["task_id"] == str(root_id)


# -- B4: leader re-add unblocks the root ---------------------------------------


async def test_leader_readd_unblocks_root_and_propagates(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="first-leader")
    _, replacement = await make_agent_member(session_factory, ws, name="second-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, task_svc = build_services(session_factory)
    root_id = await _assign(session_factory, task_svc, ws, squad, issue, admin)

    # Remove the only leader → root blocked(leader_lost).
    await svc.remove_member(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, member_id=leader.id
    )
    root = await _load_task(session_factory, root_id)
    assert root.status == "blocked"
    assert root.failure_reason == "leader_lost"

    # Add a new leader → same-txn unblock + propagation.
    await svc.add_members(
        actor=admin,
        workspace_id=ws.id,
        squad_id=squad.id,
        body=AddMembersRequest(
            members=[SquadMemberInput(member_id=str(replacement.id), role="leader")]
        ),
    )
    root = await _load_task(session_factory, root_id)
    assert root.status == "in_progress"
    assert root.failure_reason is None
    async with session_factory() as session:
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.issue_id == issue.id,
                IssueSquadAssignment.status == "active",
            )
        )
        refreshed_issue = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert assignment.leader_member_id == replacement.id
    assert refreshed_issue.assignee_id == replacement.id


# -- B5: state-machine guard ----------------------------------------------------


async def test_dispatch_and_cancel_on_terminal_task_conflict(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="term-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)
    await _set_task_fields(session_factory, root_id, status="done")

    with pytest.raises(ConflictError):
        await svc.dispatch_task(actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id)
    with pytest.raises(ConflictError):
        await svc.cancel_task(
            actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id, reason=None
        )


async def test_move_task_status_guard_and_side_effects(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="move-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    outsider = await make_human_member(session_factory, ws, role="member", name="Outsider")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)

    # Illegal edge (pending → aggregating) → 409.
    with pytest.raises(ConflictError):
        await svc.move_task_status(
            actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
            status="aggregating",
        )
    # Non-member → 403.
    with pytest.raises(ForbiddenError):
        await svc.move_task_status(
            actor=outsider, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
            status="cancelled",
        )
    # Legal manual move with summary.
    rendered = await svc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        status="cancelled", result_summary=None,
    )
    assert rendered["status"] == "cancelled"
    root = await _load_task(session_factory, root_id)
    assert root.finished_at is not None


async def test_manual_done_triggers_aggregation_for_human_led(session_factory, workspace_factory):
    """Human leader → aggregation resolves synchronously: two manual dones
    settle the root done (leaf machine + aggregate-up path)."""
    ws = await workspace_factory()
    leader = await make_human_member(session_factory, ws, role="member", name="HumanLead")
    worker = await make_human_member(session_factory, ws, role="member", name="Worker")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    await add_member(session_factory, ws, squad, worker, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)
    # Leader (orchestrator) decomposes into two human subtasks.
    result = await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="t1", assignee=SquadMemberInput(member_id=str(worker.id))),
                SubtaskInput(title="t2", assignee=SquadMemberInput(member_id=str(worker.id))),
            ]
        ),
    )
    sub_ids = [uuid.UUID(c["id"]) for c in result["created_subtasks"]]
    for sub_id in sub_ids:
        # Children were auto-dispatched (in_progress); drag them to done.
        await svc.move_task_status(
            actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=sub_id,
            status="done", result_summary="ok",
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "done"


# -- B10: summary writeback (relay side) ----------------------------------------


async def _done_root_with_assignment(session_factory, svc, ws, squad, issue, leader, admin):
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)
    await _set_task_fields(
        session_factory, root_id, status="done", result_summary="final aggregate summary"
    )
    return root_id


async def test_writeback_posts_leader_comment_on_root_done(session_factory, workspace_factory):
    from mesh.comment_inbox.service import CommentService

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="wb-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _done_root_with_assignment(
        session_factory, svc, ws, squad, issue, leader, admin
    )
    exec_id = await _make_execution(
        session_factory, ws, member=leader, issue=issue, task_id=root_id,
        role="executor", status="completed",
    )
    handler = make_squad_execution_finished_handler(
        CommentService(session_factory, signing_secret="test-secret")
    )
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(exec_id), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)

    async with session_factory() as session:
        comments = (
            (
                await session.execute(select(Comment).where(Comment.issue_id == issue.id))
            ).scalars().all()
        )
    assert len(comments) == 1
    assert "final aggregate summary" in comments[0].body_markdown

    # Replay: idempotency key dedups — still exactly one comment.
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)
    async with session_factory() as session:
        count = len(
            (await session.execute(select(Comment).where(Comment.issue_id == issue.id))).scalars().all()
        )
    assert count == 1


async def test_writeback_skipped_when_no_assignment(session_factory, workspace_factory):
    from mesh.comment_inbox.service import CommentService

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="wb-noasg")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    # A done task that never belonged to an assignment.
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            SquadTask(
                id=task_id,
                workspace_id=ws.id,
                squad_id=squad.id,
                issue_id=issue.id,
                depth=0,
                title_snapshot="orphan",
                status="done",
                result_summary="orphan summary",
                orchestrator_id=leader.id,
                root_task_id=task_id,
                created_at=now,
                updated_at=now,
            )
        )
    exec_id = await _make_execution(
        session_factory, ws, member=leader, issue=issue, task_id=task_id,
        role="executor", status="completed",
    )
    handler = make_squad_execution_finished_handler(
        CommentService(session_factory, signing_secret="test-secret")
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
    assert count == 0


# -- B11: leader change propagation via change_role -----------------------------


async def test_demote_primary_leader_propagates_via_reconcile(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader_a = await make_agent_member(session_factory, ws, name="lead-a")
    _, leader_b = await make_agent_member(session_factory, ws, name="lead-b")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader_a)
    await add_member(session_factory, ws, squad, leader_b, role="leader")
    issue = await seed_issue(session_factory, ws)
    svc, task_svc = build_services(session_factory)
    await _assign(session_factory, task_svc, ws, squad, issue, admin)

    # Demote the PRIMARY leader; reconcile must rotate AND propagate.
    await svc.change_role(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, member_id=leader_a.id, role="member"
    )
    async with session_factory() as session:
        refreshed = await session.scalar(select(type(squad)).where(type(squad).id == squad.id))
        assignment = await session.scalar(
            select(IssueSquadAssignment).where(
                IssueSquadAssignment.issue_id == issue.id,
                IssueSquadAssignment.status == "active",
            )
        )
        refreshed_issue = await session.scalar(select(Issue).where(Issue.id == issue.id))
    assert refreshed.primary_leader_id == leader_b.id
    assert assignment.leader_member_id == leader_b.id
    assert refreshed_issue.assignee_id == leader_b.id


# -- B12: wildcard escape + cursor pagination -----------------------------------


async def test_list_squads_escapes_wildcards(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="esc-leader")
    await make_squad(session_factory, ws, leader_member=leader, name="alpha")
    await make_squad(session_factory, ws, leader_member=leader, name="beta")
    svc, _ = build_services(session_factory)

    escaped = await svc.list_squads(workspace_id=ws.id, q="%")
    assert escaped["data"] == []
    literal = await svc.list_squads(workspace_id=ws.id, q="alph")
    assert [s["name"] for s in literal["data"]] == ["alpha"]


async def test_messages_and_activity_cursor_pagination(session_factory, workspace_factory):
    from mesh.squad.schemas import CreateSquadRequest, SendMessageRequest

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="page-leader")
    actor = await make_human_member(session_factory, ws, role="admin", name="Admin")
    svc, _ = build_services(session_factory)
    # Service-mediated create → squad_created + member_added activity rows.
    data = await svc.create_squad(
        actor=actor,
        workspace_id=ws.id,
        body=CreateSquadRequest(
            name="Page Squad",
            members=[SquadMemberInput(member_id=str(leader.id), role="leader")],
        ),
    )
    squad_id = uuid.UUID(data["id"])

    for i in range(5):
        await svc.send_message(
            actor=leader,
            workspace_id=ws.id,
            squad_id=squad_id,
            body=SendMessageRequest(body_markdown=f"msg-{i}"),
        )
    page1 = await svc.list_messages(workspace_id=ws.id, squad_id=squad_id, limit=2)
    assert len(page1["data"]) == 2
    assert page1["next_cursor"] is not None
    page2 = await svc.list_messages(
        workspace_id=ws.id, squad_id=squad_id, limit=2, cursor=page1["next_cursor"]
    )
    assert len(page2["data"]) == 2
    ids1 = {m["id"] for m in page1["data"]}
    ids2 = {m["id"] for m in page2["data"]}
    assert ids1.isdisjoint(ids2)
    page3 = await svc.list_messages(
        workspace_id=ws.id, squad_id=squad_id, limit=2, cursor=page2["next_cursor"]
    )
    assert len(page3["data"]) == 1
    assert page3["next_cursor"] is None

    # Activity: squad_created + member_added + message realtime rows page stably.
    act1 = await svc.list_activity(workspace_id=ws.id, squad_id=squad_id, limit=1)
    assert len(act1["data"]) == 1
    assert act1["next_cursor"] is not None
    act2 = await svc.list_activity(
        workspace_id=ws.id, squad_id=squad_id, limit=1, cursor=act1["next_cursor"]
    )
    assert len(act2["data"]) == 1
    assert act1["data"][0]["id"] != act2["data"][0]["id"]


# -- B15: instructions / member_preview / leader evaluation ---------------------


async def test_instructions_roundtrip_and_member_preview(session_factory, workspace_factory):
    from mesh.squad.schemas import CreateSquadRequest, UpdateSquadRequest

    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="instr-leader")
    worker = await make_human_member(session_factory, ws, role="member", name="Worker")
    actor = await make_human_member(session_factory, ws, role="admin", name="Admin")
    svc, _ = build_services(session_factory)

    data = await svc.create_squad(
        actor=actor,
        workspace_id=ws.id,
        body=CreateSquadRequest(
            name="Instr Squad",
            instructions="always attach regression evidence",
            members=[
                SquadMemberInput(member_id=str(leader.id), role="leader"),
                SquadMemberInput(member_id=str(worker.id), role="member"),
            ],
        ),
    )
    assert data["instructions"] == "always attach regression evidence"
    # member_preview: both members with roles, snapshot shape.
    preview = data["member_preview"]
    assert {p["role"] for p in preview} == {"leader", "member"}
    assert all({"member_id", "member_type", "name", "role"} <= set(p) for p in preview)

    updated = await svc.update_squad(
        actor=actor,
        workspace_id=ws.id,
        squad_id=uuid.UUID(data["id"]),
        body=UpdateSquadRequest(instructions="revised standing orders"),
    )
    assert updated["instructions"] == "revised standing orders"


async def test_leader_evaluation_no_action_closes_root(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="eval-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)
    exec_id = await _make_execution(
        session_factory, ws, member=leader, issue=issue, task_id=root_id,
        role="orchestrator",
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session, workspace_id=ws.id, execution_id=exec_id, status="completed",
            failure_reason=None, now=datetime.now(UTC),
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "done"
    assert root.result_summary
    async with session_factory() as session:
        activity = (
            (
                await session.execute(
                    select(SquadActivity).where(
                        SquadActivity.squad_id == squad.id,
                        SquadActivity.action == "leader_evaluated",
                    )
                )
            ).scalars().all()
        )
    assert len(activity) == 1
    assert activity[0].payload["result"] == "no_action"


async def test_leader_evaluation_failed_recorded(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="eval-fail")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _assign(session_factory, svc, ws, squad, issue, admin)
    exec_id = await _make_execution(
        session_factory, ws, member=leader, issue=issue, task_id=root_id,
        role="orchestrator",
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await observe_execution_finished_tx(
            session, workspace_id=ws.id, execution_id=exec_id, status="failed",
            failure_reason="boom", now=datetime.now(UTC),
        )
    root = await _load_task(session_factory, root_id)
    assert root.status == "failed"
    async with session_factory() as session:
        activity = (
            (
                await session.execute(
                    select(SquadActivity).where(
                        SquadActivity.squad_id == squad.id,
                        SquadActivity.action == "leader_evaluated",
                    )
                )
            ).scalars().all()
        )
    assert len(activity) == 1
    assert activity[0].payload["result"] == "failed"


async def test_get_issue_assignment_service(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws, name="asg-leader")
    admin = await make_human_member(session_factory, ws, role="admin", name="Admin")
    squad = await make_squad(session_factory, ws, leader_member=leader, name="Asg Squad")
    issue = await seed_issue(session_factory, ws)
    svc, _ = build_services(session_factory)

    assert await svc.get_issue_assignment(workspace_id=ws.id, issue_id=issue.id) is None
    _, task_svc = build_services(session_factory)
    await _assign(session_factory, task_svc, ws, squad, issue, admin)
    data = await svc.get_issue_assignment(workspace_id=ws.id, issue_id=issue.id)
    assert data is not None
    assert data["squad_id"] == str(squad.id)
    assert data["squad_name"] == "Asg Squad"
    assert data["leader"]["member_id"] == str(leader.id)


async def test_human_leader_move_completion_writes_back_summary(session_factory, workspace_factory):
    """C1 / §S8 / §4.3-7: the synchronous (human-leader) aggregation path must
    write the leader's summary back to the parent issue as a comment — same
    body + idempotency key as the relay (agent) path."""
    from mesh.db.models.comment import Comment

    ws = await workspace_factory()
    leader = await make_human_member(session_factory, ws, role="member", name="HumanLead")
    admin = await make_human_member(session_factory, ws, role="admin", name="A")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    svc, tsvc = build_services(session_factory)
    root_id = await _assign(session_factory, tsvc, ws, squad, issue, admin)
    result = await tsvc.create_subtasks(
        actor=leader, workspace_id=ws.id, squad_id=squad.id, task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[
            SubtaskInput(title="solo", assignee={"member_id": str(leader.id)}, stage=1),
        ]),
    )
    sub_id = uuid.UUID(result["created_subtasks"][0]["id"])
    # The human-assigned child was auto-dispatched to in_progress at
    # decomposition; the manual move takes it to done.
    await tsvc.move_task_status(
        actor=admin, workspace_id=ws.id, squad_id=squad.id, task_id=sub_id, status="done",
        result_summary="human child result",
    )
    async with session_factory() as session:
        root = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
        comments = (
            (await session.execute(select(Comment).where(Comment.issue_id == issue.id))).scalars().all()
        )
    assert root.status == "done"
    assert root.result_summary == "human child result"
    assert len(comments) == 1  # exactly one — shared idempotency key
    assert "human child result" in comments[0].body_markdown
    assert comments[0].author_kind == "member"
