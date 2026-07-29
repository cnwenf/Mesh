"""Analytics materialized cache (analytics.md §2.5 — this module owns it).

``analytics_snapshots`` is the ONLY table the analytics module owns. It is a
read-side acceleration copy: the source of truth for every metric is always
``issues`` / ``task_executions`` / ``execution_attempts`` / ``autopilot_runs``
and friends — on any disagreement the recomputed value wins (§2.6).

``scope_key`` is part of the cache key (§2.5 R3, §2.3.1 R4): aggregates
computed for different visibility sets never share a row, so cross-permission
cache reuse is structurally impossible. Values:

- ``ws_admin`` — full-workspace aggregate (admin/owner only);
- ``projects:<sha256(sorted visible project ids)>`` — issue metrics filtered
  to the requester's visible project set;
- ``project:<project_id>`` — single-project aggregate;
- ``exec:p<sha256(visible projects)>:a<sha256(visible agents)>`` — execution
  metrics under the unified visibility scope (§2.3.1).

``dim_hash`` is a generated column (``md5(dimensions::text)``) so the
dimensions JSONB can take part in the unique constraint without indexing
JSONB directly. ``calendar_timezone`` lives inside ``dimensions``, hence
bucketing under different timezones never shares a row (§2.2.3 R3).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import Computed

from mesh.db.base import Base

ANALYTICS_METRIC_KEY_VALUES = ("cycle_time", "velocity", "throughput", "burndown", "agent_stats")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"metric_key IN {ANALYTICS_METRIC_KEY_VALUES!r}",
            name="analytics_snapshots_metric_key_check",
        ),
        # Same (workspace, metric, visibility set, dimensions, window) keeps
        # exactly one snapshot (overwrite-style refresh). scope_key in the key
        # means cross-permission sharing is impossible (analytics.md §2.5).
        UniqueConstraint(
            "workspace_id",
            "metric_key",
            "scope_key",
            "dim_hash",
            "window_start",
            "window_end",
            name="uq_snapshots_cache",
        ),
        # Composite-FK target index (README §6.2).
        Index("uq_analytics_snapshots_ws_id", "workspace_id", "id", unique=True),
        # Cache lookup path (§2.5).
        Index(
            "idx_snapshots_lookup",
            "workspace_id",
            "metric_key",
            "scope_key",
            "dim_hash",
            "window_start",
            "window_end",
        ),
        # Worker sweep for stale snapshots (§2.6).
        Index("idx_snapshots_stale", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    scope_key: Mapped[str] = mapped_column(
        TEXT, nullable=False, server_default=text("'ws_admin'")
    )
    dimensions: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # STORED generated column — must mirror migration 0027 exactly
    # (`dim_hash TEXT GENERATED ALWAYS AS (md5(dimensions::text)) STORED`, no
    # NOT NULL) so the model↔migration drift guard (test_model_migration_drift)
    # stays green. Matches the autopilot_runs.total_tokens pattern.
    dim_hash: Mapped[str] = mapped_column(
        TEXT, Computed("md5(dimensions::text)", persisted=True)
    )
    window_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
