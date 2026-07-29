"""Export the public OpenAPI 3.1 document to docs/api/openapi.yaml
(cli.md §5.4, README §11.2).

The published contract EXCLUDES the machine API: ``/api/v1/daemon/*`` and
internal endpoints are stripped entirely (not ``x-internal`` flagged — a
flagged path still ships its full surface in the public artifact). The daemon
protocol is first-party-only (the mesh-runtime binary; runtime.md documents
it), so third-party codegen has no need for it. CI asserts zero
``/api/v1/daemon/`` paths in the published file (tests/docs/check_openapi_surface.py).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo-relative paths so the script runs from anywhere.
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR / "src"))
sys.path.insert(0, str(BACKEND_DIR))

# Path prefixes stripped from the published contract (internal-only surfaces).
EXCLUDED_PREFIXES = ("/api/v1/daemon/",)
# Individual internal paths stripped in addition to the prefixes.
EXCLUDED_PATHS = ("/api/v1/debug/error/{status}",)

OUTPUT_PATH = REPO_ROOT / "docs" / "api" / "openapi.yaml"


def build_spec() -> dict:
    # Dummy-but-valid settings: openapi generation never touches storage.
    os.environ.setdefault("MESH_AUTH_MODE", "dev")
    os.environ.setdefault("MESH_DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
    os.environ.setdefault("MESH_REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("MESH_DEVICE_CODE_PEPPER", "export-only-pepper-not-used")

    from mesh.api.app import create_app
    from mesh.config import load_settings

    settings = load_settings()
    app = create_app(settings)
    return app.openapi()


def strip_internal(spec: dict) -> dict:
    paths = spec.get("paths", {})
    kept = {
        path: item
        for path, item in paths.items()
        if not any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        and path not in EXCLUDED_PATHS
    }
    spec["paths"] = kept
    return spec


def main() -> int:
    import yaml

    spec = strip_internal(build_spec())
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        yaml.safe_dump(spec, handle, sort_keys=False, allow_unicode=True, width=100)
    print(f"wrote {OUTPUT_PATH} ({len(spec['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
