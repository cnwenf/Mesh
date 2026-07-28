"""analytics_snapshots cache hit/upsert semantics (analytics.md §2.5/§2.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.analytics.cache import fetch_snapshot, snapshot_is_fresh, upsert_snapshot
from mesh.db.models.analytics import AnalyticsSnapshot

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
TTL = timedelta(minutes=15)
WIN = dict(window_start=NOW - timedelta(days=7), window_end=NOW)
KEY = dict(
    metric_key="throughput",
    scope_key="projects:abc",
    dimensions={"granularity": "day", "calendar_timezone": "UTC"},
)


async def test_upsert_then_fresh_hit(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"series": [1]}, now=NOW, **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, row = await fetch_snapshot(session, workspace_id=ws.id, **KEY, **WIN)
        assert value == {"series": [1]}
        assert snapshot_is_fresh(row, TTL, NOW)


async def test_upsert_overwrites_same_key(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"v": 1}, now=NOW, **KEY, **WIN)
        await upsert_snapshot(
            session, workspace_id=ws.id, value={"v": 2}, now=NOW + timedelta(minutes=20), **KEY, **WIN
        )
        await session.commit()
    async with session_factory() as session:
        rows = (await session.execute(select(AnalyticsSnapshot))).scalars().all()
        assert len(rows) == 1
        assert rows[0].value == {"v": 2}
        assert rows[0].computed_at == NOW + timedelta(minutes=20)


async def test_stale_row_returned_but_not_fresh(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(
            session, workspace_id=ws.id, value={"v": 1}, now=NOW - timedelta(hours=1), **KEY, **WIN
        )
        await session.commit()
    async with session_factory() as session:
        value, row = await fetch_snapshot(session, workspace_id=ws.id, **KEY, **WIN)
        assert value == {"v": 1}
        assert not snapshot_is_fresh(row, TTL, NOW)


async def test_scope_key_mismatch_never_hits(session_factory, workspace_factory):
    """跨权限绝不命中:ws_admin 行对 projects:* 查询不可见(§2.5 R3,T33 ②)。"""
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(
            session,
            workspace_id=ws.id,
            value={"v": "admin"},
            now=NOW,
            metric_key="throughput",
            scope_key="ws_admin",
            dimensions=KEY["dimensions"],
            **WIN,
        )
        await session.commit()
    async with session_factory() as session:
        value, row = await fetch_snapshot(
            session,
            workspace_id=ws.id,
            metric_key="throughput",
            scope_key="projects:xyz",
            dimensions=KEY["dimensions"],
            **WIN,
        )
        assert value is None and row is None
        # 反向也不命中:exec scope 与 ws_admin 不共享
        value2, _ = await fetch_snapshot(
            session,
            workspace_id=ws.id,
            metric_key="throughput",
            scope_key="exec:pa:aa",
            dimensions=KEY["dimensions"],
            **WIN,
        )
        assert value2 is None


async def test_dimensions_mismatch_does_not_hit(session_factory, workspace_factory):
    """calendar_timezone 入维度指纹:不同时区分桶缓存不共享(§2.2.3 R3)。"""
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={"tz": "utc"}, now=NOW, **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, _ = await fetch_snapshot(
            session,
            workspace_id=ws.id,
            metric_key="throughput",
            scope_key="projects:abc",
            dimensions={"granularity": "day", "calendar_timezone": "Asia/Shanghai"},
            **WIN,
        )
        assert value is None


async def test_window_mismatch_does_not_hit(session_factory, workspace_factory):
    ws = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws.id, value={}, now=NOW, **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, _ = await fetch_snapshot(
            session,
            workspace_id=ws.id,
            window_start=NOW - timedelta(days=30),
            window_end=NOW,
            metric_key=KEY["metric_key"],
            scope_key=KEY["scope_key"],
            dimensions=KEY["dimensions"],
        )
        assert value is None


async def test_workspace_isolation(session_factory, workspace_factory):
    ws_a = await workspace_factory()
    ws_b = await workspace_factory()
    async with session_factory() as session:
        await upsert_snapshot(session, workspace_id=ws_a.id, value={"v": 1}, now=NOW, **KEY, **WIN)
        await session.commit()
    async with session_factory() as session:
        value, _ = await fetch_snapshot(session, workspace_id=ws_b.id, **KEY, **WIN)
        assert value is None  # 多租户隔离
