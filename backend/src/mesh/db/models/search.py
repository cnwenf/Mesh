"""Schema objects owned by the search migrations (search-command-palette.md §2.2).

The search module's queryable projections (members.search_name, the
expression indexes, the sync triggers and mesh_search_norm) live on the
owning tables' models / in migration 0035. This module declares the ONE
standalone table the migration maintains: the extension ledger.

It is registered on Base.metadata purely so the model ↔ migration drift
gate sees the table; the ORM never reads or writes it — its rows record
which extensions migration 0035 itself created, so the downgrade drops
exactly and only those.
"""

from __future__ import annotations

from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import TEXT

from mesh.db.base import Base

mesh_search_ext_ledger = Table(
    "mesh_search_ext_ledger",
    Base.metadata,
    Column("name", TEXT, primary_key=True),
)
