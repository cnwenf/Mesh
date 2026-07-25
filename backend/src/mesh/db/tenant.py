"""Multi-tenant building blocks (README §6.2 — 唯一权威).

Query convention
    Every tenant-scoped query filters on ``workspace_id`` — use
    :func:`tenant_scope` (or the model's ``workspace_id`` column directly) so
    the convention has one obvious spelling.

Migration templates
    Tenant tables must expose ``UNIQUE (workspace_id, id)`` for composite-FK
    referencing, and references to other tenants' objects must be composite
    FKs ``(workspace_id, ref_id) → target(workspace_id, id)``. Use
    :func:`unique_workspace_id_sql` / :func:`composite_fk_sql` in migrations so
    every module spells the pattern the same way.

RLS
    PostgreSQL RLS is defense-in-depth on top of the composite FKs. Tenant
    tables get a policy ``USING (workspace_id = current_setting('mesh.workspace_id')::uuid)``
    and transactions set the GUC via :func:`set_tenant_context`.

Global tables
    ``users`` and ``external_identities`` are global identity tables — they do
    NOT carry a ``workspace_id`` ownership column and are exempt from workspace
    RLS (§6.1 全局身份层 / §6.2 rule 5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKeyConstraint, Select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

TENANT_GUC = "mesh.workspace_id"

# §6.1/§6.2-5: global identity tables, exempt from workspace RLS.
GLOBAL_TABLES: frozenset[str] = frozenset({"users", "external_identities"})


def unique_workspace_id_sql(table: str) -> str:
    """Migration template: the ``UNIQUE (workspace_id, id)`` every tenant table needs."""
    return f"CREATE UNIQUE INDEX uq_{table}_ws_id ON {table} (workspace_id, id)"


def composite_fk_sql(table: str, ref_table: str, ref_column: str) -> str:
    """Migration template: same-tenant composite FK to ``ref_table(workspace_id, id)``."""
    return (
        f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{ref_column}_{ref_table} "
        f"FOREIGN KEY (workspace_id, {ref_column}) REFERENCES {ref_table} (workspace_id, id)"
    )


def composite_fk_constraint(
    ref_table: str, local_ref_column: str, *, name: str | None = None
) -> ForeignKeyConstraint:
    """ORM template: composite FK ``(workspace_id, <col>) → ref(workspace_id, id)``."""
    constraint_name = name or f"{local_ref_column}_{ref_table}"
    return ForeignKeyConstraint(
        ("workspace_id", local_ref_column),
        (f"{ref_table}.workspace_id", f"{ref_table}.id"),
        name=constraint_name,
    )


def tenant_scope(stmt: Select, model: type, workspace_id: uuid.UUID) -> Select:
    """Apply the mandatory ``workspace_id`` filter to a query (query convention)."""
    return stmt.where(model.workspace_id == workspace_id)


async def set_tenant_context(
    conn: AsyncConnection | AsyncSession, workspace_id: uuid.UUID
) -> None:
    """Set the ``mesh.workspace_id`` GUC for the current transaction (RLS)."""
    await conn.execute(
        text("SELECT set_config(:guc, :ws, true)"),
        {"guc": TENANT_GUC, "ws": str(workspace_id)},
    )
