"""analytics: analytics_snapshots materialized cache (analytics.md §2.5).

Platform-capability increment (analytics.md §2.5/§2.6, README §6.2).
Single-head chain 0001 → 0027.

Tables (tenant-scoped, RLS fail-closed per README §6.2 rule 5):

- ``analytics_snapshots`` — read-side acceleration copy for the six metric
  families. The cache is NEVER the source of truth: on disagreement the
  recomputed value wins (§2.6). ``scope_key`` is part of the cache unique
  key (R3/R4) so aggregates computed for different visibility sets never
  share a row — cross-permission cache reuse is structurally impossible.
  ``dim_hash`` is a generated column (``md5(dimensions::text)``) so the
  dimensions JSONB (project/cycle/milestone/agent/granularity/from_category/
  tz/calendar_timezone fingerprints) can join the unique constraint without
  indexing JSONB directly. ``calendar_timezone`` lives in ``dimensions``,
  hence different-timezone bucketing never shares a row (§2.2.3 R3).

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"

TENANT_TABLES = ("analytics_snapshots",)
DML_TABLES = ", ".join(TENANT_TABLES)


def upgrade() -> None:
    # -- analytics_snapshots (analytics.md §2.5) --------------------------------
    op.execute(
        """
        CREATE TABLE analytics_snapshots (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          metric_key   TEXT NOT NULL
                       CHECK (metric_key IN ('cycle_time','velocity','throughput',
                                             'burndown','agent_stats')),
          scope_key    TEXT NOT NULL DEFAULT 'ws_admin',
          dimensions   JSONB NOT NULL DEFAULT '{}',
          dim_hash     TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED,
          window_start TIMESTAMPTZ NOT NULL,
          window_end   TIMESTAMPTZ NOT NULL,
          value        JSONB NOT NULL,
          computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT uq_snapshots_cache
            UNIQUE (workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)
        )
        """
    )
    # Composite-FK target index (README §6.2).
    op.execute(
        "CREATE UNIQUE INDEX uq_analytics_snapshots_ws_id "
        "ON analytics_snapshots (workspace_id, id)"
    )
    # Cache lookup + stale sweep (§2.5/§2.6).
    op.execute(
        "CREATE INDEX idx_snapshots_lookup ON analytics_snapshots "
        "(workspace_id, metric_key, scope_key, dim_hash, window_start, window_end)"
    )
    op.execute("CREATE INDEX idx_snapshots_stale ON analytics_snapshots (computed_at)")

    # -- RLS (defense-in-depth, README §6.2 rule 5) -----------------------------
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY mesh_{table}_tenant ON {table} "
            f"USING (workspace_id = current_setting('mesh.workspace_id')::uuid)"
        )

    # -- app-role privileges -----------------------------------------------------
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {DML_TABLES} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE analytics_snapshots")
