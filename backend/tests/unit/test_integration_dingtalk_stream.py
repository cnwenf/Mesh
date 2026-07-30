"""DingTalk Stream long-connection adapter tests (integrations.md §3.2/§5.6, MES-87).

Frame protocol (ping ACK verbatim / disconnect immediate reconnect /
message frame → shared ingestion core + ACK 'received' / redelivery msgId
dedup), wss-only anti-downgrade, forced TLS verification seam, advisory
lock single-instance mutex, app_key connection sharing, backoff bounds
(2→300s ±20%), gateway-base non-default audit warning, stream_state
persistence. Real PostgreSQL; the gateway is a controllable fake
(connections/open MockTransport + scripted WS) — the TEST INJECTION DOOR
the spec mandates (MESH_DINGTALK_GATEWAY_BASE equivalent at unit level:
injectable seams; e2e exercises the real websockets stack).
"""

from __future__ import annotations

import asyncio
import json
import random
import types
import uuid
from collections import deque

import httpx
import pytest
from sqlalchemy import select

from mesh.db.models.integration import Integration, IntegrationEvent, IntegrationMessageQueue
from mesh.integrations.dingtalk_stream import (
    STATE_CONNECTED,
    STATE_DOWN,
    DingTalkStreamClient,
    StreamEndpointInsecure,
    StreamManager,
    StreamOpenError,
    build_ack,
    compute_backoff,
)
from tests.unit.integrations_support import (
    DINGTALK_APP_SECRET,
    TEST_SIGNING_SECRET,
    dingtalk_message_payload,
    make_dingtalk_binding,
    seed_dingtalk_world,
)

pytestmark = pytest.mark.unit


def _settings(**overrides):
    base = dict(
        jwt_secret=TEST_SIGNING_SECRET,
        dingtalk_gateway_base="https://api.dingtalk.com",
        dingtalk_stream_scan_interval=0.05,
        im_inbound_per_identity_per_min=20,
        im_inbound_per_conversation_per_min=60,
        im_queue_max_pending_per_conversation=50,
        im_inbound_text_max_chars=4000,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


class FakeWS:
    """Scripted WebSocket: yields queued frames (JSON), records sends."""

    def __init__(self, frames=None) -> None:
        self.frames: deque = deque(frames or [])
        self.sent: list[dict] = []
        self.closed = False
        self._waiters: list[asyncio.Future] = []

    def push(self, frame: dict) -> None:
        self.frames.append(frame)
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()

    async def recv(self):
        while not self.frames:
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self._waiters.append(waiter)
            await waiter
        item = self.frames.popleft()
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self) -> None:
        self.closed = True
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(None)
        self._waiters.clear()


def _ping_frame(message_id: str = "ping-1") -> dict:
    return {
        "specVersion": "1.0",
        "type": "SYSTEM",
        "headers": {"topic": "ping", "messageId": message_id},
        "data": "session-keepalive-42",
    }


def _disconnect_frame() -> dict:
    return {
        "specVersion": "1.0",
        "type": "SYSTEM",
        "headers": {"topic": "disconnect", "messageId": "dc-1"},
        "data": "",
    }


def _message_frame(payload: dict, message_id: str = "msg-frame-1") -> dict:
    return {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": message_id},
        "data": json.dumps(payload, ensure_ascii=False),
    }


async def _instant_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Pure protocol pieces
# ---------------------------------------------------------------------------


def test_backoff_bounds_2_to_300_with_20pct_jitter():
    rng = random.Random(87)
    first = [compute_backoff(0, rng=rng) for _ in range(200)]
    assert all(2.0 * 0.8 <= v <= 2.0 * 1.2 for v in first)
    big = [compute_backoff(50, rng=rng) for _ in range(200)]
    assert all(300.0 * 0.8 <= v <= 300.0 * 1.2 for v in big)
    # Monotone growth before the cap.
    rng2 = random.Random(1)
    mid = compute_backoff(3, rng=rng2)  # 2 * 2^3 = 16 ± 20%
    assert 16.0 * 0.8 <= mid <= 16.0 * 1.2


def test_build_ack_echoes_original_headers_verbatim():
    frame = {"headers": {"topic": "ping", "messageId": "m-9", "contentType": "application/json"}}
    ack = build_ack(frame, "session-keepalive-42")
    assert ack == {
        "code": 200,
        "headers": {"topic": "ping", "messageId": "m-9", "contentType": "application/json"},
        "message": "OK",
        "data": "session-keepalive-42",
    }


# ---------------------------------------------------------------------------
# connections/open + transport hardening
# ---------------------------------------------------------------------------


def _open_handler(status: int = 200, endpoint: str = "wss://gw.example.test/connect"):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"code": "invalidAppSecret"})
        return httpx.Response(200, json={"endpoint": endpoint, "ticket": "ticket-abc"})

    return handler, requests


async def test_open_connection_posts_credentials_and_connects_wss():
    handler, requests = _open_handler()
    connected: dict = {}

    async def ws_connect(url, *, ssl_context):
        connected["url"] = url
        connected["ssl"] = ssl_context
        return FakeWS()

    client = DingTalkStreamClient(
        app_key="dingappkey0001",
        app_secret=DINGTALK_APP_SECRET,
        gateway_base="https://gateway.test",
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
    )
    await client.open_connection()

    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1.0/gateway/connections/open"
    assert body["clientId"] == "dingappkey0001"
    assert body["clientSecret"] == DINGTALK_APP_SECRET
    topics = {s["topic"] for s in body["subscriptions"]}
    assert "/v1.0/im/bot/messages/get" in topics
    assert "/v1.0/card/instances/callback" in topics
    assert body["ua"].startswith("mesh-integration/")
    assert connected["url"].startswith("wss://gw.example.test/connect?ticket=")
    assert "ticket-abc" in connected["url"]
    assert connected["ssl"] is not None  # verification context passed through


async def test_open_connection_refuses_non_wss_endpoint():
    from unittest.mock import patch

    handler, _ = _open_handler(endpoint="ws://gw.example.test/connect")
    connected = []

    async def ws_connect(url, *, ssl_context):
        connected.append(url)
        return FakeWS()

    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
    )
    with patch("mesh.integrations.dingtalk_stream.logger") as mock_logger:
        with pytest.raises(StreamEndpointInsecure):
            await client.open_connection()
    # Anti-downgrade alert fired; NO connection attempt on the insecure endpoint.
    assert any("non-wss" in str(c.args[0]) for c in mock_logger.error.call_args_list)
    assert connected == []


async def test_open_connection_auth_failure_raises_and_never_logs_secret():
    from unittest.mock import patch

    handler, _ = _open_handler(status=401)
    client = DingTalkStreamClient(
        app_key="k", app_secret=DINGTALK_APP_SECRET, gateway_base="https://gateway.test",
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=lambda url, ssl_context: None,
    )
    with patch("mesh.integrations.dingtalk_stream.logger") as mock_logger:
        with pytest.raises(StreamOpenError):
            await client.open_connection()
    # §6.16: method/url/status only — the clientSecret never reaches logs.
    for call in mock_logger.error.call_args_list:
        rendered = " ".join(str(a) for a in call.args)
        assert DINGTALK_APP_SECRET not in rendered


# ---------------------------------------------------------------------------
# Frame loop behavior (real DB ingestion through the shared core)
# ---------------------------------------------------------------------------


async def _stream_integration(session_factory, world):
    async with session_factory() as session:
        return await session.get(Integration, world["integ_dingtalk"])


def _manager(session_factory, fake_ws, *, settings=None, redis=None):
    async def ws_connect(url, *, ssl_context):
        return fake_ws

    def http_factory():
        handler, _ = _open_handler()
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return StreamManager(
        session_factory,
        settings or _settings(),
        redis=redis,
        http_factory=http_factory,
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(7),
    )


async def test_ping_frame_acks_with_original_headers_and_data(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([_ping_frame(), TimeoutError()])  # heartbeat stop
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
        ws_connect=lambda url, ssl_context: _awaitable(fake_ws),
    )
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    assert fake_ws.sent[0] == {
        "code": 200,
        "headers": {"topic": "ping", "messageId": "ping-1"},
        "message": "OK",
        "data": "session-keepalive-42",  # original data verbatim
    }


async def _awaitable(value):
    return value


async def test_message_frame_ingests_through_shared_core_and_acks(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    frame = _message_frame(dingtalk_message_payload())
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        queue = (await session.execute(select(IntegrationMessageQueue))).scalar_one()
    assert event.signature_status == "valid"  # channel auth ≡ per-frame signature
    assert event.payload["_mesh_channel"] == "stream"
    assert event.process_status == "dispatched"
    assert queue.state == "pending"  # serial default
    assert fake_ws.sent[0]["data"] == "received"
    assert fake_ws.sent[0]["headers"] == frame["headers"]


async def test_redelivered_frame_is_msg_id_deduped_and_still_acked(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    payload = dingtalk_message_payload(msg_id="msgREDELIVER000000==")
    frame = _message_frame(payload)
    fake_ws = FakeWS([frame, frame, TimeoutError()])  # un-ACKed redelivery
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert len(events) == 1  # deduped — one ledger row
    assert len(queues) == 1  # never queued twice
    assert len(fake_ws.sent) == 2  # BOTH frames ACKed (platform stops redelivering)
    assert all(ack["data"] == "received" for ack in fake_ws.sent)


async def test_disconnect_frame_requests_immediate_reconnect(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([_disconnect_frame()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    immediate = await manager._frame_loop(
        client, [integration], heartbeat=90, signal=asyncio.Event()
    )
    assert immediate is True


async def test_heartbeat_timeout_triggers_backoff_reconnect(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([TimeoutError()])  # 90s of silence (faked)
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k", app_secret="s", gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    immediate = await manager._frame_loop(
        client, [integration], heartbeat=90, signal=asyncio.Event()
    )
    assert immediate is False  # reconnect WITH backoff


# ---------------------------------------------------------------------------
# Single-instance mutex + app_key sharing + state persistence
# ---------------------------------------------------------------------------


async def test_advisory_lock_grants_single_instance(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)

    manager_a = _manager(session_factory, FakeWS())
    manager_b = _manager(session_factory, FakeWS())

    _active_a, locked_a = await manager_a._load_locked_integrations()
    _active_b, locked_b = await manager_b._load_locked_integrations()  # A holds it

    assert len(locked_a) == 1
    assert locked_b == []

    # Release A's hold → B can acquire.
    for integration in locked_a:
        hold = integration._lock_session
        from sqlalchemy import text as _text

        await hold.execute(
            _text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": f"dingtalk_stream:{integration.id}"},
        )
        await hold.close()
    _active_b2, locked_b2 = await manager_b._load_locked_integrations()
    assert len(locked_b2) == 1
    for integration in locked_b2:
        await integration._lock_session.close()


async def test_shared_app_key_serves_one_physical_connection(session_factory):
    """Two integrations on ONE app_key → one group → one connections/open."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    # Second integration sharing the SAME app_key (different corp binding set).
    from tests.unit.integrations_support import encrypt

    async with session_factory() as session, session.begin():
        session.add(Integration(
            id=uuid.uuid4(), workspace_id=world["ws"], kind="im_dingtalk",
            name="dingtalk-shared-second",
            config={
                "app_key": "dingappkey0001",
                "corp_id": "dingcorp0001",
                "robot_code": "dingappkey0001",
                "receive_mode": "stream",
                "inbound_queue": "serial_conversation",
                "app_secret_ref": encrypt(DINGTALK_APP_SECRET),
            },
            created_by=world["member"],
        ))

    opens: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        opens.append(request)
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    fake_ws = FakeWS()

    async def ws_connect(url, *, ssl_context):
        return fake_ws

    manager = StreamManager(
        session_factory, _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(3),
    )
    await manager.scan_once()
    await asyncio.sleep(0.2)  # let the group task open its connection

    try:
        assert len(manager._groups) == 1  # ONE group for the shared app_key
        assert len(opens) == 1  # ONE physical connections/open
        async with session_factory() as session:
            rows = (await session.execute(select(Integration))).scalars().all()
        for row in rows:
            assert (row.stream_state or {}).get("state") == STATE_CONNECTED
    finally:
        await manager.shutdown()


async def test_open_failure_marks_stream_state_down(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "invalidAppSecret"})

    signal = asyncio.Event()

    async def ws_connect(url, *, ssl_context):
        raise AssertionError("must not connect on auth failure")

    manager = StreamManager(
        session_factory, _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(5),
    )
    serve = asyncio.create_task(
        manager._serve_group("dingappkey0001", [integration], "https://gateway.test", signal)
    )
    await asyncio.sleep(0.2)
    signal.set()
    await asyncio.wait_for(serve, timeout=5)

    async with session_factory() as session:
        row = await session.get(Integration, world["integ_dingtalk"])
    assert (row.stream_state or {}).get("state") == STATE_DOWN


async def test_non_default_gateway_base_triggers_audit_warning():
    manager = StreamManager(
        None,  # session factory unused: the warning fires before any DB work
        _settings(dingtalk_gateway_base="https://127.0.0.1:9443"),
        sleep=_instant_sleep,
    )
    # scan_once would need a DB for the rest — invoke just the warning path
    # by running scan_once against a stub session factory that yields no rows.
    class _NoRows:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *a, **k):
            class _R:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _R()

    async def _factory():
        return _NoRows()

    manager._session_factory = lambda: _NoRows()
    from unittest.mock import patch

    with patch("mesh.integrations.dingtalk_stream.logger") as mock_logger:
        await manager.scan_once()
        audit_calls = [
            c for c in mock_logger.error.call_args_list
            if "AUDIT" in str(c.args[0]) and "non-default" in str(c.args[0])
        ]
        assert len(audit_calls) == 1
        # Warned exactly once (flag set) — second scan stays silent.
        await manager.scan_once()
        audit_calls_2 = [
            c for c in mock_logger.error.call_args_list
            if "AUDIT" in str(c.args[0]) and "non-default" in str(c.args[0])
        ]
        assert len(audit_calls_2) == 1


# ---------------------------------------------------------------------------
# Frame-ingest edge cases (malformed payloads / routing / rotation)
# ---------------------------------------------------------------------------


async def test_ingest_message_frame_unparseable_payload_is_skipped(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    frame = {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "bad-1"},
        "data": "{not-json",
    }
    await manager._ingest_message_frame([integration], {}, frame)
    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
    assert events == []  # unparseable → audit-log, never crash, never ingest


async def test_ingest_message_frame_routes_by_robot_code(session_factory):
    """Shared connection: frames route to the integration whose robotCode
    matches (two integrations, distinct robot codes)."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)

    from mesh.db.models.integration import Integration as IntegrationRow
    from tests.unit.integrations_support import encrypt as _encrypt

    other_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(IntegrationRow(
            id=other_id, workspace_id=world["ws"], kind="im_dingtalk",
            name="dt-other-robot",
            config={
                "app_key": "dingappkey0001",
                "corp_id": "dingcorp0001",
                "robot_code": "dingOTHERrobot",
                "receive_mode": "stream",
                "inbound_queue": "serial_conversation",
                "app_secret_ref": _encrypt("other-secret"),
            },
            created_by=world["member"],
        ))
    async with session_factory() as session:
        other = await session.get(IntegrationRow, other_id)

    manager = _manager(session_factory, FakeWS())
    frame = _message_frame(dingtalk_message_payload(msg_id="msgROUTE000000000000=="))
    by_robot = {"dingappkey0001": integration, "dingOTHERrobot": other}
    await manager._ingest_message_frame([integration, other], by_robot, frame)

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    # The frame's robotCode (dingappkey0001) selects the first integration.
    assert event.integration_id == world["integ_dingtalk"]


async def test_group_closes_on_secret_rotation(session_factory):
    """Reconciliation: a changed app_secret_ref fingerprint closes the
    running group so it reconnects with the new credential."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)

    manager = _manager(session_factory, FakeWS())
    # Simulate a started group (no real connection task — record bookkeeping).
    signal = asyncio.Event()
    manager._group_signals["dingappkey0001"] = signal
    manager._group_secrets["dingappkey0001"] = manager._secrets_fingerprint([integration])
    manager._served_ids.add(integration.id)
    manager._groups["dingappkey0001"] = asyncio.get_running_loop().create_future()  # placeholder

    # Rotate the ciphertext ref in the DB.
    from mesh.db.models.integration import Integration as IntegrationRow

    async with session_factory() as session, session.begin():
        row = await session.get(IntegrationRow, world["integ_dingtalk"])
        row.config = {**row.config, "app_secret_ref": "rotated-ciphertext"}

    # Make the placeholder awaitable-completable; scan_once pops the group.
    manager._groups["dingappkey0001"].set_result(None)
    await manager.scan_once()
    assert signal.is_set()  # group signalled to stop
    assert "dingappkey0001" not in manager._groups


async def test_group_task_crash_is_reaped_and_rebuilt(session_factory):
    """H1: a group task that dies (escaping exception) is reaped — the next
    scan re-locks and rebuilds it; the app_key is never stranded."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    async def ws_connect(url, *, ssl_context):
        return FakeWS()

    manager = _manager(
        session_factory, FakeWS(),
    )
    manager._http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager._ws_connect = ws_connect

    await manager.scan_once()
    await asyncio.sleep(0.1)
    assert len(manager._groups) == 1

    # Kill the group task with an escaping exception (simulates a bug that
    # defeats the per-cycle isolation).
    group_task = next(iter(manager._groups.values()))
    group_task.cancel()
    await asyncio.sleep(0.1)

    # Reaped: bookkeeping cleared, served ids released.
    assert manager._groups == {}
    assert manager._served_ids == set()

    # Next scan rebuilds the group (re-locks the integration).
    await manager.scan_once()
    await asyncio.sleep(0.1)
    try:
        assert len(manager._groups) == 1
    finally:
        await manager.shutdown()


async def test_pending_depth_counter_api(session_factory):
    """M1: the depth counter is a standalone check the ingest core runs
    UNDER the imq_seq lock."""
    from mesh.integrations.guardrails import InboundGuardrails

    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    conversation_key = f"dingtalk:dingcorp0001:{binding.external_ref}"

    async with session_factory() as session, session.begin():
        for seq in range(1, 51):
            session.add(IntegrationMessageQueue(
                workspace_id=world["ws"],
                integration_id=world["integ_dingtalk"],
                binding_id=binding.id,
                conversation_key=conversation_key,
                seq=seq,
                dispatch_mode="serial_conversation",
                state="pending",
                sender_identity_key="dingtalk:dingcorp0001:someone",
            ))

    guardrails = InboundGuardrails(None, max_pending_per_conversation=50)
    async with session_factory() as session:
        assert await guardrails.check_pending_depth(session, conversation_key) == "rate_limited"
