"""App-path engine factory selects the restricted role when configured (M1)."""

from mesh.config import load_settings
from mesh.db.engine import app_database_url

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
