"""Real end-to-end tests for the comment & inbox module.

Real uvicorn API subprocess (as the RLS-restricted ``mesh_app`` role) +
real PostgreSQL + real Redis + real outbox relay/projector + real WebSocket
gateway. Covers the acceptance matrices the unit tier cannot: §6.9 trigger
rows through the outbox, §6.13 fan-out through the relay into the inbox,
realtime projection onto the member inbox channel, cross-tenant isolation
(T1), and the RESTRICT delete behavior (T18).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
import websockets
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mesh.comment_inbox.mentions import EXECUTION_ENQUEUE_EVENT, enqueue_idempotency_key
from mesh.config import load_settings
from mesh.db.engine import create_engine_from_settings, create_session_factory
from mesh.db.models.comment import CommentMention
from mesh.db.models.member import Member
from mesh.db.models.notification import Notification, NotificationDelivery
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.realtime import RealtimeEvent
from mesh.db.models.user import User
from mesh.workers.main import build_relay

pytestmark = pytest.mark.e2e

PASSWORD = "a-strong-passw0rd"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return login.json()["data"]["access_token"]


async def _create_workspace(client, token, slug: str) -> dict:
    resp = await client.post(
        "/api/v1/workspaces", json={"name": "Team", "slug": slug}, headers=_auth(token)
    )
    return resp.json()["data"]


async def _create_issue(client, token, ws_id, title="Login broken") -> dict:
    resp = await client.post(
        f"/api/v1/workspaces/{ws_id}/issues", json={"title": title}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


async def _invite_accept(client, owner_token, ws_id, email, role="member") -> str:
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": role},
        headers=_auth(owner_token),
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    await client.post("/api/v1/invitations/accept", json={"token": token}, headers=_auth(joiner))
    return joiner


async def _member_id_by_email(session_factory, email: str) -> uuid.UUID:
    async with session_factory() as session:
        row = await session.execute(
            select(Member.id).join(User, Member.user_id == User.id).where(User.email == email)
        )
        return row.scalar()


async def _insert_agent_member(
    session_factory, workspace_id: uuid.UUID, name: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """(member_id, agent_id) — the §6.5 key uses agent_id when present."""
    async with session_factory() as session, session.begin():
        member = Member(
            workspace_id=workspace_id,
            member_type="agent",
            agent_id=uuid.uuid4(),
            role="member",
            display_override=name,
        )
        session.add(member)
        await session.flush()
        return member.id, member.agent_id


@pytest_asyncio.fixture
async def owner_factory(db_url):
    engine = create_engine_from_settings(
        load_settings(database_url=db_url, redis_url="redis://127.0.0.1:1/0")
    )
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def relay(db_url, redis_url):
    """A real relay (all production handlers) over the test services."""
    settings = load_settings(
        database_url=db_url, redis_url=redis_url,
        auth_mode="dev", jwt_secret="e2e-comment-inbox-relay-secret-00000",
    )
    engine = create_engine_from_settings(settings)
    factory = create_session_factory(engine)
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    from mesh.auth.mailer import build_mailer
    from mesh.realtime.pubsub import RedisFanOut

    relay_instance = build_relay(
        settings, factory, RedisFanOut(redis_client), mailer=build_mailer(settings, redis_client)
    )
    yield relay_instance
    await redis_client.aclose()
    await engine.dispose()


async def _drain(relay, cycles: int = 6) -> None:
    """Run the relay until the outbox is quiet (realtime rows enqueue more)."""
    for _ in range(cycles):
        processed = await relay.run_once()
        if processed == 0:
            break
        await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# environment fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def env(api_client, owner_factory):
    alice_token = await _register_and_login(api_client, "alice@mesh.example")
    workspace = await _create_workspace(api_client, alice_token, f"ws-{uuid.uuid4().hex[:10]}")
    issue = await _create_issue(api_client, alice_token, workspace["id"])
    bob_token = await _invite_accept(
        api_client, alice_token, workspace["id"], "bob@mesh.example"
    )
    return {
        "client": api_client,
        "alice_token": alice_token,
        "bob_token": bob_token,
        "workspace_id": uuid.UUID(workspace["id"]),
        "issue": issue,
        "alice_member": await _member_id_by_email(owner_factory, "alice@mesh.example"),
        "bob_member": await _member_id_by_email(owner_factory, "bob@mesh.example"),
    }


# ---------------------------------------------------------------------------
# 1. comment lifecycle + realtime projection
# ---------------------------------------------------------------------------


async def test_comment_lifecycle_realtime_projection(env, relay, owner_factory):
    client, token, issue = env["client"], env["alice_token"], env["issue"]
    created = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": "**real** e2e comment"}, headers=_auth(token),
        )
    ).json()["data"]
    reply = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": "a reply", "parent_id": created["id"]},
            headers=_auth(token),
        )
    ).json()["data"]
    await client.post(
        f"/api/v1/comments/{created['id']}/reactions",
        json={"emoji": "🎉"}, headers=_auth(token),
    )
    await client.post(f"/api/v1/comments/{created['id']}/resolve", headers=_auth(token))

    await _drain(relay)

    async with owner_factory() as session:
        events = (
            await session.execute(
                select(RealtimeEvent)
                .where(RealtimeEvent.channel == f"issue:{issue['id']}")
                .order_by(RealtimeEvent.seq)
            )
        ).scalars().all()
    names = [event.event for event in events]
    assert "comment.created" in names
    assert "reaction.changed" in names
    assert "comment.resolved" in names
    # channel seq is monotonic (README §6.7)
    seqs = [event.seq for event in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # the comment.created payload carries the full object
    created_frame = next(e for e in events if e.event == "comment.created")
    assert created_frame.payload["id"] in (created["id"], reply["id"])


# ---------------------------------------------------------------------------
# 2. §6.9 trigger matrix through the real outbox
# ---------------------------------------------------------------------------


async def test_mention_trigger_matrix_e2e(env, relay, owner_factory):
    client, token, issue, ws_id = (
        env["client"], env["alice_token"], env["issue"], env["workspace_id"],
    )
    agent_a, agent_a_key = await _insert_agent_member(owner_factory, ws_id, "reviewer")
    agent_b, _agent_b_key = await _insert_agent_member(owner_factory, ws_id, "test-runner")

    async def enqueues():
        async with owner_factory() as session:
            rows = (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == EXECUTION_ENQUEUE_EVENT
                    )
                )
            ).scalars().all()
        return list(rows)

    # publish @A → exactly one enqueue with the §6.5 key
    first = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": f"[@reviewer](mention://member/{agent_a}) run"},
            headers=_auth(token),
        )
    ).json()["data"]
    assert len(first["triggered_execution_ids"]) == 1
    rows = await enqueues()
    assert len(rows) == 1
    assert rows[0].payload["trigger"] == "mention"
    expected_key_prefix = "ws:"  # workspace-scoped key
    assert rows[0].idempotency_key.startswith(expected_key_prefix)
    # key = sha256(agent_id|issue_id|trigger_event_id=comment_id)
    expected = enqueue_idempotency_key(
        agent_key=agent_a_key, issue_id=uuid.UUID(issue["id"]),
        trigger_event_id=uuid.UUID(first["id"]),
    )
    assert rows[0].idempotency_key == f"ws:{ws_id}:{expected}"

    # editing unrelated text → no new enqueue
    await client.patch(
        f"/api/v1/comments/{first['id']}",
        json={"body_markdown": f"[@reviewer](mention://member/{agent_a}) run (typo)"},
        headers=_auth(token),
    )
    assert len(await enqueues()) == 1

    # editing to ADD @B → only B enqueues
    await client.patch(
        f"/api/v1/comments/{first['id']}",
        json={
            "body_markdown":
            f"[@reviewer](mention://member/{agent_a}) + "
            f"[@test-runner](mention://member/{agent_b})"
        },
        headers=_auth(token),
    )
    rows = await enqueues()
    assert len(rows) == 2
    assert {r.payload["agent_member_id"] for r in rows} == {str(agent_a), str(agent_b)}

    # editing to REMOVE @A → mention soft-deleted, enqueue row stands (§6.9)
    await client.patch(
        f"/api/v1/comments/{first['id']}",
        json={"body_markdown": f"[@test-runner](mention://member/{agent_b})"},
        headers=_auth(token),
    )
    assert len(await enqueues()) == 2
    async with owner_factory() as session:
        mention_a = await session.scalar(
            select(CommentMention).where(
                CommentMention.comment_id == uuid.UUID(first["id"]),
                CommentMention.mentioned_id == agent_a,
            )
        )
    assert mention_a.deleted_at is not None

    # suppress_triggers → notify only, no enqueue
    suppressed = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={
                "body_markdown": f"[@reviewer](mention://member/{agent_a}) quiet",
                "suppress_triggers": True,
            },
            headers=_auth(token),
        )
    ).json()["data"]
    assert suppressed["triggered_execution_ids"] == []
    assert len(await enqueues()) == 2

    # NEW comment @same agent → a NEW execution (§6.9 per-comment trigger)
    second = (
        await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": f"[@reviewer](mention://member/{agent_a}) again"},
            headers=_auth(token),
        )
    ).json()["data"]
    assert len(second["triggered_execution_ids"]) == 1
    assert second["triggered_execution_ids"] != first["triggered_execution_ids"]
    assert len(await enqueues()) == 3

    # the relay consumes execution.enqueue (bridge handler) without failure
    await _drain(relay)
    async with owner_factory() as session:
        pending = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == EXECUTION_ENQUEUE_EVENT,
                    OutboxEvent.status == "pending",
                )
            )
        ).scalars().all()
    assert pending == []


# ---------------------------------------------------------------------------
# 3. §6.13 fan-out through the relay into the inbox
# ---------------------------------------------------------------------------


async def test_notification_fanout_and_delivery_ledger_e2e(env, relay, owner_factory):
    client, bob_token, issue = env["client"], env["bob_token"], env["issue"]
    # bob comments → alice (reporter) must get a normal aggregated notification
    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "I fixed the login redirect"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 201
    await _drain(relay)

    async with owner_factory() as session:
        notifications = (
            await session.execute(
                select(Notification).where(Notification.recipient_id == env["alice_member"])
            )
        ).scalars().all()
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.type == "comment_created"
    assert notification.priority == "normal"  # §6.13 matrix
    assert notification.group_key == f"issue:{issue['id']}:comment_created"
    assert notification.payload["actor_name"] == "bob"
    assert notification.payload["preview"] == "I fixed the login redirect"

    # delivery ledger: in_app sent (destination_key='') + digest email pending
    async with owner_factory() as session:
        deliveries = (
            await session.execute(
                select(NotificationDelivery).where(
                    NotificationDelivery.notification_id == notification.id
                )
            )
        ).scalars().all()
    by_channel = {row.channel: row for row in deliveries}
    assert by_channel["in_app"].state == "sent"
    assert by_channel["in_app"].destination_key == ""
    assert by_channel["email"].state == "pending"  # digest deferred
    assert by_channel["email"].error is None  # error = failure reason ONLY (R3)

    # realtime projected onto alice's inbox channel
    async with owner_factory() as session:
        events = (
            await session.execute(
                select(RealtimeEvent).where(
                    RealtimeEvent.channel == f"member:{env['alice_member']}:inbox"
                )
            )
        ).scalars().all()
    names = {event.event for event in events}
    assert "notification.created" in names
    assert "inbox.unread_count" in names
    unread_frame = next(e for e in events if e.event == "inbox.unread_count")
    assert unread_frame.payload["count"] == 1

    # bob (the actor) gets NOTHING — self-suppression (§6.13)
    async with owner_factory() as session:
        bob_rows = (
            await session.execute(
                select(Notification).where(Notification.recipient_id == env["bob_member"])
            )
        ).scalars().all()
    assert bob_rows == []

    # the HTTP inbox endpoint sees it under RLS (mesh_app role)
    listing = await client.get(
        "/api/v1/inbox",
        params={"workspace_id": str(env["workspace_id"])},
        headers=_auth(env["alice_token"]),
    )
    assert listing.status_code == 200
    assert listing.json()["data"][0]["id"] == str(notification.id)
    unread = await client.get(
        "/api/v1/inbox/unread-count",
        params={"workspace_id": str(env["workspace_id"])},
        headers=_auth(env["alice_token"]),
    )
    assert unread.json()["data"]["count"] == 1


async def test_mention_notification_is_critical_e2e(env, relay, owner_factory):
    client, bob_token, issue = env["client"], env["bob_token"], env["issue"]
    await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "@alice 需要你确认"},
        headers=_auth(bob_token),
    )
    await _drain(relay)
    async with owner_factory() as session:
        rows = (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == env["alice_member"],
                    Notification.type == "mentioned",
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].priority == "critical"  # 被 @ → critical (§6.13)


# ---------------------------------------------------------------------------
# 4. WebSocket delivery on the inbox channel
# ---------------------------------------------------------------------------


async def test_inbox_ws_delivery_e2e(env, relay, gateway_server):
    """Replay after the channel exists, then live delivery of a new comment
    (resource channels authorize once the projector materialises the row)."""
    client, alice_token, bob_token, issue = (
        env["client"], env["alice_token"], env["bob_token"], env["issue"],
    )
    # First comment materialises the inbox channel (relay projects it).
    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "materialises the channel"},
        headers=_auth(bob_token),
    )
    assert resp.status_code == 201
    await _drain(relay)

    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    async with websockets.connect(ws_url, open_timeout=10) as ws:
        await ws.send(json.dumps({"op": "auth", "token": alice_token}))
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert first["op"] == "auth_ok"
        channel = f"member:{env['alice_member']}:inbox"
        await ws.send(json.dumps({"op": "subscribe", "channel": channel}))
        replayed = []
        subscribed = None
        for _ in range(6):
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            if frame["op"] == "subscribed":
                subscribed = frame
                break
            replayed.append(frame)
        assert subscribed is not None and subscribed["channel"] == channel
        # the first notification replays from realtime_events (§6.7 resume)
        replay_events = {
            frame.get("event") for frame in replayed if frame.get("op") == "event"
        }
        assert "notification.created" in replay_events
        assert "inbox.unread_count" in replay_events

        # LIVE: a second comment → relay → redis → gateway pushes the frames.
        # Inside the 60 s window it aggregates into the same unread group, so
        # only notification.created is pushed (no unread-count change, §6.13).
        resp2 = await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": "live delivery check"},
            headers=_auth(bob_token),
        )
        assert resp2.status_code == 201
        await _drain(relay)

        live = []
        for _ in range(4):
            try:
                live.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            except TimeoutError:
                break
    events = {frame["event"] for frame in live if frame.get("op") == "event"}
    assert "notification.created" in events


async def test_inbox_ws_first_notification_live_e2e(env, relay, gateway_server):
    """Regression: subscribing BEFORE any inbox event exists must succeed —
    the channel row is absent until the first projection, but the owner must
    still receive the very first notification live (§3.6 / I9 badge)."""
    client, alice_token, bob_token, issue = (
        env["client"], env["alice_token"], env["bob_token"], env["issue"],
    )
    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    async with websockets.connect(ws_url, open_timeout=10) as ws:
        await ws.send(json.dumps({"op": "auth", "token": alice_token}))
        assert (json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))["op"] == "auth_ok"
        channel = f"member:{env['alice_member']}:inbox"
        await ws.send(json.dumps({"op": "subscribe", "channel": channel}))
        subscribed = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert subscribed["op"] == "subscribed", subscribed  # was 'error' pre-fix

        resp = await client.post(
            f"/api/v1/issues/{issue['id']}/comments",
            json={"body_markdown": "first live notification"},
            headers=_auth(bob_token),
        )
        assert resp.status_code == 201
        await _drain(relay)

        frames = []
        for _ in range(4):
            try:
                frames.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=10)))
            except asyncio.TimeoutError:
                break
    events = {frame["event"] for frame in frames if frame.get("op") == "event"}
    assert "notification.created" in events


async def test_inbox_ws_rejects_foreign_member(env, gateway_server, owner_factory):
    """member:{id}:inbox is subscribable only by the owning user (CWE-862)."""
    bob_token = env["bob_token"]
    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    async with websockets.connect(ws_url, open_timeout=10) as ws:
        await ws.send(json.dumps({"op": "auth", "token": bob_token}))
        assert (json.loads(await asyncio.wait_for(ws.recv(), timeout=5)))["op"] == "auth_ok"
        # bob tries to subscribe to ALICE's inbox channel
        await ws.send(
            json.dumps({"op": "subscribe", "channel": f"member:{env['alice_member']}:inbox"})
        )
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert frame["op"] == "error"


# ---------------------------------------------------------------------------
# 5. cross-tenant isolation (T1) + RESTRICT deletes (T18)
# ---------------------------------------------------------------------------


async def test_cross_tenant_isolation_e2e(env, api_client, owner_factory):
    outsider_token = await _register_and_login(api_client, "outsider@mesh.example")
    issue_id = env["issue"]["id"]
    listing = await api_client.get(
        f"/api/v1/issues/{issue_id}/comments", headers=_auth(outsider_token)
    )
    assert listing.status_code == 404
    post = await api_client.post(
        f"/api/v1/issues/{issue_id}/comments",
        json={"body_markdown": "intruder"}, headers=_auth(outsider_token),
    )
    assert post.status_code == 404
    # inbox of another workspace is invisible
    inbox = await api_client.get(
        "/api/v1/inbox",
        params={"workspace_id": str(env["workspace_id"])},
        headers=_auth(outsider_token),
    )
    assert inbox.status_code == 404


async def test_cross_workspace_composite_fk_rejected_e2e(env, owner_factory):
    """A comment cannot reference an issue of another workspace (README §6.2)."""
    other_ws = uuid.uuid4()
    from mesh.db.models.workspace import Workspace

    async with owner_factory() as session, session.begin():
        session.add(Workspace(id=other_ws, name="Other", slug=f"ws-{uuid.uuid4().hex[:10]}"))
    async with owner_factory() as session, session.begin():
        from mesh.db.models.issue import Issue, IssueStatus

        status = IssueStatus(workspace_id=other_ws, name="S", category="todo")
        session.add(status)
        await session.flush()
        foreign_issue = Issue(
            workspace_id=other_ws,
            identifier_namespace_key="O",
            number=1,
            identifier="O-1",
            title="foreign",
            status_id=status.id,
            state_category="todo",
        )
        session.add(foreign_issue)
    from mesh.db.models.comment import Comment

    async with owner_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                session.add(
                    Comment(
                        workspace_id=env["workspace_id"],  # tenant A…
                        issue_id=foreign_issue.id,  # …issue of tenant B
                        author_kind="system",
                        body_markdown="cross-tenant",
                    )
                )


async def test_member_delete_restrict_e2e(env, owner_factory):
    """comments.author_id is ON DELETE RESTRICT — a referenced member cannot
    be physically deleted (README §6.2 rule 6 / T18)."""
    client, token, issue = env["client"], env["alice_token"], env["issue"]
    resp = await client.post(
        f"/api/v1/issues/{issue['id']}/comments",
        json={"body_markdown": "authored history"}, headers=_auth(token),
    )
    assert resp.status_code == 201
    async with owner_factory() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                await session.execute(
                    text("DELETE FROM members WHERE id = :id"),
                    {"id": env["alice_member"]},
                )
