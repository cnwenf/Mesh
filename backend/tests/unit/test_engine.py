"""App-path engine factory selects the restricted role when configured (M1)."""

import asyncio

from mesh.config import load_settings
from mesh.db.engine import app_database_url, create_app_engine_from_settings, falls_back_to_owner

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
