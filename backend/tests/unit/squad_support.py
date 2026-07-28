"""Shared fixtures for squad-module unit tests (real PostgreSQL, no mocks)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mesh.db.models.agent import Agent
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.member import Member
from mesh.db.models.squad import Squad, SquadMember
from mesh.db.models.user import User


async def make_human_member(session_factory, workspace, *, role="member", name="Human"):
    """Create a human roster member (+user) and return the Member row."""
    user_id, member_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            User(
                id=user_id,
                email=f"squad-{user_id.hex[:10]}@corp.com",
                display_name=name,
                password_hash="unused-in-tests",
            )
        )
        await session.flush()
        member = Member(
            id=member_id,
            workspace_id=workspace.id,
            member_type="human",
            user_id=user_id,
            role=role,
            status="active",
        )
        session.add(member)
    return member


async def make_agent_member(session_factory, workspace, *, name="Agent"):
    """Create an agent + its roster member row; return (agent, member)."""
    agent_id, member_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            User(
                id=owner_id,
                email=f"squad-owner-{owner_id.hex[:8]}@corp.com",
                display_name="Agent Owner",
                password_hash="unused-in-tests",
            )
        )
        await session.flush()
        agent = Agent(
            id=agent_id,
            workspace_id=workspace.id,
            name=name,
            owner_user_id=owner_id,
            lifecycle_status="active",
        )
        session.add(agent)
        await session.flush()
        member = Member(
            id=member_id,
            workspace_id=workspace.id,
            member_type="agent",
            agent_id=agent_id,
            role="member",
            status="active",
        )
        session.add(member)
    return agent, member


async def seed_issue(session_factory, workspace, *, title="Squad issue"):
    """A minimal issue row (reuses the workspace default status if present)."""
    async with session_factory() as session, session.begin():
        status = await session.scalar(
            select(IssueStatus).where(IssueStatus.workspace_id == workspace.id).limit(1)
        )
        if status is None:
            status = IssueStatus(
                workspace_id=workspace.id,
                name=f"status-{uuid.uuid4().hex[:8]}",
                category="todo",
                is_default=True,
            )
            session.add(status)
            await session.flush()
        suffix = uuid.uuid4().hex[:6]
        issue = Issue(
            workspace_id=workspace.id,
            identifier_namespace_key=f"ws:{workspace.id}",
            number=abs(hash(suffix)) % 100000,
            identifier=f"WS-{suffix}",
            title=title,
            status_id=status.id,
            state_category="todo",
        )
        session.add(issue)
    return issue


async def make_squad(
    session_factory,
    workspace,
    *,
    leader_member,
    creator_member=None,
    name=None,
    require_plan_approval=False,
    max_decompose_depth=2,
):
    """Create a squad with one leader member; return the Squad row."""
    squad_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        squad = Squad(
            id=squad_id,
            workspace_id=workspace.id,
            name=name or f"squad-{squad_id.hex[:8]}",
            kind="standing",
            leader_mode="single",
            primary_leader_id=leader_member.id,
            require_plan_approval=require_plan_approval,
            max_decompose_depth=max_decompose_depth,
            creator_id=(creator_member or leader_member).id,
        )
        session.add(squad)
        await session.flush()
        session.add(
            SquadMember(
                workspace_id=workspace.id,
                squad_id=squad.id,
                member_id=leader_member.id,
                role="leader",
            )
        )
    return squad


async def add_member(session_factory, workspace, squad, member, *, role="member"):
    async with session_factory() as session, session.begin():
        session.add(
            SquadMember(
                workspace_id=workspace.id,
                squad_id=squad.id,
                member_id=member.id,
                role=role,
            )
        )


def build_services(session_factory):
    from mesh.comment_inbox.service import CommentService
    from mesh.squad.service import SquadService
    from mesh.squad.tasks import SquadTaskService

    # Inject a real CommentService so the §S8 parent-issue summary writeback
    # path is exercised exactly like production wiring (app.py).
    comment_service = CommentService(session_factory, signing_secret="squad-test-secret")
    return SquadService(session_factory), SquadTaskService(
        session_factory, comment_service=comment_service
    )


async def project_pending_realtime(session_factory) -> int:
    """Drain ``realtime.publish`` outbox rows into ``realtime_events``.

    The unit suite runs no projector loop, but the SSE progress stream
    replays persisted frames by per-channel seq (squad.md §3.5), so tests
    that assert stream contents must project the pending rows first.
    Returns the number of projected events.
    """
    from mesh.db.models.outbox import OutboxEvent
    from mesh.events.vocab import REALTIME_PUBLISH
    from mesh.outbox.projector import project_realtime_event

    async with session_factory() as session, session.begin():
        rows = list(
            (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == REALTIME_PUBLISH)
                )
            ).scalars()
        )
        for event in rows:
            await project_realtime_event(session, event)
    return len(rows)
