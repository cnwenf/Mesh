"""In-process route coverage for the runtime HTTP surface.

Drives the REAL FastAPI app (create_app over the test services) through
httpx ASGITransport so the console + daemon handlers execute inside the
coverage-measured process (the red-line e2e runs the same flows through
uninstrumented uvicorn subprocesses).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.runtime import Approval, Runtime, TaskExecution

pytestmark = pytest.mark.unit


def _settings_kwargs(db_url: str, redis_url: str, **overrides) -> dict:
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "runtime-routes-signing-secret-0000000000",
        "daemon_tls_required": False,
        "runtime_lease_seconds": 120,
        "storage_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-routes-test",
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def app_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    # ASGITransport skips the lifespan; bootstrap the storage bucket here
    # (MinIO unreachable → skip the storage-dependent flows).
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage optional in unit context
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _world(client: httpx.AsyncClient, suffix: str) -> tuple[str, str]:
    """Register + login + workspace; returns (jwt, ws_id)."""
    email = f"routes-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Routes-Test-12345", "display_name": "Routes"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Routes-Test-12345"}
    )
    token = login.json()["data"]["access_token"]
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"Routes {suffix}", "slug": f"routes-{suffix}"},
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["data"]
    return token, ws["id"]


async def _runtime(client, token, ws_id, name="rt-1", **body):
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes",
        json={"name": name, "kind": "self_hosted", "labels": {}, "max_concurrent": 2, **body},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _daemon_token(client, created: dict) -> str:
    resp = await client.post(
        "/api/v1/daemon/runtimes:activate",
        json={
            "activation_code": created["activation"]["code"],
            "metadata": {"hostname": "h", "capabilities": ["python"]},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["runtime_token"]


async def _enqueue_direct(session_factory, ws_id, **overrides) -> TaskExecution:
    """Insert a queued execution directly (relay-independent route tests)."""
    from mesh.db.models.runtime import TaskExecution as TE

    execution = TE(
        workspace_id=uuid.UUID(ws_id),
        agent_id=None,
        status="queued",
        **overrides,
    )
    async with session_factory() as session, session.begin():
        session.add(execution)
        await session.flush()
        session.expunge(execution)
    return execution


async def test_console_runtime_lifecycle_over_http(app_client, session_factory):
    token, ws_id = await _world(app_client, "life")
    created = await _runtime(app_client, token, ws_id)
    assert created["status"] == "pending"
    assert created["activation"]["code"].startswith("ACT-")
    rid = created["id"]
    auth = {"Authorization": f"Bearer {token}"}

    # List (filters + queue depth), detail, patch.
    listing = await app_client.get(f"/api/v1/workspaces/{ws_id}/runtimes", headers=auth)
    assert listing.status_code == 200
    assert listing.json()["data"][0]["id"] == rid
    assert "queue_depth" in listing.json()
    by_status = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes?status=pending&kind=self_hosted", headers=auth
    )
    assert len(by_status.json()["data"]) == 1

    detail = await app_client.get(f"/api/v1/workspaces/{ws_id}/runtimes/{rid}", headers=auth)
    assert detail.status_code == 200
    assert detail.json()["data"]["recent_heartbeats"] == []

    patched = await app_client.patch(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}",
        json={"name": "renamed", "labels": {"a": "b"}, "max_concurrent": 5},
        headers=auth,
    )
    assert patched.json()["data"]["name"] == "renamed"

    # 404 on non-UUID / unknown ids.
    bad = await app_client.get(f"/api/v1/workspaces/{ws_id}/runtimes/not-a-uuid", headers=auth)
    assert bad.status_code == 404
    unknown = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes/{uuid.uuid4()}", headers=auth
    )
    assert unknown.status_code == 404

    # Activate → pause (token revoked) → resume → rotate → delete.
    daemon_token = await _daemon_token(app_client, created)
    hb = await app_client.post(
        f"/api/v1/daemon/runtimes/{rid}:heartbeat",
        json={"current_load": 0, "health": "healthy", "metrics": {"cpu": 1}, "inflight": []},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert hb.status_code == 200
    paused = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}:pause", json={}, headers=auth
    )
    assert paused.json()["data"]["status"] == "paused"
    dead_hb = await app_client.post(
        f"/api/v1/daemon/runtimes/{rid}:heartbeat",
        json={"current_load": 0, "health": "healthy", "metrics": {}, "inflight": []},
        headers={"Authorization": f"Bearer {daemon_token}"},
    )
    assert dead_hb.status_code == 401  # NEW-L2: revoked token dies with the pause
    resumed = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}:resume", json={}, headers=auth
    )
    assert resumed.json()["data"]["status"] == "online"
    rotated = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}/tokens:rotate", json={}, headers=auth
    )
    new_token = rotated.json()["data"]["runtime_token"]
    assert new_token.startswith("mesh_rt_")
    # Per-runtime execution history endpoint.
    history = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}/executions", headers=auth
    )
    assert history.status_code == 200
    deleted = await app_client.delete(
        f"/api/v1/workspaces/{ws_id}/runtimes/{rid}", headers=auth
    )
    assert deleted.status_code == 204
    gone = await app_client.get(f"/api/v1/workspaces/{ws_id}/runtimes/{rid}", headers=auth)
    assert gone.status_code == 404


async def test_daemon_claim_report_cycle_over_http(app_client, session_factory):
    token, ws_id = await _world(app_client, "cycle")
    created = await _runtime(app_client, token, ws_id)
    daemon_token = await _daemon_token(app_client, created)
    dh = {"Authorization": f"Bearer {daemon_token}"}
    auth = {"Authorization": f"Bearer {token}"}
    rid = created["id"]

    # Empty queue → 204.
    empty = await app_client.post(
        f"/api/v1/daemon/runtimes/{rid}/executions:claim", json={}, headers=dh
    )
    assert empty.status_code == 204

    execution = await _enqueue_direct(session_factory, ws_id)
    claimed = await app_client.post(
        f"/api/v1/daemon/runtimes/{rid}/executions:claim", json={"diagnostics": {}}, headers=dh
    )
    assert claimed.status_code == 200
    attempt = claimed.json()["data"]["attempt"]
    assert attempt["attempt_number"] == 1

    # running → logs (redacted) → renew → completed.
    started = await app_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=dh,
    )
    assert started.status_code == 200
    logs = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/logs",
        json={"lease_seq": 1, "stream": "stdout", "start_offset": 0, "lines": ["hello"]},
        headers=dh,
    )
    assert logs.status_code == 200
    renewed = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}:renew-lease",
        json={"lease_seq": 1},
        headers=dh,
    )
    assert renewed.json()["data"]["lease_seq"] == 2
    # checkout report without a repo configured → 422 checkout_not_configured.
    norepo = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/checkouts",
        json={"lease_seq": 2, "status": "ready"},
        headers=dh,
    )
    assert norepo.status_code == 422
    done = await app_client.patch(
        f"/api/v1/daemon/attempts/{attempt['id']}",
        json={"lease_seq": 2, "status": "completed", "result": {"exit_code": 0}},
        headers=dh,
    )
    assert done.json()["data"]["execution_status"] == "completed"

    # REST log read + SSE fallback frames (in-process streaming).
    page = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}/logs?offset=0", headers=auth
    )
    assert [item["line"] for item in page.json()["data"]["lines"]] == ["hello"]
    frames = []
    async with app_client.stream(
        "GET",
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}/logs/stream?offset=0",
        headers=auth,
    ) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                import json as _json

                frames.append(_json.loads(line[6:]))
                if frames[-1].get("type") == "end":
                    break
    assert any(f.get("type") == "log" and f.get("line") == "hello" for f in frames)
    assert frames[-1]["type"] == "end"

    # Foreign runtime id with this token → 403; unknown attempt → 404.
    other = await _runtime(app_client, token, ws_id, name="rt-other")
    foreign = await app_client.post(
        f"/api/v1/daemon/runtimes/{other['id']}/executions:claim", json={}, headers=dh
    )
    assert foreign.status_code == 403
    ghost = await app_client.patch(
        f"/api/v1/daemon/attempts/{uuid.uuid4()}",
        json={"lease_seq": 1, "status": "completed"},
        headers=dh,
    )
    assert ghost.status_code == 404
    # Malformed attempt id → 404 as well.
    malformed = await app_client.patch(
        "/api/v1/daemon/attempts/not-a-uuid",
        json={"lease_seq": 1, "status": "completed"},
        headers=dh,
    )
    assert malformed.status_code == 404


async def test_daemon_tls_gate_403(db_url, redis_url, session_factory):
    """With daemon_tls_required=True, plaintext (non-proxy) requests → 403."""
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(
        load_settings(**_settings_kwargs(db_url, redis_url, daemon_tls_required=True))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/daemon/runtimes:activate",
            json={"activation_code": "ACT-AAAA-BBBB-CCCC", "metadata": {}},
        )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "tls_required"


async def test_console_executions_cancel_freeze_credentials_approvals(
    app_client, session_factory
):
    token, ws_id = await _world(app_client, "exec")
    auth = {"Authorization": f"Bearer {token}"}
    created = await _runtime(app_client, token, ws_id)
    daemon_token = await _daemon_token(app_client, created)
    dh = {"Authorization": f"Bearer {daemon_token}"}

    # Credentials CRUD over HTTP (plaintext in, *** out; reserved env 422).
    bad = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "EVIL", "kind": "env", "value": "x", "env_name": "LD_PRELOAD"},
        headers=auth,
    )
    assert bad.status_code == 422
    cred = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/credentials",
        json={"name": "GOOD", "kind": "env", "value": "good-value-1", "env_name": "GOOD_VAR"},
        headers=auth,
    )
    assert cred.status_code == 201
    cred_id = cred.json()["data"]["id"]
    listing = await app_client.get(f"/api/v1/workspaces/{ws_id}/credentials", headers=auth)
    assert listing.json()["data"][0]["value"] == "***"
    missing = await app_client.delete(
        f"/api/v1/workspaces/{ws_id}/credentials/{uuid.uuid4()}", headers=auth
    )
    assert missing.status_code == 404

    # Claim an execution that carries the credential; refetch rotates; freeze
    # revokes; refetch after freeze → 409 envelope_revoked.
    execution = await _enqueue_direct(
        session_factory, ws_id, task_spec={"credential_ids": [cred_id]}
    )
    claimed = await app_client.post(
        f"/api/v1/daemon/runtimes/{created['id']}/executions:claim", json={}, headers=dh
    )
    attempt = claimed.json()["data"]["attempt"]
    assert attempt["credentials"][0]["value"] == "good-value-1"
    refetched = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/credentials:refetch",
        json={"lease_seq": 1},
        headers=dh,
    )
    assert refetched.status_code == 200
    assert refetched.json()["data"]["credentials"][0]["envelope"] != attempt["credentials"][0]["envelope"]
    # Refetch on a terminal/foreign attempt paths.
    stale = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/credentials:refetch",
        json={"lease_seq": 99},
        headers=dh,
    )
    assert stale.status_code == 409
    froze = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}:freeze", json={}, headers=auth
    )
    assert froze.json()["data"]["revoked_envelopes"] == 1
    after = await app_client.post(
        f"/api/v1/daemon/attempts/{attempt['id']}/credentials:refetch",
        json={"lease_seq": 1},
        headers=dh,
    )
    assert after.status_code == 409
    assert after.json()["error"]["code"] == "envelope_revoked"

    # Executions listing + detail.
    execs = await app_client.get(f"/api/v1/workspaces/{ws_id}/executions", headers=auth)
    assert execs.status_code == 200
    assert len(execs.json()["data"]) == 1
    filtered = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/executions?status=completed", headers=auth
    )
    assert filtered.json()["data"] == []
    detail = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{execution.id}", headers=auth
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["credentials"][0]["value"] == "***"
    missing_exec = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/executions/{uuid.uuid4()}", headers=auth
    )
    assert missing_exec.status_code == 404

    # Cancel a queued execution via console; cancel again → idempotent.
    queued_exec = await _enqueue_direct(session_factory, ws_id)
    cancel1 = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{queued_exec.id}:cancel", json={}, headers=auth
    )
    assert cancel1.json()["data"]["status"] == "cancelled"
    cancel2 = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{queued_exec.id}:cancel", json={}, headers=auth
    )
    assert cancel2.json()["data"]["status"] == "cancelled"
    cancel_missing = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/executions/{uuid.uuid4()}:cancel", json={}, headers=auth
    )
    assert cancel_missing.status_code == 404

    # Approvals console surface: list / get / approve 404 / decide flow.
    empty_inbox = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals?role=mine", headers=auth
    )
    assert empty_inbox.status_code == 200
    missing_appr = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals/{uuid.uuid4()}", headers=auth
    )
    assert missing_appr.status_code == 404

    # Build a pending approval through the daemon protocol, then approve it.
    agent_exec = await _enqueue_direct(session_factory, ws_id)
    # The execution needs an agent roster member for the requester — create an
    # agent through the console API.
    agent = (
        await app_client.post(
            f"/api/v1/workspaces/{ws_id}/agents",
            json={"name": "Approval Agent"},
            headers=auth,
        )
    ).json()["data"]
    async with session_factory() as session, session.begin():
        from sqlalchemy import update

        await session.execute(
            update(TaskExecution)
            .where(TaskExecution.id == agent_exec.id)
            .values(agent_id=uuid.UUID(agent["id"]), status="queued")
        )
    claimed2 = await app_client.post(
        f"/api/v1/daemon/runtimes/{created['id']}/executions:claim", json={}, headers=dh
    )
    attempt2 = claimed2.json()["data"]["attempt"]
    await app_client.patch(
        f"/api/v1/daemon/attempts/{attempt2['id']}",
        json={"lease_seq": 1, "status": "running"},
        headers=dh,
    )
    appr = await app_client.post(
        f"/api/v1/daemon/executions/{agent_exec.id}/approvals",
        json={
            "lease_seq": 1,
            "attempt_id": attempt2["id"],
            "action_summary": {"action": "exec:shell"},
            "resume_context": {"checkpoint_ref": "c1"},
        },
        headers=dh,
    )
    assert appr.status_code == 200, appr.text
    approval_id = appr.json()["data"]["id"]
    inbox = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals?role=mine", headers=auth
    )
    assert [a["id"] for a in inbox.json()["data"]] == [approval_id]
    got = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/approvals/{approval_id}", headers=auth
    )
    assert got.json()["data"]["status"] == "pending"
    approved = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/approvals/{approval_id}:approve",
        json={"comment": "ok"},
        headers=auth,
    )
    assert approved.json()["data"]["execution_status"] == "queued"
    # Idempotent re-decide.
    again = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/approvals/{approval_id}:reject", json={}, headers=auth
    )
    assert again.json()["data"]["status"] == "approved"

    # Daemon approval error paths: missing/404.
    appr_404 = await app_client.post(
        f"/api/v1/daemon/executions/{uuid.uuid4()}/approvals",
        json={"lease_seq": 1, "attempt_id": attempt2["id"], "action_summary": {}},
        headers=dh,
    )
    assert appr_404.status_code == 404
    appr_malformed = await app_client.post(
        "/api/v1/daemon/executions/not-a-uuid/approvals",
        json={"lease_seq": 1, "attempt_id": attempt2["id"], "action_summary": {}},
        headers=dh,
    )
    assert appr_malformed.status_code == 404


async def test_execution_channel_checker(session_factory):
    """execution:{id}[:logs] subscription authorization (README §6.7)."""
    from mesh.realtime.auth import Principal
    from mesh.runtime.channels import make_execution_channel_checker

    world_user_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        from mesh.db.models.user import User
        from mesh.db.models.workspace import Workspace

        session.add(Workspace(id=ws_id, name="Chan WS", slug=f"chan-{ws_id.hex[:8]}"))
        session.add(
            User(
                id=world_user_id,
                email=f"chan-{ws_id.hex[:6]}@example.com",
                display_name="C",
                password_hash="x",
            )
        )
        await session.flush()
        from mesh.db.models.runtime import TaskExecution as TE

        execution = TE(workspace_id=ws_id, agent_id=None, status="queued")
        session.add(execution)
        await session.flush()
        execution_id = execution.id

    checker = make_execution_channel_checker(session_factory)
    member_principal = Principal(
        subject=str(world_user_id), workspace_ids=frozenset({ws_id})
    )
    outsider_principal = Principal(
        subject=str(world_user_id), workspace_ids=frozenset({uuid.uuid4()})
    )
    assert await checker(member_principal, f"execution:{execution_id}")
    assert await checker(member_principal, f"execution:{execution_id}:logs")
    # Other workspace's principal: not a member of the execution's workspace.
    assert not await checker(outsider_principal, f"execution:{execution_id}")
    # Garbage channel keys.
    assert not await checker(member_principal, "execution:not-a-uuid")
    assert not await checker(member_principal, "execution:not-a-uuid:logs")
