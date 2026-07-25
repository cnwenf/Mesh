"""baseline: workspaces, outbox_events, realtime_channels, realtime_events + RLS

DDL mirrors docs/specs/validation/schema_r2_validation.sql verbatim
(workspaces / outbox / realtime sections, README §6.2 rule 8 / §6.6 / §6.7)
so the runtime schema and the spec validation script stay in lockstep.

Revision ID: 0001
Revises:
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- base layer: workspaces ------------------------------------------------
    op.execute(
        """
        CREATE TABLE workspaces (
          id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name               TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
          slug               TEXT NOT NULL,
          logo_url           TEXT NULL,
          timezone           TEXT NOT NULL DEFAULT 'UTC',
          settings           JSONB NOT NULL DEFAULT '{"default_locale": "en"}',
          inbox_issue_seq    BIGINT NOT NULL DEFAULT 0 CHECK (inbox_issue_seq >= 0),
          deleted_at         TIMESTAMPTZ NULL,
          created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_workspaces_slug ON workspaces(slug) WHERE deleted_at IS NULL")

    # -- transactional outbox (README §6.6) ------------------------------------
    op.execute(
        """
        CREATE TABLE outbox_events (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          workspace_id    UUID NOT NULL REFERENCES workspaces(id),
          event_type      TEXT NOT NULL,
          payload         JSONB NOT NULL,
          idempotency_key TEXT NULL UNIQUE,
          status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','published','failed')),
          delivery_attempts INT NOT NULL DEFAULT 0,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          published_at    TIMESTAMPTZ NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_outbox_pending ON outbox_events (created_at) WHERE status = 'pending'"
    )

    # -- realtime channels / events (README §6.7: tenant key + unique write path)
    op.execute(
        """
        CREATE TABLE realtime_channels (
          channel      TEXT NOT NULL,
          workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          last_seq     BIGINT NOT NULL DEFAULT 0,
          PRIMARY KEY (channel),
          UNIQUE (workspace_id, channel)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE realtime_events (
          id            BIGINT GENERATED ALWAYS AS IDENTITY,
          workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
          channel       TEXT NOT NULL,
          seq           BIGINT NOT NULL,
          event         TEXT NOT NULL,
          payload       JSONB NOT NULL,
          outbox_event_id UUID NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          published_at  TIMESTAMPTZ NULL,
          UNIQUE (channel, seq),
          UNIQUE (outbox_event_id),
          FOREIGN KEY (workspace_id, channel) REFERENCES realtime_channels(workspace_id, channel) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX idx_realtime_events_replay ON realtime_events (channel, seq)")
    op.execute(
        "CREATE INDEX idx_realtime_events_ws_created ON realtime_events (workspace_id, created_at)"
    )

    # -- RLS defense-in-depth (README §6.2 rule 5/8) ----------------------------
    op.execute("ALTER TABLE realtime_channels ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE realtime_events  ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY mesh_rt_channels_tenant ON realtime_channels
          USING (workspace_id = current_setting('mesh.workspace_id')::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY mesh_rt_events_tenant ON realtime_events
          USING (workspace_id = current_setting('mesh.workspace_id')::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS mesh_rt_events_tenant ON realtime_events")
    op.execute("DROP POLICY IF EXISTS mesh_rt_channels_tenant ON realtime_channels")
    op.execute("DROP TABLE IF EXISTS realtime_events")
    op.execute("DROP TABLE IF EXISTS realtime_channels")
    op.execute("DROP TABLE IF EXISTS outbox_events")
    op.execute("DROP TABLE IF EXISTS workspaces")
