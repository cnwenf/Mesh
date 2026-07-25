"""PostgreSQL RLS defense-in-depth (§6.2 rule 5/8) against a real PG 16.

A non-owner login role sees only rows of the workspace named by the
``mesh.workspace_id`` GUC — and nothing at all without it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from mesh.db.models.realtime import RealtimeChannel

pytestmark = pytest.mark.e2e

RLS_ROLE = "mesh_rls_test"
RLS_PASSWORD = "mesh_rls_test_pw"


def _role_url(db_url: str) -> str:
    # postgresql+asyncpg://mesh:mesh@host:port/db → role-scoped URL
    without_scheme = db_url.split("://", 1)[1]
    host_and_db = without_scheme.split("@", 1)[1]
    return f"postgresql+asyncpg://{RLS_ROLE}:{RLS_PASSWORD}@{host_and_db}"


@pytest_asyncio.fixture
async def rls_role(session_factory, db_url):
    """Create a non-owner login role with SELECT on the realtime tables."""
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{RLS_ROLE}') THEN "
                f"CREATE ROLE {RLS_ROLE} LOGIN PASSWORD '{RLS_PASSWORD}'; END IF; END $$"
            )
        )
        await session.execute(text(f"GRANT USAGE ON SCHEMA public TO {RLS_ROLE}"))
        await session.execute(
            text(f"GRANT SELECT ON realtime_channels, realtime_events TO {RLS_ROLE}")
        )
    yield _role_url(db_url)


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


async def test_rls_filters_tenant_rows(session_factory, workspace_factory, rls_role):
    ws_a = await workspace_factory(name="A", slug="rls-a")
    ws_b = await workspace_factory(name="B", slug="rls-b")
    await _seed(session_factory, ws_a.id, "issue:rls-a", 1)
    await _seed(session_factory, ws_b.id, "issue:rls-b", 1)

    engine = create_async_engine(rls_role)
    try:
        async with engine.connect() as conn:
            # Without the GUC, the policy cannot even be evaluated.
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT count(*) FROM realtime_events"))
            await conn.rollback()  # clear the aborted autobegin transaction

            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_a.id)},
                )
                visible_channels = (
                    (await conn.execute(text("SELECT channel FROM realtime_channels")))
                    .scalars()
                    .all()
                )
                visible_event_count = (
                    await conn.execute(text("SELECT count(*) FROM realtime_events"))
                ).scalar_one()
            assert visible_channels == ["issue:rls-a"]
            assert visible_event_count == 1

            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('mesh.workspace_id', :ws, true)"),
                    {"ws": str(ws_b.id)},
                )
                visible_channels = (
                    (await conn.execute(text("SELECT channel FROM realtime_channels")))
                    .scalars()
                    .all()
                )
            assert visible_channels == ["issue:rls-b"]
    finally:
        await engine.dispose()
