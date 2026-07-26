"""In-process app integration: the real create_app() apps against real PG/Redis.

Complements the subprocess e2e: same contracts, exercised inside the coverage
scope. The API app runs over ASGI transport; the gateway app over Starlette's
real WebSocket test client.
"""

from __future__ import annotations

import httpx
import pytest
from starlette.testclient import TestClient

from mesh.api.app import create_app as create_api_app
from mesh.config import load_settings
from mesh.events.vocab import REALTIME_PUBLISH
from mesh.outbox.projector import project_realtime_event
from mesh.outbox.relay import OutboxRelay
from mesh.outbox.service import emit_realtime
from mesh.realtime.app import create_app as create_gateway_app

pytestmark = pytest.mark.e2e


@pytest.fixture
def settings(db_url, redis_url):
    return load_settings(database_url=db_url, redis_url=redis_url, auth_mode="dev")


@pytest.fixture
def api_app(settings):
    app = create_api_app(settings)
    yield app


@pytest.fixture
async def api(api_app):
    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        yield client
    # lifespan shutdown (dispose engine / close redis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_app)):
        pass


async def _publish(session_factory, workspace_id, channel, event, data):
    async with session_factory() as session, session.begin():
        await emit_realtime(
            session, workspace_id=workspace_id, channel=channel, event=event, data=data
        )
    relay = OutboxRelay(session_factory, handlers={REALTIME_PUBLISH: project_realtime_event})
    await relay.run_once()


async def test_api_health_and_ping_inprocess(api):
    assert (await api.get("/healthz")).json() == {"data": {"status": "ok"}}
    ready = (await api.get("/readyz")).json()
    assert ready["data"]["checks"] == {"database": "ok", "redis": "ok"}
    assert (await api.get("/api/v1/ping")).json() == {"data": {"pong": True}}


async def test_api_debug_error_sanitizes_500_inprocess(api):
    response = await api.get("/_debug/error", params={"status": 500})
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "internal server error"}
    }
    assert "must-not-leak" not in response.text


async def test_reconciliation_endpoint_inprocess(api, settings, workspace_factory, session_factory):
    workspace = await workspace_factory()
    channel = f"workspace:{workspace.id}:issues"
    for i in range(1, 3):
        await _publish(session_factory, workspace.id, channel, "issue.updated", {"v": i})

    # 401 without token / 403 for another workspace / 200 for owner.
    assert (await api.get("/api/v1/realtime/events", params={"channel": channel})).status_code == 401
    other_ws = await workspace_factory(name="O", slug="other-inproc")
    forbidden = await api.get(
        "/api/v1/realtime/events",
        params={"channel": channel},
        headers={"Authorization": f"Bearer mesh-dev:{other_ws.id}"},
    )
    assert forbidden.status_code == 403
    invalid_channel = await api.get(
        "/api/v1/realtime/events",
        params={"channel": "BAD CHANNEL"},
        headers={"Authorization": f"Bearer mesh-dev:{workspace.id}"},
    )
    assert invalid_channel.status_code == 400
    assert invalid_channel.json()["error"]["code"] == "invalid_channel"

    ok = await api.get(
        "/api/v1/realtime/events",
        params={"channel": channel, "since": 0, "limit": 1},
        headers={"Authorization": f"Bearer mesh-dev:{workspace.id}"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert [row["seq"] for row in body["data"]] == [1]
    assert body["next_cursor"] is not None

    page2 = await api.get(
        "/api/v1/realtime/events",
        params={"channel": channel, "since": 0, "limit": 1, "cursor": body["next_cursor"]},
        headers={"Authorization": f"Bearer mesh-dev:{workspace.id}"},
    )
    assert [row["seq"] for row in page2.json()["data"]] == [2]
    assert page2.json()["next_cursor"] is None


def test_gateway_websocket_flow_inprocess(settings, workspace_factory, session_factory):
    """Real WebSocket endpoint via Starlette TestClient (in-process)."""

    async def _seed():
        workspace = await workspace_factory()
        channel = f"workspace:{workspace.id}:issues"
        await _publish(session_factory, workspace.id, channel, "issue.updated", {"v": 1})
        return workspace, channel

    import asyncio

    workspace, channel = asyncio.get_event_loop().run_until_complete(_seed())

    gateway_app = create_gateway_app(settings)
    with TestClient(gateway_app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"op": "auth", "token": f"mesh-dev:{workspace.id}"})
            assert ws.receive_json()["op"] == "auth_ok"
            ws.send_json({"op": "subscribe", "channel": channel})
            frame = ws.receive_json()
            assert frame["op"] == "event" and frame["seq"] == 1
            subscribed = ws.receive_json()
            assert subscribed == {"op": "subscribed", "channel": channel, "last_seq": 1}
            ws.send_json({"op": "ping"})
            assert ws.receive_json()["op"] == "ping"

        # Unauthenticated first frame → error + close.
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"op": "subscribe", "channel": channel})
            frame = ws.receive_json()
            assert frame["op"] == "error"
            assert frame["code"] == "unauthorized"


def test_gateway_production_auth_mode_rejects_dev_tokens(settings):
    production_settings = load_settings(
        database_url=settings.database_url,
        redis_url=settings.redis_url,
        auth_mode="production",
        # A real signing secret so the production fail-safe lets the app boot;
        # dev-token rejection below is independent of the secret's value.
        jwt_secret="gateway-inprocess-e2e-signing-secret",
    )
    gateway_app = create_gateway_app(production_settings)
    with TestClient(gateway_app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"op": "auth", "token": "mesh-dev:11111111-1111-1111-1111-111111111111"})
            frame = ws.receive_json()
            assert frame["op"] == "error"
            assert frame["code"] == "unauthorized"
