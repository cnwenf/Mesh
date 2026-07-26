"""Async engine and session factories (SQLAlchemy 2.x + asyncpg)."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from mesh.config import Settings

logger = logging.getLogger("mesh.db.engine")


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Create the async engine for ``settings.database_url``."""
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def app_database_url(settings: Settings) -> str:
    """DB URL for the API/gateway app path: restricted role when configured (M1)."""
    return settings.app_database_url or settings.database_url


def falls_back_to_owner(settings: Settings) -> bool:
    """True when the app path would use the owner role (RLS NOT enforced)."""
    return settings.app_database_url is None


def statement_timeout_connect_args(settings: Settings) -> dict[str, Any]:
    """asyncpg connect args enforcing a per-statement timeout on app sessions.

    Backstop for the API/gateway request path (L7): a runaway query is
    cancelled by PostgreSQL instead of holding a connection and a client
    request indefinitely. The worker path (relay / projector / retention) is
    deliberately exempt — its loops run long-lived cross-tenant maintenance.
    ``0`` disables the timeout.
    """
    timeout_ms = int(settings.app_statement_timeout.total_seconds() * 1000)
    if timeout_ms <= 0:
        return {}
    return {"server_settings": {"statement_timeout": str(timeout_ms)}}


def create_app_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Engine for the API/gateway app path (restricted role → RLS applies)."""
    if falls_back_to_owner(settings):
        # Fail-loud nudge (M1): without the restricted role the app connects as
        # the table owner, and PostgreSQL RLS silently does NOT apply.
        logger.warning(
            "MESH_APP_DATABASE_URL is not set — the app path is using the owner "
            "database role, so PostgreSQL RLS will not be enforced on app "
            "connections. Set it to the restricted mesh_app role URL."
        )
    return create_async_engine(
        app_database_url(settings),
        pool_pre_ping=True,
        connect_args=statement_timeout_connect_args(settings),
    )


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async engine from a raw URL (used by tests and tools)."""
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create the session factory bound to ``engine``."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
