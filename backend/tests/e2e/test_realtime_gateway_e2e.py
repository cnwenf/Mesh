"""Realtime gateway e2e: real WS server, real HTTP reconciliation, real Redis.

Covers §9 T6 (stale cursor → resync_required → REST reconciliation) and
§9 T26-② (cross-tenant subscription rejected).
"""

from __future__ import annotations

import asyncio
import json

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
    channel = f"issue:{workspace.id}"
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


async def test_cross_tenant_subscription_is_forbidden(
    gateway_server, session_factory, workspace_factory
):
    workspace_a = await workspace_factory(name="A", slug="gw-a")
    workspace_b = await workspace_factory(name="B", slug="gw-b")
    channel = f"issue:{workspace_a.id}"
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
    channel = f"issue:{workspace_a.id}"
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
