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


def resolve_backend_dir(anchor_file: str | os.PathLike[str]) -> Path:
    """Backend package root derived from *anchor_file* alone — never the cwd.

    Ascends from the anchor's directory to the nearest ancestor containing a
    ``pyproject.toml`` (the backend project root), so any anchor in the tree
    (``tests/conftest.py``, ``tests/e2e/conftest.py``, deeper helpers) yields
    the same root regardless of nesting depth or the caller's working
    directory. A hand-counted ``dirname`` depth once pointed the e2e
    PYTHONPATH pin at ``backend/tests`` (MES-121), letting spawned servers
    resolve ``mesh`` from a stale editable install of another checkout — the
    anchor ascent removes the depth count entirely. Raises loudly if no
    manifest is found: silent mis-derivation is the failure being prevented.
    """
    current = Path(anchor_file).resolve().parent
    for _ in range(8):
        if (current / "pyproject.toml").is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise RuntimeError(
        f"no pyproject.toml found above {anchor_file!s}; cannot resolve the backend root"
    )


BACKEND_DIR = resolve_backend_dir(__file__)
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
    import asyncio

    from sqlalchemy.exc import DBAPIError

    import mesh.db.models  # noqa: F401 — register all models on Base.metadata
    from mesh.db.base import Base

    engine = create_async_engine(db_url)
    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    # E2e worker processes (relay / reaper) hold brief locks; TRUNCATE needs
    # AccessExclusive on everything, so the two can deadlock (40P01). Retry —
    # the worker transactions are short-lived and yield immediately.
    for attempt in range(5):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
            break
        except DBAPIError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "40P01" and attempt < 4:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            raise
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


@pytest_asyncio.fixture
async def member_factory(session_factory) -> Callable[..., Awaitable]:
    """Create a human member (with its user) in a workspace."""
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async def _create(workspace, *, role: str = "member", name: str | None = None):
        async with session_factory() as session, session.begin():
            user = User(
                email=f"u-{uuid.uuid4().hex[:12]}@mesh.test",
                display_name=name or "Test Member",
                password_hash="unused-in-tests",
            )
            session.add(user)
            await session.flush()
            member = Member(
                workspace_id=workspace.id,
                user_id=user.id,
                member_type="human",
                role=role,
                status="active",
            )
            session.add(member)
        return member

    return _create


# ---------------------------------------------------------------------------
# Object storage (attachment module — real MinIO, skipped when unreachable)
# ---------------------------------------------------------------------------

DEFAULT_TEST_STORAGE_ENDPOINT = "http://127.0.0.1:9000"


def get_test_storage_endpoint() -> str:
    return os.environ.get("MESH_TEST_STORAGE_ENDPOINT", DEFAULT_TEST_STORAGE_ENDPOINT)


def get_test_storage_credentials() -> tuple[str, str]:
    return (
        os.environ.get("MESH_STORAGE_ACCESS_KEY", ""),
        os.environ.get("MESH_STORAGE_SECRET_KEY", ""),
    )


@pytest.fixture(scope="session")
def storage_bucket_name() -> str:
    """One bucket per test session — tests never share object namespaces."""
    return f"mesh-test-{uuid.uuid4().hex[:12]}"


@pytest_asyncio.fixture(scope="session")
async def object_storage(storage_bucket_name: str):
    """A real ObjectStorage bound to MinIO; skips the test when unreachable."""
    import socket

    from mesh.attachment.storage import ObjectStorage, StorageConfig

    endpoint = get_test_storage_endpoint()
    host, _, port = endpoint.split("://", 1)[1].partition(":")
    try:
        with socket.create_connection((host, int(port or 80)), timeout=2):
            pass
    except OSError:
        pytest.skip(f"object storage not reachable at {endpoint}")
    access_key, secret_key = get_test_storage_credentials()
    storage = ObjectStorage(
        StorageConfig(
            endpoint=endpoint,
            public_endpoint=endpoint,
            region="us-east-1",
            access_key=access_key,
            secret_key=secret_key,
            bucket=storage_bucket_name,
        )
    )
    try:
        await storage.ensure_bucket()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"object storage bucket bootstrap failed: {exc}")
    yield storage


@pytest.fixture
def attachment_settings_kwargs(db_url: str, redis_url: str, storage_bucket_name: str) -> dict:
    """load_settings overrides wiring the API to the test services."""
    access_key, secret_key = get_test_storage_credentials()
    return {
        "database_url": db_url,
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "attachment-test-signing-secret-000000000000",
        "storage_endpoint": get_test_storage_endpoint(),
        "storage_public_endpoint": get_test_storage_endpoint(),
        "storage_access_key": access_key,
        "storage_secret_key": secret_key,
        "storage_bucket": storage_bucket_name,
    }
