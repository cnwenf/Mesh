"""Model ↔ migration drift guard (acceptance review M4).

Compares the ORM metadata against the migrated database with Alembic
autogenerate. Real drift (missing/extra tables, columns, constraints,
nullability changes) fails the test. Two known rendering-only diff classes
are tolerated by design and nothing else:

1. unique-constraint RENAME pairs — the baseline migration uses raw spec DDL
   (PostgreSQL assigns default constraint names) while the models declare
   naming-convention names; a matched remove+add pair over identical columns
   is a rename, not a schema change;
2. TEXT ↔ VARCHAR modify_type — tolerated defensively (models use PG TEXT).
"""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine


def _constraint_signature(diff) -> tuple:
    """(table, sorted column names) — identity of a constraint modulo its name."""
    constraint = diff[1]
    columns = tuple(sorted(column.name for column in constraint.columns))
    return (constraint.table.name, columns)


def _classify(diffs: list) -> list:
    """Return the list of UNEXPLAINED diffs (real drift candidates)."""
    removes: dict[tuple, object] = {}
    unexplained: list = []
    for diff in diffs:
        kind = diff[0]
        if kind == "remove_constraint":
            removes[_constraint_signature(diff)] = diff
        elif kind == "add_constraint":
            # A rename pair: same table+columns as a preceding remove.
            if removes.pop(_constraint_signature(diff), None) is None:
                unexplained.append(diff)
        elif kind == "modify_type":
            old_type = str(diff[4]).upper()
            new_type = str(diff[5]).upper()
            text_like = {"TEXT", "VARCHAR", "STRING"}
            if not ({old_type, new_type} <= text_like):
                unexplained.append(diff)
        else:
            unexplained.append(diff)
    # Removes without a matching add are real drift (dropped constraints).
    unexplained.extend(removes.values())
    return unexplained


async def test_models_match_migrated_schema(db_url):
    import mesh.db.models  # noqa: F401 — register models on Base.metadata
    from mesh.db.base import Base

    engine = create_async_engine(db_url)

    def _compare(sync_conn) -> list:
        context = MigrationContext.configure(sync_conn)
        return compare_metadata(context, Base.metadata)

    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(_compare)
    finally:
        await engine.dispose()

    drift = _classify(diffs)
    assert not drift, (
        "ORM models drifted from the migrated schema — regenerate a migration "
        f"or fix the models. Unexplained diffs: {drift!r}"
    )
