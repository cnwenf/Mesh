"""M2 regression guard: compose defaults must stay loopback-only + restricted role.

The compose stack is a local-development shape. These assertions fail if a future
edit re-exposes a published port on all interfaces or drops the restricted app
role, so the security posture cannot silently regress.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"


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
