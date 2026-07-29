"""E2E fixtures: REAL server processes + REAL API calls + REAL database writes.

Refusing mock-only theatre (per project testing rules): the API and gateway
run as genuine uvicorn subprocesses against the migrated PostgreSQL test
database and a real Redis; tests hit them over HTTP/WebSocket.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass

import httpx
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import get_test_database_url, get_test_redis_url

SERVER_READY_TIMEOUT_SECONDS = 30.0

# Restricted app role created by migration 0002 (M1). The api/gateway servers
# connect as this non-owner role so PostgreSQL RLS is enforced on the app path.
APP_ROLE = "mesh_app"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _app_role_url(db_url: str) -> str:
    """Restricted-role URL mirroring compose's MESH_APP_DATABASE_URL (M1)."""
    password = os.environ.get("MESH_APP_DB_PASSWORD", "mesh_app")
    without_scheme = db_url.split("://", 1)[1]
    host_and_db = without_scheme.split("@", 1)[1]
    return f"postgresql+asyncpg://{APP_ROLE}:{password}@{host_and_db}"


@dataclass
class RunningServer:
    name: str
    process: subprocess.Popen
    base_url: str


def _spawn(app_module: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_APP_DATABASE_URL"] = _app_role_url(get_test_database_url())
    # M1: the dev mock provider only accepts this exact callback redirect URI.
    env["MESH_OAUTH_MOCK_REDIRECT_URIS"] = "http://api.test/api/v1/auth/oauth/mock/callback"
    # Attachment module: real MinIO, same endpoint for server I/O and presigns
    # (e2e clients run on the loopback, like the compose public endpoint).
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_ACCESS_KEY"] = os.environ.get("MESH_STORAGE_ACCESS_KEY", "")
    env["MESH_STORAGE_SECRET_KEY"] = os.environ.get("MESH_STORAGE_SECRET_KEY", "")
    env["MESH_STORAGE_BUCKET"] = os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e")
    # Runtime machine API: e2e servers run plaintext loopback (TLS termination
    # is a deployment concern; the 403 gate is unit-covered) and a short lease
    # keeps the reaper red-line tests fast.
    env.setdefault("MESH_DAEMON_TLS_REQUIRED", "false")
    # Web session cookie: Secure relaxed for the plaintext loopback e2e
    # transport (same deliberate exception; production default stays true).
    env.setdefault("MESH_SESSION_COOKIE_SECURE", "false")
    # Device-code HMAC pepper (loopback-only e2e value; production sets a
    # strong secret via env — validate_auth_settings enforces it there).
    env.setdefault("MESH_DEVICE_CODE_PEPPER", "e2e-device-code-pepper-0123456789")
    env.setdefault("MESH_RUNTIME_LEASE_SECONDS", "3")
    # Skill imports (skill.md §5.3): the import e2e fetches a loopback fixture
    # source server, which the SSRF guard only permits via the allowlist.
    env.setdefault("MESH_SKILL_SOURCE_HOST_ALLOWLIST", "127.0.0.1,localhost")
    # Force the code UNDER TEST onto PYTHONPATH so e2e subprocesses do not
    # resolve `mesh` from a stale editable install of another workspace.
    import re as _re
    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.dirname(_here)
    _src = os.path.join(_backend, "src")
    _existing = [x for x in env.get("PYTHONPATH", "").split(os.pathsep)
                 if x and not _re.search(r"/workspaces/[^/]+/workdir/Mesh/backend", x)]
    env["PYTHONPATH"] = os.pathsep.join([_src, _backend] + _existing)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"{app_module}:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            # Mirror the compose gateway deployment (Settings.ws_max_size_bytes):
            # the transport-level frame ceiling is part of the contract under test.
            "--ws-max-size",
            "65536",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


async def _wait_ready(base_url: str) -> None:
    deadline = time.monotonic() + SERVER_READY_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"{base_url}/healthz", timeout=2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
    raise RuntimeError(f"server at {base_url} did not become ready")


def _terminate(server: RunningServer) -> None:
    server.process.terminate()
    try:
        server.process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.process.kill()
        server.process.wait(timeout=5)


@pytest_asyncio.fixture(scope="session")
async def api_server(provision_database) -> RunningServer:
    port = _free_port()
    server = RunningServer("api", _spawn("mesh.api.app", port), f"http://127.0.0.1:{port}")
    await _wait_ready(server.base_url)
    yield server
    _terminate(server)


@pytest_asyncio.fixture(scope="session")
async def gateway_server(provision_database) -> RunningServer:
    port = _free_port()
    server = RunningServer(
        "gateway", _spawn("mesh.realtime.app", port), f"http://127.0.0.1:{port}"
    )
    await _wait_ready(server.base_url)
    yield server
    _terminate(server)


@pytest_asyncio.fixture
async def api_client(api_server) -> httpx.AsyncClient:
    async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
        yield client


async def _truncate_all(engine, tables: str) -> None:
    """TRUNCATE with deadlock retry: background worker processes (relay /
    reaper) hold brief locks that can deadlock against AccessExclusive
    TRUNCATE (40P01); their transactions are short, so a retry resolves it."""
    import asyncio

    from sqlalchemy.exc import DBAPIError

    for attempt in range(6):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
            return
        except DBAPIError as exc:
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "40P01" and attempt < 5:
                await asyncio.sleep(0.3 * (attempt + 1))
                continue
            raise


@pytest_asyncio.fixture(autouse=True)
async def clean_tables_after_each_e2e_test(provision_database):
    """TRUNCATE every table before each e2e test for isolation."""
    import mesh.db.models  # noqa: F401 — register all models on Base.metadata
    from mesh.db.base import Base

    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    engine = create_async_engine(get_test_database_url())
    await _truncate_all(engine, tables)
    yield
    await _truncate_all(engine, tables)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def flush_redis_each_e2e_test():
    """Reset Redis (rate-limit buckets + dev mailer) before each e2e test.

    The API server keeps rate-limit counters in Redis; without a per-test flush
    the login/register buckets leak across tests and trip the limiter.
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(get_test_redis_url(), decode_responses=True)
    await client.flushdb()
    yield
    await client.flushdb()
    await client.aclose()
