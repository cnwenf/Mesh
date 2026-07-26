"""App-path engine factory selects the restricted role when configured (M1)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mesh.config import load_settings
from mesh.db.engine import (
    app_database_url,
    create_app_engine_from_settings,
    falls_back_to_owner,
    statement_timeout_connect_args,
)

REQUIRED = {
    "database_url": "postgresql+asyncpg://owner:p@h:5432/db",
    "redis_url": "redis://h:6379/0",
}


def test_app_database_url_falls_back_to_owner_url():
    settings = load_settings(**REQUIRED)
    assert app_database_url(settings) == REQUIRED["database_url"]


def test_app_database_url_prefers_restricted_url():
    restricted = "postgresql+asyncpg://mesh_app:pw@h:5432/db"
    settings = load_settings(**REQUIRED, app_database_url=restricted)
    assert app_database_url(settings) == restricted


def test_falls_back_to_owner_when_app_url_unset(monkeypatch):
    monkeypatch.delenv("MESH_APP_DATABASE_URL", raising=False)
    assert falls_back_to_owner(load_settings(**REQUIRED)) is True


def test_does_not_fall_back_when_restricted_url_set():
    settings = load_settings(**REQUIRED, app_database_url="postgresql+asyncpg://app:p@h:5432/db")
    assert falls_back_to_owner(settings) is False


def test_create_app_engine_builds_on_both_paths(monkeypatch):
    # Engines are lazy (no connection at creation); both branches must build.
    monkeypatch.delenv("MESH_APP_DATABASE_URL", raising=False)
    owner_engine = create_app_engine_from_settings(load_settings(**REQUIRED))
    asyncio.run(owner_engine.dispose())
    restricted = create_app_engine_from_settings(
        load_settings(**REQUIRED, app_database_url="postgresql+asyncpg://app:p@h:5432/db")
    )
    asyncio.run(restricted.dispose())


# --- L7: statement_timeout backstop on the app path ---


def test_statement_timeout_connect_args_default_and_disabled():
    default = statement_timeout_connect_args(load_settings(**REQUIRED))
    assert default == {"server_settings": {"statement_timeout": "30000"}}
    disabled = statement_timeout_connect_args(
        load_settings(**REQUIRED, app_statement_timeout=timedelta(0))
    )
    assert disabled == {}


def test_default_app_statement_timeout_is_30s():
    assert load_settings(**REQUIRED).app_statement_timeout == timedelta(seconds=30)


async def test_app_engine_enforces_statement_timeout_on_real_connection(db_url):
    """SHOW statement_timeout reflects the setting on an actual session."""
    engine = create_app_engine_from_settings(
        load_settings(
            database_url=db_url,
            redis_url="redis://h:6379/0",
            app_statement_timeout=timedelta(seconds=45),
        )
    )
    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SHOW statement_timeout"))).scalar()
        assert value == "45s"
    finally:
        await engine.dispose()


async def test_app_engine_cancels_query_exceeding_timeout(db_url):
    """A statement over the timeout is cancelled by PostgreSQL (L7 backstop)."""
    engine = create_app_engine_from_settings(
        load_settings(
            database_url=db_url,
            redis_url="redis://h:6379/0",
            app_statement_timeout=timedelta(milliseconds=50),
        )
    )
    try:
        with pytest.raises(DBAPIError, match="statement timeout"):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT pg_sleep(5)"))
    finally:
        await engine.dispose()


async def test_disabled_statement_timeout_leaves_pg_default(db_url):
    engine = create_app_engine_from_settings(
        load_settings(
            database_url=db_url,
            redis_url="redis://h:6379/0",
            app_statement_timeout=timedelta(0),
        )
    )
    try:
        async with engine.connect() as conn:
            value = (await conn.execute(text("SHOW statement_timeout"))).scalar()
        assert value == "0"  # PostgreSQL default: no timeout
    finally:
        await engine.dispose()
