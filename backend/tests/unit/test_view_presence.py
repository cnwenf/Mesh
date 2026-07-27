"""view.presence tests — online indicator broadcast (kanban.md §3.5/§6.7).

``note_presence`` updates a Redis present-subjects set per view channel and
broadcasts ``view.presence`` through the outbox single write path. Presence is
best-effort: any failure is swallowed so it can never break the WS session.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.workspace import Workspace
from mesh.views.presence import note_presence

pytestmark = pytest.mark.unit


async def _mk_workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Presence WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    return workspace.id


async def _presence_frames(session_factory) -> list[dict]:
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(OutboxEvent.payload).where(
                        OutboxEvent.event_type == "realtime.publish"
                    )
                )
            )
            .scalars()
            .all()
        )
    return [row for row in rows if row.get("event") == "view.presence"]


async def test_note_presence_join_broadcasts_count(session_factory, redis_client) -> None:
    workspace_id = await _mk_workspace(session_factory)
    channel = f"view:{uuid.uuid4()}"

    await note_presence(
        session_factory, redis_client, workspace_id=workspace_id, channel=channel,
        subject="user-a", joined=True,
    )
    frames = await _presence_frames(session_factory)
    assert len(frames) == 1
    data = frames[0]["data"]
    assert data["online"] == 1
    assert data["subject"] == "user-a"
    assert data["joined"] is True
    assert frames[0]["channel"] == channel


async def test_note_presence_tracks_multiple_subjects(session_factory, redis_client) -> None:
    workspace_id = await _mk_workspace(session_factory)
    channel = f"view:{uuid.uuid4()}"

    await note_presence(
        session_factory, redis_client, workspace_id=workspace_id, channel=channel,
        subject="user-a", joined=True,
    )
    await note_presence(
        session_factory, redis_client, workspace_id=workspace_id, channel=channel,
        subject="user-b", joined=True,
    )
    frames = await _presence_frames(session_factory)
    assert frames[-1]["data"]["online"] == 2
    assert frames[-1]["data"]["members"] == ["user-a", "user-b"]

    # user-a leaves → back to one online.
    await note_presence(
        session_factory, redis_client, workspace_id=workspace_id, channel=channel,
        subject="user-a", joined=False,
    )
    frames = await _presence_frames(session_factory)
    assert frames[-1]["data"]["online"] == 1
    assert frames[-1]["data"]["members"] == ["user-b"]
    assert frames[-1]["data"]["joined"] is False


async def test_note_presence_is_best_effort(session_factory, redis_client) -> None:
    # A broken redis must not raise (presence never breaks the session).
    workspace_id = await _mk_workspace(session_factory)
    channel = f"view:{uuid.uuid4()}"

    class BrokenRedis:
        async def sadd(self, *a, **k):
            raise RuntimeError("redis down")

    await note_presence(
        session_factory, BrokenRedis(), workspace_id=workspace_id, channel=channel,
        subject="user-a", joined=True,
    )
    # No frame emitted, and no exception escaped.
    assert await _presence_frames(session_factory) == []


# ---------------------------------------------------------------------------
# gateway integration: subscribe/unsubscribe to a view channel broadcasts it
# ---------------------------------------------------------------------------

_TOKEN = "test-token"


class _FixedAuthenticator:
    def __init__(self, principal):
        self._principal = principal

    async def authenticate(self, token):
        return self._principal if token == _TOKEN else None


class _OwnerAuthorizer:
    def __init__(self, owner):
        self._owner = owner

    async def authorize(self, principal, channel):
        return self._owner


class _FakeSubscriber:
    async def start(self):
        pass

    async def frames(self):
        import asyncio

        await asyncio.Event().wait()
        yield ("", {})  # pragma: no cover

    async def close(self):
        pass


class _FakeTransport:
    def __init__(self, incoming):
        import asyncio

        self._queue: asyncio.Queue = asyncio.Queue()
        for frame in incoming:
            self._queue.put_nowait(frame)
        self.sent: list[dict] = []

    async def receive_json(self):
        try:
            return self._queue.get_nowait()
        except Exception:
            raise ConnectionError("client gone") from None

    async def send_json(self, data):
        self.sent.append(data)

    async def close(self):
        pass


async def test_gateway_subscribe_announces_view_presence(session_factory, redis_client) -> None:
    from mesh.realtime.auth import Principal
    from mesh.realtime.session import RealtimeSession

    workspace_id = await _mk_workspace(session_factory)
    view_id = uuid.uuid4()
    channel = f"view:{view_id}"
    transport = _FakeTransport(
        [{"op": "auth", "token": _TOKEN}, {"op": "subscribe", "channel": channel}]
    )
    session = RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=_FixedAuthenticator(Principal(subject="tester", workspace_ids=frozenset())),
        authorizer=_OwnerAuthorizer(workspace_id),
        subscriber_factory=_FakeSubscriber,
        replay_page_size=100,
        ping_interval=3600,
        redis=redis_client,
    )
    await session.run()

    frames = await _presence_frames(session_factory)
    # Joined on subscribe, left when the connection drained/closed.
    joined = [f for f in frames if f["data"]["joined"] is True]
    left = [f for f in frames if f["data"]["joined"] is False]
    assert joined and joined[0]["channel"] == channel
    assert joined[0]["data"]["subject"] == "tester"
    assert left  # departure announced on disconnect
