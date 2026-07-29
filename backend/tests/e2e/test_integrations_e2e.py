"""Integrations E2E — REAL api + REAL worker + REAL PostgreSQL/Redis
(integrations.md §5, T29, README §6.17).

Red lines over actual HTTP + a real worker process (outbox relay incl.
webhook.dispatch derivation + webhook delivery worker): signature-reject
never dispatches, dedup, replay window, anti pre-occupation, disabled
integration, §6.9 integration-triggered executions with the exact
idempotency key, T29 global binding key / scope XOR / project cascade /
external_identities multi-workspace model + unlink authorization, VCS
auto-link + auto status flow (flow C), outbound ledger + retry + breaker.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text

from mesh.auth.security import encrypt_secret
from mesh.config import DEV_JWT_SECRET
from mesh.db.models.integration import (
    ExternalIdentity,
    IntegrationEvent,
    VcsLink,
    WebhookSubscriptionDelivery,
)
from mesh.db.models.issue import Issue
from mesh.db.models.runtime import TaskExecution

PASSWORD = "Intg-E2E-123456"
NOW_TOLERANCE = timedelta(seconds=300)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="module")
async def integrations_worker(provision_database):
    """Real worker: relay (+ webhook.dispatch derivation) + delivery worker."""
    env = os.environ.copy()
    from tests.conftest import get_test_database_url, get_test_redis_url

    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_WEBHOOK_DELIVERY_POLL_INTERVAL"] = "0.2"
    env["MESH_WEBHOOK_DELIVERY_BASE_SECONDS"] = "1"
    env["MESH_WEBHOOK_DELIVERY_MAX_SECONDS"] = "2"
    env["MESH_WEBHOOK_DELIVERY_MAX_ATTEMPTS"] = "2"
    env["MESH_WEBHOOK_CIRCUIT_BREAK_THRESHOLD"] = "2"
    env["MESH_WEBHOOK_DELIVERY_TIMEOUT_SECONDS"] = "2"
    env["MESH_STORAGE_ENDPOINT"] = os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100")
    env["MESH_STORAGE_ACCESS_KEY"] = os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh")
    env["MESH_STORAGE_SECRET_KEY"] = os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret")
    import re as _re

    _here = os.path.dirname(os.path.abspath(__file__))
    _backend = os.path.dirname(_here)
    _src = os.path.join(_backend, "src")
    _existing = [
        x
        for x in env.get("PYTHONPATH", "").split(os.pathsep)
        if x and not _re.search(r"/workspaces/[^/]+/workdir/Mesh/backend", x)
    ]
    env["PYTHONPATH"] = os.pathsep.join([_src, _backend] + _existing)
    log_file = open("/tmp/integrations_worker.log", "wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    await asyncio.sleep(2.0)
    assert process.poll() is None, "integrations worker died during startup"
    yield process
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "INTG E2E"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def setup_world(api_client: httpx.AsyncClient, suffix: str) -> dict:
    token = await _register_and_login(api_client, f"intg-{suffix}@e2e.mesh")
    resp = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "INTG E2E", "slug": f"intg-{suffix}"},
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
    members = (await api_client.get(f"/api/v1/workspaces/{ws_id}/members", headers=_auth(token))).json()[
        "data"
    ]
    member_id = next(m["id"] for m in members if m.get("member_type") == "human")
    return {
        "token": token,
        "ws_id": ws_id,
        "agent_id": agent_resp.json()["data"]["id"],
        "member_id": member_id,
    }


def encrypt(value: str) -> str:
    """Ciphertext for config *_ref entries — the e2e server runs on the
    default dev signing key."""
    return encrypt_secret(value, DEV_JWT_SECRET)


def slack_sign(secret: str, body: bytes, ts: int) -> dict:
    sig = hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {
        "x-slack-signature": f"v0={sig}",
        "x-slack-request-timestamp": str(ts),
        "content-type": "application/json",
    }


async def poll_until(query_fn, timeout: float = 12.0, interval: float = 0.3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await query_fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


# ---------------------------------------------------------------------------
# §5.1 inbound ingestion red lines
# ---------------------------------------------------------------------------


async def _make_slack_world(api_client, suffix, team="T_E2E"):
    world = await setup_world(api_client, suffix)
    signing_secret = f"sss-{uuid.uuid4().hex}"
    resp = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_slack",
            "name": f"slack-{suffix}",
            "config": {
                "team_id": team,
                "bot_user_id": "U_BOT",
                "signing_secret_ref": encrypt(signing_secret),
            },
        },
        headers=_auth(world["token"]),
    )
    assert resp.status_code == 201, resp.text
    world["integration"] = resp.json()["data"]["integration"]
    world["signing_secret"] = signing_secret
    # bind a channel to the agent
    bind = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration']['id']}/bindings",
        json={
            "external_ref": "C_E2E",
            "bound_agent_id": world["agent_id"],
            "match_config": {"trigger_on": ["mention"]},
        },
        headers=_auth(world["token"]),
    )
    assert bind.status_code == 201, bind.text
    world["binding_id"] = bind.json()["data"]["id"]
    return world


def _slack_mention_payload(team: str, ts_str: str) -> dict:
    return {
        "type": "event_callback",
        "team_id": team,
        "event": {
            "type": "message",
            "channel": "C_E2E",
            "user": "U_HUMAN",
            "text": "<@U_BOT> 帮忙看一下",
            "event_ts": ts_str,
        },
    }


async def test_slack_ingestion_dispatches_real_execution(api_client, integrations_worker, session_factory):
    world = await _make_slack_world(api_client, "disp")
    ts_str = "111.0001"
    body = json.dumps(_slack_mention_payload("T_E2E", ts_str)).encode()
    ts = int(datetime.now(UTC).timestamp())
    resp = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers=slack_sign(world["signing_secret"], body, ts),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["process_status"] == "dispatched"

    # The real relay materializes task_executions with trigger='integration'
    # and the §6.9 idempotency key.
    expected_key = hashlib.sha256(
        f"{world['agent_id']}|{world['binding_id']}|T_E2E:{ts_str}".encode()
    ).hexdigest()

    async def find_execution():
        async with session_factory() as session:
            return (
                (
                    await session.execute(
                        select(TaskExecution).where(TaskExecution.idempotency_key == expected_key)
                    )
                )
                .scalars()
                .first()
            )

    execution = await poll_until(find_execution)
    assert execution is not None, "relay must materialize the execution (real outbox path)"
    assert execution.trigger == "integration"
    assert str(execution.agent_id) == world["agent_id"]
    assert execution.task_spec["untrusted_context"]["provider"] == "slack"


async def test_duplicate_event_deduped_no_second_execution(api_client, integrations_worker, session_factory):
    world = await _make_slack_world(api_client, "dedup")
    body = json.dumps(_slack_mention_payload("T_E2E", "222.0001")).encode()
    ts = int(datetime.now(UTC).timestamp())
    headers = slack_sign(world["signing_secret"], body, ts)
    first = await api_client.post("/api/v1/integrations/slack/events", content=body, headers=headers)
    second = await api_client.post("/api/v1/integrations/slack/events", content=body, headers=headers)
    assert first.json()["process_status"] == "dispatched"
    assert second.status_code == 200
    assert second.json()["process_status"] == "deduped"
    await asyncio.sleep(1.5)
    async with session_factory() as session:
        count = (
            await session.execute(
                select(TaskExecution.id).where(TaskExecution.workspace_id == uuid.UUID(world["ws_id"]))
            )
        ).all()
        assert len(count) == 1, "duplicate event must not enqueue twice (§6.9)"


async def test_invalid_missing_replay_never_dispatch(api_client, integrations_worker, session_factory):
    world = await _make_slack_world(api_client, "reject")
    ts = int(datetime.now(UTC).timestamp())

    # Three DISTINCT bodies (the rejected namespace dedups per body hash —
    # repeated forgeries of the same body share one audit row by design).
    bodies = [json.dumps(_slack_mention_payload("T_E2E", f"333.000{i}")).encode() for i in range(3)]
    # invalid signature
    bad = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=bodies[0],
        headers=slack_sign("WRONG", bodies[0], ts),
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_signature"
    # missing signature
    missing = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=bodies[1],
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == 401
    # replay window: valid signature with stale timestamp
    stale = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=bodies[2],
        headers=slack_sign(world["signing_secret"], bodies[2], ts - 301),
    )
    assert stale.status_code == 401

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(IntegrationEvent).where(IntegrationEvent.workspace_id == uuid.UUID(world["ws_id"]))
                )
            )
            .scalars()
            .all()
        )
        rejected = [r for r in rows if r.process_status == "rejected"]
        assert len(rejected) == 3, "each rejected body audited in rejected namespace"
        assert all(r.external_event_id.startswith("rejected:") for r in rejected)
        statuses = {r.signature_status for r in rejected}
        assert "invalid" in statuses and "missing" in statuses
        executions = (
            (
                await session.execute(
                    select(TaskExecution).where(TaskExecution.workspace_id == uuid.UUID(world["ws_id"]))
                )
            )
            .scalars()
            .all()
        )
        assert executions == [], "rejected events must NEVER dispatch (§5.1)"


async def test_forgery_cannot_preoccupy_event_id(api_client, integrations_worker, session_factory):
    world = await _make_slack_world(api_client, "preoc")
    body = json.dumps(_slack_mention_payload("T_E2E", "444.0001")).encode()
    ts = int(datetime.now(UTC).timestamp())
    # unsigned forgery first
    forged = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers=slack_sign("WRONG", body, ts),
    )
    assert forged.status_code == 401
    # legitimate delivery of the SAME external event id still dispatches
    legit = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers=slack_sign(world["signing_secret"], body, ts),
    )
    assert legit.status_code == 200
    assert legit.json()["process_status"] == "dispatched", (
        "rejected:<hash> namespace must not pre-occupy legitimate ids (§5.1)"
    )


async def test_disabled_integration_rejects(api_client, integrations_worker):
    world = await _make_slack_world(api_client, "disbl")
    patch = await api_client.patch(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration']['id']}",
        json={"status": "disabled"},
        headers=_auth(world["token"]),
    )
    assert patch.status_code == 200
    body = json.dumps(_slack_mention_payload("T_E2E", "555.0001")).encode()
    ts = int(datetime.now(UTC).timestamp())
    resp = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers=slack_sign(world["signing_secret"], body, ts),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "integration_disabled"


async def test_feishu_challenge_echo(api_client):
    world = await setup_world(api_client, "fschal")
    verification_token = f"fvt-{uuid.uuid4().hex}"
    encrypt_key = f"fek-{uuid.uuid4().hex}"
    resp = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_feishu",
            "name": "fs-chal",
            "config": {
                "tenant_key": "tk-chal",
                "verification_token_ref": encrypt(verification_token),
                "encrypt_key_ref": encrypt(encrypt_key),
            },
        },
        headers=_auth(world["token"]),
    )
    assert resp.status_code == 201
    challenge_body = json.dumps(
        {
            "type": "url_verification",
            "challenge": "mesh-challenge-1",
            "token": verification_token,
        }
    ).encode()
    ok = await api_client.post("/api/v1/integrations/feishu/events", content=challenge_body)
    assert ok.status_code == 200
    assert ok.json() == {"challenge": "mesh-challenge-1"}
    bad = await api_client.post(
        "/api/v1/integrations/feishu/events",
        content=json.dumps({"type": "url_verification", "challenge": "x", "token": "nope"}).encode(),
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_challenge"


async def test_unmatched_message_audited_not_executed(api_client, integrations_worker, session_factory):
    world = await _make_slack_world(api_client, "unmtch")
    payload = _slack_mention_payload("T_E2E", "666.0001")
    payload["event"]["channel"] = "C_UNBOUND"  # no binding for this channel
    body = json.dumps(payload).encode()
    ts = int(datetime.now(UTC).timestamp())
    resp = await api_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers=slack_sign(world["signing_secret"], body, ts),
    )
    assert resp.status_code == 200
    assert resp.json()["process_status"] == "received"
    await asyncio.sleep(1.0)
    async with session_factory() as session:
        executions = (
            (
                await session.execute(
                    select(TaskExecution).where(TaskExecution.workspace_id == uuid.UUID(world["ws_id"]))
                )
            )
            .scalars()
            .all()
        )
        assert executions == [], "unmatched → audit only, never execute (§6.9)"


# ---------------------------------------------------------------------------
# T29 — global binding key / scope XOR / project cascade
# ---------------------------------------------------------------------------


async def test_t29_cross_workspace_binding_conflict(api_client):
    world_a = await setup_world(api_client, "t29a")
    world_b = await setup_world(api_client, "t29b")
    for world in (world_a, world_b):
        resp = await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={
                "kind": "im_slack",
                "name": f"slack-{world['ws_id'][:6]}",
                "config": {"team_id": "T_SHARED"},
            },
            headers=_auth(world["token"]),
        )
        assert resp.status_code == 201
        world["integration"] = resp.json()["data"]["integration"]
    bind_a = await api_client.post(
        f"/api/v1/workspaces/{world_a['ws_id']}/integrations/{world_a['integration']['id']}/bindings",
        json={"external_ref": "C_SHARED"},
        headers=_auth(world_a["token"]),
    )
    assert bind_a.status_code == 201
    bind_b = await api_client.post(
        f"/api/v1/workspaces/{world_b['ws_id']}/integrations/{world_b['integration']['id']}/bindings",
        json={"external_ref": "C_SHARED"},
        headers=_auth(world_b["token"]),
    )
    assert bind_b.status_code == 409, "global external-identity key must reject cross-ws grab (T29①)"
    assert bind_b.json()["error"]["code"] == "binding_conflict"


async def test_t29_scope_xor_and_project_cascade(api_client, session_factory):
    world = await setup_world(api_client, "t29xor")
    resp = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "im_slack", "name": "slack-xor", "config": {"team_id": "T_XOR"}},
        headers=_auth(world["token"]),
    )
    integration_id = resp.json()["data"]["integration"]["id"]
    project = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/projects",
            json={"name": "P-XOR", "key": "PX"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]

    xor1 = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/bindings",
        json={"external_ref": "C_X1", "scope": "workspace", "project_id": project["id"]},
        headers=_auth(world["token"]),
    )
    assert xor1.status_code == 422, "workspace scope + project_id must violate XOR CHECK (T29②)"
    xor2 = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/bindings",
        json={"external_ref": "C_X2", "scope": "project"},
        headers=_auth(world["token"]),
    )
    assert xor2.status_code == 422, "project scope without project_id must violate XOR CHECK"

    ok = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}/bindings",
        json={"external_ref": "C_PROJ", "scope": "project", "project_id": project["id"]},
        headers=_auth(world["token"]),
    )
    assert ok.status_code == 201
    # T29③: PHYSICAL project deletion (the API delete is soft; the FK
    # contract is ON DELETE CASCADE — verified with a real DELETE).
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": uuid.UUID(project["id"])})
    async with session_factory() as session:
        remaining = (
            await session.execute(
                text("SELECT count(*) FROM integration_bindings WHERE external_ref = 'C_PROJ'")
            )
        ).scalar_one()
        assert remaining == 0, "project delete must cascade project-scoped bindings (T29③)"


# ---------------------------------------------------------------------------
# T29 — external_identities multi-workspace model + unlink authorization
# ---------------------------------------------------------------------------


async def test_t29_identity_multi_workspace_and_origin_delete(
    api_client, integrations_worker, session_factory
):
    # One user owns two workspaces (the multi-workspace member model, §6.1).
    token = await _register_and_login(api_client, "multi-owner@e2e.mesh")
    ws_a = (
        await api_client.post(
            "/api/v1/workspaces",
            json={"name": "MW A", "slug": "mw-a"},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    ws_b = (
        await api_client.post(
            "/api/v1/workspaces",
            json={"name": "MW B", "slug": "mw-b"},
            headers=_auth(token),
        )
    ).json()["data"]["id"]
    for ws in (ws_a, ws_b):
        integ = await api_client.post(
            f"/api/v1/workspaces/{ws}/integrations",
            json={"kind": "im_slack", "name": f"slack-mw-{ws[:6]}", "config": {"team_id": "T_MULTI"}},
            headers=_auth(token),
        )
        assert integ.status_code == 201
    integ_a = (await api_client.get(f"/api/v1/workspaces/{ws_a}/integrations", headers=_auth(token))).json()[
        "data"
    ][0]
    integ_b = (await api_client.get(f"/api/v1/workspaces/{ws_b}/integrations", headers=_auth(token))).json()[
        "data"
    ][0]

    # Link the identity in workspace A (code → dev outbox → confirm).
    from redis.asyncio import Redis

    from tests.conftest import get_test_redis_url

    redis = Redis.from_url(get_test_redis_url(), decode_responses=True)
    link = await api_client.post(
        f"/api/v1/workspaces/{ws_a}/external-identities:link",
        json={"provider": "slack", "integration_id": integ_a["id"], "external_user_key": "U_MULTI"},
        headers=_auth(token),
    )
    assert link.status_code == 200, link.text
    code = await redis.get("mesh:identity-dev-outbox:slack:T_MULTI:U_MULTI")
    assert code is not None
    confirm = await api_client.post(
        f"/api/v1/workspaces/{ws_a}/external-identities:link-confirm",
        json={"provider": "slack", "integration_id": integ_a["id"], "code": code},
        headers=_auth(token),
    )
    assert confirm.status_code == 200, confirm.text
    identity_id = confirm.json()["data"]["id"]
    await redis.aclose()

    # T29⑦: duplicate link of the same external account → 409 (global key).
    dup = await api_client.post(
        f"/api/v1/workspaces/{ws_b}/external-identities:link",
        json={"provider": "slack", "integration_id": integ_b["id"], "external_user_key": "U_MULTI"},
        headers=_auth(token),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "identity_already_linked"

    # T29⑨: delete the link-origin workspace A. The product path is a soft
    # delete (the append-only audit trail blocks physical purge while audit
    # rows reference the workspace — by design); the mapping must survive
    # and workspace B's callbacks keep resolving. The physical-DELETE
    # contract (created_in_workspace_id column-level SET NULL, no cascade)
    # is unit-verified in test_integration_identities (audit-free world).
    delete_a = await api_client.request(
        "DELETE",
        f"/api/v1/workspaces/{ws_a}",
        headers=_auth(token),
        json={"confirm_slug": "mw-a"},
    )
    assert delete_a.status_code in (200, 204), delete_a.text
    async with session_factory() as session:
        row = await session.get(ExternalIdentity, uuid.UUID(identity_id))
        assert row is not None, "global mapping must survive origin-workspace delete (T29⑨)"

    listed_b = await api_client.get(f"/api/v1/workspaces/{ws_b}/external-identities", headers=_auth(token))
    assert any(i["id"] == identity_id for i in listed_b.json()["data"]), (
        "mapping visible from workspace B after A deletion (single global row)"
    )

    # T29⑩: structure negatives — no workspace_id column, no workspace RLS.
    async with session_factory() as session:
        column = (
            await session.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='external_identities' AND column_name='workspace_id'"
                )
            )
        ).first()
        assert column is None
        policies = (
            await session.execute(text("SELECT 1 FROM pg_policies WHERE tablename='external_identities'"))
        ).first()
        assert policies is None, "external_identities must have NO workspace RLS (T29⑩)"
        cascade = (
            await session.execute(
                text("""
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid='external_identities'::regclass
              AND c.confrelid='workspaces'::regclass AND c.confdeltype='c'
        """)
            )
        ).first()
        assert cascade is None, "no CASCADE FK from external_identities to workspaces"

    # T29⑪: unlink by a different user (workspace B admin) → 403, no bypass.
    other_token = await _register_and_login(api_client, "unlink-stranger@e2e.mesh")
    # stranger joins ws_b as admin via invitation is complex; owner-role is
    # irrelevant anyway — the stranger's own workspace suffices: create one.
    ws_c = (
        await api_client.post(
            "/api/v1/workspaces",
            json={"name": "MW C", "slug": "mw-c"},
            headers=_auth(other_token),
        )
    ).json()["data"]["id"]
    forbidden = await api_client.delete(
        f"/api/v1/workspaces/{ws_c}/external-identities/{identity_id}",
        headers=_auth(other_token),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "identity_unlink_forbidden"

    # Owner unlink succeeds and takes effect immediately (mapping gone).
    unlinked = await api_client.delete(
        f"/api/v1/workspaces/{ws_b}/external-identities/{identity_id}",
        headers=_auth(token),
    )
    assert unlinked.status_code == 204
    async with session_factory() as session:
        assert await session.get(ExternalIdentity, uuid.UUID(identity_id)) is None


# ---------------------------------------------------------------------------
# VCS flow C — github PR merged → auto link + status flow + comment
# ---------------------------------------------------------------------------


async def test_vcs_flow_pr_merged_auto_status(api_client, integrations_worker, session_factory):
    world = await setup_world(api_client, "vcsc")
    webhook_secret = f"gws-{uuid.uuid4().hex}"
    integ = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={
                "kind": "vcs_github",
                "name": "gh-flow",
                "config": {
                    "installation_id": "999001",
                    "webhook_secret_ref": encrypt(webhook_secret),
                },
            },
            headers=_auth(world["token"]),
        )
    ).json()["data"]["integration"]
    # binding on repo acme/flow with auto_status_map
    bind = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integ['id']}/bindings",
        json={"external_ref": "acme/flow", "match_config": {"auto_status_map": {"merged": "done"}}},
        headers=_auth(world["token"]),
    )
    assert bind.status_code == 201
    # create the issue and learn its identifier
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "ship it"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    identifier = issue["identifier"]

    payload = {
        "action": "closed",
        "repository": {"full_name": "acme/flow"},
        "installation": {"id": 999001},
        "sender": {"login": "dev"},
        "pull_request": {
            "number": 42,
            "title": f"{identifier} ship the thing",
            "state": "closed",
            "merged": True,
            "merged_at": "2026-07-29T10:00:00Z",
            "head": {"ref": "feature/ship"},
        },
    }
    body = json.dumps(payload).encode()
    sig = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    resp = await api_client.post(
        "/api/v1/integrations/github/events",
        content=body,
        headers={
            "x-hub-signature-256": f"sha256={sig}",
            "x-github-event": "pull_request",
            "x-github-delivery": f"del-{uuid.uuid4().hex[:10]}",
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 200, resp.text
    issue_uuid = uuid.UUID(issue["id"])

    async def issue_done():
        async with session_factory() as session:
            row = await session.get(Issue, issue_uuid)
            return row if row.state_category == "done" else None

    done_issue = await poll_until(issue_done, timeout=15)
    assert done_issue is not None, "PR merged must auto-transition issue to done (flow C)"
    assert done_issue.completed_at is not None

    async with session_factory() as session:
        links = (
            (await session.execute(select(VcsLink).where(VcsLink.mesh_entity_id == issue_uuid)))
            .scalars()
            .all()
        )
        assert len(links) == 1
        assert links[0].external_object_ref == "acme/flow#42"
        assert links[0].status == "stale"
        assert links[0].external_state.get("pr_state") == "merged"
        comment_count = (
            await session.execute(
                text("SELECT count(*) FROM comments WHERE issue_id = :iid AND author_kind = 'system'"),
                {"iid": issue_uuid},
            )
        ).scalar_one()
        assert comment_count == 1, "system comment trail for auto status flow"

    # repeat the same event (redelivery) → idempotent: still one link/comment
    resp2 = await api_client.post(
        "/api/v1/integrations/github/events",
        content=body,
        headers={
            "x-hub-signature-256": f"sha256={sig}",
            "x-github-event": "pull_request",
            "x-github-delivery": f"del-{uuid.uuid4().hex[:10]}",
            "content-type": "application/json",
        },
    )
    # duplicate delivery id is new, but same PR object → no new link
    assert resp2.status_code == 200
    async with session_factory() as session:
        links = (
            (await session.execute(select(VcsLink).where(VcsLink.mesh_entity_id == issue_uuid)))
            .scalars()
            .all()
        )
        assert len(links) == 1, "redelivered event must not double-link (§3.3)"

    # issue sidebar endpoint lists ACTIVE links only (LOW-2): the merged PR's
    # link is stale (history, asserted above), so the active view excludes it.
    sidebar = await api_client.get(f"/api/v1/issues/{issue['id']}/vcs-links", headers=_auth(world["token"]))
    assert sidebar.status_code == 200
    assert sidebar.json()["data"] == []


# ---------------------------------------------------------------------------
# §5.3 outbound subscriptions — ledger, retry, breaker
# ---------------------------------------------------------------------------


async def test_outbound_ledger_retry_and_breaker(api_client, integrations_worker, session_factory):
    world = await setup_world(api_client, "outb")
    created = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://nonexistent-e2e-host.invalid/hook", "event_types": ["issue.created"]},
        headers=_auth(world["token"]),
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["secret"].startswith("whsec_"), "secret shown exactly once at create (§5.3)"
    subscription_id = data["id"]
    got = await api_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}",
        headers=_auth(world["token"]),
    )
    assert "secret" not in got.json()["data"], "secret key never echoed again (§6.16)"
    assert data["secret"] not in json.dumps(got.json()), "plaintext never echoed (§6.16)"

    # Trigger a real domain event → relay derives webhook.dispatch → the
    # delivery worker posts (fails: unresolvable host) → retries → breaker.
    issue = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/issues",
        json={"title": "trigger outbound"},
        headers=_auth(world["token"]),
    )
    assert issue.status_code == 201

    async def delivery_failed():
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(WebhookSubscriptionDelivery).where(
                            WebhookSubscriptionDelivery.subscription_id == uuid.UUID(subscription_id)
                        )
                    )
                )
                .scalars()
                .first()
            )
            return row if row is not None and row.state == "failed" else None

    delivery = await poll_until(delivery_failed, timeout=30)
    assert delivery is not None, "delivery ledger row must reach terminal failed"
    assert delivery.attempts >= 2, "retries with backoff before terminal failure (§5.3)"
    assert delivery.last_error, "failure reason recorded"

    # Second event → second failure → breaker trips (threshold=2).
    await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/issues",
        json={"title": "trigger outbound 2"},
        headers=_auth(world["token"]),
    )

    async def breaker_open():
        resp = await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}",
            headers=_auth(world["token"]),
        )
        sub = resp.json()["data"]
        return sub if sub["status"] == "disabled" and sub["fail_count"] >= 2 else None

    tripped = await poll_until(breaker_open, timeout=30)
    assert tripped is not None, "circuit breaker must disable the subscription (§5.3)"

    # manual retry while breaker open → 422 subscription_circuit_open
    deliveries = (
        await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}/deliveries",
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    failed_delivery = next(d for d in deliveries if d["state"] == "failed")
    blocked = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}"
        f"/deliveries/{failed_delivery['id']}/retry",
        headers=_auth(world["token"]),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "subscription_circuit_open"

    # resume clears fail_count; retry requeues the failed delivery.
    resumed = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}/resume",
        headers=_auth(world["token"]),
    )
    assert resumed.status_code == 200
    assert resumed.json()["data"]["fail_count"] == 0
    retried = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}"
        f"/deliveries/{failed_delivery['id']}/retry",
        headers=_auth(world["token"]),
    )
    assert retried.status_code == 200
    assert retried.json()["data"]["state"] == "pending"


async def test_subscriber_can_restore_domain_event_from_delivery(
    api_client, integrations_worker, session_factory
):
    """P8 / HIGH-1: a subscriber reconstructs the domain event from a delivery
    alone. The real outbox→relay→dispatch chain persists the REAL event type +
    data on the ledger row (the wire Mesh-Event/body format is unit-proven with
    a real socket; the SSRF guard refuses loopback sinks, so the durable row a
    subscriber consumes is asserted here)."""
    world = await setup_world(api_client, "restore")
    created = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://subscriber-e2e-host.invalid/hook", "event_types": []},
        headers=_auth(world["token"]),
    )
    assert created.status_code == 201
    subscription_id = uuid.UUID(created.json()["data"]["id"])
    # Emit a real domain event.
    issue = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/issues",
        json={"title": "restore me"},
        headers=_auth(world["token"]),
    )
    assert issue.status_code == 201

    async def restored_delivery():
        # The catch-all subscription also receives integration.updated (from
        # its own creation); select the DOMAIN event delivery specifically.
        async with session_factory() as session:
            return (
                (
                    await session.execute(
                        select(WebhookSubscriptionDelivery).where(
                            WebhookSubscriptionDelivery.subscription_id == subscription_id,
                            WebhookSubscriptionDelivery.event_type.like("issue.%"),
                        )
                    )
                )
                .scalars()
                .first()
            )

    delivery = await poll_until(restored_delivery, timeout=20)
    assert delivery is not None, "relay must derive a delivery for the domain event"
    # The delivery is self-describing: header type == body event == real type.
    assert delivery.event_type == delivery.payload["event"]
    assert delivery.event_type.startswith("issue.")
    assert isinstance(delivery.payload["data"], dict) and delivery.payload["data"]


async def test_connection_probe_drives_health_fields(api_client):
    """:test persists connector health (§2.2 / §4.1) — healthy stamps success,
    auth_failed stamps last_error for the re-authorize banner."""
    world = await setup_world(api_client, "probe")
    # webhook_outbound has no platform credentials → healthy without HTTP.
    healthy_integration = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "webhook_outbound", "name": "outbound-probe"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]["integration"]
    tested = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{healthy_integration['id']}:test",
        headers=_auth(world["token"]),
    )
    assert tested.status_code == 200
    assert tested.json()["data"]["health_state"] == "healthy"
    got = (
        await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{healthy_integration['id']}",
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    assert got["health_state"] == "healthy"
    assert got["last_success_at"] is not None
    assert got["last_error"] is None

    # im_slack WITHOUT a secret → auth_failed missing_credentials (persisted).
    bad_integration = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-probe", "config": {"team_id": "T_PR"}},
            headers=_auth(world["token"]),
        )
    ).json()["data"]["integration"]
    bad = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{bad_integration['id']}:test",
        headers=_auth(world["token"]),
    )
    assert bad.status_code == 200
    assert bad.json()["data"] == {"health_state": "auth_failed", "detail": "missing_credentials"}
    bad_got = (
        await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{bad_integration['id']}",
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    assert bad_got["health_state"] == "auth_failed"
    assert bad_got["last_error"] == "missing_credentials"


async def test_send_test_event_positive_and_circuit_open(api_client, integrations_worker, session_factory):
    """:send-test synthesizes a webhook.test delivery that walks the full path
    (§3.1 P1); a breaker-open subscription rejects it (§3.4)."""
    world = await setup_world(api_client, "sendtest")
    subscription = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
            json={"url": "https://sendtest-e2e-host.invalid/hook"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    sent = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription['id']}:send-test",
        headers=_auth(world["token"]),
    )
    assert sent.status_code == 201, sent.text
    data = sent.json()["data"]
    assert data["event_type"] == "webhook.test"
    assert data["state"] == "pending"
    delivery_id = uuid.UUID(data["id"])

    # The real worker picks the synthetic delivery up and attempts delivery.
    async def attempted():
        async with session_factory() as session:
            row = await session.get(WebhookSubscriptionDelivery, delivery_id)
            return row if row is not None and row.attempts >= 1 else None

    attempted_row = await poll_until(attempted, timeout=20)
    assert attempted_row is not None, "worker must attempt the synthetic delivery"

    # Circuit open → 422 subscription_circuit_open.
    await api_client.patch(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription['id']}",
        json={"status": "disabled"},
        headers=_auth(world["token"]),
    )
    blocked = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription['id']}:send-test",
        headers=_auth(world["token"]),
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "subscription_circuit_open"


async def test_list_responses_carry_observability_fields(api_client, integrations_worker):
    """§4.1: connected-list events_7d, subscription success_rate, VCS deep link."""
    world = await setup_world(api_client, "obsfields")
    (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-obs", "config": {"team_id": "T_OBS"}},
            headers=_auth(world["token"]),
        )
    ).json()["data"]["integration"]
    listed = (
        await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=_auth(world["token"])
        )
    ).json()["data"]
    assert listed and all("events_7d" in item for item in listed)

    subscription = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
            json={"url": "https://obs-e2e-host.invalid/hook"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    subs = (
        await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    assert any(s["id"] == subscription["id"] for s in subs)
    assert all("success_rate" in s for s in subs)

    # VCS deep link is rendered on the issue sidebar (clickable <a> source).
    vcs_integration = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "vcs_github", "name": "gh-obs", "config": {"installation_id": "555"}},
            headers=_auth(world["token"]),
        )
    ).json()["data"]["integration"]
    issue = (
        await api_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "link obs"},
            headers=_auth(world["token"]),
        )
    ).json()["data"]
    link = await api_client.post(
        "/api/v1/integrations/vcs/links",
        json={
            "integration_id": vcs_integration["id"],
            "vcs_ref": {"type": "pull_request", "id": "acme/obs#12"},
            "issue_id": issue["id"],
        },
        headers=_auth(world["token"]),
    )
    assert link.status_code == 201
    sidebar = (
        await api_client.get(f"/api/v1/issues/{issue['id']}/vcs-links", headers=_auth(world["token"]))
    ).json()["data"]
    assert len(sidebar) == 1
    assert sidebar[0]["url"] == "https://github.com/acme/obs/pull/12"


async def test_outbound_https_only_and_ssrf(api_client):
    world = await setup_world(api_client, "ssrf")
    http_url = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "http://plain.example.com/x"},
        headers=_auth(world["token"]),
    )
    assert http_url.status_code == 400
    assert http_url.json()["error"]["code"] == "invalid_url_scheme"
    metadata = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://169.254.169.254/latest/meta-data"},
        headers=_auth(world["token"]),
    )
    assert metadata.status_code == 422
    assert metadata.json()["error"]["code"] == "ssrf_blocked"
    private = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://10.1.2.3/x"},
        headers=_auth(world["token"]),
    )
    assert private.status_code == 422
