"""DingTalk dual-receive E2E (integrations.md §5.6, MES-87) — REAL API +
REAL worker process + REAL PostgreSQL/Redis; the DingTalk gateway is a
controllable TLS fake reached through the deploy-time test-injection door
(MESH_DINGTALK_GATEWAY_BASE) — with REAL certificate verification (the
worker subprocess trusts a purpose-generated test CA via SSL_CERT_FILE;
verify=False is never used anywhere on this path).

Red lines over actual HTTP + a real ``python -m mesh.workers`` process:
HTTP callback signature positive/negative/replay, msgId dedup, msgtype
matrix audit-only, pre-signature (integration, IP) silent-200 limiter;
Stream connections/open with the real app_secret, message-frame ingestion
through the shared core (_mesh_channel='stream'), ping ACK verbatim,
redelivery msgId dedup, disconnect → immediate reconnect, stream-status
endpoint truth source.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.db.models.integration import (
    ExternalIdentity,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.runtime import Approval, TaskExecution
from mesh.integrations.dingtalk_cards import derive_out_track_id

PASSWORD = "DT-E2E-123456"
APP_SECRET = "dt-e2e-app-secret-000000000000000000"
CORP_ID = "dingcorpe2e0001"
APP_KEY = "dingappkeye2e0001"
CONVERSATION_ID = "cidE2EOfficial000000=="

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# TLS fake gateway (test CA + HTTPS connections/open + WSS frame server)
# ---------------------------------------------------------------------------


def _generate_test_ca(tmp_path):
    """A purpose-built test CA + server certificate for 127.0.0.1. The
    worker verifies the gateway with the REAL default ssl context pointed
    at this CA (SSL_CERT_FILE) — no verification bypass."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Mesh E2E Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    srv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(srv_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=7))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(
        srv_cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)
    )
    key_path.write_bytes(
        srv_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert_path), str(key_path))
    return str(ca_path), server_ctx


class FakeGatewayState:
    def __init__(self) -> None:
        self.open_requests: list[dict] = []
        self.ws_port: int | None = None
        # (seq, ws) pairs — seq orders connections across reconnects/tests
        # so a test can target ONLY connections created after its own setup
        # (never a stale socket from a previous group lifecycle).
        self.connections: list[tuple[int, object]] = []
        self.conn_seq = 0
        self.acks: list[dict] = []
        self.ticket_counter = 0
        self.lock = threading.Lock()

    def newest_conn(self, after_seq: int = 0):
        live = [(s, ws) for s, ws in self.connections if s > after_seq]
        return max(live)[1] if live else None


def _start_https_gateway(state: FakeGatewayState, server_ctx: ssl.SSLContext):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence request logging
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode() or "{}")
            with state.lock:
                state.open_requests.append({"path": self.path, "body": body, "at": time.time()})
                state.ticket_counter += 1
                ticket = f"ticket-{state.ticket_counter}"
            if body.get("clientSecret") != APP_SECRET:
                payload = json.dumps({"code": "invalidAppSecret"}).encode()
                self.send_response(401)
            else:
                payload = json.dumps(
                    {"endpoint": f"wss://127.0.0.1:{state.ws_port}/ws", "ticket": ticket}
                ).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            accepted = (
                parsed.path == "/gettoken"
                and query.get("appkey") == [APP_KEY]
                and query.get("appsecret") == [APP_SECRET]
            )
            payload = json.dumps(
                {"errcode": 0, "access_token": "e2e-owner-proof"}
                if accepted
                else {"errcode": 40089, "errmsg": "invalid app credentials"}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.socket = server_ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


@pytest_asyncio.fixture(scope="module")
async def fake_dingtalk_gateway(tmp_path_factory):
    import websockets

    tmp_path = tmp_path_factory.mktemp("dt-gateway")
    ca_path, server_ctx = _generate_test_ca(tmp_path)
    state = FakeGatewayState()
    httpd, https_port = _start_https_gateway(state, server_ctx)

    async def ws_handler(ws):
        with state.lock:
            state.conn_seq += 1
            entry = (state.conn_seq, ws)
        state.connections.append(entry)
        try:
            async for message in ws:
                state.acks.append(json.loads(message))
        except Exception:
            pass
        finally:
            with contextlib.suppress(ValueError):
                state.connections.remove(entry)

    ws_server = await websockets.serve(ws_handler, "127.0.0.1", 0, ssl=server_ctx)
    state.ws_port = ws_server.sockets[0].getsockname()[1]
    state.https_port = https_port
    state.ca_path = ca_path
    yield state
    ws_server.close()
    await ws_server.wait_closed()
    httpd.shutdown()


@pytest_asyncio.fixture(scope="module")
async def api_server(provision_database, fake_dingtalk_gateway):
    """Real API process whose first-claim proof hits the TLS fake OAPI."""
    from tests.e2e.conftest import RunningServer, _free_port, _spawn, _terminate, _wait_ready

    port = _free_port()
    overrides = {
        "MESH_DINGTALK_OAPI_BASE": (f"https://127.0.0.1:{fake_dingtalk_gateway.https_port}"),
        "SSL_CERT_FILE": fake_dingtalk_gateway.ca_path,
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        server = RunningServer("api", _spawn("mesh.api.app", port), f"http://127.0.0.1:{port}")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    await _wait_ready(server.base_url)
    yield server
    _terminate(server)


@pytest_asyncio.fixture(scope="module")
async def dingtalk_worker(provision_database, fake_dingtalk_gateway):
    """Real worker process pointed at the fake gateway via the deploy-time
    door; trusts the test CA via SSL_CERT_FILE (real verification)."""
    env = os.environ.copy()
    from tests.conftest import get_test_database_url, get_test_redis_url

    env["MESH_DATABASE_URL"] = get_test_database_url()
    env["MESH_REDIS_URL"] = get_test_redis_url()
    env["MESH_AUTH_MODE"] = "dev"
    env["MESH_OUTBOX_POLL_INTERVAL"] = "0.2"
    env["MESH_DINGTALK_GATEWAY_BASE"] = f"https://127.0.0.1:{fake_dingtalk_gateway.https_port}"
    env["MESH_DINGTALK_STREAM_SCAN_INTERVAL"] = "0.5"
    env["MESH_APP_BASE_URL"] = "https://mesh.e2e.example"
    env["SSL_CERT_FILE"] = fake_dingtalk_gateway.ca_path
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
    log_file = open("/tmp/dingtalk_worker.log", "wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "mesh.workers"],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        await asyncio.sleep(2.5)
        assert process.poll() is None, "dingtalk worker died during startup"
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log_file.close()


# ---------------------------------------------------------------------------
# Auth + world helpers (real API)
# ---------------------------------------------------------------------------


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "DT E2E"},
    )
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _make_dingtalk_world(api_client, suffix: str, *, receive_mode: str) -> dict:
    token = await _register_and_login(api_client, f"dt-{suffix}@e2e.mesh")
    resp = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "DT E2E", "slug": f"dt-{suffix}"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["data"]["id"]
    agent_resp = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/agents",
        json={"name": f"dt-agent-{suffix}"},
        headers=_auth(token),
    )
    assert agent_resp.status_code == 201, agent_resp.text
    integ = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/integrations",
        json={
            "kind": "im_dingtalk",
            "name": f"dt-{suffix}",
            "config": {
                "app_key": APP_KEY,
                "corp_id": CORP_ID,
                "robot_code": APP_KEY,
                "receive_mode": receive_mode,
            },
            "secret": APP_SECRET,
        },
        headers=_auth(token),
    )
    assert integ.status_code == 201, integ.text
    integration_id = integ.json()["data"]["integration"]["id"]
    bind = await api_client.post(
        f"/api/v1/workspaces/{ws_id}/integrations/{integration_id}/bindings",
        json={
            "external_ref": CONVERSATION_ID,
            "bound_agent_id": agent_resp.json()["data"]["id"],
            "match_config": {"trigger_on": ["mention", "direct_message"]},
        },
        headers=_auth(token),
    )
    assert bind.status_code == 201, bind.text
    return {
        "token": token,
        "ws_id": ws_id,
        "integration_id": integration_id,
        "agent_id": agent_resp.json()["data"]["id"],
    }


def _sign_headers(secret: str = APP_SECRET, offset_seconds: float = 0.0) -> dict:
    ts_ms = str(int((datetime.now(UTC).timestamp() + offset_seconds) * 1000))
    material = f"{ts_ms}\n{secret}".encode()
    sign = base64.b64encode(hmac.new(secret.encode(), material, hashlib.sha256).digest()).decode()
    return {"timestamp": ts_ms, "sign": sign, "content-type": "application/json"}


def _message(msg_id: str | None = None, text: str = "帮我查下报警", msgtype: str = "text") -> dict:
    return {
        "msgId": msg_id or f"msgE2E{uuid.uuid4().hex[:16]}==",
        "conversationId": CONVERSATION_ID,
        "conversationType": "2",
        "chatbotCorpId": CORP_ID,
        "robotCode": APP_KEY,
        "msgtype": msgtype,
        "senderStaffId": "014728255240768602",
        "senderId": "$:LWCP_v1:$E2Esender000000000000000000000000",
        "senderNick": "E2E 值班",
        "isInAtList": True,
        "text": {"content": f" {text}"},
    }


async def poll_until(query_fn, timeout: float = 15.0, interval: float = 0.3):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await query_fn()
        if last:
            return last
        await asyncio.sleep(interval)
    return last


# ---------------------------------------------------------------------------
# HTTP callback mode
# ---------------------------------------------------------------------------


async def test_http_callback_valid_signature_ingests_real_e2e(api_client, session_factory):
    world = await _make_dingtalk_world(api_client, "http1", receive_mode="http")
    body = json.dumps(_message()).encode()
    resp = await api_client.post(
        "/api/v1/integrations/dingtalk/events",
        content=body,
        headers=_sign_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["process_status"] == "dispatched"

    async def _row():
        async with session_factory() as session:
            return (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.integration_id == uuid.UUID(world["integration_id"])
                    )
                )
            ).scalar_one_or_none()

    event = await poll_until(_row)
    assert event is not None
    assert event.signature_status == "valid"
    assert event.process_status == "dispatched"
    assert event.payload["_mesh_channel"] == "http"

    # Deterministic state wait, NOT a transient snapshot. This test does
    # not request the worker fixture (the module's worker spins up at the
    # first stream test, AFTER this one), so the item normally stays
    # pending — but when a worker IS live (test reordering / selection),
    # the serial item walks pending → dispatching → processing. Accept
    # every in-chain non-terminal state so the assertion is deterministic
    # in both contexts (converges immediately — the ingest transaction
    # has already committed the item by the time we get here).
    async def _http_in_queue_chain():
        async with session_factory() as session:
            rows = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        if len(rows) != 1 or rows[0].state not in (
            "pending",
            "dispatching",
            "processing",
        ):
            return None
        return rows[0]

    queue = await poll_until(_http_in_queue_chain, timeout=15.0)
    assert queue is not None, "queue item never reached the queue chain"
    assert queue.seq == 1
    assert queue.conversation_key == f"dingtalk:{CORP_ID}:{CONVERSATION_ID}"


async def test_http_callback_reject_never_dispatches_real_e2e(api_client, session_factory):
    world = await _make_dingtalk_world(api_client, "http2", receive_mode="http")
    body = json.dumps(_message()).encode()

    bad = await api_client.post(
        "/api/v1/integrations/dingtalk/events",
        content=body,
        headers=_sign_headers(secret="wrong-secret-0000000000000000000000"),
    )
    assert bad.status_code == 401
    replay = await api_client.post(
        "/api/v1/integrations/dingtalk/events",
        content=body,
        headers=_sign_headers(offset_seconds=-3601),
    )
    assert replay.status_code == 401
    missing = await api_client.post(
        "/api/v1/integrations/dingtalk/events",
        content=body,
        headers={"timestamp": _sign_headers()["timestamp"], "content-type": "application/json"},
    )
    assert missing.status_code == 401

    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        events = (
            (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.integration_id == uuid.UUID(world["integration_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert queues == []  # never queued
    assert all(e.process_status == "rejected" for e in events)


async def test_http_callback_dedup_real_e2e(api_client, session_factory):
    await _make_dingtalk_world(api_client, "http3", receive_mode="http")
    payload = _message(msg_id="msgE2EDEDUP000000000==")
    body = json.dumps(payload).encode()
    first = await api_client.post(
        "/api/v1/integrations/dingtalk/events", content=body, headers=_sign_headers()
    )
    second = await api_client.post(
        "/api/v1/integrations/dingtalk/events", content=body, headers=_sign_headers()
    )
    assert first.json()["process_status"] == "dispatched"
    assert second.status_code == 200
    assert second.json()["process_status"] == "deduped"
    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert len(queues) == 1


async def test_http_callback_non_text_audit_only_real_e2e(api_client, session_factory):
    world = await _make_dingtalk_world(api_client, "http4", receive_mode="http")
    body = json.dumps(_message(msgtype="picture")).encode()
    resp = await api_client.post(
        "/api/v1/integrations/dingtalk/events", content=body, headers=_sign_headers()
    )
    assert resp.status_code == 200
    assert resp.json()["process_status"] == "processed"
    async with session_factory() as session:
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        event = (
            await session.execute(
                select(IntegrationEvent).where(
                    IntegrationEvent.integration_id == uuid.UUID(world["integration_id"])
                )
            )
        ).scalar_one()
    assert queues == []
    assert event.process_status == "processed"


async def test_http_pre_signature_limit_silent_200_real_e2e(api_client):
    """auth.md §3.6: the (integration, IP) coarse layer answers over-limit
    callers with a SILENT 200 (non-2xx would trigger platform retry
    amplification)."""
    await _make_dingtalk_world(api_client, "http5", receive_mode="http")
    body = json.dumps(_message(msg_id="msgE2ELIMIT00000000==")).encode()
    headers = _sign_headers()

    last = None
    for _ in range(125):
        last = await api_client.post("/api/v1/integrations/dingtalk/events", content=body, headers=headers)
    assert last.status_code == 200  # silent — never 429 from this layer
    # Indistinguishable from ordinary acceptance — the over-limit caller
    # learns nothing about the defense (audit trail carries the truth).
    assert last.json() == {"received": True, "event_id": "", "process_status": "received"}


# ---------------------------------------------------------------------------
# Stream mode (real worker process + TLS fake gateway)
# ---------------------------------------------------------------------------


def _frame(payload: dict, topic: str = "/v1.0/im/bot/messages/get", mid: str | None = None):
    return {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {"topic": topic, "messageId": mid or uuid.uuid4().hex},
        "data": json.dumps(payload, ensure_ascii=False),
    }


async def _wait_for_connection(gateway, after_seq: int = 0):
    async def _conn():
        return gateway.newest_conn(after_seq)

    conn = await poll_until(_conn, timeout=25.0)
    assert conn is not None, "worker never established the Stream connection"
    return conn


async def test_stream_connects_with_real_credentials_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    world = await _make_dingtalk_world(api_client, "stream1", receive_mode="stream")

    async def _opened():
        return fake_dingtalk_gateway.open_requests or None

    opened = await poll_until(_opened, timeout=20.0)
    assert opened, "worker never called connections/open"
    body = opened[0]["body"]
    assert body["clientId"] == APP_KEY
    assert body["clientSecret"] == APP_SECRET  # in-memory plaintext, once, to the gateway
    topics = {s["topic"] for s in body["subscriptions"]}
    assert "/v1.0/im/bot/messages/get" in topics

    # stream-status endpoint: connected truth source.
    async def _status():
        resp = await api_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration_id']}/stream-status",
            headers=_auth(world["token"]),
        )
        data = resp.json()["data"]
        return data if data["state"] == "connected" else None

    status = await poll_until(_status, timeout=15.0)
    assert status is not None
    assert status["last_frame_at"] is not None


async def test_stream_message_frame_ingests_and_acks_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    pre_seq = fake_dingtalk_gateway.conn_seq
    await _make_dingtalk_world(api_client, "stream2", receive_mode="stream")
    conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)

    msg_id = "msgE2ESTREAM00000000=="
    await conn.send(json.dumps(_frame(_message(msg_id=msg_id))))

    async def _ingested():
        async with session_factory() as session:
            return (
                await session.execute(
                    select(IntegrationEvent).where(IntegrationEvent.external_event_id == msg_id)
                )
            ).scalar_one_or_none()

    event = await poll_until(_ingested, timeout=15.0)
    assert event is not None
    assert event.signature_status == "valid"
    assert event.payload["_mesh_channel"] == "stream"
    assert event.process_status == "dispatched"

    async def _acked():
        return [a for a in fake_dingtalk_gateway.acks if a.get("data") == "received"] or None

    acks = await poll_until(_acked, timeout=10.0)
    assert acks, "no 'received' ACK returned to the gateway"
    assert acks[0]["code"] == 200

    async def _in_dispatch_chain():
        async with session_factory() as session:
            rows = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
        if len(rows) != 1 or rows[0].state not in ("dispatching", "processing"):
            return None
        return rows[0]

    queue = await poll_until(_in_dispatch_chain, timeout=15.0)
    assert queue is not None, "queue item never reached dispatching/processing"


async def test_stream_card_callback_decides_and_returns_lifecycle_ack_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    pre_seq = fake_dingtalk_gateway.conn_seq
    world = await _make_dingtalk_world(api_client, "stream-card", receive_mode="stream")
    conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)
    staff_id = "014728255240768602"
    async with session_factory() as session, session.begin():
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == uuid.UUID(world["ws_id"]),
                Member.member_type == "human",
            )
        )
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
            requested_by_member_id=member.id,
            action_summary={"action": "Deploy E2E service", "permission": "write"},
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(approval)
        session.add(
            ExternalIdentity(
                provider="dingtalk",
                provider_tenant_key=CORP_ID,
                external_user_key=staff_id,
                user_id=member.user_id,
                created_in_workspace_id=uuid.UUID(world["ws_id"]),
            )
        )
        await session.flush()
        approval_id = approval.id

    message_id = f"card-e2e-{uuid.uuid4().hex}"
    payload = {
        "outTrackId": derive_out_track_id(approval_id, uuid.UUID(world["integration_id"])),
        "corpId": CORP_ID,
        "userId": staff_id,
        "userIdType": "staffId",
        "content": {
            "cardPrivateData": {
                "actionIds": ["approve"],
                "params": {
                    "approval_id": str(approval_id),
                    "decision": "approve",
                },
            }
        },
    }
    frame = _frame(
        payload,
        topic="/v1.0/card/instances/callback",
        mid=message_id,
    )
    await conn.send(json.dumps(frame))

    async def _decided():
        async with session_factory() as session:
            row = await session.get(Approval, approval_id)
            return row if row is not None and row.status == "approved" else None

    assert await poll_until(_decided, timeout=15.0) is not None

    async def _lifecycle_ack():
        return next(
            (
                ack
                for ack in fake_dingtalk_gateway.acks
                if ack.get("headers", {}).get("messageId") == message_id
            ),
            None,
        )

    ack = await poll_until(_lifecycle_ack, timeout=10.0)
    assert ack["code"] == 200
    assert "已批准" in ack["data"]["cardData"]["cardParamMap"]["status_text"]

    # A real WSS redelivery remains idempotent and receives the same terminal
    # writeback instead of re-running the approval action.
    await conn.send(json.dumps(frame))

    async def _two_lifecycle_acks():
        matches = [
            ack for ack in fake_dingtalk_gateway.acks if ack.get("headers", {}).get("messageId") == message_id
        ]
        return matches if len(matches) >= 2 else None

    assert await poll_until(_two_lifecycle_acks, timeout=10.0)


async def test_stream_redelivery_is_msgid_deduped_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    pre_seq = fake_dingtalk_gateway.conn_seq
    await _make_dingtalk_world(api_client, "stream3", receive_mode="stream")
    conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)

    msg_id = "msgE2EREPUSH00000000=="
    frame = _frame(_message(msg_id=msg_id))
    await conn.send(json.dumps(frame))

    async def _ingested():
        async with session_factory() as session:
            return (
                await session.execute(
                    select(IntegrationEvent).where(IntegrationEvent.external_event_id == msg_id)
                )
            ).scalar_one_or_none()

    assert await poll_until(_ingested, timeout=15.0) is not None
    acks_before = len(fake_dingtalk_gateway.acks)
    # Platform redelivers the un-ACKed frame (simulated: send it again).
    await conn.send(json.dumps(frame))

    async def _second_ack():
        received = [a for a in fake_dingtalk_gateway.acks if a.get("data") == "received"]
        return received if len(received) >= 2 else None

    assert await poll_until(_second_ack, timeout=10.0), "redelivery was not ACKed"
    async with session_factory() as session:
        events = (
            (
                await session.execute(
                    select(IntegrationEvent).where(IntegrationEvent.external_event_id == msg_id)
                )
            )
            .scalars()
            .all()
        )
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert len(events) == 1  # msgId dedup — one ledger row
    assert len(queues) == 1  # never queued twice
    assert acks_before >= 1


async def test_stream_ping_acked_verbatim_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    pre_seq = fake_dingtalk_gateway.conn_seq
    await _make_dingtalk_world(api_client, "stream4", receive_mode="stream")
    conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)

    ping = {
        "specVersion": "1.0",
        "type": "SYSTEM",
        "headers": {"topic": "ping", "messageId": "e2e-ping-1"},
        "data": "e2e-keepalive-session",
    }
    await conn.send(json.dumps(ping))

    async def _ping_ack():
        return [
            a for a in fake_dingtalk_gateway.acks if a.get("headers", {}).get("messageId") == "e2e-ping-1"
        ] or None

    acks = await poll_until(_ping_ack, timeout=10.0)
    assert acks, "SYSTEM ping was not ACKed"
    ack = acks[0]
    assert ack["code"] == 200
    assert ack["headers"] == {"topic": "ping", "messageId": "e2e-ping-1"}  # verbatim
    assert ack["data"] == "e2e-keepalive-session"  # original data echoed


async def test_stream_disconnect_triggers_reconnect_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    pre_seq = fake_dingtalk_gateway.conn_seq
    await _make_dingtalk_world(api_client, "stream5", receive_mode="stream")
    conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)
    opens_before = len(fake_dingtalk_gateway.open_requests)

    disconnect = {
        "specVersion": "1.0",
        "type": "SYSTEM",
        "headers": {"topic": "disconnect", "messageId": "e2e-dc-1"},
        "data": "",
    }
    await conn.send(json.dumps(disconnect))

    async def _reopened():
        return len(fake_dingtalk_gateway.open_requests) > opens_before

    assert await poll_until(_reopened, timeout=15.0), "worker did not re-run connections/open"


async def test_explicit_reconnect_api_replaces_live_socket_real_e2e(
    api_client, dingtalk_worker, fake_dingtalk_gateway, session_factory
):
    """The UI action's durable marker must reach the real worker/socket."""
    pre_seq = fake_dingtalk_gateway.conn_seq
    world = await _make_dingtalk_world(api_client, "stream6", receive_mode="stream")
    old_conn = await _wait_for_connection(fake_dingtalk_gateway, after_seq=pre_seq)
    with fake_dingtalk_gateway.lock:
        old_seq = next(seq for seq, conn in fake_dingtalk_gateway.connections if conn is old_conn)
        opens_before = len(fake_dingtalk_gateway.open_requests)

    response = await api_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{world['integration_id']}:reconnect",
        headers=_auth(world["token"]),
    )
    assert response.status_code == 202, response.text
    assert response.json()["data"]["accepted"] is True

    async def _replaced():
        with fake_dingtalk_gateway.lock:
            live = list(fake_dingtalk_gateway.connections)
            opened = len(fake_dingtalk_gateway.open_requests)
        return (
            opened > opens_before
            and all(conn is not old_conn for _seq, conn in live)
            and any(seq > old_seq for seq, _conn in live)
        )

    assert await poll_until(_replaced, timeout=15.0), (
        "explicit reconnect did not replace the worker's physical Stream socket"
    )
