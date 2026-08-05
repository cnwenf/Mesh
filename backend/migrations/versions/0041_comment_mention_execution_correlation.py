"""Keep pending mention triggers separate from canonical executions.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "comment_mentions",
        sa.Column("pending_trigger_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Older writers stored the execution.enqueue outbox id in the public field,
    # while partially upgraded writers may already have stored a canonical
    # TaskExecution id. Backfill materialized outbox rows first, then move only
    # values that are not same-workspace executions into the pending column.
    op.execute(
        """
        WITH materialized AS (
          SELECT DISTINCT ON (cm.id)
                 cm.id AS mention_id,
                 execution.id AS execution_id
            FROM comment_mentions AS cm
            JOIN outbox_events AS enqueue_event
              ON enqueue_event.workspace_id = cm.workspace_id
             AND enqueue_event.id = cm.triggered_execution_id
             AND enqueue_event.event_type = 'execution.enqueue'
            JOIN task_executions AS execution
              ON execution.workspace_id = cm.workspace_id
             AND execution.trigger = 'mention'
             AND (
                  execution.idempotency_key =
                    enqueue_event.payload->>'idempotency_key'
                  OR enqueue_event.idempotency_key =
                    'ws:' || cm.workspace_id::text || ':' || execution.idempotency_key
             )
           WHERE NOT EXISTS (
                 SELECT 1
                   FROM task_executions AS canonical
                  WHERE canonical.workspace_id = cm.workspace_id
                    AND canonical.id = cm.triggered_execution_id
           )
           ORDER BY cm.id, execution.queued_at, execution.id
        )
        UPDATE comment_mentions AS cm
           SET triggered_execution_id = materialized.execution_id,
               pending_trigger_event_id = NULL
          FROM materialized
         WHERE cm.id = materialized.mention_id
        """
    )
    op.execute(
        """
        UPDATE comment_mentions AS cm
           SET pending_trigger_event_id = cm.triggered_execution_id,
               triggered_execution_id = NULL
         WHERE cm.triggered_execution_id IS NOT NULL
           AND NOT EXISTS (
                 SELECT 1
                   FROM task_executions AS execution
                  WHERE execution.workspace_id = cm.workspace_id
                    AND execution.id = cm.triggered_execution_id
           )
        """
    )
    op.execute(
        """
        ALTER TABLE comment_mentions
          ADD CONSTRAINT comment_mentions_triggered_execution_id_task_executions
          FOREIGN KEY (workspace_id, triggered_execution_id)
          REFERENCES task_executions(workspace_id, id)
          ON DELETE SET NULL (triggered_execution_id)
        """
    )
    op.create_index(
        "idx_mentions_pending_trigger",
        "comment_mentions",
        ["workspace_id", "pending_trigger_event_id"],
        unique=False,
        postgresql_where=sa.text("pending_trigger_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_constraint(
        "comment_mentions_triggered_execution_id_task_executions",
        "comment_mentions",
        type_="foreignkey",
    )
    # Restore the old outbox-id representation when its event is still
    # available. Canonical ids without a surviving event are preserved rather
    # than discarded; the old schema had no FK and can safely retain them.
    op.execute(
        """
        WITH legacy_links AS (
          SELECT DISTINCT ON (cm.id)
                 cm.id AS mention_id,
                 enqueue_event.id AS enqueue_event_id
            FROM comment_mentions AS cm
            JOIN task_executions AS execution
              ON execution.workspace_id = cm.workspace_id
             AND execution.id = cm.triggered_execution_id
            JOIN outbox_events AS enqueue_event
              ON enqueue_event.workspace_id = cm.workspace_id
             AND enqueue_event.event_type = 'execution.enqueue'
             AND (
                  execution.idempotency_key =
                    enqueue_event.payload->>'idempotency_key'
                  OR enqueue_event.idempotency_key =
                    'ws:' || cm.workspace_id::text || ':' || execution.idempotency_key
             )
           ORDER BY cm.id, enqueue_event.created_at, enqueue_event.id
        )
        UPDATE comment_mentions AS cm
           SET triggered_execution_id = legacy_links.enqueue_event_id
          FROM legacy_links
         WHERE cm.id = legacy_links.mention_id
        """
    )
    op.execute(
        """
        UPDATE comment_mentions
           SET triggered_execution_id = pending_trigger_event_id
         WHERE triggered_execution_id IS NULL
           AND pending_trigger_event_id IS NOT NULL
        """
    )
    op.drop_index("idx_mentions_pending_trigger", table_name="comment_mentions")
    op.drop_column("comment_mentions", "pending_trigger_event_id")
