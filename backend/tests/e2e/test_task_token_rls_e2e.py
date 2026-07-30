"""MES-96 P2-4 / security MEDIUM-1 — attempt_task_tokens fail-closed RLS.

runtime-executor.md §2.2 declares ``attempt_task_tokens`` carries fail-closed
RLS, but migration 0029 created the table WITHOUT enabling it — a tenant's task
token rows were visible to any other tenant on the app path. Migration 0034
enables RLS with the standard tenant policy (0004/0008/0033 template).

These tests connect as the restricted, non-owner ``mesh_app`` role the API uses
in compose (RLS does not apply to the table owner) and prove the backstop is
live: with the ``mesh.workspace_id`` GUC set, only the named tenant's token rows
are visible; with it unset, the query fails closed. Before 0034 both fail
(cross-tenant visible, no fail-closed error).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.e2e.conftest import _app_role_url
from tests.unit.runtime_support import make_execution, make_runtime

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def app_role_engine(db_url):
    """An engine connected as the restricted app role the API/gateway use."""
    engine = create_async_engine(_app_role_url(db_url))
    yield engine
    await engine.dispose()


async def _seed_token_row(session_factory, workspace_id) -> uuid.UUID:
    """Insert one active task token row, building the minimal FK chain the
    table requires (runtime → execution → attempt → token). Runs as the table
    OWNER, so it bypasses RLS — only the app-role reads are RLS-subject."""
    runtime = await make_runtime(session_factory, workspace_id)
    execution = await make_execution(session_factory, workspace_id, None)
    attempt_id = uuid.uuid4()
    token_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO execution_attempts "
                "(id, workspace_id, execution_id, attempt_number, runtime_id, status, claimed_at) "
                "VALUES (:id, :ws, :e, 1, :r, 'running', now())"
            ),
            {"id": attempt_id, "ws": workspace_id, "e": execution.id, "r": runtime.id},
        )
        await session.execute(
            text(
                "INSERT INTO attempt_task_tokens "
                "(id, workspace_id, attempt_id, runtime_id, lease_seq, token_hash, expires_at) "
                "VALUES (:id, :ws, :a, :r, 1, :h, now() + interval '1 hour')"
            ),
            {
                "id": token_id,
                "ws": workspace_id,
                "a": attempt_id,
                "r": runtime.id,
                "h": f"task-token-{token_id.hex}",
            },
        )
    return token_id


async def test_task_token_rls_enabled_with_tenant_policy(db_session):
    """Schema-level backstop: RLS is on and the policy anchors the workspace GUC."""
    row = (
        await db_session.execute(
            text(
                "SELECT rowsecurity FROM pg_tables WHERE tablename = 'attempt_task_tokens'"
            )
        )
    ).scalar_one()
    assert row is True
    qual = (
        await db_session.execute(
            text(
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
                "WHERE polname = 'mesh_attempt_task_tokens_tenant'"
            )
        )
    ).scalar_one_or_none()
    assert qual is not None
    assert "mesh.workspace_id" in qual


async def test_task_token_rls_cross_tenant_hidden_with_guc(
    app_role_engine, session_factory, workspace_factory
):
    ws_a = await workspace_factory(name="A", slug="tasktok-a")
    ws_b = await workspace_factory(name="B", slug="tasktok-b")
    await _seed_token_row(session_factory, ws_a.id)
    await _seed_token_row(session_factory, ws_b.id)

    async with app_role_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                {"ws": str(ws_a.id)},
            )
            count = (
                await conn.execute(text("SELECT count(*) FROM attempt_task_tokens"))
            ).scalar_one()
            tenants = (
                await conn.execute(
                    text("SELECT DISTINCT workspace_id FROM attempt_task_tokens")
                )
            ).scalars().all()
    # Only tenant A's token row is visible — tenant B is hidden by RLS.
    assert count == 1
    assert tenants == [ws_a.id]


async def test_task_token_rls_fails_closed_without_guc(
    app_role_engine, session_factory, workspace_factory
):
    ws_a = await workspace_factory(name="A", slug="tasktok-fc")
    await _seed_token_row(session_factory, ws_a.id)
    async with app_role_engine.connect() as conn:
        # Without the GUC the policy cannot be evaluated → error (fail-closed).
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT count(*) FROM attempt_task_tokens"))
