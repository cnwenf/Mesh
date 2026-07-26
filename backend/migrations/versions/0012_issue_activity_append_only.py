"""issue_activity: revoke app-role write-back (MES-46 M2)

issue_activity is the immutable change trail for issues; the service layer only
INSERTs into it (issue.md §5.6) and reads it back. Migration 0009 granted the
app role ``SELECT, INSERT, UPDATE, DELETE`` in a single statement, leaving
UPDATE / DELETE available to a compromised or injected app role — the trail
could be rewritten or erased. Strip the write-back (auth.md §5.5 least
privilege), matching the ``REVOKE UPDATE, DELETE ON audit_logs`` in 0004.

Deliberate deviation from audit_logs' SECOND layer (a BEFORE UPDATE OR DELETE
reject trigger): audit_logs can carry that trigger because its member FK is
RESTRICT, so nothing legitimately mutates its rows. issue_activity instead has
``ON DELETE CASCADE`` (issue_id → issues) and ``ON DELETE SET NULL``
(actor_member_id → members) referential actions. A blanket reject trigger fires
on exactly those FK-driven row changes and breaks issue deletion and member
physical-delete (README §9 T18). The privilege revocation alone closes the hole:
mesh_app can no longer issue a direct UPDATE / DELETE against the trail, while
FK cascade / SET NULL — system-enforced referential actions that never check
table grants — keep working.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-26
"""
from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    # 0009 granted SELECT, INSERT, UPDATE, DELETE in one statement; the trail
    # only needs SELECT + INSERT (auth.md §5.5, audit_logs parity from 0004).
    op.execute(f"REVOKE UPDATE, DELETE ON issue_activity FROM {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT UPDATE, DELETE ON issue_activity TO {APP_ROLE}")
