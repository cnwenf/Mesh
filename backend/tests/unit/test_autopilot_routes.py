"""In-process route coverage for the autopilot HTTP surface (autopilot.md §3).

Drives the REAL FastAPI app through httpx ASGITransport — console CRUD,
the bare-JSON inbound webhook contract, and the approval thin wrappers.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from mesh.db.models.member import Member

pytestmark = pytest.mark.unit


def _settings_kwargs(db_url: str, redis_url: str, **overrides) -> dict:
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": "autopilot-routes-signing-secret-000000000",
        "daemon_tls_required": False,
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"),
        "storage_public_endpoint": os.environ.get(
            "MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9000"
        ),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-autopilot-routes-test",
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def app_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage optional in unit context
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _world(client: httpx.AsyncClient, suffix: str) -> tuple[str, str, str]:
    email = f"ap-routes-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Routes-Test-12345", "display_name": "AP Routes"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Routes-Test-12345"}
    )
    token = login.json()["data"]["access_token"]
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"AP {suffix}", "slug": f"ap-routes-{suffix}"},
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["data"]
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": f"ap-agent-{suffix}"},
            headers={"Authorization": f"Bearer {token}"},
        )
    ).json()["data"]
    return token, ws["id"], agent["id"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_rule_crud_and_lifecycle(app_client) -> None:
    token, ws_id, agent_id = await _world(app_client, "crud")
    # create
    resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "每日站会前汇总",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai"},
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "汇总"}
            ],
            "executor_agent_id": agent_id,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    rule = resp.json()["data"]
    assert rule["next_run_at"] is not None
    assert rule["guardrails"]["cascade_max_depth"] == 3

    # invalid cron → 400 invalid_cron
    bad = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "bad",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "99 * * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cron"

    # missing executor → 422 executor_required
    noexec = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "no-executor",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "run_agent_prompt", "prompt": "x"}],
        },
        headers=_auth(token),
    )
    assert noexec.status_code == 422
    assert noexec.json()["error"]["code"] == "executor_required"

    # duplicate name → 409
    dup = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "每日站会前汇总",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    assert dup.status_code == 409

    # list / detail
    listing = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots", headers=_auth(token)
    )
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1

    detail = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}", headers=_auth(token)
    )
    assert detail.status_code == 200

    preview = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/preview-schedule?count=3",
        headers=_auth(token),
    )
    assert preview.status_code == 200
    assert len(preview.json()["data"]["next_runs"]) == 3

    # patch
    patched = await app_client.patch(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}",
        json={"name": "重命名"},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "重命名"

    # pause / resume
    paused = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/pause", headers=_auth(token)
    )
    assert paused.json()["data"]["status"] == "paused"
    resumed = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/resume", headers=_auth(token)
    )
    assert resumed.json()["data"]["status"] == "active"

    # test-run dry + real
    dry = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/test-run",
        json={"dry_run": True, "simulate_trigger_payload": {}},
        headers=_auth(token),
    )
    assert dry.status_code == 200
    assert "would_run" in dry.json()["data"]
    real = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/test-run",
        json={"simulate_trigger_payload": {}},
        headers=_auth(token),
    )
    assert real.status_code == 202
    run_id = real.json()["data"]["run_id"]

    # runs list / detail / artifacts
    runs = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}/runs", headers=_auth(token)
    )
    assert runs.status_code == 200 and len(runs.json()["data"]) == 1
    run_detail = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}", headers=_auth(token)
    )
    assert run_detail.status_code == 200
    assert "attempts" in run_detail.json()["data"]
    artifacts = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/artifacts", headers=_auth(token)
    )
    assert artifacts.status_code == 200

    # delete (soft)
    deleted = await app_client.delete(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}", headers=_auth(token)
    )
    assert deleted.status_code == 204
    gone = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule['id']}", headers=_auth(token)
    )
    assert gone.status_code == 404


async def test_kill_switch_endpoint(app_client) -> None:
    token, ws_id, _agent_id = await _world(app_client, "kill")
    await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "r1",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    on = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/kill-switch",
        json={"enabled": True, "reason": "紧急止血"},
        headers=_auth(token),
    )
    assert on.status_code == 200
    assert on.json()["data"]["paused_autopilots"] == 1
    state = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/autopilots/kill-switch", headers=_auth(token)
    )
    assert state.json()["data"]["kill_switch"] is True
    off = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/kill-switch",
        json={"enabled": False},
        headers=_auth(token),
    )
    assert off.json()["data"]["paused_autopilots"] == 1


async def test_webhook_secrets_and_inbound_contract(app_client) -> None:
    token, ws_id, _agent_id = await _world(app_client, "hook")
    # create secret — plaintext shown once
    created = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets",
        json={"label": "prod"},
        headers=_auth(token),
    )
    assert created.status_code == 201
    secret_data = created.json()["data"]
    assert secret_data["token"].startswith("whk_")
    webhook_secret = secret_data["secret"]
    webhook_token = secret_data["token"]

    # list never echoes secrets
    listing = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets", headers=_auth(token)
    )
    assert listing.status_code == 200
    for row in listing.json()["data"]:
        assert "secret" not in row and "token" not in row

    # a webhook rule bound to this secret
    rule_resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "inbound",
            "trigger_type": "webhook_received",
            "trigger_config": {"secret_id": secret_data["id"]},
            "action_config": [{"type": "send_notification", "message": "alert"}],
        },
        headers=_auth(token),
    )
    assert rule_resp.status_code == 201, rule_resp.text

    # rotate: old token stops working
    rotated = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets/{secret_data['id']}/rotate",
        headers=_auth(token),
    )
    assert rotated.status_code == 201
    new_token = rotated.json()["data"]["token"]
    new_secret = rotated.json()["data"]["secret"]

    body = b'{"alert": {"severity": "critical"}}'
    timestamp = int(datetime.now(UTC).timestamp())

    def signature(secret: str) -> str:
        digest = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        return f"t={timestamp},v1={digest}"

    # OLD token → 401 (revoked)
    old = await app_client.post(
        f"/api/v1/webhooks/inbound/{webhook_token}",
        content=body,
        headers={"x-signature": signature(webhook_secret), "x-event-id": "evt_old"},
    )
    assert old.status_code == 401

    # bad signature on the NEW token → 401, never dispatched
    bad = await app_client.post(
        f"/api/v1/webhooks/inbound/{new_token}",
        content=body,
        headers={"x-signature": f"t={timestamp},v1={'0' * 64}", "x-event-id": "evt_bad"},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_signature"

    # valid signature → bare JSON contract, dispatched
    ok = await app_client.post(
        f"/api/v1/webhooks/inbound/{new_token}",
        content=body,
        headers={
            "x-signature": signature(new_secret),
            "x-event-id": "evt_ok",
            "x-event-type": "alert.triggered",
        },
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["received"] is True
    assert payload["process_status"] == "dispatched"
    assert payload["run_id"]
    assert "data" not in payload  # bare contract, NOT the §6.14 envelope

    # redelivery → deduped
    dup = await app_client.post(
        f"/api/v1/webhooks/inbound/{new_token}",
        content=body,
        headers={
            "x-signature": signature(new_secret),
            "x-event-id": "evt_ok",
            "x-event-type": "alert.triggered",
        },
    )
    assert dup.status_code == 200
    assert dup.json()["process_status"] == "deduped"

    # missing signature → 401
    missing = await app_client.post(
        f"/api/v1/webhooks/inbound/{new_token}", content=body, headers={"x-event-id": "evt_no_sig"}
    )
    assert missing.status_code == 401


async def test_approval_thin_wrapper(app_client, session_factory) -> None:
    from sqlalchemy import select

    from mesh.db.models.autopilot import Autopilot, AutopilotRun

    token, ws_id, _agent_id = await _world(app_client, "appr")
    # seed a rule + waiting_approval run + pending approval directly
    async with session_factory() as session:
        member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == uuid.UUID(ws_id), Member.member_type == "human"
                )
            )
        ).scalars().first()
    async with session_factory() as session, session.begin():
        rule = Autopilot(
            workspace_id=uuid.UUID(ws_id), name="gated",
            trigger_type="schedule",
            trigger_config={"cron": "0 9 * * *", "timezone": "UTC"},
            action_config=[{"type": "send_notification", "message": "x"}],
            guardrails={}, require_approval=True,
            created_by=member.id,
        )
        session.add(rule)
        await session.flush()
        run = AutopilotRun(
            workspace_id=uuid.UUID(ws_id), autopilot_id=rule.id,
            trigger_type="schedule", trigger_snapshot={}, status="waiting_approval",
        )
        session.add(run)
        await session.flush()
        from datetime import timedelta

        from mesh.db.models.runtime import Approval

        approval = Approval(
            workspace_id=uuid.UUID(ws_id),
            subject_type="autopilot_action",
            subject_run_id=run.id,
            requested_by_member_id=member.id,
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(approval)
        run_id, approval_id = str(run.id), str(approval.id)

    # approve through the autopilot thin wrapper
    resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/approve",
        json={"comment": "go"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "approved"
    async with session_factory() as session:
        row = await session.scalar(
            select(AutopilotRun).where(AutopilotRun.id == uuid.UUID(run_id))
        )
    assert row.status == "running"

    # wrapper on a run without a pending approval → 422
    conflict = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/approve", headers=_auth(token)
    )
    assert conflict.status_code == 422
    del approval_id

    # reject path on a fresh waiting run
    async with session_factory() as session, session.begin():
        run2 = AutopilotRun(
            workspace_id=uuid.UUID(ws_id), autopilot_id=rule.id,
            trigger_type="schedule", trigger_snapshot={}, status="waiting_approval",
        )
        session.add(run2)
        await session.flush()
        from mesh.db.models.runtime import Approval as Ap2

        ap2 = Ap2(
            workspace_id=uuid.UUID(ws_id),
            subject_type="autopilot_action",
            subject_run_id=run2.id,
            requested_by_member_id=member.id,
            action_summary={},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        session.add(ap2)
        run2_id = str(run2.id)
    rejected = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run2_id}/reject", headers=_auth(token)
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "rejected"
    async with session_factory() as session:
        row2 = await session.scalar(
            select(AutopilotRun).where(AutopilotRun.id == uuid.UUID(run2_id))
        )
    assert row2.status == "cancelled"


async def test_run_cancel_and_404s(app_client) -> None:
    token, ws_id, _agent_id = await _world(app_client, "cancel")
    created = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "c1",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token),
    )
    rule_id = created.json()["data"]["id"]
    test_run = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/{rule_id}/test-run",
        json={},
        headers=_auth(token),
    )
    run_id = test_run.json()["data"]["run_id"]
    cancelled = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/cancel", headers=_auth(token)
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    # cancelling again → 409 state conflict
    again = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilot-runs/{run_id}/cancel", headers=_auth(token)
    )
    assert again.status_code == 409
    # 404s
    assert (
        await app_client.get(f"/api/v1/workspaces/{ws_id}/autopilots/{uuid.uuid4()}", headers=_auth(token))
    ).status_code == 404
    assert (
        await app_client.get(
            f"/api/v1/workspaces/{ws_id}/autopilot-runs/{uuid.uuid4()}", headers=_auth(token)
        )
    ).status_code == 404
    # unauthenticated
    assert (
        await app_client.get(f"/api/v1/workspaces/{ws_id}/autopilots")
    ).status_code == 401


async def test_cross_workspace_isolation_404(app_client) -> None:
    token_a, ws_a, _ = await _world(app_client, "iso-a")
    _token_b, ws_b, _ = await _world(app_client, "iso-b")
    created = await app_client.post(
        f"/api/v1/workspaces/{ws_a}/autopilots",
        json={
            "name": "private",
            "trigger_type": "schedule",
            "trigger_config": {"cron": "0 9 * * *", "timezone": "UTC"},
            "action_config": [{"type": "send_notification", "message": "x"}],
        },
        headers=_auth(token_a),
    )
    rule_id = created.json()["data"]["id"]
    # token A is not a member of workspace B → membership gate 404
    foreign = await app_client.get(
        f"/api/v1/workspaces/{ws_b}/autopilots/{rule_id}", headers=_auth(token_a)
    )
    assert foreign.status_code == 404


# ---------------------------------------------------------------------------
# acceptance round 2: stateless preview endpoint + webhook events endpoint
# ---------------------------------------------------------------------------


async def test_stateless_preview_schedule_endpoint(app_client) -> None:
    token, ws_id, _ = await _world(app_client, "ap-preview")
    resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/preview-schedule",
        json={"cron": "0 9 * * 1-5", "timezone": "Asia/Shanghai", "count": 5},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]["next_runs"]) == 5
    bad = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots/preview-schedule",
        json={"cron": "not a cron", "timezone": "UTC"},
        headers=_auth(token),
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_cron"


async def test_webhook_events_endpoint_lists_and_filters(app_client) -> None:
    token, ws_id, agent_id = await _world(app_client, "ap-events")
    secret = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/webhook-secrets", json={}, headers=_auth(token)
    )
    cred = secret.json()["data"]
    rule_resp = await app_client.post(
        f"/api/v1/workspaces/{ws_id}/autopilots",
        json={
            "name": "event-sink",
            "trigger_type": "webhook_received",
            "trigger_config": {"secret_id": cred["id"]},
            "action_config": [
                {"type": "run_agent_prompt", "executor_agent_id": agent_id, "prompt": "x"}
            ],
            "executor_agent_id": agent_id,
            "concurrency_limit": 10,
            "rate_limit_max": 100,
        },
        headers=_auth(token),
    )
    assert rule_resp.status_code == 201, rule_resp.text
    import hashlib as _h
    import hmac as _hm
    import time as _t

    body = b'{"severity": "critical"}'
    ts = int(_t.time())
    digest = _hm.new(cred["secret"].encode(), f"{ts}.".encode() + body, _h.sha256).hexdigest()
    ok = await app_client.post(
        f"/api/v1/webhooks/inbound/{cred['token']}",
        content=body,
        headers={
            "X-Signature": f"t={ts},v1={digest}",
            "X-Event-Id": "evt-route-1",
            "X-Event-Type": "alert.triggered",
        },
    )
    assert ok.status_code == 200, ok.text
    bad = await app_client.post(
        f"/api/v1/webhooks/inbound/{cred['token']}",
        content=body,
        headers={"X-Event-Id": "evt-route-2"},
    )
    assert bad.status_code == 401

    listing = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/webhook-events", headers=_auth(token)
    )
    assert listing.status_code == 200
    rows = listing.json()["data"]
    assert len(rows) == 2
    rejected = await app_client.get(
        f"/api/v1/workspaces/{ws_id}/webhook-events?process_status=rejected",
        headers=_auth(token),
    )
    rejected_rows = rejected.json()["data"]
    assert len(rejected_rows) == 1
    assert rejected_rows[0]["signature_status"] == "missing"
    # payload is audited but auth headers are never persisted
    assert "authorization" not in (rejected_rows[0]["headers"] or {})
