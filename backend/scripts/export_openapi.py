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
# Individual internal paths stripped in addition to the prefixes. Dev-only
# /_debug/* routes carry include_in_schema=False and never reach the document;
# nothing else is stripped by exact path today.
EXCLUDED_PATHS: tuple[str, ...] = ()

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


def _refs_in(node: object) -> set[str]:
    """Every ``#/components/schemas/<Name>`` reference reachable from ``node``."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(
                "#/components/schemas/"
            ):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _refs_in(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs_in(value)
    return found


def _reachable_schemas(spec: dict) -> set[str]:
    """Schema names referenced (transitively) by the kept public paths.

    Schemas only referenced by stripped internal paths must NOT ship: a
    description string inside one still leaks the internal machine interface
    (cli.md §5.4 — 整体剔除,非 x-internal 标记).
    """
    seen = _refs_in(spec.get("paths", {}))
    schemas = spec.get("components", {}).get("schemas", {})
    frontier = set(seen)
    while frontier:
        name = frontier.pop()
        frontier |= _refs_in(schemas.get(name, {})) - seen
        seen |= frontier
    return seen


def strip_internal(spec: dict) -> dict:
    paths = spec.get("paths", {})
    kept = {
        path: item
        for path, item in paths.items()
        if not any(path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        and path not in EXCLUDED_PATHS
    }
    spec["paths"] = kept
    schemas = spec.get("components", {}).get("schemas", {})
    reachable = _reachable_schemas(spec)
    orphans = sorted(set(schemas) - reachable)
    for name in orphans:
        del schemas[name]
    if orphans:
        print(f"stripped {len(orphans)} orphaned internal schemas (e.g. {orphans[0]})")
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
