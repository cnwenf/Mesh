"""CLI ↔ OpenAPI contract drift detection (cli.md §5.4).

The committed docs/api/openapi.yaml must match what the app actually serves
(minus the stripped internal surfaces). A route added without regenerating
the artifact fails here — regenerate with backend/scripts/export_openapi.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mesh.config import load_settings

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OPENAPI_PATH = REPO_ROOT / "docs" / "api" / "openapi.yaml"

EXCLUDED_PREFIXES = ("/api/v1/daemon/",)
EXCLUDED_PATHS = ("/api/v1/debug/error/{status}",)


def _live_paths(db_url, redis_url) -> set[tuple[str, str]]:
    from mesh.api.app import create_app

    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="contract-test-secret-01234567890123456789",
        device_code_pepper="contract-test-pepper",
    )
    app = create_app(settings)
    spec = app.openapi()
    pairs = {
        (method, path)
        for path, item in spec.get("paths", {}).items()
        for method in item
        if not any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        and path not in EXCLUDED_PATHS
    }
    return pairs


def _committed_paths() -> set[tuple[str, str]]:
    with open(OPENAPI_PATH, encoding="utf-8") as handle:
        spec = yaml.safe_load(handle)
    return {
        (method, path)
        for path, item in spec.get("paths", {}).items()
        for method in item
    }


async def test_committed_openapi_matches_live_app(db_url, redis_url):
    assert OPENAPI_PATH.exists(), "docs/api/openapi.yaml missing — run scripts/export_openapi.py"
    live = _live_paths(db_url, redis_url)
    committed = _committed_paths()
    missing = live - committed
    stale = committed - live
    assert not missing and not stale, (
        "openapi.yaml drifted from the app. "
        f"missing={sorted(missing)[:8]} stale={sorted(stale)[:8]} — "
        "regenerate: python backend/scripts/export_openapi.py"
    )


async def test_published_contract_has_no_daemon_surface():
    committed = _committed_paths()
    daemon = [p for _m, p in committed if p.startswith("/api/v1/daemon/")]
    assert daemon == []
