"""app role: restricted non-owner login role for API/gateway + grants (M1)

PostgreSQL RLS does not apply to the table owner (here ``mesh``, which is also a
superuser), so the tenant policies on ``realtime_channels``/``realtime_events``
were a no-op for every application connection. The API and realtime gateway now
connect as the restricted, non-owner ``mesh_app`` role so RLS is enforced on the
app path (README §6.2 rule 5). The worker keeps the owner role for cross-tenant
relay / projector / retention. The role password is operator configuration
(``MESH_APP_DB_PASSWORD``; a local-dev default like the documented ``mesh:mesh``).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-25
"""
from __future__ import annotations

import os

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "mesh_app"


def _app_role_password() -> str:
    # Operator-supplied configuration (not user input); the local-dev default is
    # documented in .env.example. Escaped before embedding in the DDL literal.
    return os.environ.get("MESH_APP_DB_PASSWORD", "mesh_app")


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    role = APP_ROLE
    password = _quoted(_app_role_password())
    # Idempotent: create the restricted role if absent, then make it a login role.
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
        f"THEN CREATE ROLE {role}; END IF; END $$"
    )
    op.execute(
        f"ALTER ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN PASSWORD {password}"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    # Realtime tables: the app reads only (writes go through the worker/projector,
    # which connects as the owner).
    op.execute(f"GRANT SELECT ON realtime_channels, realtime_events TO {role}")
    # Business + outbox tables: the app reads/writes (the outbox is the sole write
    # path, §6.6).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON workspaces, outbox_events TO {role}")
    # Sequences (realtime_events identity + any future sequences).
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    # Future owner-created tables/sequences get app privileges automatically so
    # later module migrations don't have to remember to GRANT.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}"
    )


def downgrade() -> None:
    role = APP_ROLE
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {role}"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
    op.execute(f"DROP ROLE IF EXISTS {role}")
