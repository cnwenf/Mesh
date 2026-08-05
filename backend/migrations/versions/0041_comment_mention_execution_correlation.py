"""Keep pending mention triggers separate from canonical executions.

Revision ID: 0041
Revises: 0040
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
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
    # Existing values were enqueue outbox ids, not TaskExecution ids. Preserve
    # their correlation in the pending column and clear the public/canonical
    # field; the relay will populate it with the real execution on delivery.
    op.execute(
        """
        UPDATE comment_mentions
           SET pending_trigger_event_id = triggered_execution_id,
               triggered_execution_id = NULL
         WHERE triggered_execution_id IS NOT NULL
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
