"""Realtime gateway e2e: real WS server, real HTTP reconciliation, real Redis.

Covers §9 T6 (stale cursor → resync_required → REST reconciliation) and
§9 T26-② (cross-tenant subscription rejected).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import pytest
import websockets
from sqlalchemy import text

from mesh.events.vocab import REALTIME_PUBLISH
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.outbox.service import emit_realtime
from mesh.realtime.pubsub import RedisFanOut

pytestmark = pytest.mark.e2e


async def _ws_connect(gateway_server):
    ws_url = gateway_server.base_url.replace("http://", "ws://") + "/ws"
    return await websockets.connect(ws_url, open_timeout=10)


async def _recv_frame(ws, timeout=5.0):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


async def _publish_via_relay(session_factory, workspace_id, channel, event, data, redis_client=None):
    """The production path: business tx → outbox → relay → projector → Redis fan-out."""
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session, workspace_id=workspace_id, channel=channel, event=event, data=data
        )
    fanout = RedisFanOut(redis_client) if redis_client is not None else None
    relay = OutboxRelay(
        session_factory, handlers={REALTIME_PUBLISH: project_realtime_event}, fanout=fanout
    )
    await relay.run_once()


async def test_full_flow_auth_subscribe_replay_live_and_resync(
    gateway_server, api_server, session_factory, workspace_factory, redis_client
):
    workspace = await workspace_factory()
    channel = f"workspace:{workspace.id}:issues"
    token = f"mesh-dev:{workspace.id}"

    # Seed 3 events through the real outbox → projector → Redis fan-out path.
    for i in range(1, 4):
        await _publish_via_relay(
            session_factory, workspace.id, channel, "issue.updated", {"v": i}, redis_client
        )

    ws = await _ws_connect(gateway_server)
    try:
        # First-frame auth (§6.16 — no token in URL).
        await ws.send(json.dumps({"op": "auth", "token": token}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"

        # Subscribe with resume_from=2 → replay seq 2, 3 then subscribed.
        await ws.send(json.dumps({"op": "subscribe", "channel": channel, "resume_from": 2}))
        frame_a = await _recv_frame(ws)
        frame_b = await _recv_frame(ws)
        subscribed = await _recv_frame(ws)
        assert (frame_a["seq"], frame_b["seq"]) == (2, 3)
        assert frame_a["event"] == "issue.updated"
        assert frame_a["payload"] == {"v": 2}
        assert subscribed == {"op": "subscribed", "channel": channel, "last_seq": 3}

        # Live delivery: project a new event while subscribed.
        await _publish_via_relay(
            session_factory, workspace.id, channel, "issue.updated", {"v": 4}, redis_client
        )
        live = await _recv_frame(ws, timeout=10)
        assert live["op"] == "event"
        assert live["seq"] == 4
        assert live["payload"] == {"v": 4}
    finally:
        await ws.close()

    # --- T6: retention purge removes old events, stale cursor → resync_required
    async with session_factory() as session, session.begin():
        await session.execute(
            text("DELETE FROM realtime_events WHERE channel = :ch AND seq <= 3"),
            {"ch": channel},
        )

    ws2 = await _ws_connect(gateway_server)
    try:
        await ws2.send(json.dumps({"op": "auth", "token": token}))
        assert (await _recv_frame(ws2))["op"] == "auth_ok"
        await ws2.send(json.dumps({"op": "subscribe", "channel": channel, "resume_from": 2}))
        resync = await _recv_frame(ws2)
        assert resync["op"] == "resync_required"
        assert resync["watermark"] == 4
        assert resync["rest"].startswith("/api/v1/realtime/events?channel=")
        assert "since=2" in resync["rest"]

        # Reconcile over REST with the dev token — pulls the surviving events.
        import httpx

        async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
            response = await client.get(
                resync["rest"], headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        body = response.json()
        assert [event["seq"] for event in body["data"]] == [4]
        assert body["next_cursor"] is None
    finally:
        await ws2.close()


async def test_unauthenticated_first_frame_is_rejected(gateway_server):
    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "subscribe", "channel": "issue:x"}))
        frame = await _recv_frame(ws)
        assert frame["op"] == "error"
        assert frame["code"] == "unauthorized"
    finally:
        await ws.close()


async def test_bool_and_negative_resume_from_rejected_over_real_ws(
    gateway_server, workspace_factory
):
    """L2: JSON `true` (bool is an int subclass) and negative resume_from must
    be answered with a validation_error over a real socket — never crash the
    replay query — and the connection must stay usable afterwards."""
    workspace = await workspace_factory()
    channel = f"issue:{workspace.id}"
    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "auth", "token": f"mesh-dev:{workspace.id}"}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"

        for bad in (True, False, -5):
            await ws.send(json.dumps({"op": "subscribe", "channel": channel, "resume_from": bad}))
            frame = await _recv_frame(ws)
            assert frame["op"] == "error"
            assert frame["code"] == "validation_error"

        # Connection survives the rejections and still answers pings.
        await ws.send(json.dumps({"op": "ping"}))
        assert (await _recv_frame(ws))["op"] == "ping"
    finally:
        await ws.close()


# --- M4: DoS hardening over a real socket ---


async def test_frame_flood_is_rate_limited_and_connection_closed(
    gateway_server, workspace_factory
):
    """A client flooding frames past the per-second budget is served at most
    the budget and then dropped (rate_limited error frame on the normal path)."""
    workspace = await workspace_factory()
    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "auth", "token": f"mesh-dev:{workspace.id}"}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"

        # A real client reads while it writes — drain concurrently so the
        # server's pongs and the final error frame are consumed as they land.
        received: list[dict] = []
        closed = asyncio.Event()

        async def _reader() -> None:
            with contextlib.suppress(websockets.ConnectionClosed):
                while True:
                    received.append(await _recv_frame(ws, timeout=15))
            closed.set()

        reader = asyncio.create_task(_reader())
        with contextlib.suppress(websockets.ConnectionClosed):
            for _ in range(200):  # default budget is 30/rolling second
                await ws.send(json.dumps({"op": "ping"}))
                await asyncio.sleep(0)
        await asyncio.wait_for(closed.wait(), timeout=15)
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader

        codes = [f.get("code") for f in received if f.get("op") == "error"]
        pongs = sum(1 for f in received if f.get("op") == "ping")
        # Defense contract: the flood is NOT fully serviced — roughly the
        # per-second budget (30; exact semantics, including the error frame,
        # are pinned by the fake-clock unit tests) and then the drop. The
        # rate_limited frame is the normal path, but the still-flooding
        # client's in-flight frames can make the transport abort before it
        # is read — that is still a drop, so both observations are valid.
        assert 25 <= pongs <= 40, f"pongs={pongs}"
        assert codes in ([], ["rate_limited"]), f"codes={codes} pongs={pongs}"
        assert closed.is_set()
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


async def test_oversized_frame_is_rejected_at_transport(gateway_server, workspace_factory):
    """A frame over the transport ceiling (--ws-max-size 65536, mirroring
    compose) kills the connection at the transport layer."""
    workspace = await workspace_factory()
    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "auth", "token": f"mesh-dev:{workspace.id}"}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"
        await ws.send(json.dumps({"op": "ping", "filler": "x" * 100_000}))
        with pytest.raises(websockets.ConnectionClosed):
            for _ in range(10):
                await _recv_frame(ws, timeout=10)
    finally:
        with contextlib.suppress(Exception):
            await ws.close()


async def test_cross_tenant_subscription_is_forbidden(
    gateway_server, session_factory, workspace_factory
):
    workspace_a = await workspace_factory(name="A", slug="gw-a")
    workspace_b = await workspace_factory(name="B", slug="gw-b")
    channel = f"workspace:{workspace_a.id}:issues"
    await _publish_via_relay(
        session_factory, workspace_a.id, channel, "issue.updated", {"v": 1}
    )

    ws = await _ws_connect(gateway_server)
    try:
        # Token scoped to workspace B tries to subscribe to A's channel.
        await ws.send(json.dumps({"op": "auth", "token": f"mesh-dev:{workspace_b.id}"}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"
        await ws.send(json.dumps({"op": "subscribe", "channel": channel}))
        frame = await _recv_frame(ws)
        assert frame["op"] == "error"
        assert frame["code"] == "forbidden"
    finally:
        await ws.close()


async def test_reconciliation_rest_requires_auth_and_tenant_match(
    api_server, session_factory, workspace_factory
):
    workspace_a = await workspace_factory(name="A", slug="rest-a")
    workspace_b = await workspace_factory(name="B", slug="rest-b")
    channel = f"workspace:{workspace_a.id}:issues"
    await _publish_via_relay(
        session_factory, workspace_a.id, channel, "issue.updated", {"v": 1}
    )

    import httpx

    async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
        # No token → 401 envelope.
        unauthenticated = await client.get(
            "/api/v1/realtime/events", params={"channel": channel, "since": 0}
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "unauthorized"

        # Wrong workspace token → 403.
        forbidden = await client.get(
            "/api/v1/realtime/events",
            params={"channel": channel, "since": 0},
            headers={"Authorization": f"Bearer mesh-dev:{workspace_b.id}"},
        )
        assert forbidden.status_code == 403

        # Owner token → events with keyset pagination.
        ok = await client.get(
            "/api/v1/realtime/events",
            params={"channel": channel, "since": 0, "limit": 1},
            headers={"Authorization": f"Bearer mesh-dev:{workspace_a.id}"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["seq"] == 1
        assert body["next_cursor"] is None  # only one event exists


async def test_jwt_session_first_frame_auth_subscribe_and_cross_tenant(
    gateway_server, api_server, session_factory, workspace_factory, redis_client
):
    """Session JWT first-frame auth (auth.md §3.1 credentials, README §6.16).

    A real logged-in user authenticates with the access JWT, subscribes to
    their own workspace channel, is denied a foreign tenant's channel, and
    reconciles over REST with the same Bearer token.
    """
    import uuid
    from datetime import timedelta

    import httpx

    from mesh.auth.jwt import encode_access_token
    from mesh.config import DEV_JWT_SECRET
    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    ws_a = await workspace_factory(name="A", slug="gw-jwt-a")
    ws_b = await workspace_factory(name="B", slug="gw-jwt-b")
    user_id = uuid.uuid4()
    async with session_factory() as session, session.begin():
        session.add(User(id=user_id, email="jwt-gw@corp.com", display_name="J"))
    async with session_factory() as session, session.begin():
        session.add(Member(workspace_id=ws_a.id, member_type="human", user_id=user_id))
    token, _ = encode_access_token(
        subject=user_id, secret=DEV_JWT_SECRET, algorithm="HS256", ttl=timedelta(minutes=5)
    )

    channel_a = f"workspace:{ws_a.id}"
    await _publish_via_relay(
        session_factory, ws_a.id, channel_a, "workspace.updated", {"v": 1}, redis_client
    )

    ws = await _ws_connect(gateway_server)
    try:
        await ws.send(json.dumps({"op": "auth", "token": token}))
        assert (await _recv_frame(ws))["op"] == "auth_ok"

        await ws.send(json.dumps({"op": "subscribe", "channel": channel_a}))
        frame = await _recv_frame(ws)
        assert frame["op"] == "event"
        assert frame["event"] == "workspace.updated"
        subscribed = await _recv_frame(ws)
        assert subscribed["op"] == "subscribed"
        assert subscribed["channel"] == channel_a

        # Non-member tenant channel → forbidden, connection stays up.
        await ws.send(json.dumps({"op": "subscribe", "channel": f"workspace:{ws_b.id}"}))
        denied = await _recv_frame(ws)
        assert denied["op"] == "error"
        assert denied["code"] == "forbidden"
    finally:
        await ws.close()

    # REST reconciliation accepts the same session JWT.
    async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
        response = await client.get(
            "/api/v1/realtime/events",
            params={"channel": channel_a, "since": 0},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert [event["event"] for event in body["data"]] == ["workspace.updated"]

    # Invalid JWT → 401 on the REST path.
    async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
        unauthorized = await client.get(
            "/api/v1/realtime/events",
            params={"channel": channel_a, "since": 0},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert unauthorized.status_code == 401


async def test_reconciliation_foreign_typed_cursor_is_400_not_500(
    api_server, session_factory, workspace_factory
):
    """L5: a well-formed cursor from another endpoint (datetime + UUID keyset
    against the int-seq + BIGINT-id reconcile listing) must answer 400
    invalid_cursor over real HTTP, not a neutral 500 from the DB layer."""
    from datetime import UTC, datetime

    import httpx

    from mesh.api.pagination import encode_cursor

    workspace = await workspace_factory()
    channel = f"workspace:{workspace.id}"
    await _publish_via_relay(
        session_factory, workspace.id, channel, "workspace.updated", {"v": 1}
    )
    foreign_cursor = encode_cursor(datetime(2026, 7, 25, tzinfo=UTC), uuid.uuid4())
    async with httpx.AsyncClient(base_url=api_server.base_url, timeout=10) as client:
        response = await client.get(
            "/api/v1/realtime/events",
            params={"channel": channel, "since": 0, "cursor": foreign_cursor},
            headers={"Authorization": f"Bearer mesh-dev:{workspace.id}"},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "invalid_cursor"


# --- P1 regression: standalone gateway must not leak private-project events ---


async def _register_and_login(client, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-strong-passw0rd", "display_name": email.split("@")[0]},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "a-strong-passw0rd"}
    )
    return login.json()["data"]["access_token"]


async def _invite_accept(client, owner_token, ws_id, email):
    inv = await client.post(
        f"/api/v1/workspaces/{ws_id}/invitations",
        json={"emails": [email], "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    token = inv.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    joiner = await _register_and_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept",
        json={"token": token},
        headers={"Authorization": f"Bearer {joiner}"},
    )
    return accepted.json()["data"]["member"]["id"], joiner


async def test_gateway_private_project_subscribe_forbidden_for_non_member(
    gateway_server, api_client, session_factory, redis_client
):
    """CWE-862 regression: on the standalone gateway /ws a workspace member who is
    NOT a member of a *private* project must be forbidden from its ``project:{id}``
    channel, while a real project member is allowed. The gateway shares the same
    resource checker as the API factory (register_resource_checkers)."""
    from sqlalchemy import select

    from mesh.db.models.member import Member
    from mesh.project.schemas import AddProjectMemberRequest, CreateProjectRequest
    from mesh.project.service import ProjectService

    owner_token = await _register_and_login(api_client, "gw-owner@corp.com")
    ws = (
        await api_client.post(
            "/api/v1/workspaces",
            json={"name": "GW", "slug": f"gw-{uuid.uuid4().hex[:8]}"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
    ).json()["data"]
    ws_id = uuid.UUID(ws["id"])
    member_a_id, token_a = await _invite_accept(api_client, owner_token, ws["id"], "gw-a@corp.com")
    member_b_id, token_b = await _invite_accept(api_client, owner_token, ws["id"], "gw-b@corp.com")

    # Owner's roster row = actor for project writes (owner is the project lead).
    async with session_factory() as session:
        owner_user_id = (
            await session.execute(
                text("SELECT id FROM users WHERE email = 'gw-owner@corp.com'")
            )
        ).scalar_one()
        owner_member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == ws_id, Member.user_id == owner_user_id
                )
            )
        ).scalar_one()

    service = ProjectService(session_factory)
    project = await service.create_project(
        actor=owner_member,
        workspace_id=ws_id,
        body=CreateProjectRequest(name="Secret", key="SEC", visibility="private"),
    )
    project_id = uuid.UUID(project["id"])
    await service.add_project_member(
        actor=owner_member,
        workspace_id=ws_id,
        project_id=project_id,
        body=AddProjectMemberRequest(member_id=member_a_id, role="member"),
    )

    # Seed the channel row through the real outbox → projector path so the
    # row-probe path has something to find (the leak is on the checker, not the
    # row probe).
    await _publish_via_relay(
        session_factory,
        ws_id,
        f"project:{project_id}",
        "project.created",
        {"project": {"id": str(project_id)}},
        redis_client,
    )

    # Non-member (B) on the gateway → forbidden.
    ws_b = await _ws_connect(gateway_server)
    try:
        await ws_b.send(json.dumps({"op": "auth", "token": token_b}))
        assert (await _recv_frame(ws_b))["op"] == "auth_ok"
        await ws_b.send(json.dumps({"op": "subscribe", "channel": f"project:{project_id}"}))
        frame_b = await _recv_frame(ws_b)
        assert frame_b["op"] == "error"
        assert frame_b["code"] == "forbidden"
    finally:
        await ws_b.close()

    # Real project member (A) on the gateway → subscribed.
    ws_a = await _ws_connect(gateway_server)
    try:
        await ws_a.send(json.dumps({"op": "auth", "token": token_a}))
        assert (await _recv_frame(ws_a))["op"] == "auth_ok"
        await ws_a.send(json.dumps({"op": "subscribe", "channel": f"project:{project_id}"}))
        # The pump subscribes before replay, so the seeded event may arrive before
        # the ``subscribed`` ack; drain until the ack, asserting no ``forbidden``.
        got_subscribed = False
        for _ in range(5):
            frame_a = await _recv_frame(ws_a)
            assert frame_a.get("code") != "forbidden", frame_a
            if frame_a["op"] == "subscribed":
                got_subscribed = True
                break
        assert got_subscribed
    finally:
        await ws_a.close()
