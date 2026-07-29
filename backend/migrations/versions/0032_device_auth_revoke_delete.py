"""auth: revoke the unused DELETE grant on device_authorizations (MES-80 C1)

Least privilege (audit C1): the app role never deletes device grants — state
transitions are UPDATEs and the reaper invalidates in place; terminal rows
stay for audit/code-space history. 0030 granted DELETE out of template habit;
take it back.

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-30
"""
from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def upgrade() -> None:
    op.execute(f"REVOKE DELETE ON device_authorizations FROM {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT DELETE ON device_authorizations TO {APP_ROLE}")
