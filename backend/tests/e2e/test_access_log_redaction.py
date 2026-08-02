"""§5.3 real-server channel sampling — the raw search q never reaches logs.

Spawns a REAL uvicorn in the PRODUCTION shape (``--no-access-log`` + the
self-managed ``mesh.access`` middleware at INFO level — NOT the e2e
``--log-level warning`` that would mask the channel), issues a search with a
marker q, and samples the process logs: the marker must be absent while the
path IS logged. Static config assertions pin the deployment shape (compose /
Dockerfile pass --no-access-log; nginx logs path-only, never $args/$request).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _spawn_env(db_url: str, redis_url: str) -> dict:
    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = db_url
    env["MESH_REDIS_URL"] = redis_url
    env["MESH_AUTH_MODE"] = "dev"
    host_and_db = db_url.split("://", 1)[1].split("@", 1)[1]
    app_password = os.environ.get("MESH_APP_DB_PASSWORD", "mesh_app")
    env["MESH_APP_DATABASE_URL"] = f"postgresql+asyncpg://mesh_app:{app_password}@{host_and_db}"
    env["MESH_JWT_SECRET"] = "access-log-e2e-signing-secret-00000000"
    env["MESH_SEARCH_CURSOR_SECRET"] = "access-log-e2e-cursor-secret-00000000"
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env.setdefault("MESH_STORAGE_ACCESS_KEY", "mesh")
    env.setdefault("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret")
    env.setdefault("MESH_STORAGE_BUCKET", "mesh-e2e")
    env.setdefault("MESH_DAEMON_TLS_REQUIRED", "false")
    # Force the code under test onto PYTHONPATH (mirror tests/e2e/conftest).
    import re as _re

    src = str(BACKEND / "src")
    existing = [
        x
        for x in env.get("PYTHONPATH", "").split(os.pathsep)
        if x and not _re.search(r"/workspaces/[^/]+/workdir/Mesh/backend", x)
    ]
    env["PYTHONPATH"] = os.pathsep.join([src, str(BACKEND)] + existing)
    return env


async def test_access_log_channel_sampling_real_server(db_url, redis_url, tmp_path):
    port = _free_port()
    log_path = tmp_path / "api.log"
    env = _spawn_env(db_url, redis_url)
    with open(log_path, "wb") as log_file:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "mesh.api.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                # Production shape: INFO level (so the channel is live) with
                # the default access log DISABLED (compose/Dockerfile shape).
                "--log-level",
                "info",
                "--no-access-log",
            ],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            cwd=str(BACKEND),
        )
        try:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient(base_url=base, timeout=5) as client:
                # Wait for healthz.
                for _ in range(120):
                    try:
                        r = await client.get("/healthz")
                        if r.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.25)
                else:
                    raise AssertionError("API did not become healthy")

                marker = f"TOPSECRET{uuid.uuid4().hex}"
                ws = uuid.uuid4()
                r = await client.get(
                    f"/api/v1/workspaces/{ws}/search", params={"q": marker}
                )
                # 401/404 are fine — the middleware logs regardless of outcome.
                assert r.status_code in (400, 401, 403, 404)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    logs = log_path.read_text(encoding="utf-8", errors="replace")
    assert marker not in logs, "raw search q leaked into the access log channel"
    # The mesh.access middleware logs the path (without query) at INFO.
    assert f"/api/v1/workspaces/{ws}/search" in logs, "mesh.access path log missing"


def test_deploy_configs_never_log_query_strings():
    """Static shape guard: compose/Dockerfile disable uvicorn's default
    access log; nginx logs path-only (never $args / the $request line)."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "--no-access-log" in compose, "compose api must pass --no-access-log"
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-access-log" in dockerfile, "Dockerfile CMD must pass --no-access-log"

    nginx = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    # The active access_log format must not embed the query string.
    access_lines = [
        line for line in nginx.splitlines() if line.strip().startswith("access_log")
    ]
    assert access_lines, "nginx access_log directive missing"
    for line in access_lines:
        fmt_name = line.split()[-1].rstrip(";")
        # Find the log_format block and assert it carries no $args / $request.
        start = nginx.index(f"log_format {fmt_name}")
        block = nginx[start : nginx.index(";", start)]
        assert "$args" not in block, "nginx access log format embeds $args"
        # $request (full request line, query included) — but NOT $request_method.
        assert not re.search(r"\$request(?!\w)", block), (
            "nginx access log format embeds the full $request line"
        )
