"""Unit tests for the squad relay handlers (outbox consumers) and the WS
channel authorizer. These run the handlers directly against real PostgreSQL
(the e2e worker exercises them in-process, but that isn't measured by unit
coverage)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from mesh.db.models.runtime import TaskExecution
from mesh.db.models.squad import SquadTask
from mesh.realtime.auth import Principal
from mesh.squad.channels import make_squad_channel_checker
from mesh.squad.relay import (
    squad_execution_finished_handler,
    squad_plan_decided_handler,
)
from tests.unit.squad_support import (
    build_services,
    make_agent_member,
    make_human_member,
    make_squad,
    seed_issue,
)

pytestmark = pytest.mark.unit


async def _root_in_awaiting(session_factory, ws, svc, squad, issue, leader):
    class Body:
        issue_id = str(issue.id)

    actor = leader
    r = await svc.assign_issue_to_squad(actor=actor, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(subtasks=[SubtaskInput(title="x")]),
    )
    return root_id


async def test_plan_decided_handler_approved(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=True)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _root_in_awaiting(session_factory, ws, svc, squad, issue, leader)

    event = SimpleNamespace(
        workspace_id=ws.id,
        payload={"subject_task_id": str(root_id), "decision": "approved"},
    )
    async with session_factory() as session, session.begin():
        await squad_plan_decided_handler(session, event)
    async with session_factory() as session:
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert task.status in ("dispatching", "in_progress")


async def test_plan_decided_handler_expired(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader, require_plan_approval=True)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    root_id = await _root_in_awaiting(session_factory, ws, svc, squad, issue, leader)
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"subject_task_id": str(root_id), "decision": "expired"}
    )
    async with session_factory() as session, session.begin():
        await squad_plan_decided_handler(session, event)
    async with session_factory() as session:
        task = await session.scalar(select(SquadTask).where(SquadTask.id == root_id))
    assert task.status == "failed"
    assert task.failure_reason == "approval_expired"


async def test_plan_decided_handler_ignores_bad_payload(session_factory, workspace_factory):
    ws = await workspace_factory()
    # Missing task id / unknown decision → handler returns without error.
    for payload in ({"decision": "approved"}, {"subject_task_id": str(uuid.uuid4()), "decision": "weird"}):
        event = SimpleNamespace(workspace_id=ws.id, payload=payload)
        async with session_factory() as session, session.begin():
            await squad_plan_decided_handler(session, event)


async def test_execution_finished_handler_maps_terminal(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    _, coder = await make_agent_member(session_factory, ws, name="coder")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    from tests.unit.squad_support import add_member

    await add_member(session_factory, ws, squad, coder, role="member")
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=leader, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])
    from mesh.squad.schemas import CreateSubtasksRequest, SubtaskInput

    res = await svc.create_subtasks(
        actor=leader,
        workspace_id=ws.id,
        squad_id=squad.id,
        task_id=root_id,
        body=CreateSubtasksRequest(
            subtasks=[
                SubtaskInput(title="a", assignee={"member_id": str(coder.id)}, stage=1)
            ]
        ),
    )
    sub_id = uuid.UUID(res["created_subtasks"][0]["id"])
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
    event = SimpleNamespace(
        workspace_id=ws.id,
        payload={"execution_id": str(exec_id), "status": "failed", "failure_reason": "boom"},
    )
    async with session_factory() as session, session.begin():
        await squad_execution_finished_handler(session, event)
    async with session_factory() as session:
        sub = await session.scalar(select(SquadTask).where(SquadTask.id == sub_id))
    assert sub.status == "failed"
    assert sub.failure_reason == "boom"


async def test_execution_finished_handler_ignores_non_squad(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, coder = await make_agent_member(session_factory, ws)
    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id, workspace_id=ws.id, agent_id=coder.agent_id, trigger="assign",
                status="running", task_spec={"kind": "issue_assignment"},
            )
        )
    event = SimpleNamespace(workspace_id=ws.id, payload={"execution_id": str(exec_id), "status": "completed"})
    async with session_factory() as session, session.begin():
        await squad_execution_finished_handler(session, event)  # no-op, no error


async def test_execution_finished_handler_missing_execution(session_factory, workspace_factory):
    ws = await workspace_factory()
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(uuid.uuid4()), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await squad_execution_finished_handler(session, event)  # no-op


async def test_execution_finished_summary_writeback_idempotent(session_factory, workspace_factory):
    """B10: a squad-assigned root finishing ``done`` with a leader summary is
    written back to the parent issue as exactly one comment, and relay replays
    never duplicate it (idempotency-key dedup)."""
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    squad = await make_squad(session_factory, ws, leader_member=leader)
    issue = await seed_issue(session_factory, ws)
    _, svc = build_services(session_factory)
    admin = await make_human_member(session_factory, ws, role="admin")

    class Body:
        issue_id = str(issue.id)

    r = await svc.assign_issue_to_squad(actor=admin, workspace_id=ws.id, squad_id=squad.id, body=Body())
    root_id = uuid.UUID(r["id"])

    # Park the root done with a leader summary, as aggregation would settle it.
    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        root = await session.scalar(
            select(SquadTask).where(SquadTask.id == root_id).with_for_update()
        )
        root.status = "done"
        root.result_summary = "final summary"

    exec_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            TaskExecution(
                id=exec_id,
                workspace_id=ws.id,
                agent_id=leader.agent_id,
                issue_id=issue.id,
                trigger="assign",
                status="completed",
                task_spec={"squad_task_id": str(root_id), "squad_role": "executor"},
            )
        )

    from mesh.comment_inbox.service import CommentService
    from mesh.squad.relay import make_squad_execution_finished_handler

    handler = make_squad_execution_finished_handler(
        CommentService(session_factory, signing_secret="test-secret")
    )
    event = SimpleNamespace(
        workspace_id=ws.id, payload={"execution_id": str(exec_id), "status": "completed"}
    )
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)

    from mesh.db.models.comment import Comment

    async with session_factory() as session:
        comments = list(
            (
                await session.execute(
                    select(Comment).where(
                        Comment.workspace_id == ws.id, Comment.issue_id == issue.id
                    )
                )
            ).scalars()
        )
    assert len(comments) == 1
    assert "final summary" in comments[0].body_markdown

    # Relay replay of the same terminal event → still exactly one comment.
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, ws.id)
        await handler(session, event)
    async with session_factory() as session:
        replayed = list(
            (
                await session.execute(
                    select(Comment).where(
                        Comment.workspace_id == ws.id, Comment.issue_id == issue.id
                    )
                )
            ).scalars()
        )
    assert len(replayed) == 1


async def test_channel_checker_allows_member_and_admin(session_factory, workspace_factory):
    ws = await workspace_factory()
    _, leader = await make_agent_member(session_factory, ws)
    human = await make_human_member(session_factory, ws, role="member")
    admin = await make_human_member(session_factory, ws, role="admin")
    outsider = await make_human_member(session_factory, ws, role="member")
    squad = await make_squad(session_factory, ws, leader_member=leader)
    from tests.unit.squad_support import add_member

    await add_member(session_factory, ws, squad, human, role="member")

    checker = make_squad_channel_checker(session_factory)
    channel = f"squad:{squad.id}"
    # Member of the squad → allowed.
    assert await checker(Principal(subject=str(human.user_id), workspace_ids=frozenset({ws.id})), channel)
    # Admin (not on squad) → allowed.
    assert await checker(Principal(subject=str(admin.user_id), workspace_ids=frozenset({ws.id})), channel)
    # Non-member, non-admin → denied.
    assert not await checker(
        Principal(subject=str(outsider.user_id), workspace_ids=frozenset({ws.id})), channel
    )
    # Malformed channel / non-UUID squad id → denied.
    assert not await checker(
        Principal(subject=str(human.user_id), workspace_ids=frozenset({ws.id})), "squad:not-a-uuid"
    )
    assert not await checker(
        Principal(subject=str(human.user_id), workspace_ids=frozenset({ws.id})), "other:xyz"
    )
    # Dev (non-UUID subject) principal → allowed.
    assert await checker(Principal(subject="dev-user", workspace_ids=frozenset({ws.id})), channel)
