"""Autopilot E2E — REAL api + REAL worker + REAL PostgreSQL (autopilot.md §5).

Covers the §5 acceptance red lines over actual HTTP and a real worker
process (outbox relay with the chained autopilot event matcher, scheduler
and executor loops):

* rule CRUD / validation (invalid_cron 400, executor_required 422, dup 409);
* event trigger through the outbox relay — including the crash-recovery
  path (business row committed BEFORE the relay starts → run still created);
* inbound webhook: HMAC 401 (invalid / stale / unknown token), valid
  dispatch, event-id dedup, rejected-namespace anti-preoccupy;
* approval gate: test-run → waiting_approval → approve resumes / reject
  cancels (README §6.10 thin wrapper over the unified approvals entity);
* kill switch: pause-all / restore;
* schedule: a due rule fires exactly once (atomic claim advances
  next_run_at; later passes do not re-fire);
* concurrency guardrail: a busy rule (limit 1) drops further triggers;
* anti-loop: the create_issue ↔ issue_created self-loop is cut by the
  cascade-depth guardrail (issue-artifact lineage), not by rate-limit
  fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.autopilot import Autopilot, WebhookEvent

PASSWORD = "Autopilot-E2E-123"
WORKER_READY_WAIT_SECONDS = 2.5


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="module")
async def autopilot_worker(provision_database):
    """Real worker process: relay (+ autopilot matcher chain) + scheduler +
    executor loops at fast intervals."""
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_AUTOPILOT_SCHEDULE_INTERVAL"] = "0.3"
    env["MESH_AUTOPILOT_EXECUTOR_INTERVAL"] = "0.3"
    env["MESH_RUNTIME_REAPER_INTERVAL"] = "1"
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_BUCKET"] = os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e")
    log_file = open("/tmp/autopilot_worker.log", "wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    await asyncio.sleep(WORKER_READY_WAIT_SECONDS)
    assert process.poll() is None, "worker died during startup"
    yield process
    log_file.close()
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "AP E2E"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


async def _setup_world(api_client: httpx.AsyncClient, suffix: str) -> tuple[str, str, str]:
    token = await _register_and_login(api_client, f"ap-{suffix}@e2e.mesh")
    resp = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "AP E2E", "slug": f"ap-{suffix}"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["data"]["id"]
    agent_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={"name": f"agent-{suffix}"},
        headers=_auth(token),
    )
    assert agent_resp.status_code == 201, agent_resp.text
    return token, ws_id, agent_resp.json()["data"]["id"]


async def _create_rule(
    api_client: httpx.AsyncClient, token: str, ws_id: str, body: dict
) -> dict:
    resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _poll_runs(
    api_client: httpx.AsyncClient,
    token: str,
    ws_id: str,
    rule_id: str,
    *,
    expect: int,
    timeout: float = 12.0,
) -> list[dict]:
    deadline = time.monotonic() + timeout
    last: list[dict] = []
    while time.monotonic() < deadline:
        resp = await api_client.get(
            f"/api/v1/workspaces/{ws_id}/autopilots/{rule_id}/runs", headers=_auth(token)
        )
        assert resp.status_code == 200, resp.text
        last = resp.json()["data"]
        if len(last) == expect:
            return last
        await asyncio.sleep(0.3)
    return last


# ---------------------------------------------------------------------------
# rule CRUD / validation
# ---------------------------------------------------------------------------


async def test_rule_crud_validation_and_preview(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "crud")

    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "每日汇总",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "汇总"}
            ],
            "executor_agent_id": agent_id,
        },
    )
    assert rule["next_run_at"] is not None
    assert rule["guardrails"]["cascade_max_depth"] == 3  # guardrails default-ON

    # invalid cron → 400 invalid_cron
    bad = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "bad-cron",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "99 * * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cron"

    # schedule without timezone → 400 invalid_trigger_config
    notz = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "no-tz",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    assert notz.status_code == 400
    assert notz.json()["error"]["code"] == "invalid_trigger_config"

    # run_agent_prompt without executor → 422 executor_required
    noex = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "no-executor",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "run_agent_prompt", "prompt": "x"}],
        },
        headers=_auth(token),
    )
    assert noex.status_code == 422
    assert noex.json()["error"]["code"] == "executor_required"

    # duplicate name → 409
    dup = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "每日汇总",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    assert dup.status_code == 409

    # preview-schedule returns upcoming fire times
    preview = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/preview-schedule?count=5",
        headers=_auth(token),
    )
    assert preview.status_code == 200
    assert len(preview.json()["data"]["next_runs"]) == 5

    # pause / resume lifecycle
    paused = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/pause", headers=_auth(token)
    )
    assert paused.json()["data"]["status"] == "paused"
    resumed = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/resume", headers=_auth(token)
    )
    assert resumed.json()["data"]["status"] == "active"
    assert resumed.json()["data"]["next_run_at"] is not None

    # cross-workspace isolation: foreign token sees 404
    other_token, other_ws, _ = await _setup_world(api_client, "crud-other")
    foreign = await api_client.get(
        f"/api/v1/workspaces/{other_ws}/autopilots/{rule['id']}", headers=_auth(other_token)
    )
    assert foreign.status_code == 404
    del other_ws


# ---------------------------------------------------------------------------
# event trigger through the outbox relay (+ crash recovery)
# ---------------------------------------------------------------------------


async def test_issue_created_trigger_through_relay(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "evt")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "new-issue-ops",
            "trigger_type": "issue_created",
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "处理"}
            ],
            "executor_agent_id": agent_id,
        },
    )
    # creating an issue writes issue.created realtime outbox in-transaction;
    # the worker relay's autopilot matcher must create a run.
    project_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": "P1", "key": "P1"},
        headers=_auth(token),
    )
    project_id = project_resp.json()["data"]["id"]
    issue_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": "线上告警", "project_id": project_id},
        headers=_auth(token),
    )
    assert issue_resp.status_code in (200, 201), issue_resp.text
    runs = await _poll_runs(api_client, token, ws_id, rule["id"], expect=1)
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "issue_created"
    assert runs[0]["trigger_snapshot"]["issue"]["title"] == "线上告警"
    # the run enqueued a task_execution (run_agent_prompt action)
    assert runs[0]["status"] in ("running", "pending", "retrying")


async def test_relay_crash_recovery_committed_event_still_fires(
    api_client, provision_database
):
    """§5.1 T5-style: the trigger event commits BEFORE the relay starts;
    starting the relay afterwards must still create the run (at-least-once)."""
    token, ws_id, agent_id = await _setup_world(api_client, "crash")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "crash-recovery",
            "trigger_type": "issue_created",
            "action_config": [{"type": "send_notification", "message": "ok"}],
            "executor_agent_id": agent_id,
        },
    )
    # create the issue with NO worker running: the outbox row stays pending.
    issue_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": "commit-before-relay"},
        headers=_auth(token),
    )
    assert issue_resp.status_code in (200, 201), issue_resp.text
    runs_now = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/runs", headers=_auth(token)
    )
    assert runs_now.json()["data"] == []  # nothing dispatched yet

    # start a fresh worker; it must pick up the committed pending event.
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_AUTOPILOT_SCHEDULE_INTERVAL"] = "0.3"
    env["MESH_AUTOPILOT_EXECUTOR_INTERVAL"] = "0.3"
    env["MESH_RUNTIME_REAPER_INTERVAL"] = "2"
    storage_endpoint = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000")
    env["MESH_STORAGE_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_PUBLIC_ENDPOINT"] = storage_endpoint
    env["MESH_STORAGE_BUCKET"] = os.environ.get("MESH_TEST_STORAGE_BUCKET", "mesh-e2e")
    log_file = open("/tmp/autopilot_worker_crash.log", "wb")
    worker = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        assert worker.poll() is None, "worker died during startup"
        runs = await _poll_runs(api_client, token, ws_id, rule["id"], expect=1, timeout=15)
        assert len(runs) == 1, "committed event was lost across relay restart"
    finally:
        worker.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            worker.wait(timeout=10)
        log_file.close()


# ---------------------------------------------------------------------------
# inbound webhook: signature, dedup, dispatch
# ---------------------------------------------------------------------------


async def test_webhook_signature_dedup_and_dispatch(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "hook")

    secret_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets",
        json={"label": "prod"},
        headers=_auth(token),
    )
    assert secret_resp.status_code == 201, secret_resp.text
    cred = secret_resp.json()["data"]
    webhook_token = cred["token"]
    webhook_secret = cred["secret"]
    assert webhook_token.startswith("whk_") and webhook_secret.startswith("whs_")

    # secret listing never echoes material
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets", headers=_auth(token)
    )
    body_text = listing.text
    assert webhook_secret not in body_text and webhook_token not in body_text

    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "alert-intake",
            "trigger_type": "webhook_received",
            "trigger_config": {"secret_id": cred["id"], "event_types": ["alert.triggered"]},
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "诊断"}
            ],
            "executor_agent_id": agent_id,
            # runs never complete here (no runtime claims the execution), so
            # widen concurrency/rate gates to let both legit events through
            "concurrency_limit": 10,
            "rate_limit_max": 100,
        },
    )

    payload = b'{"alert": {"severity": "critical"}}'
    now_ts = int(datetime.now(UTC).timestamp())

    def signature(secret: str, ts: int) -> str:
        digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
        return f"t={ts},v1={digest}"

    # (a) invalid signature → 401, never dispatched
    bad = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": f"t={now_ts},v1={'0' * 64}",
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_bad",
        },
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_signature"

    # (b) stale timestamp (replay) → 401
    stale = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": signature(webhook_secret, now_ts - 3600),
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_stale",
        },
    )
    assert stale.status_code == 401

    # (c) unknown token → 401
    unknown = await api_client.post(
        "/api/v1/webhooks/inbound/whk_nonexistent",
        content=payload,
        headers={"X-Signature": signature(webhook_secret, now_ts), "X-Event-Id": "evt_x"},
    )
    assert unknown.status_code == 401

    # (d) valid signature → dispatched + run created
    ok = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": signature(webhook_secret, now_ts),
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_ok",
        },
    )
    assert ok.status_code == 200, ok.text
    ok_body = ok.json()
    assert ok_body["received"] is True
    assert ok_body["process_status"] == "dispatched"
    assert ok_body["run_id"]
    assert "data" not in ok_body  # bare contract, not the §6.14 envelope

    # (e) same event id again → deduped, no new run
    dup = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": signature(webhook_secret, now_ts),
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_ok",
        },
    )
    assert dup.status_code == 200
    assert dup.json()["process_status"] == "deduped"

    # (f) a forgery with a FRESH event id cannot preoccupy: the rejected row
    # lives in the rejected: namespace, so a later VALID event with the same
    # id still dispatches.
    forged = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": f"t={now_ts},v1={'f' * 64}",
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_late",
        },
    )
    assert forged.status_code == 401
    legit = await api_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=payload,
        headers={
            "X-Signature": signature(webhook_secret, now_ts),
            "X-Event-Type": "alert.triggered",
            "X-Event-Id": "evt_late",
        },
    )
    assert legit.status_code == 200
    assert legit.json()["process_status"] == "dispatched"

    runs = await _poll_runs(api_client, token, ws_id, rule["id"], expect=2)
    assert len(runs) == 2  # evt_ok + evt_late, NOT the forged/duped ones


# ---------------------------------------------------------------------------
# approval gate (README §6.10 thin wrapper)
# ---------------------------------------------------------------------------


async def test_approval_gate_approve_and_reject(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "appr")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "gated",
            "trigger_type": "issue_created",
            "require_approval": True,
            "action_config": [{"type": "send_notification", "message": "x"}],
            "executor_agent_id": agent_id,
        },
    )
    # test-run → executor parks it at waiting_approval
    tr = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/test-run",
        json={},
        headers=_auth(token),
    )
    assert tr.status_code == 202, tr.text
    run_id = tr.json()["data"]["run_id"]

    async def wait_status(expected: str) -> dict:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            detail = await api_client.get(
                f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}", headers=_auth(token)
            )
            data = detail.json()["data"]
            if data["status"] == expected:
                return data
            await asyncio.sleep(0.3)
        raise AssertionError(f"run did not reach {expected}")

    await wait_status("waiting_approval")

    # approve through the autopilot thin wrapper → run resumes (running →
    # then the executor finishes the notification action → succeeded)
    approve = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/approve",
        json={"comment": "go"},
        headers=_auth(token),
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["data"]["status"] == "approved"
    final = await wait_status("succeeded")
    assert final["status"] == "succeeded"

    # second run: reject → cancelled
    tr2 = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/test-run",
        json={},
        headers=_auth(token),
    )
    run2_id = tr2.json()["data"]["run_id"]
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        detail = await api_client.get(
            f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run2_id}", headers=_auth(token)
        )
        if detail.json()["data"]["status"] == "waiting_approval":
            break
        await asyncio.sleep(0.3)
    reject = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run2_id}/reject",
        json={},
        headers=_auth(token),
    )
    assert reject.status_code == 200
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        detail = await api_client.get(
            f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run2_id}", headers=_auth(token)
        )
        if detail.json()["data"]["status"] == "cancelled":
            break
        await asyncio.sleep(0.3)
    assert detail.json()["data"]["status"] == "cancelled"
    assert detail.json()["data"]["error"]["code"] == "approval_rejected"


# ---------------------------------------------------------------------------
# kill switch
# ---------------------------------------------------------------------------


async def test_kill_switch_pauses_and_restores(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "kill")
    for name in ("r1", "r2"):
        await _create_rule(
            api_client,
            token,
            ws_id,
            {
                "name": name,
                "trigger_type": "schedule",
                "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
                "action_config": [{"type": "send_notification", "message": "x"}],
                "executor_agent_id": agent_id,
            },
        )
    on = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/kill-switch",
        json={"enabled": True, "reason": "止血"},
        headers=_auth(token),
    )
    assert on.status_code == 200, on.text
    assert on.json()["data"]["paused_autopilots"] == 2
    listing = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots", headers=_auth(token)
    )
    assert all(r["status"] == "paused" for r in listing.json()["data"])

    off = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/kill-switch",
        json={"enabled": False},
        headers=_auth(token),
    )
    assert off.json()["data"]["paused_autopilots"] == 2
    listing2 = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots", headers=_auth(token)
    )
    assert all(r["status"] == "active" for r in listing2.json()["data"])


# ---------------------------------------------------------------------------
# schedule: atomic claim fires exactly once
# ---------------------------------------------------------------------------


async def test_schedule_due_rule_fires_exactly_once(
    api_client, autopilot_worker, session_factory
):
    token, ws_id, agent_id = await _setup_world(api_client, "sched")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "hourly",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 * * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "tick"}],
            "executor_agent_id": agent_id,
        },
    )
    # force next_run_at into the past so the scheduler picks it up now
    past = datetime.now(UTC) - timedelta(minutes=2)
    async with session_factory() as session, session.begin():
        row = (
            await session.execute(
                select(Autopilot).where(Autopilot.id == uuid.UUID(rule["id"]))
            )
        ).scalar_one()
        row.next_run_at = past

    runs = await _poll_runs(api_client, token, ws_id, rule["id"], expect=1, timeout=10)
    assert len(runs) == 1
    # next_run_at advanced into the future → subsequent passes don't re-fire
    await asyncio.sleep(1.2)
    runs_again = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/runs", headers=_auth(token)
    )
    assert len(runs_again.json()["data"]) == 1
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Autopilot).where(Autopilot.id == uuid.UUID(rule["id"]))
            )
        ).scalar_one()
    assert row.next_run_at is not None and row.next_run_at > datetime.now(UTC)


# ---------------------------------------------------------------------------
# concurrency guardrail
# ---------------------------------------------------------------------------


async def test_concurrency_limit_drops_second_trigger(api_client, autopilot_worker):
    token, ws_id, agent_id = await _setup_world(api_client, "conc")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "serial",
            "trigger_type": "issue_created",
            "concurrency_limit": 1,
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "x"}
            ],
            "executor_agent_id": agent_id,
        },
    )
    # first issue → run goes running (the enqueued execution never completes:
    # no runtime is registered to claim it)
    r1 = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json={"title": "i1"}, headers=_auth(token)
    )
    assert r1.status_code in (200, 201)
    runs1 = await _poll_runs(api_client, token, ws_id, rule["id"], expect=1)
    assert len(runs1) == 1

    # second issue while the first run is in flight → dropped by the gate
    r2 = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json={"title": "i2"}, headers=_auth(token)
    )
    assert r2.status_code in (200, 201)
    await asyncio.sleep(2.0)
    runs2 = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/runs", headers=_auth(token)
    )
    assert len(runs2.json()["data"]) == 1, "second trigger should be dropped by concurrency gate"


# ---------------------------------------------------------------------------
# webhook audit trail in the database
# ---------------------------------------------------------------------------


async def test_rejected_events_audited_in_rejected_namespace(
    api_client, autopilot_worker, session_factory
):
    token, ws_id, _agent_id = await _setup_world(api_client, "audit")
    secret_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets", json={}, headers=_auth(token)
    )
    cred = secret_resp.json()["data"]
    payload = b'{"x": 1}'
    resp = await api_client.post(
        f"/api/v1/webhooks/inbound/{cred['token']}",
        content=payload,
        headers={"X-Event-Id": "evt_unsigned"},  # no signature at all
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_signature"

    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(WebhookEvent).where(
                        WebhookEvent.workspace_id == uuid.UUID(ws_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    rejected = [e for e in events if e.process_status == "rejected"]
    assert len(rejected) == 1
    assert rejected[0].signature_status == "missing"
    assert rejected[0].idempotency_key.startswith("rejected:")
    # the rejected key is content-addressed, not the event id (anti-preoccupy)
    expected_suffix = hashlib.sha256(payload).hexdigest()
    assert rejected[0].idempotency_key == f"rejected:{expected_suffix}"
    # authorization headers are never persisted
    assert "authorization" not in (rejected[0].headers or {})


async def test_create_issue_self_loop_is_cut_by_cascade_depth(api_client, autopilot_worker):
    """M1 regression (§1.1 / §5.3 / P11): the create_issue ↔ issue_created loop.

    A rule triggered by ``issue_created`` whose action creates ANOTHER issue
    loops forever unless issue triggers trace their lineage: every round
    creates a NEW issue, so without the issue-artifact trace-back the
    cascade depth stayed zero and loop detection never fired — only the
    rate limit / daily budget backstop eventually stopped the spawn storm.

    With the lineage wired, ``cascade_depth`` accumulates along the
    artifact chain and the guardrail gate cuts the chain at
    ``cascade_max_depth`` — well before the (deliberately generous) rate
    limit could engage.
    """
    token, ws_id, _agent_id = await _setup_world(api_client, "loop")
    rule = await _create_rule(
        api_client,
        token,
        ws_id,
        {
            "name": "issue-spawner",
            "trigger_type": "issue_created",
            "action_config": [{"type": "create_issue", "title": "spawn {{run.id}}"}],
            # no approval gate on create_issue — the loop must run unattended
            "guardrails": {"approval_required_actions": [], "cascade_max_depth": 3},
            # generous rate limit / concurrency: had the bug survived, the
            # loop would blow PAST 4 runs toward the 50/hour ceiling — the
            # exact-4 assertion below is what proves the cascade cut, not a
            # rate-limit fallback
            "rate_limit_max": 50,
            "concurrency_limit": 10,
        },
    )
    project_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/projects",
        json={"name": "LP", "key": "LP"},
        headers=_auth(token),
    )
    project_id = project_resp.json()["data"]["id"]
    seed = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/issues",
        json={"title": "loop-seed", "project_id": project_id},
        headers=_auth(token),
    )
    assert seed.status_code in (200, 201), seed.text

    # chain: seed → run(d0) → issue → run(d1) → issue → run(d2) → issue →
    # run(d3) → issue → next trigger is depth 4 > 3 → cascade_depth_exceeded
    runs = await _poll_runs(api_client, token, ws_id, rule["id"], expect=4, timeout=30.0)
    assert len(runs) == 4

    # the loop has STOPPED — no further runs after the cut
    await asyncio.sleep(4.0)
    resp = await api_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/runs", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    settled = resp.json()["data"]
    assert len(settled) == 4

    # the depth chain 0..3 is carried over the issue artifacts — only the
    # seed run lacks a parent
    depths = sorted(run["cascade_depth"] for run in settled)
    assert depths == [0, 1, 2, 3]
    assert [run["parent_run_id"] for run in settled].count(None) == 1
