"""M2 / MES-83 regression guard: compose must stay loopback-only + restricted
role + secret-free-of-defaults.

The compose stack is a local-development shape. These assertions fail if a future
edit re-exposes a published port on all interfaces, drops the restricted app
role, publishes a datastore to the host, or reintroduces a guessable default
secret, so the security posture cannot silently regress.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"

# Credentials that must NEVER carry a `:-default` in compose — every reference
# has to be the required form `${VAR:?...}` so the stack fails closed (MES-83).
REQUIRED_SECRET_VARS = (
    "MESH_POSTGRES_PASSWORD",
    "MESH_REDIS_PASSWORD",
    "MESH_APP_DB_PASSWORD",
    "MESH_STORAGE_ACCESS_KEY",
    "MESH_STORAGE_SECRET_KEY",
)


def _load() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _published_ports(service: dict) -> list[str]:
    return [str(port) for port in service.get("ports", [])]


def test_all_published_ports_bind_loopback():
    for name, service in _load()["services"].items():
        for port in _published_ports(service):
            assert port.startswith("127.0.0.1:"), (
                f"service {name!r} publishes {port!r} without a 127.0.0.1 bind address"
            )


def test_datastores_publish_no_host_ports():
    """MES-83: postgres and redis must be internal-only — no host port mapping.

    A publicly reachable datastore is exactly how the incident happened; the
    only way to reach these services is the internal compose network.
    """
    services = _load()["services"]
    for name in ("postgres", "redis"):
        assert _published_ports(services[name]) == [], (
            f"datastore {name!r} must not publish any host ports (MES-83)"
        )


def test_minio_publishes_loopback_only():
    """Object storage is reachable from the browser for direct presigned upload,
    so it keeps a host mapping — but strictly loopback-bound, never the network."""
    ports = _published_ports(_load()["services"]["minio"])
    assert ports, "minio keeps a loopback publish for browser direct upload"
    for port in ports:
        assert port.startswith("127.0.0.1:"), f"minio {port!r} is not loopback-bound"


def test_secret_vars_are_required_not_defaulted():
    """MES-83: every credential reference uses `${VAR:?...}` (fail closed),
    never `${VAR:-guessable}` — reintroducing a default secret fails CI."""
    text = COMPOSE.read_text(encoding="utf-8")
    for var in REQUIRED_SECRET_VARS:
        for match in re.finditer(r"\$\{" + var + r"(:[^}]*)?\}", text):
            modifier = match.group(1) or ""
            assert modifier.startswith(":?"), (
                f"{var} must be required (${{{var}:?...}}), found {match.group(0)!r}"
            )


def test_api_and_gateway_connect_as_restricted_app_role():
    services = _load()["services"]
    for name in ("api", "gateway"):
        env = services[name]["environment"]
        assert "MESH_APP_DATABASE_URL" in env, f"{name} is missing MESH_APP_DATABASE_URL"
        assert "mesh_app" in env["MESH_APP_DATABASE_URL"]


def test_api_provisions_app_role_password():
    env = _load()["services"]["api"]["environment"]
    assert "MESH_APP_DB_PASSWORD" in env


def test_gateway_bounds_websocket_frame_size():
    """M4: uvicorn's default WebSocket frame ceiling is 16MB — the gateway must
    start with an explicit, small ``--ws-max-size`` (Settings.ws_max_size_bytes)."""
    command = str(_load()["services"]["gateway"]["command"])
    assert "--ws-max-size" in command


def test_redis_requires_authentication():
    """L3: Redis must run with requirepass — an unauthenticated instance lets
    any container on the compose network forge realtime fan-out frames."""
    command = _load()["services"]["redis"].get("command") or []
    assert "--requirepass" in [str(part) for part in command]


def test_all_mesh_redis_urls_carry_credentials():
    """Every service's MESH_REDIS_URL must authenticate (redis://:pass@host)."""
    for name, service in _load()["services"].items():
        url = (service.get("environment") or {}).get("MESH_REDIS_URL")
        if url is None:
            continue
        scheme, _, rest = url.partition("://")
        assert scheme == "redis" and "@" in rest, f"{name} MESH_REDIS_URL lacks credentials"
        assert "MESH_REDIS_PASSWORD" in rest.split("@", 1)[0]
