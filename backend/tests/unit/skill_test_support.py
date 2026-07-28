"""Shared helpers for the skill module test suites (real DB, no mocks)."""

from __future__ import annotations

import uuid

from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace


async def make_workspace(session_factory, *, name: str = "Skill WS") -> Workspace:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name=name, slug=f"ws-{uuid.uuid4().hex[:12]}")
        session.add(workspace)
    return workspace


async def make_member(
    session_factory, workspace, *, role: str = "member", name: str = "Member"
) -> Member:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"{uuid.uuid4().hex[:12]}@mesh.test",
            password_hash="x",
            display_name=name,
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace.id,
            member_type="human",
            user_id=user.id,
            role=role,
            status="active",
        )
        session.add(member)
    return member


async def make_agent(session_factory, workspace, owner_user_id, *, name="Bot") -> Agent:
    """A minimal agent row (agent.md §2.3) for binding tests."""
    async with session_factory() as session, session.begin():
        agent = Agent(
            workspace_id=workspace.id,
            name=name,
            owner_user_id=owner_user_id,
        )
        session.add(agent)
        await session.flush()
        # Roster entry so the agent is a first-class member (§6.1).
        session.add(
            Member(
                workspace_id=workspace.id,
                member_type="agent",
                agent_id=agent.id,
                role="member",
                status="active",
            )
        )
    return agent
