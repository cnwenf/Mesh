"""Runtime red-line e2e (runtime.md §5 — real servers, real concurrency).

T2 (concurrent claim, exactly-once), T3 (capacity contention ≤ max, terminal
→ zero), T4 (lease expiry requeue with audit), T10 (zombie lease_seq → 409),
T16 (checkout allowlist + private-address rejection), T20 (no-match 204 with
zero capacity change), T21 (approval resume protocol) — all driven over real
HTTP against the mesh_app RLS role, with a REAL worker process running the
outbox relay (execution.enqueue consumer) and the reaper.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import subprocess
import sys
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text, update

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import ExecutionAttempt, Runtime, TaskExecution

pytestmark = pytest.mark.e2e

PASSWORD = "e2e-password-123"
WORKER_READY_WAIT_SECONDS = 2.5


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _daemon(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# worker fixture (relay + reaper, fast intervals)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def runtime_worker(provision_database):
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_RUNTIME_REAPER_INTERVAL"] = "0.5"
    env["MESH_RUNTIME_LEASE_SECONDS"] = "3"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_BUCKET"] = os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e")
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


# ---------------------------------------------------------------------------
# console / daemon helpers
# ---------------------------------------------------------------------------


async def _register_and_login(client, email: str, name: str = "RT E2E") -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _create_workspace(client, token: str, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "RT E2E", "slug": slug}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _setup_world(api_client, suffix: str) -> tuple[str, str]:
    token = await _register_and_login(api_client, f"rt-{suffix}@e2e.mesh")
    ws = await _create_workspace(api_client, token, f"rt-{suffix}")
    return token, ws["id"]


async def _create_runtime(
    api_client, token, ws_id, *, name="build-01", kind="self_hosted", labels=None, max_concurrent=1
) -> dict:
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": name, "kind": kind, "labels": labels or {}, "max_concurrent": max_concurrent},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _activate(api_client, code: str, **metadata) -> dict:
    resp = await api_client.post(
        "/api/v1/daemon/runtimes:activate",
        json={"activation_code": code, "metadata": metadata},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _activated_runtime(
    api_client, token, ws_id, *, name="build-01", kind="self_hosted", max_concurrent=1, **meta
) -> tuple[dict, str]:
    created = await _create_runtime(
        api_client, token, ws_id, name=name, kind=kind, max_concurrent=max_concurrent
    )
    activated = await _activate(
        api_client,
        created["activation"]["code"],
        hostname="e2e-host",
        os="linux-x86_64",
        cpu_cores=4,
        memory_mb=8192,
        capabilities=meta.pop("capabilities", ["python"]),
        **meta,
    )
    return created, activated["runtime_token"]


async def _create_agent(api_client, token, ws_id, name="Agent E2E") -> str:
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents", json={"name": name}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


async def _enqueue(
    session_factory,
    ws_id,
    *,
    idem: str | None = None,
    agent_id=None,
    task_spec=None,
    config_snapshot=None,
    capabilities=None,
) -> str:
    """Insert an execution.enqueue outbox event — the REAL production entry
    path; the worker relay materializes the task_execution."""
    key = idem or f"e2e-{uuid.uuid4().hex}"
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
                    "config_snapshot": config_snapshot or {},
                    "required_capabilities": capabilities or [],
                    "label_requirements": {},
                    "task_spec": task_spec or {},
                },
                idempotency_key=key,
                status="pending",
            )
        )
    return key


async def _wait_queued(session_factory, ws_id, idem, timeout=15.0) -> TaskExecution:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(TaskExecution).where(
                        TaskExecution.workspace_id == uuid.UUID(ws_id),
                        TaskExecution.idempotency_key == idem,
                    )
                )
            ).scalar_one_or_none()
        if row is not None:
            return row
        await asyncio.sleep(0.2)
    raise AssertionError(f"execution {idem} never materialized")


async def _claim(api_client, runtime_id, daemon_token):
    return await api_client.post(
        f"/api/v1/daemon/runtimes/{runtime_id}/executions:claim",
        json={"diagnostics": {}},
        headers=_daemon(daemon_token),
    )


# ---------------------------------------------------------------------------
# activation lifecycle
# ---------------------------------------------------------------------------


async def test_activation_flow_and_replay_410(api_client, runtime_worker):
    token, ws_id = await _setup_world(api_client, "act")
    created = await _create_runtime(api_client, token, ws_id)
    assert created["status"] == "pending"
    code = created["activation"]["code"]
    assert created["activation"]["release"]["sha256"]

    activated = await _activate(api_client, code, hostname="node-1")
    assert activated["runtime_token"].startswith("mesh_rt_")

    # Replay of a used code → 410; unknown code → 401.
    replay = await api_client.post(
        "/api/v1/daemon/runtimes:activate",
        json={"activation_code": code, "metadata": {}},
    )
    assert replay.status_code == 410
    assert replay.json()["error"]["code"] == "activation_expired"
    unknown = await api_client.post(
        "/api/v1/daemon/runtimes:activate",
        json={"activation_code": "ACT-XXXX-YYYY-ZZZZ", "metadata": {}},
    )
    assert unknown.status_code == 401

    # Runtime shows online via the console API.
    detail = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes/{created['id']}", headers=_auth(token)
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "online"
    assert detail.json()["data"]["hostname"] == "node-1"


async def test_daemon_auth_rejects_foreign_and_invalid_tokens(api_client, runtime_worker):
    token, ws_id = await _setup_world(api_client, "dauth")
    created_a, token_a = await _activated_runtime(api_client, token, ws_id, name="a")
    created_b, token_b = await _activated_runtime(api_client, token, ws_id, name="b")

    # A's token on B's heartbeat path → 403.
    foreign = await api_client.post(
        f"/api/v1/daemon/runtimes/{created_b['id']}:heartbeat",
        json={"current_load": 0, "health": "healthy", "metrics": {}, "inflight": []},
        headers=_daemon(token_a),
    )
    assert foreign.status_code == 403
    # Garbage token → 401.
    invalid = await api_client.post(
        f"/api/v1/daemon/runtimes/{created_a['id']}:heartbeat",
        json={"current_load": 0, "health": "healthy", "metrics": {}, "inflight": []},
        headers=_daemon("mesh_rt_invalid"),
    )
    assert invalid.status_code == 401
    # Valid heartbeat on own path → 200 with server_time.
    ok = await api_client.post(
        f"/api/v1/daemon/runtimes/{created_a['id']}:heartbeat",
        json={"current_load": 0, "health": "healthy", "metrics": {"cpu": 5}, "inflight": []},
        headers=_daemon(token_a),
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["server_time"]


# ---------------------------------------------------------------------------
# T20: no-match claim → 204, capacity untouched
# ---------------------------------------------------------------------------


async def test_t20_no_match_claim_204_capacity_unchanged(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "t20")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)

    # Empty queue.
    empty = await _claim(api_client, created["id"], daemon_token)
    assert empty.status_code == 204

    # Capability the runtime lacks → still 204, load provably unchanged.
    idem = await _enqueue(session_factory, ws_id, capabilities=["quantum"])
    await _wait_queued(session_factory, ws_id, idem)
    nomatch = await _claim(api_client, created["id"], daemon_token)
    assert nomatch.status_code == 204
    async with session_factory() as session:
        runtime = await session.get(Runtime, uuid.UUID(created["id"]))
    assert runtime.current_load == 0  # zero writes on no-match (T20)


# ---------------------------------------------------------------------------
# T2: concurrent claims → exactly one winner per task
# ---------------------------------------------------------------------------


async def test_t2_three_runtimes_race_one_task_exactly_one_winner(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "t2")
    runtimes = [
        await _activated_runtime(api_client, token, ws_id, name=f"r{i}") for i in range(3)
    ]
    idem = await _enqueue(session_factory, ws_id)
    await _wait_queued(session_factory, ws_id, idem)

    responses = await asyncio.gather(
        *[_claim(api_client, created["id"], dt) for created, dt in runtimes]
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 204, 204]  # exactly one winner

    winner = next(r for r in responses if r.status_code == 200)
    assert winner.json()["data"]["attempt"]["attempt_number"] == 1
    async with session_factory() as session:
        attempts = (
            await session.execute(
                select(ExecutionAttempt).where(
                    ExecutionAttempt.workspace_id == uuid.UUID(ws_id)
                )
            )
        ).scalars().all()
        claimed = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.workspace_id == uuid.UUID(ws_id),
                    TaskExecution.status == "claimed",
                )
            )
        ).scalars().all()
    assert len(attempts) == 1  # zero duplicate execution
    assert len(claimed) == 1


# ---------------------------------------------------------------------------
# T3: capacity contention never overshoots; terminal reports return to zero
# ---------------------------------------------------------------------------


async def test_t3_five_parallel_claims_vs_capacity_two(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "t3")
    created, daemon_token = await _activated_runtime(
        api_client, token, ws_id, max_concurrent=2
    )
    for _ in range(5):
        await _enqueue(session_factory, ws_id)
    await asyncio.sleep(2.5)  # relay drains the five enqueues

    responses = await asyncio.gather(
        *[_claim(api_client, created["id"], daemon_token) for _ in range(5)]
    )
    winners = [r for r in responses if r.status_code == 200]
    assert len(winners) == 2  # never more than max_concurrent
    async with session_factory() as session:
        runtime = await session.get(Runtime, uuid.UUID(created["id"]))
    assert runtime.current_load == 2

    # Terminal reports release capacity exactly once → zero.
    for winner in winners:
        attempt = winner.json()["data"]["attempt"]
        patch = await api_client.patch(
            f"/api/v1/daemon/attempts/{attempt['id']}",
            json={"lease_seq": 1, "status": "running"},
            headers=_daemon(daemon_token),
        )
        assert patch.status_code == 200, patch.text
        done = await api_client.patch(
            f"/api/v1/daemon/attempts/{attempt['id']}",
            json={"lease_seq": 1, "status": "completed", "result": {"exit_code": 0}},
            headers=_daemon(daemon_token),
        )
        assert done.status_code == 200, done.text
        assert done.json()["data"]["execution_status"] == "completed"
    async with session_factory() as session:
        runtime = await session.get(Runtime, uuid.UUID(created["id"]))
    assert runtime.current_load == 0


# ---------------------------------------------------------------------------
# T10: stale lease_seq → 409 on every report channel
# ---------------------------------------------------------------------------


async def test_t10_zombie_reports_rejected_409(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "t10")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id)
    await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    assert claimed.status_code == 200
    attempt = claimed.json()["data"]["attempt"]

    renewed = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}:renew-lease",
        json={"lease_seq": 1},
        headers=_daemon(daemon_token),
    )
    assert renewed.status_code == 200
    assert renewed.json()["data"]["lease_seq"] == 2

    # Zombie with the old seq is fenced everywhere.
    stale_patch = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "completed"},
        headers=_daemon(daemon_token),
    )
    assert stale_patch.status_code == 409
    assert stale_patch.json()["error"]["code"] == "lease_seq_mismatch"
    stale_renew = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}:renew-lease",
        json={"lease_seq": 1},
        headers=_daemon(daemon_token),
    )
    assert stale_renew.status_code == 409
    stale_logs = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/logs",
        json={"lease_seq": 1, "stream": "stdout", "start_offset": 0, "lines": ["x"]},
        headers=_daemon(daemon_token),
    )
    assert stale_logs.status_code == 409

    # The current holder still succeeds.
    ok = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 2, "status": "running"},
        headers=_daemon(daemon_token),
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------------------
# T4: lease expiry → reaper requeues, audit preserved, attempt #2 built
# ---------------------------------------------------------------------------


async def test_t4_lost_runtime_requeue_preserves_audit(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "t4")
    created_a, daemon_a = await _activated_runtime(api_client, token, ws_id, name="dies")
    created_b, daemon_b = await _activated_runtime(api_client, token, ws_id, name="takes-over")
    idem = await _enqueue(session_factory, ws_id)
    execution = await _wait_queued(session_factory, ws_id, idem)

    first = await _claim(api_client, created_a["id"], daemon_a)
    assert first.status_code == 200
    first_attempt_id = first.json()["data"]["attempt"]["id"]

    # Daemon A "dies": no renewals. Lease (3s) lapses; reaper (0.5s) reclaims.
    deadline = asyncio.get_event_loop().time() + 20
    requeued = None
    while asyncio.get_event_loop().time() < deadline:
        async with session_factory() as session:
            row = await session.get(TaskExecution, execution.id)
        if row.status == "queued":
            requeued = row
            break
        await asyncio.sleep(0.3)
    assert requeued is not None, "reaper never requeued the expired attempt"

    # Audit row preserved: runtime, claim time, failure reason, advanced seq.
    async with session_factory() as session:
        attempt1 = await session.get(ExecutionAttempt, uuid.UUID(first_attempt_id))
    assert attempt1.status == "reclaimed"
    assert attempt1.failure_reason == "lease_expired"
    assert attempt1.runtime_id == uuid.UUID(created_a["id"])
    assert attempt1.claimed_at is not None
    assert attempt1.lease_seq == 2  # zombie fence advanced

    # Runtime B takes over with attempt #2 (never reusing #1).
    second = await _claim(api_client, created_b["id"], daemon_b)
    assert second.status_code == 200
    assert second.json()["data"]["attempt"]["attempt_number"] == 2

    # A zombie report from A with its old seq is refused.
    zombie = await api_client.patch(
        f"/api/v1/daemon/attempts/{first_attempt_id}",
        json={"lease_seq": 1, "status": "completed"},
        headers=_daemon(daemon_a),
    )
    assert zombie.status_code == 409


# ---------------------------------------------------------------------------
# T16: checkout allowlist + private-address rejection
# ---------------------------------------------------------------------------


async def test_t16_checkout_allowlist_and_ssrf_guards(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "t16")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)

    # 1) Repo not in the workspace allowlist → 403 repo_not_allowed.
    idem = await _enqueue(
        session_factory,
        ws_id,
        config_snapshot={"repo": {"url": "https://code.example/team/app.git", "base_ref": "main"}},
    )
    await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    assert claimed.status_code == 200
    attempt_id = claimed.json()["data"]["attempt"]["id"]
    forbidden = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt_id}/checkouts",
        json={"lease_seq": 1, "status": "cloning"},
        headers=_daemon(daemon_token),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "repo_not_allowed"

    # 2) Allowlisted but cloud-metadata target on a platform-managed runtime
    #    → 403 private_address_forbidden.
    meta_url = "http://169.254.169.254/latest/meta-data.git"
    async with session_factory() as session, session.begin():
        await session.execute(
            text("UPDATE workspaces SET settings = :s WHERE id = :w"),
            {"s": '{"allowed_repos": ["http://169.254.169.254/latest/meta-data.git"]}',
             "w": uuid.UUID(ws_id)},
        )
    created_pm, daemon_pm = await _activated_runtime(
        api_client, token, ws_id, name="pm", kind="platform_managed"
    )
    idem2 = await _enqueue(
        session_factory,
        ws_id,
        config_snapshot={"repo": {"url": meta_url, "base_ref": "main"}},
    )
    await _wait_queued(session_factory, ws_id, idem2)
    claimed2 = await _claim(api_client, created_pm["id"], daemon_pm)
    assert claimed2.status_code == 200
    attempt2 = claimed2.json()["data"]["attempt"]["id"]
    ssrf = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt2}/checkouts",
        json={"lease_seq": 1, "status": "cloning"},
        headers=_daemon(daemon_pm),
    )
    assert ssrf.status_code == 403
    assert ssrf.json()["error"]["code"] == "private_address_forbidden"


# ---------------------------------------------------------------------------
# T21: approval protocol over HTTP — the single resume path
# ---------------------------------------------------------------------------


async def test_t21_approval_suspend_approve_resume(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "t21")
    agent_id = await _create_agent(api_client, token, ws_id)  # approvals need the roster row
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id, agent_id=agent_id)
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt = claimed.json()["data"]["attempt"]
    started = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )
    assert started.status_code == 200

    # Tool hits confirm_required → daemon requests approval.
    approval_resp = await api_client.post(
        f"/api/v1/daemon/executions/{execution.id}/approvals",
        json={
            "lease_seq": 1,
            "attempt_id": attempt["id"],
            "action_summary": {"action": "exec:shell", "capability": "exec:shell"},
            "resume_context": {"checkpoint_ref": "ckpt-1", "completed_steps": 3},
        },
        headers=_daemon(daemon_token),
    )
    assert approval_resp.status_code == 200, approval_resp.text
    approval = approval_resp.json()["data"]
    assert approval["status"] == "pending"

    # Attempt cancelled(awaiting_approval), lease ended, capacity released.
    async with session_factory() as session:
        stored_attempt = await session.get(ExecutionAttempt, uuid.UUID(attempt["id"]))
        stored_exec = await session.get(TaskExecution, execution.id)
        runtime = await session.get(Runtime, uuid.UUID(created["id"]))
    assert stored_attempt.status == "cancelled"
    assert stored_attempt.failure_reason == "awaiting_approval"
    assert stored_exec.status == "awaiting_approval"
    assert runtime.current_load == 0

    # Human approves via the console (workspace owner JWT).
    approve = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/approvals/{approval['id']}:approve",
        json={"comment": "go ahead"},
        headers=_auth(token),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["execution_status"] == "queued"

    # Resume: the next claim builds attempt #2 from the frozen resume_context.
    resumed = await _claim(api_client, created["id"], daemon_token)
    assert resumed.status_code == 200
    assert resumed.json()["data"]["attempt"]["attempt_number"] == 2


async def test_t21_approval_reject_cancels(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "t21r")
    agent_id = await _create_agent(api_client, token, ws_id)
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id, agent_id=agent_id)
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt = claimed.json()["data"]["attempt"]
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )
    approval_resp = await api_client.post(
        f"/api/v1/daemon/executions/{execution.id}/approvals",
        json={"lease_seq": 1, "attempt_id": attempt["id"], "action_summary": {}, "resume_context": {}},
        headers=_daemon(daemon_token),
    )
    assert approval_resp.status_code == 200
    approval = approval_resp.json()["data"]

    reject = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/approvals/{approval['id']}:reject",
        json={},
        headers=_auth(token),
    )
    assert reject.status_code == 200
    async with session_factory() as session:
        stored_exec = await session.get(TaskExecution, execution.id)
    assert stored_exec.status == "cancelled"
    assert stored_exec.failure_reason == "approval_rejected"


# ---------------------------------------------------------------------------
# NEW-M1 env-name gate + credentials one-shot delivery + log redaction/resume
# ---------------------------------------------------------------------------


async def test_env_name_gate_and_credential_redaction(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "env")

    # Credential creation with a loader-reserved env name → 422.
    bad = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "EVIL", "kind": "env", "value": "x", "env_name": "LD_PRELOAD"},
        headers=_auth(token),
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "reserved_env_name"

    # A real credential + execution declaring it.
    good = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "CI_KEY", "kind": "env", "value": "sk-live-e2e-777", "env_name": "CI_KEY"},
        headers=_auth(token),
    )
    assert good.status_code == 201
    assert good.json()["data"]["value"] == "***"  # plaintext never echoed
    cred_id = good.json()["data"]["id"]

    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(
        session_factory,
        ws_id,
        task_spec={"credential_ids": [cred_id], "env_declarations": ["CI_KEY"]},
    )
    await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    assert claimed.status_code == 200
    delivered = claimed.json()["data"]["attempt"]["credentials"]
    assert delivered and delivered[0]["value"] == "sk-live-e2e-777"  # once, at claim
    assert delivered[0]["env"] == "CI_KEY"

    # An execution with a reserved env declaration fails claim assembly (422).
    idem_bad = await _enqueue(
        session_factory, ws_id, task_spec={"env_declarations": ["LD_PRELOAD"]}
    )
    await _wait_queued(session_factory, ws_id, idem_bad)
    # First claim takes the good execution (FIFO); finish it so the bad one
    # becomes the next candidate.
    attempt = claimed.json()["data"]["attempt"]
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "completed", "result": {}},
        headers=_daemon(daemon_token),
    )
    bad_claim = await _claim(api_client, created["id"], daemon_token)
    assert bad_claim.status_code == 422
    assert bad_claim.json()["error"]["code"] == "reserved_env_name"
    async with session_factory() as session:
        runtime = await session.get(Runtime, uuid.UUID(created["id"]))
    assert runtime.current_load == 0  # claim rolled back entirely


async def test_logs_redaction_and_rest_resume(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "logs")
    cred = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "LEAKY", "kind": "env", "value": "ultra-secret-999", "env_name": "LEAKY"},
        headers=_auth(token),
    )
    assert cred.status_code == 201
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id)
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt = claimed.json()["data"]["attempt"]
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )

    posted = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/logs",
        json={
            "lease_seq": 1,
            "stream": "stdout",
            "start_offset": 0,
            "lines": ["$ pytest", "password=ultra-secret-999", "PASSED"],
        },
        headers=_daemon(daemon_token),
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["data"]["redacted_hits"] == 1
    end_offset = posted.json()["data"]["accepted_end_offset"]

    # REST read: secret never appears; resume from a mid offset works.
    page = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}/logs?offset=0",
        headers=_auth(token),
    )
    assert page.status_code == 200
    lines = [item["line"] for item in page.json()["data"]["lines"]]
    assert lines == ["$ pytest", "password=***", "PASSED"]
    assert page.json()["data"]["next_offset"] == end_offset

    mid = len("$ pytest\n")
    partial = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}/logs?offset={mid}",
        headers=_auth(token),
    )
    assert [item["line"] for item in partial.json()["data"]["lines"]] == [
        "password=***",
        "PASSED",
    ]


# ---------------------------------------------------------------------------
# Console surface: patch / executions listing & detail / credential CRUD /
# freeze / daemon refetch / SSE smoke
# ---------------------------------------------------------------------------


async def test_console_runtime_patch_and_executions_surface(
    api_client, runtime_worker, session_factory
):
    token, ws_id = await _setup_world(api_client, "console")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id, name="patchme")

    # PATCH name / labels / max_concurrent.
    patched = await api_client.patch(
        f"/api/v1/workspaces/{ws_id}/runtimes/{created['id']}",
        json={"name": "renamed-rt", "labels": {"zone": "a"}, "max_concurrent": 3},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "renamed-rt"
    assert patched.json()["data"]["max_concurrent"] == 3

    # Runtimes listing shows queue depth + filters.
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?search=renamed", headers=_auth(token)
    )
    assert listing.status_code == 200
    assert [r["name"] for r in listing.json()["data"]] == ["renamed-rt"]
    assert "queue_depth" in listing.json()

    # Claim one execution through the daemon, then read it via the console.
    idem = await _enqueue(session_factory, ws_id)
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt = claimed.json()["data"]["attempt"]
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )

    execs = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/executions", headers=_auth(token)
    )
    assert execs.status_code == 200
    assert [e["id"] for e in execs.json()["data"]] == [str(execution.id)]

    detail = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}", headers=_auth(token)
    )
    assert detail.status_code == 200
    body = detail.json()["data"]
    assert body["status"] == "running"
    assert body["attempts"][0]["status"] == "running"
    assert body["retry_count"] == 0

    # Cancel via console → cancelling; daemon finishes it → cancelled.
    cancel = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}:cancel",
        json={},
        headers=_auth(token),
    )
    assert cancel.status_code == 200
    assert cancel.json()["data"]["status"] == "cancelling"
    done = await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "cancelled"},
        headers=_daemon(daemon_token),
    )
    assert done.status_code == 200
    assert done.json()["data"]["execution_status"] == "cancelled"


async def test_console_credentials_crud_and_freeze(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "creds")
    created = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "API_KEY", "kind": "env", "value": "plain-value-1", "env_name": "API_KEY"},
        headers=_auth(token),
    )
    assert created.status_code == 201
    cred_id = created.json()["data"]["id"]
    assert created.json()["data"]["value"] == "***"

    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/credentials", headers=_auth(token)
    )
    assert [c["id"] for c in listing.json()["data"]] == [cred_id]

    # Freeze an execution with that credential injected → envelope revoked.
    created_rt, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(
        session_factory, ws_id, task_spec={"credential_ids": [cred_id]}
    )
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created_rt["id"], daemon_token)
    assert claimed.json()["data"]["attempt"]["credentials"][0]["value"] == "plain-value-1"
    freeze = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}:freeze",
        json={},
        headers=_auth(token),
    )
    assert freeze.status_code == 200
    assert freeze.json()["data"]["revoked_envelopes"] == 1

    # Refetch after freeze is refused (envelopes revoked).
    attempt_id = claimed.json()["data"]["attempt"]["id"]
    refetch = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
        json={"lease_seq": 1},
        headers=_daemon(daemon_token),
    )
    assert refetch.status_code == 409
    assert refetch.json()["error"]["code"] == "envelope_revoked"

    deleted = await api_client.delete(
        f"/api/v1/workspaces/{ws_id}/credentials/{cred_id}", headers=_auth(token)
    )
    assert deleted.status_code == 204
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/credentials", headers=_auth(token)
    )
    assert listing.json()["data"] == []


async def test_daemon_refetch_rotates_envelope(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "refetch")
    cred = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "ROT_KEY", "kind": "env", "value": "rot-me-777", "env_name": "ROT_KEY"},
        headers=_auth(token),
    )
    cred_id = cred.json()["data"]["id"]
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id, task_spec={"credential_ids": [cred_id]})
    await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt_id = claimed.json()["data"]["attempt"]["id"]
    first_envelope = claimed.json()["data"]["attempt"]["credentials"][0]["envelope"]

    refetched = await api_client.post(
        f"/api/v1/daemon/attempts/{attempt_id}/credentials:refetch",
        json={"lease_seq": 1},
        headers=_daemon(daemon_token),
    )
    assert refetched.status_code == 200
    delivered = refetched.json()["data"]["credentials"][0]
    assert delivered["value"] == "rot-me-777"
    assert delivered["envelope"] != first_envelope  # new envelope, old revoked


async def test_logs_sse_fallback_smoke(api_client, runtime_worker, session_factory):
    token, ws_id = await _setup_world(api_client, "sse")
    created, daemon_token = await _activated_runtime(api_client, token, ws_id)
    idem = await _enqueue(session_factory, ws_id)
    execution = await _wait_queued(session_factory, ws_id, idem)
    claimed = await _claim(api_client, created["id"], daemon_token)
    attempt = claimed.json()["data"]["attempt"]
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=_daemon(daemon_token),
    )
    await api_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/logs",
        json={"lease_seq": 1, "stream": "stdout", "start_offset": 0, "lines": ["sse-line-1"]},
        headers=_daemon(daemon_token),
    )
    # Finish the attempt so the stream emits an end frame and closes.
    await api_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "completed", "result": {}},
        headers=_daemon(daemon_token),
    )
    seen_log = seen_end = False
    async with api_client.stream(
        "GET",
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}/logs/stream?offset=0",
        headers=_auth(token),
        timeout=20,
    ) as stream:
        async for line in stream.aiter_lines():
            if not line.startswith("data: "):
                continue
            frame = __import__("json").loads(line[len("data: "):])
            if frame.get("type") == "log" and frame.get("line") == "sse-line-1":
                seen_log = True
            if frame.get("type") == "end":
                seen_end = True
                break
    assert seen_log and seen_end
