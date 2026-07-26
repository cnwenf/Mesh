"""Gateway connection state machine driven by an in-memory transport."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from mesh.realtime.auth import Principal
from mesh.realtime.session import (
    FRAME_AUTH_OK,
    FRAME_ERROR,
    FRAME_EVENT,
    FRAME_PING,
    FRAME_RESYNC_REQUIRED,
    FRAME_SUBSCRIBED,
    RealtimeSession,
    resync_rest_url,
)

TOKEN = "test-token"
PRINCIPAL = Principal(subject="tester", workspace_ids=frozenset())
ALLOW_WS = uuid.UUID("33333333-3333-3333-3333-333333333333")


class FixedAuthenticator:
    def __init__(self, principal):
        self._principal = principal

    async def authenticate(self, token):
        return self._principal if token == TOKEN else None


class AllowAuthorizer:
    async def authorize(self, principal, channel):
        return ALLOW_WS


class DenyAuthorizer:
    async def authorize(self, principal, channel):
        return None


class FakeSubscriber:
    async def start(self):
        pass

    async def frames(self):
        await asyncio.Event().wait()  # block until cancelled
        yield ("", {})  # pragma: no cover — unreachable, makes this an async generator

    async def close(self):
        pass


class FakeTransport:
    def __init__(self, incoming):
        self._queue = asyncio.Queue()
        for frame in incoming:
            self._queue.put_nowait(frame)
        self.sent: list[dict] = []
        self.closed = False

    async def receive_json(self):
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            raise ConnectionError("client gone") from None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        self.closed = True


def _session(transport, session_factory, authorizer=None):
    return RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=FixedAuthenticator(PRINCIPAL),
        authorizer=authorizer or AllowAuthorizer(),
        subscriber_factory=FakeSubscriber,
        replay_page_size=100,
        ping_interval=3600,
    )


async def _seed_events(session_factory, workspace_id, channel, count, start_seq=1):
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO realtime_channels (channel, workspace_id, last_seq) "
                "VALUES (:ch, :ws, :seq) ON CONFLICT (channel) "
                "DO UPDATE SET last_seq = EXCLUDED.last_seq"
            ),
            {"ch": channel, "ws": workspace_id, "seq": count + start_seq - 1},
        )
        for seq in range(start_seq, start_seq + count):
            await session.execute(
                text(
                    "INSERT INTO realtime_events "
                    "(workspace_id, channel, seq, event, payload, outbox_event_id) "
                    "VALUES (:ws, :ch, :seq, 'issue.updated', :payload, gen_random_uuid())"
                ),
                {"ws": workspace_id, "ch": channel, "seq": seq, "payload": f'{{"i": {seq}}}'},
            )


def _ops(transport):
    return [frame.get("op") for frame in transport.sent]


async def test_first_frame_must_be_auth(session_factory):
    transport = FakeTransport([{"op": "subscribe", "channel": "issue:x"}])
    await _session(transport, session_factory).run()
    assert _ops(transport) == [FRAME_ERROR]
    assert transport.sent[0]["code"] == "unauthorized"
    assert transport.closed


async def test_bad_token_rejected(session_factory):
    transport = FakeTransport([{"op": "auth", "token": "wrong"}])
    await _session(transport, session_factory).run()
    assert transport.sent[0] == {
        "op": FRAME_ERROR,
        "code": "unauthorized",
        "message": "invalid or expired token",
    }
    assert transport.closed


async def test_undecodable_first_frame_is_auth_failed_not_timeout(session_factory):
    class GarbageTransport(FakeTransport):
        async def receive_json(self):
            raise ValueError("invalid json")

    transport = GarbageTransport([])
    await _session(transport, session_factory).run()
    assert transport.sent[0]["op"] == FRAME_ERROR
    assert transport.sent[0]["code"] == "unauthorized"
    assert transport.sent[0]["message"] == "authentication failed"
    assert transport.closed


async def test_auth_ok_then_subscribe_replays_from_resume(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_events(session_factory, workspace.id, "issue:play", 3)
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:play", "resume_from": 2},
        ]
    )
    await _session(transport, session_factory).run()
    ops = _ops(transport)
    assert ops[0] == FRAME_AUTH_OK
    events = [f for f in transport.sent if f["op"] == FRAME_EVENT]
    assert [f["seq"] for f in events] == [2, 3]
    assert transport.sent[-1] == {
        "op": FRAME_SUBSCRIBED,
        "channel": "issue:play",
        "last_seq": 3,
    }


async def test_replay_pages_through_backlog_larger_than_page_size(
    session_factory, workspace_factory
):
    workspace = await workspace_factory()
    await _seed_events(session_factory, workspace.id, "issue:big", 5)
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:big", "resume_from": 1},
        ]
    )
    session = RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=FixedAuthenticator(PRINCIPAL),
        authorizer=AllowAuthorizer(),
        subscriber_factory=FakeSubscriber,
        replay_page_size=2,  # force multiple replay pages
    )
    await session.run()
    events = [f for f in transport.sent if f["op"] == FRAME_EVENT]
    assert [f["seq"] for f in events] == [1, 2, 3, 4, 5]  # no silent drops
    subscribed = [f for f in transport.sent if f["op"] == FRAME_SUBSCRIBED]
    assert subscribed[-1]["last_seq"] == 5


async def test_subscribe_without_resume_replays_everything(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_events(session_factory, workspace.id, "issue:all", 2)
    transport = FakeTransport(
        [{"op": "auth", "token": TOKEN}, {"op": "subscribe", "channel": "issue:all"}]
    )
    await _session(transport, session_factory).run()
    events = [f for f in transport.sent if f["op"] == FRAME_EVENT]
    assert [f["seq"] for f in events] == [1, 2]
    assert events[0]["event"] == "issue.updated"
    assert events[0]["payload"] == {"i": 1}


async def test_stale_cursor_sends_resync_required(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_events(session_factory, workspace.id, "issue:stale", 2)
    # Retention purge removed seq 1: only seq 2 remains but resume_from=1.
    async with session_factory() as session, session.begin():
        await session.execute(text("DELETE FROM realtime_events WHERE seq = 1 AND channel = 'issue:stale'"))
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:stale", "resume_from": 1},
        ]
    )
    await _session(transport, session_factory).run()
    resync = [f for f in transport.sent if f["op"] == FRAME_RESYNC_REQUIRED]
    assert len(resync) == 1
    assert resync[0]["channel"] == "issue:stale"
    assert resync[0]["watermark"] == 2
    assert resync[0]["rest"] == resync_rest_url("issue:stale", 1)
    # No subscribed frame after resync_required.
    assert FRAME_SUBSCRIBED not in _ops(transport)


async def test_fully_purged_channel_with_ahead_cursor_is_stale(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO realtime_channels (channel, workspace_id, last_seq) "
                "VALUES ('issue:purged', :ws, 5)"
            ),
            {"ws": workspace.id},
        )
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:purged", "resume_from": 3},
        ]
    )
    await _session(transport, session_factory).run()
    assert FRAME_RESYNC_REQUIRED in _ops(transport)


async def test_up_to_date_cursor_on_purged_channel_subscribes(session_factory, workspace_factory):
    workspace = await workspace_factory()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO realtime_channels (channel, workspace_id, last_seq) "
                "VALUES ('issue:caught-up', :ws, 5)"
            ),
            {"ws": workspace.id},
        )
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:caught-up", "resume_from": 6},
        ]
    )
    await _session(transport, session_factory).run()
    ops = _ops(transport)
    assert FRAME_RESYNC_REQUIRED not in ops
    assert FRAME_SUBSCRIBED in ops


async def test_forbidden_channel_keeps_connection_alive(session_factory, workspace_factory):
    workspace = await workspace_factory()
    await _seed_events(session_factory, workspace.id, "issue:secret", 1)
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:secret"},
            {"op": "ping"},
        ]
    )
    await _session(transport, session_factory, authorizer=DenyAuthorizer()).run()
    errors = [f for f in transport.sent if f["op"] == FRAME_ERROR]
    assert errors[0]["code"] == "forbidden"
    assert not transport.closed  # connection survives a denied subscription
    assert _ops(transport)[-1] == FRAME_PING  # ping still answered


async def test_unsubscribe_and_unknown_op(session_factory):
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "unsubscribe", "channel": "issue:x"},
            {"op": "bogus"},
        ]
    )
    await _session(transport, session_factory).run()
    errors = [f for f in transport.sent if f["op"] == FRAME_ERROR]
    assert errors[0]["code"] == "validation_error"


async def test_invalid_subscribe_payloads(session_factory):
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe"},  # missing channel
            {"op": "subscribe", "channel": "issue:x", "resume_from": "3"},  # bad type
        ]
    )
    await _session(transport, session_factory).run()
    errors = [f for f in transport.sent if f["op"] == FRAME_ERROR]
    assert len(errors) == 2
    assert all(e["code"] == "validation_error" for e in errors)


async def test_subscribe_resume_from_bool_rejected_without_disconnect(session_factory):
    # JSON `true` decodes to a Python bool — an int subclass. It must be
    # rejected as validation_error, never reach the replay SQL (which would
    # abort the connection), and leave the connection usable.
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:x", "resume_from": True},
            {"op": "subscribe", "channel": "issue:x", "resume_from": False},
            {"op": "ping"},
        ]
    )
    await _session(transport, session_factory).run()
    errors = [f for f in transport.sent if f["op"] == FRAME_ERROR]
    assert len(errors) == 2
    assert all(e["code"] == "validation_error" for e in errors)
    assert not transport.closed
    assert _ops(transport)[-1] == FRAME_PING  # connection still answers


async def test_subscribe_negative_resume_from_rejected(session_factory):
    transport = FakeTransport(
        [
            {"op": "auth", "token": TOKEN},
            {"op": "subscribe", "channel": "issue:x", "resume_from": -1},
            {"op": "ping"},
        ]
    )
    await _session(transport, session_factory).run()
    errors = [f for f in transport.sent if f["op"] == FRAME_ERROR]
    assert len(errors) == 1
    assert errors[0]["code"] == "validation_error"
    assert not transport.closed
    assert _ops(transport)[-1] == FRAME_PING


class BlockingTransport(FakeTransport):
    """Serves queued frames, then blocks forever (silent client)."""

    async def receive_json(self):
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            await asyncio.Event().wait()
            return {}  # pragma: no cover


async def test_auth_timeout_closes_connection(session_factory):
    transport = BlockingTransport([])
    session = RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=FixedAuthenticator(PRINCIPAL),
        authorizer=AllowAuthorizer(),
        subscriber_factory=FakeSubscriber,
        auth_timeout=0.05,
    )
    await session.run()
    assert transport.sent[0]["op"] == FRAME_ERROR
    assert transport.sent[0]["code"] == "unauthorized"
    assert transport.closed


async def test_heartbeat_sends_ping_frames(session_factory):
    transport = BlockingTransport([{"op": "auth", "token": TOKEN}])
    session = RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=FixedAuthenticator(PRINCIPAL),
        authorizer=AllowAuthorizer(),
        subscriber_factory=FakeSubscriber,
        ping_interval=0.05,
    )
    task = asyncio.create_task(session.run())
    deadline = asyncio.get_event_loop().time() + 5
    while not any(frame.get("op") == FRAME_PING for frame in transport.sent):
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("no heartbeat ping received")
        await asyncio.sleep(0.02)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_resync_rest_url_encoding():
    url = resync_rest_url("workspace:ws-1:issues", 12)
    assert url == "/api/v1/realtime/events?channel=workspace%3Aws-1%3Aissues&since=12"
