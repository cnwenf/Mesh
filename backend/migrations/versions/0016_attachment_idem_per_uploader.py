"""attachment: scope the Idempotency-Key unique index per uploader (F6)

The original ``uq_attachments_idem (workspace_id, idempotency_key)`` let a
client replay ANOTHER member's client-generated key and receive their first
record's response (uploader/file name/links leak), and two concurrent
same-key inserts collided into a bare IntegrityError → generic 500 instead of
README §6.5 "重复投递返回首次结果".

Re-scoped to ``(workspace_id, uploader_id, idempotency_key)``: the key now
de-duplicates per requesting member (the service replays per uploader and
catches the concurrent-insert conflict to replay the winner). Partial index —
NULL keys (no Idempotency-Key header) never collide.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-27
"""
from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_attachments_idem")
    op.execute(
        "CREATE UNIQUE INDEX uq_attachments_idem "
        "ON attachments(workspace_id, uploader_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_attachments_idem")
    op.execute(
        "CREATE UNIQUE INDEX uq_attachments_idem "
        "ON attachments(workspace_id, idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
