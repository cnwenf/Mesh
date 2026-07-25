"""Shared fixtures: a real PostgreSQL 16 test database (migrated) and Redis.

Every test — unit and e2e — runs against real services; nothing on the
contract paths is mocked.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
SPECS_DIR = REPO_ROOT / "docs" / "specs"

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/mesh_test"
DEFAULT_TEST_REDIS_URL = "redis://127.0.0.1:6390/1"


def get_test_database_url() -> str:
    return os.environ.get("MESH_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def get_test_redis_url() -> str:
    return os.environ.get("MESH_TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL)


def _database_name(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _maintenance_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/postgres"


async def _ensure_database(url: str) -> None:
    engine = create_async_engine(_maintenance_url(url), isolation_level="AUTOCOMMIT")
    name = _database_name(url)
    async with engine.connect() as conn:
        exists = (
            await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
        ).scalar()
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


def _run_migrations(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def db_url() -> str:
    return get_test_database_url()


@pytest.fixture(scope="session")
def redis_url() -> str:
    return get_test_redis_url()


@pytest.fixture(scope="session", autouse=True)
def provision_database(db_url: str) -> None:
    """Create the test database once per session and migrate it to head."""
    asyncio.run(_ensure_database(db_url))
    _run_migrations(db_url)


@pytest.fixture(scope="session")
def session_factory(db_url: str):
    engine = create_async_engine(db_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    # dispose in a loop — the session loop may already be closing
    try:
        asyncio.get_event_loop().run_until_complete(engine.dispose())
    except RuntimeError:
        pass


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_url: str):
    """TRUNCATE all tables before each test — isolation regardless of fixtures used."""
    import mesh.db.models  # noqa: F401 — register all models on Base.metadata
    from mesh.db.base import Base

    engine = create_async_engine(db_url)
    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_url: str) -> AsyncIterator[AsyncSession]:
    """A session bound to the test database."""
    engine = create_async_engine(db_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def redis_client(redis_url: str):
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def workspace_factory(
    session_factory,
) -> Callable[..., Awaitable]:
    """Create committed workspaces visible to every session."""
    from mesh.db.models.workspace import Workspace

    async def _create(name: str = "Workspace", slug: str | None = None):
        async with session_factory() as session, session.begin():
            workspace = Workspace(name=name, slug=slug or f"ws-{uuid.uuid4().hex[:12]}")
            session.add(workspace)
        return workspace

    return _create
