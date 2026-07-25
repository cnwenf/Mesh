"""Async engine and session factories (SQLAlchemy 2.x + asyncpg)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mesh.config import Settings


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Create the async engine for ``settings.database_url``."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async engine from a raw URL (used by tests and tools)."""
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
