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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mesh.db.models.audit import AuditLog
from mesh.db.models.integration import Integration, IntegrationEvent, IntegrationMessageQueue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import Approval
from mesh.integrations.dingtalk_stream import (
    STATE_CONNECTED,
    STATE_DOWN,
    STATE_RECONNECTING,
    DingTalkStreamClient,
    StreamEndpointInsecure,
    StreamManager,
    StreamOpenError,
    build_ack,
    compute_backoff,
)
from tests.unit.integrations_support import (
    DINGTALK_APP_SECRET,
    NOW,
    TEST_SIGNING_SECRET,
    dingtalk_message_payload,
    make_dingtalk_binding,
    seed_dingtalk_world,
)
from tests.unit.test_dingtalk_cards import (
    _card_payload,
    _make_approval,
    _map_identity,
)
from tests.unit.test_dingtalk_cards import (
    _seed as seed_dingtalk_card_world,
)

pytestmark = pytest.mark.unit


def _settings(**overrides):
    base = dict(
        jwt_secret=TEST_SIGNING_SECRET,
        app_base_url="https://mesh.example.com",
        dingtalk_gateway_base="https://api.dingtalk.com",
        dingtalk_stream_scan_interval=0.05,
        # The FULL IM layer field set (MES-88/89) the shared core reads —
        # the unit wiring mirrors production Settings so the seam is tested
        # with honest inputs, not a partial stand-in.
        im_ack_coalesce_window_seconds=5.0,
        im_inbound_per_identity_per_min=20,
        im_inbound_per_conversation_per_min=60,
        im_queue_max_pending_per_conversation=50,
        im_inbound_text_max_chars=4000,
        im_dispatch_lease_buffer_seconds=300,
        context_append_max_count=20,
        context_append_max_chars=32000,
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
        if isinstance(item, (str, bytes)):
            return item
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


def _card_frame(payload: dict, message_id: str = "card-frame-1") -> dict:
    return {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {
            "topic": "/v1.0/card/instances/callback",
            "messageId": message_id,
        },
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
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
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
        app_key="k",
        app_secret=DINGTALK_APP_SECRET,
        gateway_base="https://gateway.test",
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
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
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
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
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
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
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


async def test_frame_ingest_survives_partial_settings_stand_in(session_factory):
    """R2 root cause regression: the shared core's settings resolution is
    TOTAL — a settings object missing every IM-layer field (the legacy
    stand-in shape) must never AttributeError mid-ingest. An exception
    there escapes to the per-frame isolation handler and the frame would
    go un-ingested until platform redelivery, so the resolution fills the
    gaps with the config-mirroring defaults instead."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    frame = _message_frame(dingtalk_message_payload())
    fake_ws = FakeWS([frame, TimeoutError()])
    partial = types.SimpleNamespace(
        jwt_secret=TEST_SIGNING_SECRET,
        dingtalk_gateway_base="https://api.dingtalk.com",
        dingtalk_stream_scan_interval=0.05,
    )  # NO im_* / context_append_* fields at all
    manager = _manager(session_factory, fake_ws, settings=partial)
    client = DingTalkStreamClient(
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.process_status == "dispatched"  # ingested, not silently dropped
    assert fake_ws.sent[0]["data"] == "received"  # ACKed


async def test_frame_ingest_failure_is_isolated_but_never_silent(session_factory):
    """R2 observability contract: when ingest raises inside the frame loop,
    the frame is skipped WITHOUT an ACK (platform redelivers → msgId dedup
    makes it idempotent — the at-least-once half) AND the failure leaves a
    durable trace — exact in-memory count + throttled stream_state marker
    (the §3.9 diagnostic truth source) — never a silent drop."""
    from unittest.mock import patch

    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    frame = _message_frame(dingtalk_message_payload())
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    with patch(
        "mesh.integrations.dingtalk_stream.ingest_verified_event",
        side_effect=RuntimeError("boom-ingest-down"),
    ):
        await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    # Redelivery semantics: no ledger row, NO ACK sent.
    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
    assert events == []
    assert fake_ws.sent == []
    # Observability half: exact count + durable stream_state marker.
    assert manager._frame_error_counts["dingappkey0001"] == 1
    async with session_factory() as session:
        row = await session.get(Integration, integration.id)
    state = row.stream_state or {}
    assert state["frame_error_count"] == 1
    assert "boom-ingest-down" in state["last_frame_error"]
    assert state["last_frame_error_at"]


async def test_frame_error_throttle_is_isolated_per_app_group(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    first = await _stream_integration(session_factory, world)
    second_id = uuid.uuid4()
    from tests.unit.integrations_support import encrypt as _encrypt

    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=second_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="second-error-group",
                config={
                    "app_key": "dingappkeySECOND",
                    "corp_id": "dingcorpSECOND",
                    "robot_code": "dingrobotSECOND",
                    "receive_mode": "stream",
                    "app_secret_ref": _encrypt(DINGTALK_APP_SECRET),
                },
                created_by=world["member"],
            )
        )
    async with session_factory() as session:
        second = await session.get(Integration, second_id)
    manager = _manager(session_factory, FakeWS())

    await manager._record_frame_error([first], RuntimeError("first-app-error"))
    await manager._record_frame_error([second], RuntimeError("second-app-error"))

    async with session_factory() as session:
        stored_first = await session.get(Integration, first.id)
        stored_second = await session.get(Integration, second.id)
    assert stored_first.stream_state["frame_error_count"] == 1
    assert "first-app-error" in stored_first.stream_state["last_frame_error"]
    assert stored_second.stream_state["frame_error_count"] == 1
    assert "second-app-error" in stored_second.stream_state["last_frame_error"]


async def test_frame_error_trailing_flush_persists_throttled_tail(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())

    await manager._record_frame_error([integration], RuntimeError("first-error"))
    await manager._record_frame_error([integration], RuntimeError("tail-error"))
    await manager._flush_frame_errors([integration])

    async with session_factory() as session:
        stored = await session.get(Integration, integration.id)
    assert stored.stream_state["frame_error_count"] == 2
    assert "tail-error" in stored.stream_state["last_frame_error"]


async def test_frame_error_count_is_monotonic_across_worker_handoff(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    async with session_factory() as session, session.begin():
        row = await session.get(Integration, world["integ_dingtalk"])
        row.stream_state = {"frame_error_count": 50}

    first_manager = _manager(session_factory, FakeWS())
    first = await _stream_integration(session_factory, world)
    await first_manager._record_frame_error([first], RuntimeError("after-restart"))

    second_manager = _manager(session_factory, FakeWS())
    second = await _stream_integration(session_factory, world)
    await second_manager._record_frame_error([second], RuntimeError("after-handoff"))

    async with session_factory() as session:
        stored = await session.get(Integration, world["integ_dingtalk"])
    assert stored.stream_state["frame_error_count"] == 52
    assert "after-handoff" in stored.stream_state["last_frame_error"]


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_text"),
    [("approve", "approved", "已批准"), ("reject", "rejected", "已拒绝")],
)
async def test_stream_card_callback_decides_and_acks_lifecycle_body(
    session_factory, decision, expected_status, expected_text
):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    frame = _card_frame(_card_payload(approval.id, decision=decision))
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Approval, approval.id)
    assert stored.status == expected_status
    assert len(fake_ws.sent) == 1
    assert expected_text in fake_ws.sent[0]["data"]["cardData"]["cardParamMap"]["status_text"]


async def test_stream_card_callback_forbidden_is_acked_without_decision(session_factory):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world)
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    fake_ws = FakeWS([_card_frame(_card_payload(approval.id)), TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Approval, approval.id)
    assert stored.status == "pending"
    assert "无权限" in fake_ws.sent[0]["data"]["cardData"]["cardParamMap"]["status_text"]


async def test_stream_expired_card_uses_deployment_ui_deep_link(session_factory):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world, status="expired")
    await _map_identity(session_factory, world)
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    fake_ws = FakeWS([_card_frame(_card_payload(approval.id)), TimeoutError()])
    manager = _manager(
        session_factory,
        fake_ws,
        settings=_settings(app_base_url="https://mesh.example.com/root/"),
    )
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    assert fake_ws.sent[0]["data"]["cardData"]["cardParamMap"]["detail_url"] == (
        f"https://mesh.example.com/root/w/intg-{world['ws'].hex[:10]}/approvals?approval_id={approval.id}"
    )


async def test_stream_card_redelivery_is_idempotent_and_each_frame_is_acked(session_factory):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    frame = _card_frame(_card_payload(approval.id))
    fake_ws = FakeWS([frame, frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Approval, approval.id)
    assert stored.status == "approved"
    assert len(fake_ws.sent) == 2
    assert all("已批准" in ack["data"]["cardData"]["cardParamMap"]["status_text"] for ack in fake_ws.sent)


async def test_stream_card_with_wrong_corp_is_not_acked(session_factory):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    payload = _card_payload(approval.id)
    payload["corpId"] = "ding-other-corp"
    fake_ws = FakeWS([_card_frame(payload), TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Approval, approval.id)
    assert stored.status == "pending"
    assert fake_ws.sent == []


@pytest.mark.parametrize(
    ("app_base_url", "expected_reason"),
    [
        ("https://mesh.example.com", "integration_disabled"),
        ("", "app_base_url_missing"),
    ],
)
async def test_stream_card_rechecks_truth_and_commits_denial_before_no_ack(
    session_factory, app_base_url, expected_reason
):
    world = await seed_dingtalk_card_world(session_factory)
    approval = await _make_approval(session_factory, world)
    await _map_identity(session_factory, world)
    async with session_factory() as session:
        stale_integration = await session.get(Integration, world["integ_dingtalk"])
    if expected_reason == "integration_disabled":
        async with session_factory() as session, session.begin():
            authoritative = await session.get(Integration, world["integ_dingtalk"])
            authoritative.status = "disabled"

    payload = _card_payload(approval.id, integration_id=world["integ_dingtalk"])
    fake_ws = FakeWS([_card_frame(payload), TimeoutError()])
    manager = _manager(
        session_factory,
        fake_ws,
        settings=_settings(app_base_url=app_base_url),
    )
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [stale_integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Approval, approval.id)
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "integration.card_callback_denied")
            )
        ).scalar_one()
    assert stored.status == "pending"
    assert audit.metadata_["reason"] == expected_reason
    assert fake_ws.sent == []


async def test_disconnect_frame_requests_immediate_reconnect(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([_disconnect_frame()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    immediate = await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())
    assert immediate is True


async def test_heartbeat_timeout_triggers_backoff_reconnect(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([TimeoutError()])  # 90s of silence (faked)
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(
        app_key="k",
        app_secret="s",
        gateway_base="https://gateway.test",
    )
    client._ws = fake_ws

    immediate = await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())
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
            {"key": "dingtalk_stream_app:dingappkey0001"},
        )
        await hold.close()
    _active_b2, locked_b2 = await manager_b._load_locked_integrations()
    assert len(locked_b2) == 1
    for integration in locked_b2:
        await integration._lock_session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": "dingtalk_stream_app:dingappkey0001"},
        )
        await integration._lock_session.close()


async def test_shared_app_key_lock_is_all_or_nothing_across_managers(session_factory):
    """One app key has one lock owner; workers can never split its rows."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    from tests.unit.integrations_support import encrypt

    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=uuid.uuid4(),
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="dingtalk-shared-lock-second",
                config={
                    "app_key": "dingappkey0001",
                    "corp_id": "dingcorp0001",
                    "robot_code": "dingOTHERrobot",
                    "receive_mode": "stream",
                    "inbound_queue": "serial_conversation",
                    "app_secret_ref": encrypt(DINGTALK_APP_SECRET),
                },
                created_by=world["member"],
            )
        )

    manager_a = _manager(session_factory, FakeWS())
    manager_b = _manager(session_factory, FakeWS())
    active_a, locked_a = await manager_a._load_locked_integrations()
    _active_b, locked_b = await manager_b._load_locked_integrations()

    assert len(active_a) == 2
    assert {item.id for item in locked_a} == {item.id for item in active_a}
    assert locked_b == []
    lock_holders = [item for item in locked_a if getattr(item, "_lock_session", None) is not None]
    assert len(lock_holders) == 1

    hold = lock_holders[0]._lock_session
    await hold.execute(
        text("SELECT pg_advisory_unlock(hashtext(:key))"),
        {"key": "dingtalk_stream_app:dingappkey0001"},
    )
    await hold.close()
    _active_b2, locked_b2 = await manager_b._load_locked_integrations()
    assert len(locked_b2) == 2
    replacement_holders = [item for item in locked_b2 if getattr(item, "_lock_session", None) is not None]
    assert len(replacement_holders) == 1
    await replacement_holders[0]._lock_session.execute(
        text("SELECT pg_advisory_unlock(hashtext(:key))"),
        {"key": "dingtalk_stream_app:dingappkey0001"},
    )
    await replacement_holders[0]._lock_session.close()


async def test_shared_app_key_serves_one_physical_connection(session_factory):
    """Two integrations on ONE app_key → one group → one connections/open."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    # Second integration sharing the SAME app_key (different corp binding set).
    from tests.unit.integrations_support import encrypt

    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=uuid.uuid4(),
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="dingtalk-shared-second",
                config={
                    "app_key": "dingappkey0001",
                    "corp_id": "dingcorpSECOND",
                    "robot_code": "dingrobotSECOND",
                    "receive_mode": "stream",
                    "inbound_queue": "serial_conversation",
                    "app_secret_ref": encrypt(DINGTALK_APP_SECRET),
                },
                created_by=world["member"],
            )
        )

    opens: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        opens.append(request)
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    fake_ws = FakeWS()

    async def ws_connect(url, *, ssl_context):
        return fake_ws

    manager = StreamManager(
        session_factory,
        _settings(),
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


async def test_foreign_workspace_app_key_row_cannot_block_the_owner_socket(session_factory):
    owner = await seed_dingtalk_world(session_factory, receive_mode="stream")
    foreign = await seed_dingtalk_world(
        session_factory,
        receive_mode="stream",
        config_extra={"app_secret_ref": "invalid-foreign-ciphertext"},
    )
    opens: list[httpx.Request] = []
    sockets: list[FakeWS] = []

    def handler(request: httpx.Request) -> httpx.Response:
        opens.append(request)
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    async def ws_connect(*args, **kwargs):
        socket = FakeWS()
        sockets.append(socket)
        return socket

    manager = StreamManager(
        session_factory,
        _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
    )
    await manager.scan_once()
    await asyncio.sleep(0.1)
    try:
        assert len(opens) == 1
        assert len(sockets) == 1
        async with session_factory() as session:
            owner_row = await session.get(Integration, owner["integ_dingtalk"])
            foreign_row = await session.get(Integration, foreign["integ_dingtalk"])
        assert (owner_row.stream_state or {}).get("state") == STATE_CONNECTED
        assert (foreign_row.stream_state or {}).get("state") != STATE_CONNECTED
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
        session_factory,
        _settings(),
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


async def test_connected_state_write_failure_closes_the_open_socket(session_factory):
    """An exception after connections/open must not orphan a live socket."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    socket = FakeWS()

    class OpenClient:
        async def open_connection(self) -> None:
            return None

        async def close(self) -> None:
            await socket.close()

    manager = _manager(session_factory, socket)
    mark_calls = 0

    async def fail_connected(*args, **kwargs) -> None:
        nonlocal mark_calls
        mark_calls += 1
        if mark_calls == 2:
            raise RuntimeError("connected state write failed")

    manager._mark_group = fail_connected

    with pytest.raises(RuntimeError, match="connected state write failed"):
        await manager._serve_once(
            OpenClient(),
            [integration],
            attempt=0,
            base=2,
            maximum=300,
            heartbeat=90,
            signal=asyncio.Event(),
        )
    assert socket.closed


async def test_disconnected_socket_is_marked_reconnecting_before_backoff(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    sleep_started = asyncio.Event()
    release_sleep = asyncio.Event()
    slept: list[float] = []

    async def blocking_sleep(seconds: float) -> None:
        slept.append(seconds)
        sleep_started.set()
        await release_sleep.wait()

    class CycleClient:
        async def open_connection(self) -> None:
            return None

        async def close(self) -> None:
            return None

    manager = StreamManager(session_factory, _settings(), sleep=blocking_sleep)
    states: list[str] = []
    persisted_delays: list[float] = []

    async def record_state(_integrations, state, *args, **kwargs) -> None:
        states.append(state)
        persisted_delays.append(kwargs["backoff_seconds"])

    async def disconnected(*args, **kwargs) -> bool:
        return False

    manager._mark_group = record_state
    manager._frame_loop = disconnected
    cycle = asyncio.create_task(
        manager._serve_once(
            CycleClient(),
            [integration],
            attempt=0,
            base=2,
            maximum=300,
            heartbeat=90,
            signal=asyncio.Event(),
        )
    )
    await asyncio.wait_for(sleep_started.wait(), timeout=1)
    try:
        assert states == [STATE_RECONNECTING, STATE_CONNECTED, STATE_RECONNECTING]
        assert persisted_delays[:2] == [0, 0]
        assert persisted_delays[2] == slept[0]
    finally:
        release_sleep.set()
        await asyncio.wait_for(cycle, timeout=1)


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
            c
            for c in mock_logger.error.call_args_list
            if "AUDIT" in str(c.args[0]) and "non-default" in str(c.args[0])
        ]
        assert len(audit_calls) == 1
        # Warned exactly once (flag set) — second scan stays silent.
        await manager.scan_once()
        audit_calls_2 = [
            c
            for c in mock_logger.error.call_args_list
            if "AUDIT" in str(c.args[0]) and "non-default" in str(c.args[0])
        ]
        assert len(audit_calls_2) == 1


# ---------------------------------------------------------------------------
# Frame-ingest edge cases (malformed payloads / routing / rotation)
# ---------------------------------------------------------------------------


async def test_unparseable_message_frame_is_durably_diagnosed_before_ack(
    session_factory,
):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    frame = {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/im/bot/messages/get", "messageId": "bad-1"},
        "data": "{not-json",
    }
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
        stored = await session.get(Integration, integration.id)
    assert events == []  # no safe integration attribution exists
    assert stored.stream_state["frame_error_count"] == 1
    assert "unparseable message frame" in stored.stream_state["last_frame_error"]
    assert fake_ws.sent[0]["data"] == "received"


@pytest.mark.parametrize("raw_frame", ["{not-json", "[]"])
async def test_malformed_wire_frame_is_diagnosed_unacked_and_reconnects(session_factory, raw_frame):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    fake_ws = FakeWS([raw_frame])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    immediate = await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Integration, integration.id)
    assert immediate is False
    assert stored.stream_state["frame_error_count"] == 1
    assert "malformed stream frame" in stored.stream_state["last_frame_error"]
    assert fake_ws.sent == []


async def test_unknown_callback_topic_is_diagnosed_and_not_acked(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    frame = {
        "specVersion": "1.0",
        "type": "CALLBACK",
        "headers": {"topic": "/v1.0/future/unknown", "messageId": "unknown-1"},
        "data": "{}",
    }
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        stored = await session.get(Integration, integration.id)
    assert stored.stream_state["frame_error_count"] == 1
    assert "unsupported callback topic" in stored.stream_state["last_frame_error"]
    assert fake_ws.sent == []


async def test_routed_malformed_message_is_rejected_in_ledger_then_acked(
    session_factory,
):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    payload = dingtalk_message_payload()
    payload.pop("msgId")  # exact route, but normalization must reject it
    frame = _message_frame(payload, message_id="malformed-routed-1")
    fake_ws = FakeWS([frame, TimeoutError()])
    manager = _manager(session_factory, fake_ws)
    client = DingTalkStreamClient(app_key="k", app_secret="s", gateway_base="https://gateway.test")
    client._ws = fake_ws

    await manager._frame_loop(client, [integration], heartbeat=90, signal=asyncio.Event())

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
        queues = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert event.integration_id == integration.id
    assert event.external_event_id.startswith("rejected:")
    assert event.signature_status == "valid"
    assert event.process_status == "rejected"
    assert event.visibility_scope == "unknown"
    assert event.payload["_mesh_reject_reason"] == "malformed_payload"
    assert event.payload["_mesh_channel"] == "stream"
    assert queues == []
    assert fake_ws.sent[0]["data"] == "received"


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
        session.add(
            IntegrationRow(
                id=other_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
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
            )
        )
    async with session_factory() as session:
        other = await session.get(IntegrationRow, other_id)

    manager = _manager(session_factory, FakeWS())
    frame = _message_frame(dingtalk_message_payload(msg_id="msgROUTE000000000000=="))
    by_identity = {
        ("dingcorp0001", "dingappkey0001"): [integration],
        ("dingcorp0001", "dingOTHERrobot"): [other],
    }
    await manager._ingest_message_frame([integration, other], by_identity, frame)

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    # The frame's robotCode (dingappkey0001) selects the first integration.
    assert event.integration_id == world["integ_dingtalk"]


async def test_ingest_message_frame_routes_by_corp_and_robot_identity(session_factory):
    """A shared app-key socket must not collapse tenants that reuse a robot code."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)

    from mesh.db.models.integration import Integration as IntegrationRow
    from tests.unit.integrations_support import encrypt as _encrypt

    other_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(
            IntegrationRow(
                id=other_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="dt-other-corp",
                config={
                    "app_key": "dingappkey0001",
                    "corp_id": "dingcorpOTHER",
                    "robot_code": "dingappkey0001",
                    "receive_mode": "stream",
                    "inbound_queue": "serial_conversation",
                    "app_secret_ref": _encrypt("other-secret"),
                },
                created_by=world["member"],
            )
        )
    async with session_factory() as session:
        other = await session.get(IntegrationRow, other_id)

    manager = _manager(session_factory, FakeWS())
    frame = _message_frame(
        dingtalk_message_payload(
            msg_id="msgCORPROUTE0000000000==",
            corp_id="dingcorp0001",
            robot_code="dingappkey0001",
        )
    )
    by_identity = {
        ("dingcorp0001", "dingappkey0001"): [integration],
        ("dingcorpOTHER", "dingappkey0001"): [other],
    }
    # Put the target first: the legacy robot-only dict comprehension silently
    # overwrote it with ``other`` and routed the frame across corp identities.
    await manager._ingest_message_frame([integration, other], by_identity, frame)

    async with session_factory() as session:
        event = (await session.execute(select(IntegrationEvent))).scalar_one()
    assert event.integration_id == world["integ_dingtalk"]


async def test_ingest_message_frame_fails_closed_for_ambiguous_identity(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    frame = _message_frame(dingtalk_message_payload(msg_id="msgAMBIG00000000000000=="))

    await manager._ingest_message_frame(
        [integration, integration],
        {("dingcorp0001", "dingappkey0001"): [integration, integration]},
        frame,
    )

    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
    assert events == []


@pytest.mark.parametrize("mutation", ["disabled", "http", "deleted"])
async def test_ingest_message_frame_reloads_route_truth_before_queueing(session_factory, mutation):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    stale_integration = await _stream_integration(session_factory, world)
    async with session_factory() as session, session.begin():
        authoritative = await session.get(Integration, world["integ_dingtalk"])
        if mutation == "disabled":
            authoritative.status = "disabled"
        elif mutation == "http":
            authoritative.config = {**authoritative.config, "receive_mode": "http"}
        else:
            authoritative.deleted_at = NOW

    manager = _manager(session_factory, FakeWS())
    frame = _message_frame(dingtalk_message_payload(msg_id="msgDISABLEDTRUTH00000=="))
    safe_to_ack = await manager._ingest_message_frame(
        [stale_integration],
        {("dingcorp0001", "dingappkey0001"): [stale_integration]},
        frame,
    )

    async with session_factory() as session:
        events = (await session.execute(select(IntegrationEvent))).scalars().all()
        queued = (await session.execute(select(IntegrationMessageQueue))).scalars().all()
    assert safe_to_ack is True
    assert len(events) == 1
    assert events[0].process_status == "rejected"
    assert queued == []


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
    manager._group_fingerprints["dingappkey0001"] = manager._connection_fingerprint([integration])
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


async def test_group_closes_on_routing_identity_rotation(session_factory):
    """Changing corp/robot identity rebuilds the socket's immutable route index."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    signal = asyncio.Event()
    manager._group_signals["dingappkey0001"] = signal
    manager._group_fingerprints["dingappkey0001"] = manager._connection_fingerprint([integration])
    manager._served_ids.add(integration.id)
    manager._groups["dingappkey0001"] = asyncio.get_running_loop().create_future()

    async with session_factory() as session, session.begin():
        row = await session.get(Integration, world["integ_dingtalk"])
        row.config = {
            **row.config,
            "corp_id": "dingcorpROTATED",
            "robot_code": "dingrobotROTATED",
        }

    manager._groups["dingappkey0001"].set_result(None)
    await manager.scan_once()
    assert signal.is_set()
    assert "dingappkey0001" not in manager._groups


async def test_group_closes_on_reconnect_policy_update(session_factory):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    signal = asyncio.Event()
    manager._group_signals["dingappkey0001"] = signal
    manager._group_fingerprints["dingappkey0001"] = manager._connection_fingerprint([integration])
    manager._served_ids.add(integration.id)
    manager._groups["dingappkey0001"] = asyncio.get_running_loop().create_future()

    async with session_factory() as session, session.begin():
        row = await session.get(Integration, world["integ_dingtalk"])
        row.config = {
            **row.config,
            "stream_reconnect": {
                "base_seconds": 4,
                "max_seconds": 120,
                "heartbeat_timeout_seconds": 45,
            },
        }

    manager._groups["dingappkey0001"].set_result(None)
    await manager.scan_once()
    assert signal.is_set()
    assert "dingappkey0001" not in manager._groups


async def test_group_closes_on_explicit_durable_reconnect_request(session_factory):
    """The API marker closes a live socket and the next scan rebuilds it."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)
    from mesh.integrations.service import IntegrationService

    opened_sockets: list[FakeWS] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    async def ws_connect(url, *, ssl_context):
        socket = FakeWS()
        opened_sockets.append(socket)
        return socket

    manager = StreamManager(
        session_factory,
        _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(9),
    )

    async def wait_until(predicate, *, timeout: float = 2.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)

    try:
        await manager.scan_once()
        await wait_until(lambda: len(opened_sockets) == 1)
        first_socket = opened_sockets[0]

        await IntegrationService(session_factory, TEST_SIGNING_SECRET).request_stream_reconnect(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )
        await manager.scan_once()
        await wait_until(lambda: first_socket.closed)

        # The cancelled group releases its advisory lock asynchronously.  A
        # reconciliation retry must acquire it and establish a new socket.
        await wait_until(lambda: not manager._served_ids)
        for _ in range(20):
            await manager.scan_once()
            if len(opened_sockets) == 2:
                break
            await asyncio.sleep(0.02)
        assert len(opened_sockets) == 2
        assert opened_sockets[1] is not first_socket
        assert not opened_sockets[1].closed
    finally:
        await manager.shutdown()


async def test_stale_group_exit_callback_does_not_reap_replacement(session_factory):
    """An old task callback must never clear a newer app-key owner."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    old_task = asyncio.create_task(asyncio.sleep(0))
    await old_task
    replacement = asyncio.create_task(asyncio.Event().wait())
    replacement_integrations = [integration]
    manager._groups["dingappkey0001"] = replacement
    manager._group_signals["dingappkey0001"] = asyncio.Event()
    manager._group_fingerprints["dingappkey0001"] = "replacement"
    manager._group_integrations["dingappkey0001"] = replacement_integrations
    manager._served_ids.add(integration.id)

    manager._on_group_exit("dingappkey0001", old_task, [integration])

    assert manager._groups["dingappkey0001"] is replacement
    assert manager._group_integrations["dingappkey0001"] is replacement_integrations
    assert integration.id in manager._served_ids
    replacement.cancel()
    await asyncio.gather(replacement, return_exceptions=True)


async def test_shutdown_waits_for_group_finally_before_returning():
    manager = StreamManager(None, _settings(), sleep=_instant_sleep)
    cleaned = asyncio.Event()

    async def group() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    task = asyncio.create_task(group())
    await asyncio.sleep(0)
    manager._groups["dingappkey0001"] = task

    await manager.shutdown()

    assert task.done()
    assert cleaned.is_set()


async def test_interruptible_sleep_cancellation_reaps_both_child_waiters():
    started = asyncio.Event()
    cleaned = asyncio.Event()
    child_tasks: list[asyncio.Task] = []

    async def long_sleep(_seconds: float) -> None:
        child_tasks.append(asyncio.current_task())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    manager = StreamManager(None, _settings(), sleep=long_sleep)
    sleeping = asyncio.create_task(manager._interruptible_sleep(300, asyncio.Event()))
    await asyncio.wait_for(started.wait(), timeout=1)
    sleeping.cancel()
    await asyncio.gather(sleeping, return_exceptions=True)

    try:
        assert cleaned.is_set()
        assert child_tasks and all(task.done() for task in child_tasks)
    finally:
        for task in child_tasks:
            task.cancel()
        await asyncio.gather(*child_tasks, return_exceptions=True)


async def test_shutdown_awaits_lock_close_when_group_cancelled_before_first_step():
    close_started = asyncio.Event()
    allow_close = asyncio.Event()
    close_finished = asyncio.Event()

    class BlockingLockSession:
        async def close(self) -> None:
            close_started.set()
            await allow_close.wait()
            close_finished.set()

    manager = StreamManager(None, _settings(), sleep=_instant_sleep)
    integration = types.SimpleNamespace(id=uuid.uuid4(), _lock_session=BlockingLockSession())

    async def never_started() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(never_started())
    task.add_done_callback(lambda exited: manager._on_group_exit("dingappkey0001", exited, [integration]))
    manager._groups["dingappkey0001"] = task
    manager._group_integrations["dingappkey0001"] = [integration]
    manager._served_ids.add(integration.id)
    task.cancel()  # cancellation before the coroutine's first instruction

    shutdown_task = asyncio.create_task(manager.shutdown())
    await asyncio.wait_for(close_started.wait(), timeout=1)
    try:
        assert not shutdown_task.done()
    finally:
        allow_close.set()
        await asyncio.wait_for(shutdown_task, timeout=1)
    assert close_finished.is_set()


async def test_cancel_before_first_step_releases_real_advisory_lock(session_factory, db_url):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    manager_a = _manager(session_factory, FakeWS())
    # A distinct engine guarantees PostgreSQL sees a different physical
    # session; reusing manager_a's pool could reacquire its leaked
    # session-level advisory lock reentrantly and mask the bug.
    engine_b = create_async_engine(db_url, pool_pre_ping=True)
    factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    manager_b = _manager(factory_b, FakeWS())
    releases = 0
    original_release = manager_a._release_lock_session

    async def tracked_release(app_key, lock_session):
        nonlocal releases
        releases += 1
        await original_release(app_key, lock_session)

    manager_a._release_lock_session = tracked_release

    # scan_once returns immediately after creating the group task. Calling
    # shutdown without yielding cancels it before _serve_group can enter its
    # try/finally lock-release path.
    await manager_a.scan_once()
    await manager_a.shutdown()
    assert releases == 1

    try:
        _active, locked_by_b = await manager_b._load_locked_integrations()
        assert [item.id for item in locked_by_b] == [world["integ_dingtalk"]]
        lock_holder = next(item for item in locked_by_b if getattr(item, "_lock_session", None) is not None)
        await lock_holder._lock_session.execute(
            text("SELECT pg_advisory_unlock(hashtext(:key))"),
            {"key": "dingtalk_stream_app:dingappkey0001"},
        )
        await lock_holder._lock_session.close()
    finally:
        await engine_b.dispose()


async def test_cancelled_scan_releases_locks_not_transferred_to_group_tasks(session_factory, db_url):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    first = await _stream_integration(session_factory, world)
    second_id = uuid.uuid4()
    from tests.unit.integrations_support import encrypt as _encrypt

    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=second_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="second-app",
                config={
                    "app_key": "dingappkeySECOND",
                    "corp_id": "dingcorpSECOND",
                    "robot_code": "dingrobotSECOND",
                    "receive_mode": "stream",
                    "inbound_queue": "serial_conversation",
                    "app_secret_ref": _encrypt(DINGTALK_APP_SECRET),
                },
                created_by=world["member"],
            )
        )

    retiring = asyncio.Event()
    release_retiring = asyncio.Event()

    async def old_group() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            retiring.set()
            await release_retiring.wait()

    manager = _manager(session_factory, FakeWS())
    old_task = asyncio.create_task(old_group())
    await asyncio.sleep(0)
    manager._groups["dingappkey0001"] = old_task
    manager._group_signals["dingappkey0001"] = asyncio.Event()
    manager._group_fingerprints["dingappkey0001"] = "stale"
    manager._group_integrations["dingappkey0001"] = [first]
    manager._served_ids.add(first.id)

    scan = asyncio.create_task(manager.scan_once())
    await asyncio.wait_for(retiring.wait(), timeout=2)
    scan.cancel()
    release_retiring.set()
    await asyncio.gather(scan, old_task, return_exceptions=True)

    engine_b = create_async_engine(db_url, pool_pre_ping=True)
    factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    checker = _manager(factory_b, FakeWS())
    try:
        _active, locked = await checker._load_locked_integrations()
        assert {item.id for item in locked} == {first.id, second_id}
        holders = [item for item in locked if getattr(item, "_lock_session", None) is not None]
        for holder in holders:
            app_key = str(holder.config["app_key"])
            await holder._lock_session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"dingtalk_stream_app:{app_key}"},
            )
            await holder._lock_session.close()
    finally:
        await engine_b.dispose()


async def test_second_group_acquire_failure_releases_first_real_lock(session_factory, db_url):
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    first = await _stream_integration(session_factory, world)
    second_id = uuid.uuid4()
    from tests.unit.integrations_support import encrypt as _encrypt

    async with session_factory() as session, session.begin():
        session.add(
            Integration(
                id=second_id,
                workspace_id=world["ws"],
                kind="im_dingtalk",
                name="second-acquire-fails",
                config={
                    "app_key": "dingappkeySECOND",
                    "corp_id": "dingcorpSECOND",
                    "robot_code": "dingrobotSECOND",
                    "receive_mode": "stream",
                    "app_secret_ref": _encrypt(DINGTALK_APP_SECRET),
                },
                created_by=world["member"],
            )
        )

    created = 0

    class FailingHold:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("second app lock query failed")

        async def close(self):
            await self._wrapped.close()

    def flaky_factory():
        nonlocal created
        created += 1
        session = session_factory()
        # 1 = discovery session, 2 = first app lock, 3 = second app lock.
        return FailingHold(session) if created == 3 else session

    manager = _manager(session_factory, FakeWS())
    manager._session_factory = flaky_factory
    with pytest.raises(RuntimeError, match="second app lock query failed"):
        await manager._load_locked_integrations()

    engine_b = create_async_engine(db_url, pool_pre_ping=True)
    factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    checker = _manager(factory_b, FakeWS())
    try:
        _active, locked = await checker._load_locked_integrations()
        assert {item.id for item in locked} == {first.id, second_id}
        groups = {
            str(item.config["app_key"]): [item]
            for item in locked
            if getattr(item, "_lock_session", None) is not None
        }
        await checker._release_group_locks(groups)
    finally:
        await engine_b.dispose()


@pytest.mark.parametrize("worker_write_kind", ["state", "frame_error"])
async def test_stream_state_writer_does_not_erase_concurrent_api_reconnect_marker(
    session_factory, monkeypatch, worker_write_kind
):
    """API and worker JSONB writers serialize on the integration row."""
    from mesh.integrations import service as service_mod
    from mesh.integrations.service import IntegrationService

    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    integration = await _stream_integration(session_factory, world)
    manager = _manager(session_factory, FakeWS())
    api_holds_lock = asyncio.Event()
    release_api = asyncio.Event()
    original_emit = service_mod.emit_realtime

    async def blocking_emit(*args, **kwargs):
        api_holds_lock.set()
        await release_api.wait()
        return await original_emit(*args, **kwargs)

    monkeypatch.setattr(service_mod, "emit_realtime", blocking_emit)
    api_write = asyncio.create_task(
        IntegrationService(session_factory, TEST_SIGNING_SECRET).request_stream_reconnect(
            workspace_id=world["ws"], integration_id=world["integ_dingtalk"]
        )
    )
    await asyncio.wait_for(api_holds_lock.wait(), timeout=2)
    if worker_write_kind == "state":
        worker_write = asyncio.create_task(
            manager._set_stream_state(integration, STATE_CONNECTED, backoff_seconds=0, broadcast=False)
        )
    else:
        worker_write = asyncio.create_task(
            manager._record_frame_error([integration], RuntimeError("frame failed"))
        )
    await asyncio.sleep(0.05)
    release_api.set()
    await asyncio.wait_for(api_write, timeout=2)
    await asyncio.wait_for(worker_write, timeout=2)

    async with session_factory() as session:
        stored = await session.get(Integration, world["integ_dingtalk"])
    assert uuid.UUID(stored.stream_state["reconnect_request_id"])
    if worker_write_kind == "state":
        assert stored.stream_state["state"] == STATE_CONNECTED
    else:
        assert stored.stream_state["state"] == "reconnecting"
        assert stored.stream_state["frame_error_count"] == 1


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
        session_factory,
        FakeWS(),
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


async def test_pending_depth_gate_rejects_over_limit_through_core(session_factory):
    """§2.10: the AUTHORITATIVE pending-depth re-check runs under the
    imq_seq lock inside ``message_queue.enqueue_message`` — a conversation
    already at the cap of 50 rejects the next message (bare 200, rejected
    audit, real msgId keeps the dedupe slot) even with no Redis fast path."""
    from mesh.integrations.dingtalk import normalize_message_payload
    from mesh.integrations.ingest import ingest_verified_event
    from tests.unit.integrations_support import dingtalk_message_payload

    world = await seed_dingtalk_world(session_factory)
    binding = await make_dingtalk_binding(session_factory, world=world)
    conversation_key = f"dingtalk:dingcorp0001:{binding.external_ref}"

    async with session_factory() as session, session.begin():
        for seq in range(1, 51):
            session.add(
                IntegrationMessageQueue(
                    workspace_id=world["ws"],
                    integration_id=world["integ_dingtalk"],
                    binding_id=binding.id,
                    conversation_key=conversation_key,
                    seq=seq,
                    dispatch_mode="serial_conversation",
                    state="pending",
                    sender_identity_key="dingtalk:dingcorp0001:someone",
                )
            )

    envelope = normalize_message_payload(dingtalk_message_payload(), max_chars=4000, channel="stream")
    async with session_factory() as session:
        integration = await session.get(Integration, world["integ_dingtalk"])
    async with session_factory() as session, session.begin():
        result = await ingest_verified_event(session, integration=integration, envelope=envelope, now=NOW)
    assert result.process_status == "rejected"
    assert result.body["reason"] == "rate_limited"
    assert result.status_code == 200
    # The queue stayed at exactly the cap — nothing was enqueued.
    async with session_factory() as session:
        depth = (
            await session.execute(select(func.count()).select_from(IntegrationMessageQueue))
        ).scalar_one()
    assert depth == 50


# ---------------------------------------------------------------------------
# Review-round fixes: M2 (backoff reset) + M3 (transition-only broadcast,
# undecryptable-secret backoff loop)
# ---------------------------------------------------------------------------


async def test_backoff_counter_resets_after_successful_connection(session_factory):
    """M2: a cycle that REACHED CONNECTED is not a consecutive open
    failure — the counter resets, so repeated connect-then-drop cycles
    keep reconnecting at ~base instead of the historical maximum."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    async def ws_connect(url, *, ssl_context):
        return FakeWS()  # recv blocks empty → heartbeat path; close to drop

    manager = StreamManager(
        session_factory,
        _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(11),
    )
    await manager.scan_once()
    await asyncio.sleep(0.2)  # many connect→(drop via close)→reconnect cycles
    try:
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(Integration).where(
                            Integration.id
                            == (await session.execute(select(Integration.id))).scalars().first()
                        )
                    )
                )
                .scalars()
                .first()
            )
        # Backoff stayed at the FIRST rung (~2s ±20%) across many cycles —
        # without the reset it would have grown 2→4→8→…→300.
        assert (row.stream_state or {}).get("backoff_seconds", 0) <= 2.4
    finally:
        await manager.shutdown()


async def test_backoff_grows_on_consecutive_open_failures(session_factory):
    """M2 negative control: when the open itself keeps failing (never
    CONNECTED), the counter grows — backoff visibly exceeds the base."""
    world = await seed_dingtalk_world(session_factory, receive_mode="stream")
    await make_dingtalk_binding(session_factory, world=world)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "invalidAppSecret"})

    manager = StreamManager(
        session_factory,
        _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=lambda url, ssl_context: None,
        sleep=_instant_sleep,
        rng=random.Random(13),
    )
    from mesh.db.models.integration import Integration as IntegrationRow

    # Let the real group loop run against the failing gateway.
    await manager.scan_once()
    # Poll: consecutive open failures ⇒ the persisted backoff grows past
    # base and the group settles DOWN (snapshot-tolerant: a mid-cycle read
    # may transiently show reconnecting).
    deadline = asyncio.get_running_loop().time() + 10
    backoff_seen = 0.0
    state = None
    while asyncio.get_running_loop().time() < deadline:
        async with session_factory() as session:
            row = (await session.execute(select(IntegrationRow))).scalar_one()
        state = (row.stream_state or {}).get("state")
        backoff_seen = max(backoff_seen, (row.stream_state or {}).get("backoff_seconds", 0))
        if backoff_seen >= 3.0 and state == STATE_DOWN:
            break
        await asyncio.sleep(0.05)
    try:
        assert backoff_seen >= 3.0, f"backoff never grew past base (last={backoff_seen})"
        assert state == STATE_DOWN
    finally:
        await manager.shutdown()


async def test_undecryptable_secret_single_down_broadcast_then_recovery(session_factory):
    """M3: an undecryptable app_secret keeps the group ALIVE (DOWN +
    backoff, re-checking each cycle) and broadcasts the DOWN transition
    EXACTLY ONCE (no outbox/realtime flood); fixing the ciphertext (a
    rotation back to a valid secret) reconnects without a rescan."""
    world = await seed_dingtalk_world(
        session_factory,
        receive_mode="stream",
        config_extra={"app_secret_ref": "garbage-not-fernet-ciphertext"},
    )
    await make_dingtalk_binding(session_factory, world=world)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"endpoint": "wss://gw.test/c", "ticket": "t"})

    async def ws_connect(url, *, ssl_context):
        return FakeWS()

    manager = StreamManager(
        session_factory,
        _settings(),
        http_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ws_connect=ws_connect,
        sleep=_instant_sleep,
        rng=random.Random(17),
    )
    await manager.scan_once()

    async def _stream_broadcasts():
        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.event_type == "realtime.publish",
                            OutboxEvent.payload["event"].astext == "integration.updated",
                        )
                    )
                )
                .scalars()
                .all()
            )
        return [r for r in rows if (r.payload.get("data") or {}).get("subject") == "stream_channel"]

    # A fixed sleep made this assertion depend on database load in the full
    # suite. Poll the durable outbox truth instead, with a bounded deadline.
    deadline = asyncio.get_running_loop().time() + 5
    broadcasts = []
    while asyncio.get_running_loop().time() < deadline:
        broadcasts = await _stream_broadcasts()
        if broadcasts:
            break
        await asyncio.sleep(0.05)
    assert len(broadcasts) == 1, f"DOWN broadcast fired {len(broadcasts)} times (flood)"
    async with session_factory() as session:
        row = await session.get(Integration, world["integ_dingtalk"])
    assert (row.stream_state or {}).get("state") == STATE_DOWN

    # Rotate to a VALID ciphertext — the alive group re-decrypts each cycle
    # and reconnects on its own (no rescan needed).
    from tests.unit.integrations_support import encrypt as _encrypt

    async with session_factory() as session, session.begin():
        row = await session.get(Integration, world["integ_dingtalk"])
        row.config = {**row.config, "app_secret_ref": _encrypt(DINGTALK_APP_SECRET)}

    async def _connected():
        async with session_factory() as session:
            row = await session.get(Integration, world["integ_dingtalk"])
        return (row.stream_state or {}).get("state") == STATE_CONNECTED

    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        if await _connected():
            break
        await asyncio.sleep(0.05)
    try:
        assert await _connected(), "group did not recover after secret rotation"
    finally:
        await manager.shutdown()
