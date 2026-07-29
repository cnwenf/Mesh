"""Task principal e2e tests — §2.2 S-05 / auth.md §2.5.1.

Real HTTP tests for /api/v1/task/* endpoints using mesh_task_ tokens
obtained via the daemon claim flow. Validates:
1. Legal task token → 200 with current context
2. Forged mesh_task_ → 401
3. Console token on task route → 401
4. Task token on execution read → 200
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import TaskExecution

PASSWORD = "e2e-password-123"
WORKER_READY_WAIT_SECONDS = 3


@pytest_asyncio.fixture(scope="module")
async def runtime_worker(provision_database):
    """Worker process (relay + reaper) for task token e2e tests."""
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_RUNTIME_REAPER_INTERVAL"] = "0.5"
    env["MESH_RUNTIME_LEASE_SECONDS"] = "3"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    storage_endpoint = os.environ.get(
        "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
    )
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_BUCKET"] = os.environ.get(
        "MESH_TEST_STORAGE_BUCKET", "mesh-e2e"
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    await asyncio.sleep(WORKER_READY_WAIT_SECONDS)
    assert process.poll() is None, "worker died during startup"
    yield process
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _daemon(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# helpers (same patterns as test_runtime_e2e.py)
# ---------------------------------------------------------------------------


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Task E2E"},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces",
        json={"name": "Task E2E", "slug": slug},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _create_agent(api_client, token, ws_id) -> str:
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={"name": "task-agent"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _setup_world(api_client, suffix: str) -> tuple[str, str, str]:
    token = await _register_and_login(api_client, f"task-{suffix}@e2e.mesh")
    ws = await _create_workspace(api_client, token, f"task-{suffix}")
    agent_id = await _create_agent(api_client, token, ws["id"])
    return token, ws["id"], agent_id


async def _activated_runtime(api_client, token, ws_id) -> tuple[dict, str]:
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": "task-rt", "kind": "self_hosted", "max_concurrent": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()["data"]
    resp = await api_client.post(
        "/api/v1/daemon/runtimes:activate",
        json={
            "activation_code": created["activation"]["code"],
            "metadata": {
                "hostname": "task-e2e",
                "os": "linux",
                "cpu_cores": 2,
                "memory_mb": 4096,
                "capabilities": ["python"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return created, resp.json()["data"]["runtime_token"]


async def _enqueue_and_wait(
    session_factory, ws_id, agent_id, *, timeout=15.0
) -> TaskExecution:
    key = f"task-e2e-{uuid.uuid4().hex}"
    async with session_factory() as session, session.begin():
        session.add(
            OutboxEvent(
                workspace_id=uuid.UUID(ws_id),
                event_type="execution.enqueue",
                payload={
                    "intent": "enqueue",
                    "agent_id": agent_id,
                    "issue_id": None,
                    "trigger": "manual",
                    "trigger_event_id": str(uuid.uuid4()),
                    "idempotency_key": key,
                    "config_snapshot": {},
                    "required_capabilities": [],
                    "label_requirements": {},
                    "task_spec": {},
                },
                idempotency_key=key,
                status="pending",
            )
        )
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.workspace_id == uuid.UUID(ws_id),
                        TaskExecution.idempotency_key == key,
                    )
                )
            ).scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(0.2)
    raise AssertionError(f"execution {key} never materialized")


async def _claim_task_token(api_client, runtime_id, daemon_token) -> str:
    """Claim an execution and extract the task token from the response."""
    resp = await api_client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}/executions:claim",
        json={"diagnostics": {}},
        headers=_daemon(daemon_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    task_token = data["attempt"].get("task_token")
    assert task_token is not None, "claim response missing task_token"
    assert task_token.startswith("mesh_task_")
    return task_token


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_context_with_legal_token(
    api_client, runtime_worker, session_factory
):
    """① Legal task token → GET /api/v1/task/context → 200."""
    token, ws_id, agent_id = await _setup_world(api_client, "ctx")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    await _enqueue_and_wait(session_factory, ws_id, agent_id)
    task_token = await _claim_task_token(api_client, created["id"], daemon_token)

    resp = await api_client.get(
        "/api/v1/task/context",
        headers=_auth(task_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "attempt_id" in data
    assert "workspace_id" in data
    assert "methods" in data


@pytest.mark.asyncio
async def test_task_execution_read_with_legal_token(
    api_client, runtime_worker, session_factory
):
    """① Legal task token → GET /api/v1/task/executions/{id} → 200."""
    token, ws_id, agent_id = await _setup_world(api_client, "exec")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    execution = await _enqueue_and_wait(session_factory, ws_id, agent_id)
    task_token = await _claim_task_token(api_client, created["id"], daemon_token)

    resp = await api_client.get(
        f"/api/v1/task/executions/{execution.id}",
        headers=_auth(task_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == str(execution.id)
    assert data["status"] in ("claimed", "queued")


@pytest.mark.asyncio
async def test_task_route_rejects_forged_token(api_client, runtime_worker):
    """② Forged mesh_task_ → 401."""
    resp = await api_client.get(
        "/api/v1/task/context",
        headers=_auth("mesh_task_forged_token_that_does_not_exist"),
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_task_route_rejects_console_token(
    api_client, runtime_worker, session_factory
):
    """③ Console PAT on task route → 401 (not a task token)."""
    token, ws_id, agent_id = await _setup_world(api_client, "console")
    resp = await api_client.get(
        "/api/v1/task/context",
        headers=_auth(token),
    )
    assert resp.status_code == 401, resp.text
