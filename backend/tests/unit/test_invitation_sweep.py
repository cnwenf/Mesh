"""Invitation sweep worker loop tests (workspace.md §4.4 timed expiry)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text

from mesh.db.models.workspace import WorkspaceInvitation
from mesh.workers.invitation_sweep import invitation_sweep_loop
from mesh.workspace.invitations import InvitationService
from mesh.workspace.service import WorkspaceService

pytestmark = pytest.mark.unit


async def test_sweep_loop_expires_past_due_links_and_stops(session_factory):
    # Seed a workspace + a past-due invitation.
    ws_service = WorkspaceService(session_factory)
    inv_service = InvitationService(session_factory)
    async with session_factory() as session, session.begin():
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'S') RETURNING id"),
                {"e": "sweep-loop@corp.com"},
            )
        ).scalar_one()
    from mesh.db.models.user import User

    created = await ws_service.create_workspace(
        user=User(id=user_id, email="sweep-loop@corp.com", display_name="S"),
        name="Sweep",
        slug="sweep-loop",
    )
    async with session_factory() as session:
        from mesh.db.models.member import Member

        admin = await session.scalar(
            select(Member).where(
                Member.workspace_id == created["id"], Member.user_id == user_id
            )
        )
    invitation = (
        await inv_service.create_invitations(
            actor=admin, workspace_id=created["id"]
        )
    )[0]
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE workspace_invitations SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": invitation["id"]},
        )

    stop = asyncio.Event()
    task = asyncio.create_task(
        invitation_sweep_loop(session_factory, interval=0.05, stop=stop)
    )
    # Wait until the sweep flips the row.
    deadline = asyncio.get_event_loop().time() + 5
    flipped = False
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            status = await session.scalar(
                select(WorkspaceInvitation.status).where(
                    WorkspaceInvitation.id == invitation["id"]
                )
            )
        if status == "expired":
            flipped = True
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert flipped
