"""chat security indexes: session-scoped idempotency + unique streaming guard

MES-67 security review fixes:

- **M1**: scope the message idempotency de-duplication by ``session_id`` so a
  member reusing another member's ``Idempotency-Key`` on their own session can
  neither read nor overwrite the other session's resources (the lookup in the
  service is session-scoped to match). Drops the workspace-only partial unique
  index and recreates it as ``(workspace_id, session_id, idempotency_key)``.

- **M4**: enforce the single-concurrency invariant (§3.5 / §5.3) at the
  database layer with a UNIQUE partial index on ``session_id`` where
  ``generation_status='streaming'``. The previous ``idx_chat_messages_streaming``
  was a plain (non-unique) index, so a check-then-act race under READ COMMITTED
  could land two streaming rows in one session. A losing concurrent insert now
  raises a unique violation that the service maps to 409 ``generation_in_progress``.

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-28
"""
from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # M1 — session-scoped idempotency de-dup.
    op.execute("DROP INDEX IF EXISTS uq_chat_messages_idempotency")
    op.execute(
        "CREATE UNIQUE INDEX uq_chat_messages_idempotency "
        "ON chat_messages(workspace_id, session_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    # M4 — authoritative single-concurrency guard.
    op.execute("DROP INDEX IF EXISTS idx_chat_messages_streaming")
    op.execute(
        "CREATE UNIQUE INDEX uq_chat_messages_one_streaming "
        "ON chat_messages(session_id) WHERE generation_status = 'streaming'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_chat_messages_one_streaming")
    op.execute(
        "CREATE INDEX idx_chat_messages_streaming "
        "ON chat_messages(session_id) WHERE generation_status = 'streaming'"
    )
    op.execute("DROP INDEX IF EXISTS uq_chat_messages_idempotency")
    op.execute(
        "CREATE UNIQUE INDEX uq_chat_messages_idempotency "
        "ON chat_messages(workspace_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
