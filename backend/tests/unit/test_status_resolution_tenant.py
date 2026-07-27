"""Tenant-isolation regression: resolve_default_status (found via MES-32).

``resolve_default_status`` previously built its final fallback query without a
``workspace_id`` predicate — with multiple workspaces present it could resolve
ANOTHER tenant's default status (``ORDER BY position, id LIMIT 1`` across the
whole table), which blew up as a composite-FK violation when the issue row
was inserted (intermittent 500s on issue creation in multi-workspace flows).
This test is deterministic: tenant B's default carries the maximum UUID, so
without tenant filtering tenant A's smaller random default always sorts first.
"""

from __future__ import annotations

import uuid

import pytest

from mesh.db.models.issue import IssueStatus
from mesh.db.models.workspace import Workspace
from mesh.issue.statuses import resolve_default_status, seed_default_statuses

pytestmark = pytest.mark.unit

BIGGEST_UUID = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")


async def test_resolve_default_status_never_crosses_tenants(session_factory):
    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    # Tenant A: the full canonical set (server-random UUIDs — all < BIGGEST).
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=ws_a.id, project_id=None)
    # Tenant B: a single default with the maximum UUID.
    async with session_factory() as session, session.begin():
        session.add(
            IssueStatus(
                id=BIGGEST_UUID,
                workspace_id=ws_b.id,
                name="Todo",
                category="todo",
                position=0,
                is_default=True,
                color="#000000",
            )
        )
    async with session_factory() as session:
        resolved = await resolve_default_status(
            session, workspace_id=ws_b.id, project_id=None
        )
    assert resolved.id == BIGGEST_UUID
    assert resolved.workspace_id == ws_b.id


async def test_resolve_default_status_category_scoped_to_tenant(session_factory):
    """The category fallback path is tenant-scoped as well."""
    async with session_factory() as session, session.begin():
        ws_a = Workspace(name="A", slug=f"ws-a-{uuid.uuid4().hex[:8]}")
        ws_b = Workspace(name="B", slug=f"ws-b-{uuid.uuid4().hex[:8]}")
        session.add_all([ws_a, ws_b])
    async with session_factory() as session, session.begin():
        await seed_default_statuses(session, workspace_id=ws_a.id, project_id=None)
        # Tenant B has NO 'done' status at all.
    from mesh.errors import BusinessRuleError

    async with session_factory() as session:
        # Asking tenant B for a 'done'-category default must NOT hand back
        # tenant A's done status; with nothing in scope the self-heal seeds
        # tenant B's own set, so the resolution stays within the tenant.
        resolved = await resolve_default_status(
            session, workspace_id=ws_b.id, project_id=None, category="done"
        )
    assert resolved.workspace_id == ws_b.id
    assert resolved.category == "done"
    _ = BusinessRuleError  # documented alternative outcome; not expected here
