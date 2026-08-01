"""Migration 0035 contract: normalization function, projection, index set.

Mirrors the schema validation assertions (docs/specs/validation/
schema_r2_validation.sql T37) against the migrated test database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.unit

# The exact 11 search indexes (search-command-palette.md §2.2 R4-M2):
# 9 mesh_search_norm expression indexes + 2 member projection column indexes.
SEARCH_INDEXES = frozenset(
    {
        "idx_members_search_name_trgm",
        "idx_members_search_name_prefix",
        "idx_issues_title_trgm",
        "idx_issues_title_prefix",
        "idx_issues_identifier_prefix",
        "idx_projects_name_trgm",
        "idx_projects_name_prefix",
        "idx_views_name_trgm",
        "idx_views_name_prefix",
        "idx_chat_sessions_title_trgm",
        "idx_chat_sessions_title_prefix",
    }
)


async def test_mesh_search_norm_behavior(db_session):
    rows = (
        await db_session.execute(
            text(
                "SELECT public.mesh_search_norm('José') AS accented, "
                "public.mesh_search_norm('ZHANG Wei') AS uppercased, "
                "public.mesh_search_norm(NULL) AS null_in"
            )
        )
    ).one()
    assert rows.accented == "jose"
    assert rows.uppercased == "zhang wei"
    assert rows.null_in is None


async def test_mesh_search_norm_is_immutable(db_session):
    volatility = (
        await db_session.execute(
            text(
                "SELECT provolatile FROM pg_proc "
                "WHERE proname = 'mesh_search_norm' "
                "AND pronamespace = 'public'::regnamespace"
            )
        )
    ).scalar_one()
    # 'i' = IMMUTABLE — the expression-index prerequisite. asyncpg returns
    # the "char" catalog type as bytes.
    assert volatility in ("i", b"i")


async def test_search_index_set_exact(db_session):
    names = {
        row[0]
        for row in (
            await db_session.execute(
                text("SELECT indexname FROM pg_indexes WHERE indexname = ANY(:names)"),
                {"names": list(SEARCH_INDEXES)},
            )
        )
    }
    assert names == SEARCH_INDEXES, f"missing search indexes: {SEARCH_INDEXES - names}"


async def test_key_index_definitions(db_session):
    defs = {
        row.name: row.defn
        for row in (
            await db_session.execute(
                text(
                    "SELECT indexname AS name, indexdef AS defn FROM pg_indexes "
                    "WHERE indexname IN ("
                    "'idx_members_search_name_prefix','idx_issues_title_trgm',"
                    "'idx_issues_identifier_prefix','idx_members_search_name_trgm')"
                )
            )
        )
    }
    member_prefix = defs["idx_members_search_name_prefix"]
    assert "text_pattern_ops" in member_prefix
    assert "WHERE (status <> 'removed'::text)" in member_prefix

    title_trgm = defs["idx_issues_title_trgm"]
    assert "gin_trgm_ops" in title_trgm
    assert "mesh_search_norm(title)" in title_trgm
    assert "WHERE (deleted_at IS NULL)" in title_trgm

    identifier_prefix = defs["idx_issues_identifier_prefix"]
    assert "mesh_search_norm(identifier)" in identifier_prefix
    assert "text_pattern_ops" in identifier_prefix

    member_trgm = defs["idx_members_search_name_trgm"]
    assert "gin_trgm_ops" in member_trgm


async def test_prefix_query_naturally_hits_pattern_index(db_session):
    """Realistic-scale natural planning picks the pattern index (T37-5b).

    Under tiny-table statistics the planner may legitimately prefer the
    workspace support index; with a realistic member count + ANALYZE the
    selective prefix predicate must naturally win. Failure here means the
    index is ineffective for the real query, not a planner preference.
    """
    ws_id = "11111111-1111-1111-1111-111111111111"
    await db_session.execute(
        text(
            "INSERT INTO workspaces (id, name, slug) "
            "VALUES (:ws, 'search-migration-ws', 'search-migration-ws') "
            "ON CONFLICT DO NOTHING"
        ),
        {"ws": ws_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, display_name) "
            "SELECT gen_random_uuid(), 'bulk-' || g || '@x.dev', 'Bulk User ' || g "
            "FROM generate_series(1, 3000) g"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO members (workspace_id, member_type, user_id, role, "
            "display_override, search_name) "
            "SELECT :ws, 'human', u.id, 'member', 'Bulk ' || u.email, "
            "public.mesh_search_norm('Bulk ' || u.email) "
            "FROM (SELECT id, email FROM users WHERE email LIKE 'bulk-%') u"
        ),
        {"ws": ws_id},
    )
    await db_session.execute(text("ANALYZE members"))
    await db_session.commit()

    plan = "\n".join(
        row[0]
        for row in (
            await db_session.execute(
                text(
                    "EXPLAIN SELECT id FROM members "
                    "WHERE workspace_id = :ws "
                    "AND status <> 'removed' "
                    "AND search_name LIKE public.mesh_search_norm('jo') || '%'"
                ),
                {"ws": ws_id},
            )
        )
    )
    assert "idx_members_search_name_prefix" in plan


async def test_members_search_name_column(db_session):
    column = (
        await db_session.execute(
            text(
                "SELECT column_default, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'members' AND column_name = 'search_name'"
            )
        )
    ).one()
    assert column.is_nullable == "NO"
    assert "''" in column.column_default
