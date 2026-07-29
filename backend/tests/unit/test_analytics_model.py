"""analytics_snapshots model + migration 0028 behavior (analytics.md §2.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.analytics import AnalyticsSnapshot

pytestmark = pytest.mark.unit

WIN = dict(
    window_start=datetime(2026, 7, 1, tzinfo=UTC),
    window_end=datetime(2026, 7, 8, tzinfo=UTC),
)


async def test_snapshot_roundtrip_and_generated_dim_hash(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        session.add(
            AnalyticsSnapshot(
                workspace_id=ws.id,
                metric_key="throughput",
                scope_key="projects:abc",
                dimensions={"granularity": "day", "calendar_timezone": "UTC"},
                value={"series": []},
                **WIN,
            )
        )
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(AnalyticsSnapshot))).scalar_one()
        assert row.dim_hash and len(row.dim_hash) == 32  # md5 hex
        assert row.scope_key == "projects:abc"
        assert row.metric_key == "throughput"
        assert row.value == {"series": []}


async def test_dim_hash_varies_with_dimensions(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        session.add_all(
            [
                AnalyticsSnapshot(
                    workspace_id=ws.id,
                    metric_key="throughput",
                    dimensions={"calendar_timezone": "UTC"},
                    value={},
                    **WIN,
                ),
                AnalyticsSnapshot(
                    workspace_id=ws.id,
                    metric_key="throughput",
                    dimensions={"calendar_timezone": "Asia/Shanghai"},
                    value={},
                    **WIN,
                ),
            ]
        )
        await session.commit()
    async with session_factory() as session:
        rows = (await session.execute(select(AnalyticsSnapshot))).scalars().all()
        assert len({r.dim_hash for r in rows}) == 2  # 不同时区维度分行


async def test_unique_key_blocks_same_scope_duplicate(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        session.add(
            AnalyticsSnapshot(
                workspace_id=ws.id,
                metric_key="throughput",
                scope_key="ws_admin",
                dimensions={"g": "day"},
                value={"v": 1},
                **WIN,
            )
        )
        await session.commit()
    async with session_factory() as session:
        session.add(
            AnalyticsSnapshot(
                workspace_id=ws.id,
                metric_key="throughput",
                scope_key="ws_admin",
                dimensions={"g": "day"},
                value={"v": 2},
                **WIN,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_different_scope_keys_coexist(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        for scope in ("ws_admin", "projects:abc", "exec:px:ay"):
            session.add(
                AnalyticsSnapshot(
                    workspace_id=ws.id,
                    metric_key="agent_stats",
                    scope_key=scope,
                    dimensions={"agent_id": "a1"},
                    value={},
                    **WIN,
                )
            )
        await session.commit()
    async with session_factory() as session:
        rows = (await session.execute(select(AnalyticsSnapshot))).scalars().all()
        assert len(rows) == 3  # 跨权限物理分行


async def test_metric_key_check_constraint(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        session.add(
            AnalyticsSnapshot(
                workspace_id=ws.id, metric_key="bogus", dimensions={}, value={}, **WIN
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_rls_policy_and_grants_exist(session_factory):
    async with session_factory() as session:
        policies = (
            await session.execute(
                text("SELECT polname FROM pg_policy WHERE polrelid = 'analytics_snapshots'::regclass")
            )
        ).all()
        assert [r[0] for r in policies] == ["mesh_analytics_snapshots_tenant"]
        grants = (
            await session.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'analytics_snapshots' AND grantee = 'mesh_app'"
                )
            )
        ).all()
        assert {r[0] for r in grants} == {"SELECT", "INSERT", "UPDATE", "DELETE"}
