"""M1 acceptance: the application connection role (mesh_app) is RLS-enforced.

In the default deployment shape the API/gateway connect as ``mesh_app``
(non-owner, non-superuser). These tests connect as that exact role against real
PostgreSQL 16 and prove the RLS backstop is live on the app path:

* the role is restricted (not the table owner, not a superuser);
* an unset ``mesh.workspace_id`` GUC fails closed;
* with the GUC set, only the named tenant's rows are visible (cross-tenant hidden).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from mesh.db.models.realtime import RealtimeChannel
from tests.e2e.conftest import _app_role_url

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def app_role_engine(db_url):
    """An engine connected as the restricted app role the API/gateway use in compose."""
    engine = create_async_engine(_app_role_url(db_url))
    yield engine
    await engine.dispose()


async def _seed(session_factory, workspace_id, channel, seq):
    async with session_factory() as session, session.begin():
        session.add(RealtimeChannel(channel=channel, workspace_id=workspace_id, last_seq=seq))
        await session.flush()  # channel row must exist before the FK-referencing INSERT
        await session.execute(
            text(
                "INSERT INTO realtime_events (workspace_id, channel, seq, event, payload, outbox_event_id) "
                "VALUES (:ws, :ch, :seq, 'issue.updated', '{}', gen_random_uuid())"
            ),
            {"ws": workspace_id, "ch": channel, "seq": seq},
        )


async def test_app_role_is_restricted_not_owner_or_superuser(app_role_engine):
    async with app_role_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT rolsuper, "
                    "(SELECT tableowner FROM pg_tables WHERE tablename = 'realtime_events') AS owner "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
    assert row.rolsuper is False
    assert row.owner != "mesh_app"  # the app role is NOT the table owner


async def test_app_role_fails_closed_without_guc(app_role_engine):
    async with app_role_engine.connect() as conn:
        # Without the GUC the policy cannot even be evaluated → error (fail-closed).
        with pytest.raises(DBAPIError):
            await conn.execute(text("SELECT count(*) FROM realtime_events"))


async def test_app_role_cross_tenant_hidden_with_guc(
    app_role_engine, session_factory, workspace_factory
):
    ws_a = await workspace_factory(name="A", slug="app-a")
    ws_b = await workspace_factory(name="B", slug="app-b")
    await _seed(session_factory, ws_a.id, "issue:app-a", 1)
    await _seed(session_factory, ws_b.id, "issue:app-b", 1)

    async with app_role_engine.connect() as conn:
        async with conn.begin():
            await conn.execute(
                text("SELECT set_config('mesh.workspace_id', :ws, true)"), {"ws": str(ws_a.id)}
            )
            channels = (
                (await conn.execute(text("SELECT channel FROM realtime_channels")))
                .scalars()
                .all()
            )
            event_count = (
                await conn.execute(text("SELECT count(*) FROM realtime_events"))
            ).scalar_one()
    # Only tenant A is visible — tenant B is hidden by RLS on the app path.
    assert channels == ["issue:app-a"]
    assert event_count == 1
