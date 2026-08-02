"""DingTalk outbound E2E — REAL API server + REAL worker processes + REAL
PostgreSQL/Redis against a local DingTalk OpenAPI test double
(integrations.md §5.6: token single-flight, ack at-most-once, chunked
results, rate-limit backoff, card lifecycle, diagnostics split, redaction).

The test double is a real HTTP server on loopback (nothing on the contract
path is mocked): worker processes talk to it over real sockets via the
deployment-time ``MESH_DINGTALK_API_BASE`` / ``MESH_DINGTALK_OAPI_BASE``
environment variables.
"""

from __future__ import annotations

import asyncio
import base64
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
from sqlalchemy import select

from mesh.auth.security import encrypt_secret
from mesh.config import DEV_JWT_SECRET
from mesh.db.models.integration import (
    ExternalIdentity,
    Integration,
    IntegrationBinding,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.integrations.ack import elect_ack_leader
from mesh.integrations.im_outbound import IM_SEND_EVENT_TYPE, chunk_idempotency_key
from mesh.outbox.service import emit_event
from tests.e2e.dingtalk_fake_server import LEGACY_GETTOKEN_PATH, start_fake_dingtalk
from tests.e2e.test_integrations_e2e import poll_until, setup_world

pytestmark = pytest.mark.e2e

APP_SECRET = "ding-e2e-app-secret-0123456789"
CORP_ID = "dingcorpE2E"
CONV_REF = "cidE2ECONV=="
CONV_KEY = f"dingtalk:{CORP_ID}:{CONV_REF}"
WORKER_LOGS = []


# ---------------------------------------------------------------------------
# Fixtures — fake platform + real API server + real workers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_dingtalk():
    server, base_url, state = start_fake_dingtalk(expected_app_secret=APP_SECRET)
    yield base_url, state
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def _reset_fake_state(fake_dingtalk):
    """Per-test reset: the fake platform is module-scoped, its counters
    must not leak across tests."""
    _base, state = fake_dingtalk
    with state.lock:
        state.requests.clear()
        state.token_calls = 0
        state.send_queue.clear()
        state.token_delay = 0.0
        state.token_status = 200
        state.token_body = None
        state.send_status = 200
        state.send_body = None
        state.card_status = 200
    yield


def _base_env() -> dict:
    from tests.conftest import get_test_database_url, get_test_redis_url

    env = os.environ.copy()
    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_APP_BASE_URL"] = "http://mesh.test"
    env.setdefault("MESH_DEVICE_CODE_PEPPER", "e2e-device-code-pepper-0123456789")
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
    return env


@pytest_asyncio.fixture(scope="module")
async def dt_api_server(provision_database, fake_dingtalk):
    """Real API server with both DingTalk API bases pointing at the double."""
    fake_base, _state = fake_dingtalk
    env = _base_env()
    env["MESH_DINGTALK_API_BASE"] = fake_base
    env["MESH_DINGTALK_OAPI_BASE"] = fake_base
    env.setdefault("MESH_SESSION_COOKIE_SECURE", "false")
    env.setdefault("MESH_AUTH_RATE_LIMIT", "100000")

    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "mesh.api.app:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                if (await client.get(f"{base_url}/healthz", timeout=2)).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.2)
        else:
            process.kill()
            raise RuntimeError("dt api server did not become ready")
    yield base_url
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


def _spawn_worker(fake_base: str, log_path: str, **extra_env) -> subprocess.Popen:
    env = _base_env()
    env["MESH_DINGTALK_API_BASE"] = fake_base
    env["MESH_DINGTALK_OAPI_BASE"] = fake_base
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_IM_SEND_POLL_INTERVAL"] = "0.2"
    env["MESH_IM_RATE_LIMIT_BASE_SECONDS"] = "0.3"
    env["MESH_DINGTALK_REQUEST_TIMEOUT"] = "5"
    env.update({k: str(v) for k, v in extra_env.items()})
    log_file = open(log_path, "wb")
    WORKER_LOGS.append(log_path)
    return subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )


@pytest_asyncio.fixture(scope="module")
async def dt_worker(provision_database, fake_dingtalk):
    fake_base, _state = fake_dingtalk
    process = _spawn_worker(fake_base, "/tmp/dingtalk_worker_1.log")
    await asyncio.sleep(2.5)
    assert process.poll() is None, "dingtalk worker died during startup"
    yield process
    process.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)


# ---------------------------------------------------------------------------
# World helpers
# ---------------------------------------------------------------------------


async def _make_world(api_client: httpx.AsyncClient, suffix: str) -> dict:
    world = await setup_world(api_client, f"dt-{suffix}")
    resp = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_dingtalk",
            "name": f"dingtalk-{suffix}",
            "config": {
                "app_key": f"dingkey-{suffix}",
                "corp_id": CORP_ID,
                "robot_code": f"robot-{suffix}",
                "receive_mode": "stream",
                "ack_template": "✅ 已接收，处理中",
                "verbosity": "final_only",
            },
            "secret": APP_SECRET,
        },
        headers={"Authorization": f"Bearer {world['token']}"},
    )
    assert resp.status_code == 201, resp.text
    world["integration_id"] = resp.json()["data"]["integration"]["id"]
    return world


async def _bind(session_factory, world) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        binding = IntegrationBinding(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=uuid.UUID(world["integration_id"]),
            provider="dingtalk",
            provider_tenant_key=CORP_ID,
            scope="workspace",
            external_ref=CONV_REF,
            match_config={"trigger_on": ["mention"]},
            bound_agent_id=uuid.UUID(world["agent_id"]),
            status="active",
        )
        session.add(binding)
        await session.flush()
    return binding.id


async def _enqueue_acked_item(
    session_factory, world, *, seq: int, window_base: datetime, offset: float
) -> IntegrationMessageQueue:
    async with session_factory() as session, session.begin():
        item = IntegrationMessageQueue(
            workspace_id=uuid.UUID(world["ws_id"]),
            integration_id=uuid.UUID(world["integration_id"]),
            binding_id=world["binding_id"],
            conversation_key=CONV_KEY,
            seq=seq,
            dispatch_mode="serial_conversation",
            state="pending",
            message_excerpt=f"e2e task {seq}",
            sender_identity_key=f"dingtalk:{CORP_ID}:staffE2E",
            ack_window_at=window_base + timedelta(seconds=offset),
            enqueued_at=window_base + timedelta(seconds=offset),
        )
        session.add(item)
        await session.flush()
        await elect_ack_leader(
            session,
            item=item,
            ack_template="✅ 已接收，处理中",
            coalesce_window=timedelta(seconds=5),
            conversation_type="group",
        )
    return item


# ---------------------------------------------------------------------------
# §5.6 — test-send / stream-status diagnostics split
# ---------------------------------------------------------------------------


async def test_e2e_test_send_and_stream_status_split(dt_api_server, fake_dingtalk, session_factory):
    _base_url, state = fake_dingtalk
    async with httpx.AsyncClient(base_url=dt_api_server, timeout=15) as client:
        world = await _make_world(client, "send")
        ownership_proofs = state.calls(LEGACY_GETTOKEN_PATH)
        assert len(ownership_proofs) == 1
        assert ownership_proofs[0]["body"] == {"appkey": "dingkey-send"}
        auth = {"Authorization": f"Bearer {world['token']}"}
        base = f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration_id']}"
        # receive channel DOWN — must not affect outbound
        async with session_factory() as session, session.begin():
            integration = await session.get(Integration, uuid.UUID(world["integration_id"]))
            integration.stream_state = {"state": "down", "backoff_seconds": 128}
        resp = await client.post(
            f"{base}/test-send",
            json={"conversation_ref": CONV_REF, "conversation_type": "group"},
            headers=auth,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["status"] == "sent"
        assert "stream_channel_unavailable" not in resp.text
        sends = state.group_sends()
        assert len(sends) == 1
        assert sends[0]["body"]["openConversationId"] == CONV_REF
        assert sends[0]["headers"].get("x-acs-dingtalk-access-token", "").startswith("tok-")
        # stream-status: the ONLY place 503 stream_channel_unavailable appears
        status_resp = await client.get(f"{base}/stream-status", headers=auth)
        assert status_resp.status_code == 503
        err = status_resp.json()["error"]
        assert err["code"] == "stream_channel_unavailable"
        assert err["details"]["state"] == "down"


# ---------------------------------------------------------------------------
# §5.6 — ack leading-edge merge through the REAL worker
# ---------------------------------------------------------------------------


async def test_e2e_ack_leading_edge_single_send(dt_worker, fake_dingtalk, session_factory):
    _base_url, state = fake_dingtalk
    world = await _seed_world_db(session_factory, suffix="ack")
    window_base = datetime.now(UTC)
    leader = await _enqueue_acked_item(session_factory, world, seq=1, window_base=window_base, offset=0)
    f1 = await _enqueue_acked_item(session_factory, world, seq=2, window_base=window_base, offset=1)
    f2 = await _enqueue_acked_item(session_factory, world, seq=3, window_base=window_base, offset=2)

    async def _sent():
        async with session_factory() as session:
            row = await session.get(IntegrationMessageQueue, leader.id)
            return row.ack_sent_at is not None

    assert await poll_until(_sent, timeout=15), "leader ack never sent by the real worker"
    # platform got EXACTLY ONE confirmation
    assert len(state.group_sends()) == 1
    content = json.loads(state.group_sends()[0]["body"]["msgParam"])["content"]
    assert content.startswith("✅ 已接收")
    async with session_factory() as session:
        leader_row = await session.get(IntegrationMessageQueue, leader.id)
        assert leader_row.ack_attempted_at is not None and leader_row.ack_sent_at is not None
        for fid in (f1.id, f2.id):
            follower = await session.get(IntegrationMessageQueue, fid)
            assert follower.ack_sent_at is None  # represented ≠ sent
            assert follower.ack_represented_at is not None
            assert follower.ack_merged_into == leader.id
    # no trailing "N 条" summary message
    assert len(state.group_sends()) == 1


# ---------------------------------------------------------------------------
# §5.6 — chunked result delivery through the REAL worker
# ---------------------------------------------------------------------------


async def test_e2e_notification_chunks_delivered(dt_worker, fake_dingtalk, session_factory):
    _base_url, state = fake_dingtalk
    world = await _seed_world_db(session_factory, suffix="chunks")
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=uuid.UUID(world["ws_id"]),
            recipient_id=uuid.UUID(world["member_id"]),
            type="execution_finished",
            priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=uuid.UUID(world["ws_id"]),
            notification_id=notification.id,
            channel="im",
            provider="dingtalk",
            destination_key=f"dingtalk:{world['binding_id']}:{CONV_REF}",
            integration_id=uuid.UUID(world["integration_id"]),
            binding_id=world["binding_id"],
            external_target=json.dumps({"chunks_total": 2, "sent_chunks": 0}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        notification_id, delivery_id = notification.id, delivery.id
        for index in range(2):
            await emit_event(
                session,
                workspace_id=uuid.UUID(world["ws_id"]),
                event_type=IM_SEND_EVENT_TYPE,
                payload={
                    "kind": "notification",
                    "workspace_id": world["ws_id"],
                    "integration_id": world["integration_id"],
                    "binding_id": str(world["binding_id"]),
                    "conversation_key": CONV_KEY,
                    "conversation_type": "group",
                    "chunk_index": index,
                    "chunks_total": 2,
                    "text": f"结果分段 {index} " + "内容" * 100,
                    "delivery_id": str(delivery_id),
                },
                idempotency_key=chunk_idempotency_key(notification_id, index),
            )

    async def _delivered():
        async with session_factory() as session:
            row = await session.get(NotificationDelivery, delivery_id)
            return row.state == "sent"

    assert await poll_until(_delivered, timeout=15), "chunks never delivered"
    sends = state.group_sends()
    assert len(sends) == 2
    titles = [json.loads(s["body"]["msgParam"])["title"] for s in sends]
    assert titles == ["Mesh 执行结果 (1/2)", "Mesh 执行结果 (2/2)"]
    async with session_factory() as session:
        row = await session.get(NotificationDelivery, delivery_id)
        assert json.loads(row.external_target)["sent_chunks"] == 2


# ---------------------------------------------------------------------------
# §5.6 — rate-limit classification: backoff without failure budget
# ---------------------------------------------------------------------------


async def test_e2e_rate_limit_backoff_no_budget(dt_worker, fake_dingtalk, session_factory):
    _base_url, state = fake_dingtalk
    # first two sends hit the platform rate limit, then success
    state.send_queue.extend(
        [
            (400, {"code": "send.too.fast", "message": "slow down",
                   "flowControlledStaffIdList": ["staffE2E"]}),
            (400, {"code": "send.too.fast", "message": "slow down"}),
        ]
    )
    world = await _seed_world_db(session_factory, suffix="rl")
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=uuid.UUID(world["ws_id"]),
            recipient_id=uuid.UUID(world["member_id"]),
            type="execution_finished",
            priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=uuid.UUID(world["ws_id"]),
            notification_id=notification.id,
            channel="im",
            provider="dingtalk",
            destination_key=f"dingtalk:{world['binding_id']}:rl",
            integration_id=uuid.UUID(world["integration_id"]),
            binding_id=world["binding_id"],
            external_target=json.dumps({"chunks_total": 1}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        notification_id, delivery_id = notification.id, delivery.id
        await emit_event(
            session,
            workspace_id=uuid.UUID(world["ws_id"]),
            event_type=IM_SEND_EVENT_TYPE,
            payload={
                "kind": "notification",
                "workspace_id": world["ws_id"],
                "integration_id": world["integration_id"],
                "binding_id": str(world["binding_id"]),
                "conversation_key": CONV_KEY,
                "conversation_type": "group",
                "chunk_index": 0,
                "chunks_total": 1,
                "text": "限流重试结果",
                "delivery_id": str(delivery_id),
            },
            idempotency_key=chunk_idempotency_key(notification_id, 0),
        )

    async def _delivered():
        async with session_factory() as session:
            row = await session.get(NotificationDelivery, delivery_id)
            return row.state == "sent"

    assert await poll_until(_delivered, timeout=20), "rate-limited chunk never retried"
    # THREE platform calls (two 429-class + one success), budget UNTOUCHED
    assert len(state.group_sends()) == 3
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        event = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
        )
        assert event.status == "published"
        assert event.delivery_attempts == 0  # failure budget never consumed


# ---------------------------------------------------------------------------
# §5.6 — two worker replicas refresh the token EXACTLY once
# ---------------------------------------------------------------------------


async def test_e2e_token_single_flight_two_replicas(fake_dingtalk, session_factory, provision_database):
    base_url, state = fake_dingtalk
    state.token_delay = 1.2  # widen the refresh window so both replicas overlap
    before = state.token_calls
    workers = [
        _spawn_worker(base_url, f"/tmp/dingtalk_worker_sf_{i}.log") for i in (1, 2)
    ]
    try:
        await asyncio.sleep(2.5)
        assert all(p.poll() is None for p in workers), "single-flight worker died at startup"
        world = await _seed_world_db(session_factory, suffix="sf")
        window_base = datetime.now(UTC)
        # two independent conversations → both replicas race for tokens
        await _enqueue_acked_item(session_factory, world, seq=1, window_base=window_base, offset=0)
        async with session_factory() as session, session.begin():
            item2 = IntegrationMessageQueue(
                workspace_id=uuid.UUID(world["ws_id"]),
                integration_id=uuid.UUID(world["integration_id"]),
                binding_id=world["binding_id"],
                conversation_key=f"dingtalk:{CORP_ID}:cidSECOND==",
                seq=1,
                dispatch_mode="serial_conversation",
                state="pending",
                message_excerpt="second conversation",
                sender_identity_key=f"dingtalk:{CORP_ID}:staffE2E",
                ack_window_at=window_base,
                enqueued_at=window_base,
            )
            session.add(item2)
            await session.flush()
            await elect_ack_leader(
                session, item=item2, ack_template="✅ 已接收，处理中",
                coalesce_window=timedelta(seconds=5), conversation_type="group",
            )

        async def _both_sent():
            return len(state.group_sends()) >= 2

        assert await poll_until(_both_sent, timeout=20), "replicas did not deliver both acks"
        # the accessToken endpoint was hit EXACTLY once (shared Redis cache +
        # single-flight lock across the two processes)
        assert state.token_calls - before == 1
    finally:
        for process in workers:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
        state.token_delay = 0.0


# ---------------------------------------------------------------------------
# §5.6 — approval card HTTP callback: full auth chain + lifecycle writeback
# ---------------------------------------------------------------------------


def _dingtalk_sign(app_secret: str, ts_ms: int) -> str:
    material = f"{ts_ms}\n{app_secret}".encode()
    return base64.b64encode(hmac.new(app_secret.encode(), material, hashlib.sha256).digest()).decode()


async def test_e2e_card_http_callback_full_auth_chain(dt_api_server, session_factory):
    world = await _seed_world_db(session_factory, suffix="card")
    # approval subject: a real awaiting-approval execution
    async with session_factory() as session, session.begin():
        execution = TaskExecution(
            workspace_id=uuid.UUID(world["ws_id"]),
            agent_id=uuid.UUID(world["agent_id"]),
            trigger="integration",
            status="awaiting_approval",
            task_spec={},
        )
        session.add(execution)
        await session.flush()
        approval = Approval(
            workspace_id=uuid.UUID(world["ws_id"]),
            subject_type="tool_call",
            subject_execution_id=execution.id,
            requested_by_member_id=uuid.UUID(world["member_id"]),
            action_summary={"action": "生产环境部署", "capability": "deploy",
                            "permission": "write", "impact_scope": "api 服务"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(approval)
        await session.flush()
        approval_id = approval.id
        member = await session.get(Member, uuid.UUID(world["member_id"]))
        session.add(ExternalIdentity(
            provider="dingtalk", provider_tenant_key=f"{CORP_ID}-CARD",
            external_user_key="staffCARD", user_id=member.user_id,
            created_in_workspace_id=uuid.UUID(world["ws_id"]),
        ))
    # map the integration's corp into this world
    async with session_factory() as session, session.begin():
        integration = Integration(
            id=uuid.uuid4(), workspace_id=uuid.UUID(world["ws_id"]),
            kind="im_dingtalk", name=f"dingtalk-card-{uuid.uuid4().hex[:6]}",
            config={"app_key": "dingkey-card", "corp_id": f"{CORP_ID}-CARD",
                    "robot_code": "robot-card", "receive_mode": "http"},
            secret_ref=encrypt_secret(APP_SECRET, DEV_JWT_SECRET),
            created_by=uuid.UUID(world["member_id"]),
        )
        session.add(integration)

    payload = {
        "outTrackId": f"mesh-appr-{approval_id.hex}",
        "corpId": f"{CORP_ID}-CARD",
        "userId": "staffCARD",
        "userIdType": "staffId",
        "content": {"cardPrivateData": {"actionIds": ["approve"],
                                        "params": {"approval_id": str(approval_id),
                                                   "decision": "approve"}}},
    }
    body = json.dumps(payload).encode()
    ts_ms = int(time.time() * 1000)
    headers = {"timestamp": str(ts_ms), "sign": _dingtalk_sign(APP_SECRET, ts_ms),
               "content-type": "application/json"}
    async with httpx.AsyncClient(base_url=dt_api_server, timeout=15) as client:
        resp = await client.post("/api/v1/integrations/dingtalk/cards", content=body, headers=headers)
        assert resp.status_code == 200, resp.text
        card_data = resp.json()["cardData"]["cardParamMap"]
        assert "已批准" in card_data["status_text"]
        assert card_data["buttons_disabled"] == "true"
    async with session_factory() as session:
        row = await session.get(Approval, approval_id)
        assert row.status == "approved"
        assert row.decision_comment == "via dingtalk card callback"

    # unmapped clicker → 403, approval UNCHANGED, lifecycle card 无权限
    payload2 = {**payload, "userId": "staffUNMAPPED",
                "content": {"cardPrivateData": {"params": {"approval_id": str(approval_id),
                                                           "decision": "reject"}}}}
    body2 = json.dumps(payload2).encode()
    headers2 = {"timestamp": str(ts_ms), "sign": _dingtalk_sign(APP_SECRET, ts_ms),
                "content-type": "application/json"}
    async with httpx.AsyncClient(base_url=dt_api_server, timeout=15) as client:
        resp2 = await client.post("/api/v1/integrations/dingtalk/cards", content=body2, headers=headers2)
        assert resp2.status_code == 403
        assert "无权限" in resp2.json()["cardData"]["cardParamMap"]["status_text"]
    async with session_factory() as session:
        row = await session.get(Approval, approval_id)
        assert row.status == "approved"  # unchanged by the denied click

    # invalid signature → 401, never forwarded
    headers3 = {"timestamp": str(ts_ms), "sign": _dingtalk_sign("wrong-secret", ts_ms),
                "content-type": "application/json"}
    async with httpx.AsyncClient(base_url=dt_api_server, timeout=15) as client:
        resp3 = await client.post("/api/v1/integrations/dingtalk/cards", content=body, headers=headers3)
        assert resp3.status_code == 401


# ---------------------------------------------------------------------------
# §6.16 — the app secret never reaches worker logs
# ---------------------------------------------------------------------------


async def test_e2e_app_secret_never_in_worker_logs(dt_worker, fake_dingtalk, session_factory):
    _base_url, state = fake_dingtalk
    # produce real outbound traffic first (token exchange + message send)
    world = await _seed_world_db(session_factory, suffix="redact")
    await _enqueue_acked_item(
        session_factory, world, seq=1, window_base=datetime.now(UTC), offset=0
    )

    async def _sent():
        return len(state.group_sends()) >= 1

    assert await poll_until(_sent, timeout=15), "no outbound traffic to inspect"
    # the wire legitimately carries the secret (token exchange) …
    token_requests = state.calls("/v1.0/oauth2/accessToken")
    assert token_requests, "token endpoint was never called"
    assert token_requests[0]["body"]["appSecret"] == APP_SECRET
    # … but NO worker log line may contain the secret or a token value
    assert WORKER_LOGS, "worker logs were never captured"
    for log_path in WORKER_LOGS:
        try:
            content = open(log_path, encoding="utf-8", errors="replace").read()
        except FileNotFoundError:
            continue
        assert APP_SECRET not in content, f"app secret leaked into {log_path}"
        assert "tok-" not in content, f"access token leaked into {log_path}"


# ---------------------------------------------------------------------------
# DB-seeded world (worker-focused tests bypass the API)
# ---------------------------------------------------------------------------


async def _seed_world_db(session_factory, *, suffix: str) -> dict:
    from mesh.db.models.agent import Agent
    from mesh.db.models.user import User
    from mesh.db.models.workspace import Workspace

    ids = {k: uuid.uuid4() for k in ("ws", "user", "member", "agent", "integration")}
    async with session_factory() as session, session.begin():
        session.add(Workspace(id=ids["ws"], name=f"DT E2E {suffix}", slug=f"dt-e2e-{suffix}"))
        session.add(User(id=ids["user"], email=f"dt-{suffix}@e2e.mesh",
                         display_name="DT E2E", password_hash="unused"))
        await session.flush()
        session.add(Agent(id=ids["agent"], workspace_id=ids["ws"], name=f"agent-{suffix}",
                          owner_user_id=ids["user"], lifecycle_status="active"))
        await session.flush()
        session.add(Member(id=ids["member"], workspace_id=ids["ws"], member_type="human",
                           user_id=ids["user"], role="admin", status="active"))
        await session.flush()
        integration = Integration(
            id=ids["integration"], workspace_id=ids["ws"], kind="im_dingtalk",
            name=f"dingtalk-{suffix}",
            config={"app_key": f"dingkey-{suffix}", "corp_id": CORP_ID,
                    "robot_code": f"robot-{suffix}", "receive_mode": "stream",
                    "verbosity": "final_only"},
            secret_ref=encrypt_secret(APP_SECRET, DEV_JWT_SECRET),
            created_by=ids["member"],
        )
        session.add(integration)
        await session.flush()
        binding = IntegrationBinding(
            workspace_id=ids["ws"], integration_id=ids["integration"],
            provider="dingtalk", provider_tenant_key=CORP_ID,
            scope="workspace", external_ref=CONV_REF,
            match_config={"trigger_on": ["mention"]},
            bound_agent_id=ids["agent"], status="active",
        )
        session.add(binding)
        await session.flush()
    return {
        "ws_id": str(ids["ws"]),
        "member_id": str(ids["member"]),
        "agent_id": str(ids["agent"]),
        "integration_id": str(ids["integration"]),
        "binding_id": binding.id,
    }


# ---------------------------------------------------------------------------
# §5.6 / §3.10 — token_refresh_busy is NEVER terminal, never consumes budget
# ---------------------------------------------------------------------------


async def test_e2e_token_busy_never_terminal(
    dt_worker, fake_dingtalk, session_factory, redis_client
):
    """A foreign replica holds the refresh lease while the worker delivers:
    the event must stay PENDING with delivery_attempts == 0 (busy defers
    ``available_at`` only — never terminal, never budget); after the lease
    is released and the backoff expires, the same event succeeds."""
    _base_url, state = fake_dingtalk
    world = await _seed_world_db(session_factory, suffix="busy")
    async with session_factory() as session, session.begin():
        notification = Notification(
            workspace_id=uuid.UUID(world["ws_id"]),
            recipient_id=uuid.UUID(world["member_id"]),
            type="execution_finished",
            priority="normal",
        )
        session.add(notification)
        await session.flush()
        delivery = NotificationDelivery(
            workspace_id=uuid.UUID(world["ws_id"]),
            notification_id=notification.id,
            channel="im",
            provider="dingtalk",
            destination_key=f"dingtalk:{world['binding_id']}:{CONV_REF}",
            integration_id=uuid.UUID(world["integration_id"]),
            binding_id=world["binding_id"],
            external_target=json.dumps({"chunks_total": 1, "sent_chunks": 0}),
            state="pending",
        )
        session.add(delivery)
        await session.flush()
        notification_id, delivery_id = notification.id, delivery.id
    # Hold the refresh lease BEFORE the event exists — the worker can never
    # acquire it until the test releases (deterministic busy window).
    lock_key = f"dingtalk:token_lock:{world['integration_id']}"
    await redis_client.set(lock_key, "e2e-foreign-owner", nx=True, ex=30)
    async with session_factory() as session, session.begin():
        await emit_event(
            session,
            workspace_id=uuid.UUID(world["ws_id"]),
            event_type=IM_SEND_EVENT_TYPE,
            payload={
                "kind": "notification",
                "workspace_id": world["ws_id"],
                "integration_id": world["integration_id"],
                "binding_id": str(world["binding_id"]),
                "conversation_key": CONV_KEY,
                "conversation_type": "group",
                "chunk_index": 0,
                "chunks_total": 1,
                "text": "busy 窗口内不终态",
                "delivery_id": str(delivery_id),
            },
            idempotency_key=chunk_idempotency_key(notification_id, 0),
        )
    # Worker pass 1: ~12s follower wait → retry still blocked (lock held
    # ~13s) → busy → available_at deferred ~2s, attempts untouched.
    await asyncio.sleep(13.2)
    from mesh.db.models.outbox import OutboxEvent

    async with session_factory() as session:
        event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
            )
        ).scalar_one()
        assert event.status == "pending", "busy must keep the event pending"
        assert event.delivery_attempts == 0, "busy must not consume the budget"
        assert event.available_at is not None
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.state == "pending"
        assert row.error == "token_refresh_busy"  # ledger records the busy try
    assert state.group_sends() == []  # nothing delivered during the busy window

    # Release the lease; after the short backoff the worker succeeds.
    await redis_client.delete(lock_key)

    async def _delivered():
        async with session_factory() as session:
            row = await session.get(NotificationDelivery, delivery_id)
            return row.state == "sent"

    assert await poll_until(_delivered, timeout=20), "busy event never delivered"
    async with session_factory() as session:
        event = (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == IM_SEND_EVENT_TYPE)
            )
        ).scalar_one()
        assert event.status == "published"
        assert event.delivery_attempts == 0  # budget STILL untouched
        row = await session.get(NotificationDelivery, delivery_id)
        assert row.error is None  # transient busy trace cleared
    assert len(state.group_sends()) == 1
