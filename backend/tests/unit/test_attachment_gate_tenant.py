"""Tenant-context regression for attachment/data-jobs workspace gating (MES-80 A3).

``gate_workspace`` serves the PAT/agent branch of the attachment + data-jobs
route layer. ``members`` is RLS-protected: under the restricted app role the
policy casts the ``mesh.workspace_id`` GUC to uuid, so the token branch MUST
set the tenant context before the roster read — an unset GUC is ``''`` and the
read dies with ``invalid input syntax for type uuid: ""`` (a 500 on every
PAT-driven export/import, the CI headless path). Owner-role suites never trip
this, so the invariant is asserted via a recording spy — role-independent.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import mesh.attachment.auth as gate_mod
from mesh.attachment.auth import Caller, gate_workspace
from mesh.auth.tokens import TokenService
from mesh.errors import NotFoundError

pytestmark = pytest.mark.unit


async def _seed_workspace_member(session_factory) -> tuple[uuid.UUID, uuid.UUID, object]:
    """A workspace + active human owner member; returns (ws_id, member_id, member)."""
    from mesh.db.models.member import Member

    async with session_factory() as session, session.begin():
        ws_id = (
            await session.execute(
                text("INSERT INTO workspaces (name, slug) VALUES ('G', :s) RETURNING id"),
                {"s": f"gate-{uuid.uuid4().hex[:12]}"},
            )
        ).scalar_one()
        user_id = (
            await session.execute(
                text("INSERT INTO users (email, display_name) VALUES (:e, 'G') RETURNING id"),
                {"e": f"{uuid.uuid4().hex[:12]}@gate.com"},
            )
        ).scalar_one()
        member_id = (
            await session.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role, status) "
                    "VALUES (:ws, 'human', :u, 'admin', 'active') RETURNING id"
                ),
                {"ws": ws_id, "u": user_id},
            )
        ).scalar_one()
    async with session_factory() as session:
        member = await session.get(Member, member_id)
    return ws_id, member_id, member


async def _issue_pat(session_factory, member, ws_id: uuid.UUID) -> str:
    created = await TokenService(session_factory).create_token(
        actor=member, workspace_id=ws_id, name="gate-pat", scopes=["issue:read"]
    )
    return created["token"]


async def test_pat_gate_sets_tenant_context_before_roster_read(session_factory, monkeypatch):
    # Arrange — a PAT for the workspace under test.
    ws_id, member_id, member = await _seed_workspace_member(session_factory)
    pat = await _issue_pat(session_factory, member, ws_id)
    resolved = await TokenService(session_factory).resolve_pat(token=pat, ip_address=None)
    assert resolved is not None

    calls: list[uuid.UUID] = []
    original = gate_mod.set_tenant_context

    async def spy(conn, workspace_id):
        calls.append(workspace_id)
        return await original(conn, workspace_id)

    monkeypatch.setattr(gate_mod, "set_tenant_context", spy)

    # Act
    async with session_factory() as session:
        resolved_member = await gate_workspace(session, Caller(user=None, token=resolved), ws_id)

    # Assert — membership resolved AND the GUC was set for this workspace
    # (without it the app-role RLS policy casts '' to uuid → 500).
    assert resolved_member.id == member_id
    assert calls == [ws_id]


async def test_pat_gate_foreign_workspace_is_404(session_factory):
    # Arrange — a PAT for workspace A, a gate on workspace B.
    ws_a, _member_a_id, member_a = await _seed_workspace_member(session_factory)
    ws_b, _member_b_id, _member_b = await _seed_workspace_member(session_factory)
    pat = await _issue_pat(session_factory, member_a, ws_a)
    resolved = await TokenService(session_factory).resolve_pat(token=pat, ip_address=None)
    assert resolved is not None

    # Act / Assert — no membership leak across workspaces (§5.3 one 404).
    async with session_factory() as session:
        with pytest.raises(NotFoundError):
            await gate_workspace(session, Caller(user=None, token=resolved), ws_b)
