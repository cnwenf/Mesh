"""analytics_snapshots cache helpers (analytics.md §2.5/§2.6).

The cache is an acceleration copy, never the source of truth. A hit
requires metric/dimensions/window match AND ``scope_key`` equality with the
requester's visibility fingerprint — cross-permission reuse is impossible
because the lookup pins ``scope_key`` (a ``ws_admin`` row is never returned
to a non-admin, T33 ②). Overwrite-style refresh via ON CONFLICT on the
named unique constraint.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mesh.db.models.analytics import AnalyticsSnapshot

SNAPSHOT_CACHE_CONSTRAINT = "uq_snapshots_cache"


def snapshot_is_fresh(row: AnalyticsSnapshot, ttl: timedelta, now: datetime) -> bool:
    return now - row.computed_at <= ttl


async def fetch_snapshot(
    session,
    *,
    workspace_id,
    metric_key: str,
    scope_key: str,
    dimensions: dict,
    window_start: datetime,
    window_end: datetime,
) -> tuple[dict | None, AnalyticsSnapshot | None]:
    """Return (value, row); (None, None) on miss.

    The ``scope_key`` equality in the WHERE is the cross-permission guard:
    rows computed under a different visibility set never match.
    """
    stmt = select(AnalyticsSnapshot).where(
        AnalyticsSnapshot.workspace_id == workspace_id,
        AnalyticsSnapshot.metric_key == metric_key,
        AnalyticsSnapshot.scope_key == scope_key,
        AnalyticsSnapshot.dimensions == dimensions,
        AnalyticsSnapshot.window_start == window_start,
        AnalyticsSnapshot.window_end == window_end,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None, None
    return row.value, row


async def upsert_snapshot(
    session,
    *,
    workspace_id,
    metric_key: str,
    scope_key: str,
    dimensions: dict,
    window_start: datetime,
    window_end: datetime,
    value: dict,
    now: datetime,
) -> AnalyticsSnapshot:
    """Overwrite-style refresh: same key → replace value/computed_at."""
    stmt = pg_insert(AnalyticsSnapshot).values(
        workspace_id=workspace_id,
        metric_key=metric_key,
        scope_key=scope_key,
        dimensions=dimensions,
        window_start=window_start,
        window_end=window_end,
        value=value,
        computed_at=now,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint=SNAPSHOT_CACHE_CONSTRAINT,
        set_={
            "value": stmt.excluded.value,
            "computed_at": stmt.excluded.computed_at,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    return (
        await session.execute(
            select(AnalyticsSnapshot).where(
                AnalyticsSnapshot.workspace_id == workspace_id,
                AnalyticsSnapshot.metric_key == metric_key,
                AnalyticsSnapshot.scope_key == scope_key,
                AnalyticsSnapshot.dimensions == dimensions,
                AnalyticsSnapshot.window_start == window_start,
                AnalyticsSnapshot.window_end == window_end,
            )
        )
    ).scalar_one()
