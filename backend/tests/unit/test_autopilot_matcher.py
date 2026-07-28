"""autopilot.matcher — outbox relay consumer: domain events → triggers (§4.5 / §6.6)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mesh.autopilot.matcher import match_domain_event
from mesh.db.models.autopilot import AutopilotArtifact, AutopilotRun
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.label import IssueLabel, Label
from mesh.db.models.outbox import OutboxEvent
from tests.unit.autopilot_support import make_rule, make_run
from tests.unit.runtime_support import seed_world


async def _seed_issue(session_factory, world, *, title="登录报错", priority="high", labels=("bug",)) -> Issue:
    async with session_factory() as session, session.begin():
        status = IssueStatus(
            workspace_id=world["ws_id"], name="Todo", category="todo"
        )
        session.add(status)
        await session.flush()
        issue = Issue(
            workspace_id=world["ws_id"],
            identifier_namespace_key="TST",
            number=int(uuid.uuid4().int % 100000),
            identifier=f"TST-{int(uuid.uuid4().int % 100000)}",
            title=title,
            description="线上出现问题",
            status_id=status.id,
            state_category="todo",
            priority=priority,
        )
        session.add(issue)
        await session.flush()
        for label_name in labels:
            label = Label(
                workspace_id=world["ws_id"], name=label_name, color="#ff0000"
            )
            session.add(label)
            await session.flush()
            session.add(
                IssueLabel(
                    workspace_id=world["ws_id"], issue_id=issue.id, label_id=label.id
                )
            )
    return issue


async def _outbox_event(session_factory, world, *, event: str, data: dict) -> OutboxEvent:
    outbox = OutboxEvent(
        workspace_id=world["ws_id"],
        event_type="realtime.publish",
        payload={
            "channel": f"workspace:{world['ws_id']}:issues",
            "event": event,
            "data": data,
        },
    )
    async with session_factory() as session, session.begin():
        session.add(outbox)
    return outbox


async def _runs(session_factory) -> list[AutopilotRun]:
    async with session_factory() as session:
        return list((await session.execute(select(AutopilotRun))).scalars().all())


async def test_issue_created_trigger_creates_run(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_created",
    )
    event = await _outbox_event(
        session_factory, world, event="issue.created",
        data={"id": str(issue.id), "title": issue.title},
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    runs = await _runs(session_factory)
    assert len(runs) == 1
    assert runs[0].trigger_snapshot["issue"]["id"] == str(issue.id)
    assert runs[0].trigger_snapshot["event"] == "issue.created"


async def test_no_candidate_rules_fast_path(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    # only a schedule rule exists — no event triggers to match
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="schedule",
    )
    event = await _outbox_event(
        session_factory, world, event="issue.created", data={"id": str(issue.id)}
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert await _runs(session_factory) == []


async def test_unhandled_event_ignored(session_factory) -> None:
    world = await seed_world(session_factory)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_created",
    )
    event = await _outbox_event(
        session_factory, world, event="label.created", data={"id": str(uuid.uuid4())}
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert await _runs(session_factory) == []


async def test_issue_status_changed_trigger_and_filters(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world, priority="high")
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_status_changed",
        trigger_config={"to_status": ["in_progress"]},
        filter_config={"priorities": ["high", "critical"], "labels": ["bug"]},
    )
    # status changed to a matching status
    event = await _outbox_event(
        session_factory, world, event="issue.updated",
        data={
            "id": str(issue.id),
            "changes": {"status_id": str(uuid.uuid4()), "status": {"name": "in_progress"}},
            "visibility": {"project_id": None, "state_category": "in_progress"},
        },
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert len(await _runs(session_factory)) == 1

    # status changed to a NON-matching status → no new run
    event2 = await _outbox_event(
        session_factory, world, event="issue.updated",
        data={
            "id": str(issue.id),
            "changes": {"status_id": str(uuid.uuid4()), "status": {"name": "done"}},
            "visibility": {"project_id": None, "state_category": "done"},
        },
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event2)
    assert len(await _runs(session_factory)) == 1


async def test_issue_status_from_status_requires_prior_state(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_status_changed",
        trigger_config={"from_status": ["todo"], "to_status": ["in_progress"]},
    )
    # internal issue.updated carries no prior status → from_status cannot match
    event = await _outbox_event(
        session_factory, world, event="issue.updated",
        data={"id": str(issue.id), "changes": {"status_id": "x", "status": {"name": "in_progress"}}},
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert len(await _runs(session_factory)) == 0

    # issue.moved DOES carry from/to categories
    event2 = await _outbox_event(
        session_factory, world, event="issue.moved",
        data={
            "id": str(issue.id),
            "from": {"state_category": "todo"},
            "to": {"state_category": "in_progress"},
        },
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event2)
    assert len(await _runs(session_factory)) == 1


async def test_issue_field_changed_watch_fields(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_field_changed",
        trigger_config={"watch_fields": ["priority"]},
    )
    # watched field changed
    event = await _outbox_event(
        session_factory, world, event="issue.updated",
        data={"id": str(issue.id), "changes": {"priority": "critical"}},
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert len(await _runs(session_factory)) == 1
    # unwatched field changed → nothing
    event2 = await _outbox_event(
        session_factory, world, event="issue.updated",
        data={"id": str(issue.id), "changes": {"description": "new text"}},
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event2)
    assert len(await _runs(session_factory)) == 1


async def test_comment_created_and_agent_mentioned(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="comment_created",
    )
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="agent_mentioned",
        trigger_config={"target_agent_ids": [str(world["agent_id"])]},
    )
    event = await _outbox_event(
        session_factory, world, event="comment.created",
        data={
            "id": str(uuid.uuid4()),
            "issue_id": str(issue.id),
            "body_text": "@值班agent 帮忙看下",
            "author": {"id": str(world["member_id"]), "name": "RT Owner"},
            "mentions": [{"id": str(world["agent_member_id"]), "member_type": "agent", "name": "Agent RT"}],
        },
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    runs = await _runs(session_factory)
    types = sorted(run.trigger_type for run in runs)
    assert types == ["agent_mentioned", "comment_created"]


async def test_agent_mentioned_target_filter(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    other_agent = uuid.uuid4()
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="agent_mentioned",
        trigger_config={"target_agent_ids": [str(other_agent)]},
    )
    event = await _outbox_event(
        session_factory, world, event="comment.created",
        data={
            "id": str(uuid.uuid4()),
            "issue_id": str(issue.id),
            "body_text": "hi",
            "mentions": [{"id": str(world["agent_member_id"]), "member_type": "agent"}],
        },
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert await _runs(session_factory) == []


async def test_cascade_lineage_from_agent_produced_comment(session_factory) -> None:
    world = await seed_world(session_factory)
    issue = await _seed_issue(session_factory, world)
    # an upstream rule of a DIFFERENT trigger type produced the comment via
    # its run (artifact anchor) — it must not itself re-match the comment event
    parent_rule = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_created",
    )
    parent_run = await make_run(session_factory, parent_rule, status="running", cascade_depth=2)
    comment_id = uuid.uuid4()
    # the parent run produced this comment (artifact reference)
    async with session_factory() as session, session.begin():
        session.add(AutopilotArtifact(
            workspace_id=world["ws_id"], run_id=parent_run.id,
            artifact_type="comment", ref_table="comments", ref_id=comment_id,
        ))
    downstream = await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="comment_created",
        guardrails={"cascade_max_depth": 3},
    )
    del downstream
    event = await _outbox_event(
        session_factory, world, event="comment.created",
        data={"id": str(comment_id), "issue_id": str(issue.id), "body_text": "again", "mentions": []},
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    runs = await _runs(session_factory)
    children = [r for r in runs if r.parent_run_id == parent_run.id]
    assert len(children) == 1
    assert children[0].cascade_depth == 3  # parent depth + 1

    # a rule with cascade_max_depth=2 refuses the depth-3 child
    event2 = await _outbox_event(
        session_factory, world, event="comment.created",
        data={"id": str(comment_id), "issue_id": str(issue.id), "body_text": "x", "mentions": []},
    )
    async with session_factory() as session, session.begin():
        from mesh.db.models.autopilot import Autopilot

        row = await session.scalar(select(Autopilot).where(Autopilot.id == children[0].autopilot_id))
        guardrails = dict(row.guardrails)
        guardrails["cascade_max_depth"] = 2
        row.guardrails = guardrails
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event2)
    runs_after = await _runs(session_factory)
    assert len(runs_after) == len(runs)  # no new downstream runs


async def test_scope_project_ids_filtering(session_factory) -> None:
    from mesh.db.models.project import Project

    world = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        project = Project(workspace_id=world["ws_id"], name="P1", key="p1", visibility="public")
        session.add(project)
    issue = await _seed_issue(session_factory, world)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(Issue).where(Issue.id == issue.id))
        row.project_id = project.id
    await make_rule(
        session_factory, world["ws_id"], created_by=world["member_id"],
        trigger_type="issue_created",
        trigger_config={"scope_project_ids": [str(uuid.uuid4())]},  # different project
    )
    event = await _outbox_event(
        session_factory, world, event="issue.created", data={"id": str(issue.id)}
    )
    async with session_factory() as session, session.begin():
        await match_domain_event(session, event)
    assert await _runs(session_factory) == []
