"""Member online presence tests (MES-189 B6 — member.md §3.1/§3.5).

Covers the gateway-driven online set (Redis hash with TTL), the
transition-only ``member.presence`` broadcast on the workspace channel, the
workspace-scoped REST snapshot, and the best-effort contract (presence must
never break the session). Real PostgreSQL + Redis throughout.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import select

from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.member.presence import (
    member_presence_snapshot,
    note_member_presence,
)

pytestmark = pytest.mark.unit

PRESENCE_KEY_PREFIX = "mesh:presence:members:"


async def _mk_workspace(session_factory) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        workspace = Workspace(name="Presence WS", slug=f"ws-{uuid.uuid4().hex[:10]}")
        session.add(workspace)
    return workspace.id


async def _mk_member(session_factory, workspace_id: uuid.UUID) -> uuid.UUID:
    async with session_factory() as session, session.begin():
        user = User(
            email=f"pres-{uuid.uuid4().hex[:8]}@x.io", display_name="P"
        )
        session.add(user)
        await session.flush()
        member = Member(
            workspace_id=workspace_id,
            user_id=user.id,
            role="member",
            member_type="human",
        )
        session.add(member)
    return member.id


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
    return [row for row in rows if row.get("event") == "member.presence"]


# ---------------------------------------------------------------------------
# note_member_presence + snapshot
# ---------------------------------------------------------------------------


async def test_join_broadcasts_online_and_snapshot_lists_member(session_factory, redis_client):
    workspace_id = await _mk_workspace(session_factory)
    member_id = await _mk_member(session_factory, workspace_id)

    await note_member_presence(
        session_factory, redis_client,
        workspace_id=workspace_id, member_id=member_id, joined=True,
    )

    frames = await _presence_frames(session_factory)
    assert len(frames) == 1
    assert frames[0]["channel"] == f"workspace:{workspace_id}"
    assert frames[0]["data"] == {"member_id": str(member_id), "presence": "online"}

    snapshot = await member_presence_snapshot(redis_client, workspace_id=workspace_id)
    assert snapshot == [str(member_id)]
    # Online entries carry a TTL so a crashed gateway can't leak stale state.
    assert await redis_client.ttl(f"{PRESENCE_KEY_PREFIX}{workspace_id}") > 0


async def test_extra_connection_is_quiet_and_partial_leave_stays_online(
    session_factory, redis_client
):
    workspace_id = await _mk_workspace(session_factory)
    member_id = await _mk_member(session_factory, workspace_id)

    join = dict(workspace_id=workspace_id, member_id=member_id, joined=True)
    leave = dict(workspace_id=workspace_id, member_id=member_id, joined=False)
    await note_member_presence(session_factory, redis_client, **join)
    await note_member_presence(session_factory, redis_client, **join)  # second tab
    frames = await _presence_frames(session_factory)
    assert len(frames) == 1  # only the 0→1 edge broadcast

    await note_member_presence(session_factory, redis_client, **leave)  # one tab closes
    frames = await _presence_frames(session_factory)
    assert len(frames) == 1  # still online — no transition
    assert await member_presence_snapshot(redis_client, workspace_id=workspace_id) == [
        str(member_id)
    ]

    await note_member_presence(session_factory, redis_client, **leave)  # last tab closes
    frames = await _presence_frames(session_factory)
    assert len(frames) == 2
    assert frames[1]["data"] == {"member_id": str(member_id), "presence": "offline"}
    assert await member_presence_snapshot(redis_client, workspace_id=workspace_id) == []


async def test_presence_is_best_effort_on_redis_failure(session_factory):
    workspace_id = await _mk_workspace(session_factory)
    member_id = await _mk_member(session_factory, workspace_id)

    class BrokenRedis:
        async def hincrby(self, *a, **k):
            raise RuntimeError("redis down")

        async def hkeys(self, *a, **k):
            raise RuntimeError("redis down")

    broken = BrokenRedis()
    # No exception escapes either path.
    await note_member_presence(
        session_factory, broken,
        workspace_id=workspace_id, member_id=member_id, joined=True,
    )
    assert await member_presence_snapshot(broken, workspace_id=workspace_id) == []
    assert await _presence_frames(session_factory) == []


# ---------------------------------------------------------------------------
# gateway session integration
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
        await asyncio.Event().wait()
        yield ("", {})  # pragma: no cover

    async def close(self):
        pass


class _FakeTransport:
    def __init__(self, incoming):
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


async def _run_session(session_factory, redis_client, *, subject, workspace_id, frames):
    from mesh.realtime.auth import Principal
    from mesh.realtime.session import RealtimeSession

    transport = _FakeTransport(frames)
    session = RealtimeSession(
        transport,
        session_factory=session_factory,
        authenticator=_FixedAuthenticator(
            Principal(subject=subject, workspace_ids=frozenset({workspace_id}))
        ),
        authorizer=_OwnerAuthorizer(workspace_id),
        subscriber_factory=_FakeSubscriber,
        replay_page_size=100,
        ping_interval=3600,
        redis=redis_client,
    )
    await session.run()
    return transport


async def test_subscribe_marks_online_and_disconnect_marks_offline(session_factory, redis_client):
    workspace_id = await _mk_workspace(session_factory)
    member_id = await _mk_member(session_factory, workspace_id)
    user_id = await _user_id_of_member(session_factory, member_id, workspace_id)
    channel = f"workspace:{workspace_id}"

    await _run_session(
        session_factory, redis_client,
        subject=str(user_id), workspace_id=workspace_id,
        frames=[{"op": "auth", "token": _TOKEN}, {"op": "subscribe", "channel": channel}],
    )

    frames = await _presence_frames(session_factory)
    assert [f["data"]["presence"] for f in frames] == ["online", "offline"]
    assert all(f["data"]["member_id"] == str(member_id) for f in frames)
    # After disconnect the snapshot drains.
    assert await member_presence_snapshot(redis_client, workspace_id=workspace_id) == []


async def test_last_channel_unsubscribe_clears_presence_not_partial(session_factory, redis_client):
    workspace_id = await _mk_workspace(session_factory)
    member_id = await _mk_member(session_factory, workspace_id)
    user_id = await _user_id_of_member(session_factory, member_id, workspace_id)
    channel_a = f"workspace:{workspace_id}"
    channel_b = f"workspace:{workspace_id}:issues"

    await _run_session(
        session_factory, redis_client,
        subject=str(user_id), workspace_id=workspace_id,
        frames=[
            {"op": "auth", "token": _TOKEN},
            {"op": "subscribe", "channel": channel_a},
            {"op": "subscribe", "channel": channel_b},
            {"op": "unsubscribe", "channel": channel_a},  # still one channel left
            {"op": "unsubscribe", "channel": channel_b},  # last one → offline
        ],
    )

    frames = await _presence_frames(session_factory)
    assert [f["data"]["presence"] for f in frames] == ["online", "offline"]


async def test_dev_principal_without_roster_row_stays_invisible(session_factory, redis_client):
    workspace_id = await _mk_workspace(session_factory)

    await _run_session(
        session_factory, redis_client,
        subject="dev-user",  # not a UUID → no roster resolution
        workspace_id=workspace_id,
        frames=[
            {"op": "auth", "token": _TOKEN},
            {"op": "subscribe", "channel": f"workspace:{workspace_id}"},
        ],
    )

    assert await _presence_frames(session_factory) == []
    assert await member_presence_snapshot(redis_client, workspace_id=workspace_id) == []


async def _user_id_of_member(
    session_factory, member_id: uuid.UUID, workspace_id: uuid.UUID
) -> uuid.UUID:
    from mesh.db.tenant import set_tenant_context

    async with session_factory() as session:
        await set_tenant_context(session, workspace_id)
        return await session.scalar(select(Member.user_id).where(Member.id == member_id))


# ---------------------------------------------------------------------------
# REST snapshot endpoint (real app, real auth chain)
# ---------------------------------------------------------------------------

PASSWORD = "a-strong-passw0rd"


@pytest.fixture
def app(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    settings = load_settings(
        database_url=db_url,
        redis_url=redis_url,
        auth_mode="dev",
        jwt_secret="inprocess-member-presence-test-secret-0000",
    )
    return create_app(settings)


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c
    await app.state.redis.aclose()
    await app.state.engine.dispose()


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    return login.json()["data"]["access_token"]


async def test_presence_endpoint_reflects_gateway_state_with_membership_gate(client, app):
    owner = await _register_and_login(client, "owner-mp@corp.com")
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": "mem-pres"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert resp.status_code == 201, resp.text
    ws = resp.json()["data"]

    roster = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members",
        headers={"Authorization": f"Bearer {owner}"},
    )
    member_id = roster.json()["data"][0]["id"]

    # Empty before any gateway connection.
    empty = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/presence",
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert empty.status_code == 200
    assert empty.json()["data"] == {
        "workspace_id": ws["id"], "online_member_ids": [], "count": 0,
    }

    # Simulate the gateway counting this member online (real Redis write).
    await note_member_presence(
        app.state.session_factory, app.state.redis,
        workspace_id=uuid.UUID(ws["id"]), member_id=uuid.UUID(member_id), joined=True,
    )
    online = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/presence",
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert online.status_code == 200
    assert online.json()["data"] == {
        "workspace_id": ws["id"], "online_member_ids": [member_id], "count": 1,
    }

    # Membership gate: outsider gets the same 404 as an unknown workspace.
    outsider = await _register_and_login(client, "out-mp@corp.com")
    denied = await client.get(
        f"/api/v1/workspaces/{ws['id']}/members/presence",
        headers={"Authorization": f"Bearer {outsider}"},
    )
    assert denied.status_code == 404
    # Unauthenticated → 401.
    anon = await client.get(f"/api/v1/workspaces/{ws['id']}/members/presence")
    assert anon.status_code == 401
