"""Search migration contract: normalization, projection, indexes, and repair.

Mirrors the schema validation assertions (docs/specs/validation/
schema_r2_validation.sql T37) against the migrated test database.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

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

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _sync_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")


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


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO mesh_search_ext_ledger(name) VALUES ('security-probe')",
        "UPDATE mesh_search_ext_ledger SET name = name WHERE FALSE",
        "DELETE FROM mesh_search_ext_ledger WHERE FALSE",
    ],
)
async def test_app_role_cannot_mutate_extension_ledger(db_session, statement):
    """The deployment role cannot tamper with downgrade ownership evidence."""
    await db_session.execute(text("SET LOCAL ROLE mesh_app"))
    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(text(statement))
    assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"
    # The failed statement aborts this test transaction; rollback also resets
    # SET LOCAL ROLE so fixture teardown continues as the migration owner.
    await db_session.rollback()


async def test_extension_ledger_remains_owned_by_migration_role(db_session):
    row = (
        await db_session.execute(
            text(
                "SELECT current_user AS migration_user, "
                "pg_get_userbyid(c.relowner) AS table_owner "
                "FROM pg_class c "
                "WHERE c.oid = 'mesh_search_ext_ledger'::regclass"
            )
        )
    ).one()
    assert row.table_owner == row.migration_user


def test_0037_backfills_legacy_0036_search_contract(db_url):
    """A previously upgraded 0036 database receives the complete contract."""
    database_name = f"mesh_search_0037_{uuid.uuid4().hex}"
    database_url = f"{db_url.rsplit('/', 1)[0]}/{database_name}"
    maintenance_url = f"{_sync_url(db_url).rsplit('/', 1)[0]}/postgres"
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    database_engine = None

    try:
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        config = _alembic_config(database_url)
        command.upgrade(config, "0036")
        database_engine = create_engine(_sync_url(database_url))

        # Reproduce databases that consumed the original 0035 before these
        # two late search-contract functions and the ACL fixes existed.
        with database_engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP FUNCTION public.mesh_search_text_score(TEXT, TEXT)"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION public.mesh_resync_search_name(TEXT, UUID)"
            )
            connection.exec_driver_sql(
                "GRANT ALL PRIVILEGES ON TABLE mesh_search_ext_ledger "
                "TO PUBLIC, mesh_app"
            )
            connection.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION public.mesh_member_search_name(UUID) "
                "TO PUBLIC, mesh_app"
            )
            workspace_id = connection.execute(
                text(
                    "INSERT INTO workspaces (name, slug) "
                    "VALUES ('Legacy ACL', 'legacy-acl') RETURNING id"
                )
            ).scalar_one()
            user_id = connection.execute(
                text(
                    "INSERT INTO users (email, display_name) "
                    "VALUES ('legacy-acl@example.test', 'Before Name') RETURNING id"
                )
            ).scalar_one()
            member_id = connection.execute(
                text(
                    "INSERT INTO members (workspace_id, member_type, user_id, role) "
                    "VALUES (:workspace_id, 'human', :user_id, 'member') RETURNING id"
                ),
                {"workspace_id": workspace_id, "user_id": user_id},
            ).scalar_one()
            legacy_helper_acl_count = connection.exec_driver_sql(
                "SELECT count(*) "
                "FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(COALESCE("
                "p.proacl, acldefault('f', p.proowner))) acl "
                "LEFT JOIN pg_roles r ON r.oid = acl.grantee "
                "WHERE p.oid = "
                "'public.mesh_member_search_name(uuid)'::regprocedure "
                "AND acl.privilege_type = 'EXECUTE' "
                "AND (acl.grantee = 0 OR r.rolname = 'mesh_app')"
            ).scalar_one()
            assert legacy_helper_acl_count == 2

        command.upgrade(config, "0037")

        with database_engine.connect() as connection:
            repaired = connection.exec_driver_sql(
                "SELECT "
                "to_regprocedure('public.mesh_search_text_score(text,text)') IS NOT NULL, "
                "to_regprocedure('public.mesh_resync_search_name(text,uuid)') IS NOT NULL, "
                "public.mesh_search_text_score('Project Alpha', 'pro'), "
                "public.mesh_resync_search_name('all', NULL)"
            ).one()
            assert repaired == (True, True, 80, 0)

            unauthorized_acl_count = connection.exec_driver_sql(
                "SELECT count(*) "
                "FROM pg_class c "
                "CROSS JOIN LATERAL aclexplode(COALESCE("
                "c.relacl, acldefault('r', c.relowner))) acl "
                "LEFT JOIN pg_roles r ON r.oid = acl.grantee "
                "WHERE c.oid = 'mesh_search_ext_ledger'::regclass "
                "AND (acl.grantee = 0 OR r.rolname = 'mesh_app')"
            ).scalar_one()
            assert unauthorized_acl_count == 0

            unauthorized_helper_acl_count = connection.exec_driver_sql(
                "SELECT count(*) "
                "FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(COALESCE("
                "p.proacl, acldefault('f', p.proowner))) acl "
                "LEFT JOIN pg_roles r ON r.oid = acl.grantee "
                "WHERE p.oid = "
                "'public.mesh_member_search_name(uuid)'::regprocedure "
                "AND acl.privilege_type = 'EXECUTE' "
                "AND (acl.grantee = 0 OR r.rolname = 'mesh_app')"
            ).scalar_one()
            assert unauthorized_helper_acl_count == 0

            owner_projection = connection.execute(
                text("SELECT public.mesh_member_search_name(:member_id)"),
                {"member_id": member_id},
            ).scalar_one()
            assert owner_projection == "before name"

        # A global app-role user update carries no tenant GUC. Its SECURITY
        # DEFINER trigger must still reach the owner-only helper and update the
        # projection even though the caller cannot invoke that helper directly.
        with database_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE mesh_app")
            result = connection.execute(
                text("UPDATE users SET display_name = 'After Rename' WHERE id = :user_id"),
                {"user_id": user_id},
            )
            assert result.rowcount == 1

        with database_engine.connect() as connection:
            updated_projection = connection.execute(
                text("SELECT search_name FROM members WHERE id = :member_id"),
                {"member_id": member_id},
            ).scalar_one()
            assert updated_projection == "after rename"

        with database_engine.connect() as connection:
            transaction = connection.begin()
            connection.exec_driver_sql("SET LOCAL ROLE mesh_app")
            with pytest.raises(DBAPIError) as exc_info:
                connection.execute(
                    text("SELECT public.mesh_member_search_name(:member_id)"),
                    {"member_id": member_id},
                )
            assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"
            transaction.rollback()

        with database_engine.connect() as connection:
            transaction = connection.begin()
            connection.exec_driver_sql("SET LOCAL ROLE mesh_app")
            with pytest.raises(DBAPIError) as exc_info:
                connection.exec_driver_sql(
                    "INSERT INTO mesh_search_ext_ledger(name) VALUES ('security-probe')"
                )
            assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"
            transaction.rollback()

        # The security revokes must not lock out the owning migration role.
        # A full 0037 -> 0034 downgrade reads the ledger and removes exactly
        # the extensions that 0035 created.
        database_engine.dispose()
        database_engine = None
        command.downgrade(config, "0034")
        database_engine = create_engine(_sync_url(database_url))
        with database_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "0034"
            assert connection.exec_driver_sql(
                "SELECT to_regclass('public.mesh_search_ext_ledger') IS NULL"
            ).scalar_one()
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'public.mesh_search_text_score(text,text)') IS NULL"
            ).scalar_one()
            assert connection.exec_driver_sql(
                "SELECT to_regprocedure("
                "'public.mesh_resync_search_name(text,uuid)') IS NULL"
            ).scalar_one()
    finally:
        if database_engine is not None:
            database_engine.dispose()
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(
                f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'
            )
        maintenance_engine.dispose()
