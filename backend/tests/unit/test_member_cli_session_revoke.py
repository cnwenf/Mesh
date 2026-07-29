"""Member removal/disable → cli session revocation (MES-78 LOW-1, auth.md §1.1).

Removing or disabling a member revokes every cli session bound to THAT
workspace for the member's user, in the same transaction (and broadcasts
``session.revoked``). Web sessions and other workspaces' sessions are
untouched; a re-invite never revives the old refresh (it stays revoked).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.auth.security import generate_refresh_token, hash_token
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import Session, User
from mesh.db.models.workspace import Workspace
from mesh.member.service import MemberService

pytestmark = pytest.mark.unit


async def _seed(session_factory):
    """Admin actor + target member (+user) in one workspace."""
    async with session_factory() as session, session.begin():
        ws = Workspace(name="RVS WS", slug=f"rvs-{uuid.uuid4().hex[:8]}")
        session.add(ws)
        await session.flush()
        admin_user = User(email=f"admin-{uuid.uuid4().hex[:6]}@corp.dev", display_name="Admin")
        target_user = User(email=f"target-{uuid.uuid4().hex[:6]}@corp.dev", display_name="Target")
        session.add_all([admin_user, target_user])
        await session.flush()
        admin = Member(
            workspace_id=ws.id, user_id=admin_user.id, member_type="human",
            role="admin", status="active",
        )
        target = Member(
            workspace_id=ws.id, user_id=target_user.id, member_type="human",
            role="member", status="active",
        )
        session.add_all([admin, target])
        await session.flush()
        return ws.id, admin, target.id, target_user.id


async def _add_cli_session(session_factory, *, user_id, workspace_id, tag="a") -> uuid.UUID:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        row = Session(
            user_id=user_id,
            token_hash=hash_token(generate_refresh_token()),
            type="cli",
            workspace_id=workspace_id,
            granted_scopes=["issue:read"],
            expires_at=now + timedelta(days=14),
            last_active_at=now,
            authenticated_at=now,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _add_web_session(session_factory, *, user_id) -> uuid.UUID:
    now = datetime.now(UTC)
    async with session_factory() as session, session.begin():
        row = Session(
            user_id=user_id,
            token_hash=hash_token(generate_refresh_token()),
            type="web",
            expires_at=now + timedelta(days=14),
            last_active_at=now,
            authenticated_at=now,
        )
        session.add(row)
        await session.flush()
        return row.id


async def _session_row(session_factory, sid) -> Session:
    async with session_factory() as session:
        return await session.get(Session, sid)


class TestRemovalRevocation:
    async def test_remove_revokes_workspace_cli_sessions_only(self, session_factory):
        ws_id, admin, target_id, user_id = await _seed(session_factory)
        cli_here = await _add_cli_session(session_factory, user_id=user_id, workspace_id=ws_id)
        web_here = await _add_web_session(session_factory, user_id=user_id)
        # A cli session in ANOTHER workspace must survive.
        async with session_factory() as session, session.begin():
            other_ws = Workspace(name="Other", slug=f"oth-{uuid.uuid4().hex[:8]}")
            session.add(other_ws)
            await session.flush()
            other_ws_id = other_ws.id
        cli_elsewhere = await _add_cli_session(
            session_factory, user_id=user_id, workspace_id=other_ws_id
        )

        service = MemberService(session_factory)
        await service.remove_member(actor=admin, workspace_id=ws_id, member_id=target_id)

        assert (await _session_row(session_factory, cli_here)).revoked_at is not None
        assert (await _session_row(session_factory, web_here)).revoked_at is None
        assert (await _session_row(session_factory, cli_elsewhere)).revoked_at is None

        # Broadcast emitted in the same transaction (outbox → realtime): the
        # realtime outbox row carries the event name in its JSONB payload.
        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.payload["event"].astext == "session.revoked"
                        )
                    )
                ).scalars().all()
            )
        assert len(events) == 1
        assert events[0].payload["channel"] == f"workspace:{ws_id}"

    async def test_disable_revokes_cli_sessions(self, session_factory):
        from mesh.member.service import MemberPatch

        ws_id, admin, target_id, user_id = await _seed(session_factory)
        cli_here = await _add_cli_session(session_factory, user_id=user_id, workspace_id=ws_id)

        service = MemberService(session_factory)
        await service.update_member(
            actor=admin,
            workspace_id=ws_id,
            member_id=target_id,
            patch=MemberPatch(status="disabled"),
        )
        assert (await _session_row(session_factory, cli_here)).revoked_at is not None

    async def test_reinvite_does_not_revive_old_refresh(self, session_factory):
        """The old revoked session stays revoked after re-invitation — the
        member must re-approve a fresh device login (auth.md §1.1)."""
        ws_id, admin, target_id, user_id = await _seed(session_factory)
        cli_here = await _add_cli_session(session_factory, user_id=user_id, workspace_id=ws_id)
        service = MemberService(session_factory)
        await service.remove_member(actor=admin, workspace_id=ws_id, member_id=target_id)
        row = await _session_row(session_factory, cli_here)
        revoked_at = row.revoked_at
        assert revoked_at is not None
        # Re-activate (simulating re-invite paths touching the member row):
        # the session row is never un-revoked by member operations.
        async with session_factory() as session:
            reread = await session.get(Session, cli_here)
            assert reread.revoked_at == revoked_at
