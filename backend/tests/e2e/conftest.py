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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


@pytest_asyncio.fixture(autouse=True)
async def clean_tables_after_each_e2e_test(provision_database):
    """TRUNCATE every table before each e2e test for isolation."""
    from mesh.db.base import Base

    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    engine = create_async_engine(get_test_database_url())
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await engine.dispose()
