"""Health readiness failures, lifespan shutdown, and HTTP-exception edge cases."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from mesh.api.app import create_app
from mesh.api.error_handlers import install_error_handlers
from mesh.config import load_settings
from mesh.errors import INTERNAL_ERROR_MESSAGE


def _settings(db_url, redis_url, **overrides):
    return load_settings(database_url=db_url, redis_url=redis_url, **overrides)


async def test_readyz_reports_database_unavailable(db_url, redis_url):
    app = create_app(_settings("postgresql+asyncpg://mesh:mesh@127.0.0.1:1/nope", redis_url))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "service_unavailable"
    assert "database" in body["error"]["details"]["unavailable"]
    await app.state.redis.aclose()
    await app.state.engine.dispose()


async def test_readyz_reports_redis_unavailable(db_url):
    app = create_app(_settings(db_url, "redis://127.0.0.1:1/0"))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/readyz")
    assert response.status_code == 503
    assert "redis" in response.json()["error"]["details"]["unavailable"]
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def test_api_lifespan_runs_on_testclient(db_url, redis_url):
    app = create_app(_settings(db_url, redis_url))
    with TestClient(app) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
    # Exiting the client context ran the lifespan shutdown (engine disposed).


async def test_http_exception_500_is_sanitized():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/server-boom")
    async def server_boom():
        raise HTTPException(status_code=500, detail="sql=SELECT secret FROM users")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/server-boom")
    assert response.status_code == 500
    body = response.json()
    assert body == {"error": {"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}}


async def test_http_exception_non_string_detail_uses_default_message():
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/complex-detail")
    async def complex_detail():
        raise HTTPException(status_code=404, detail={"structured": True})

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/complex-detail")
    assert response.json() == {"error": {"code": "not_found", "message": "resource not found"}}


async def test_run_forever_without_stop_event_polls(session_factory, workspace_factory):
    from mesh.outbox.relay import OutboxRelay
    from mesh.outbox.service import emit_event

    workspace = await workspace_factory()
    seen = []

    async def handler(session, event):
        seen.append(event.id)
        return None

    relay = OutboxRelay(
        session_factory, handlers={"loop.event": handler}, poll_interval=0.05
    )
    task = asyncio.create_task(relay.run_forever())  # no stop event: sleeps between polls
    await asyncio.sleep(0.1)
    async with session_factory() as session, session.begin():
        await emit_event(
            session, workspace_id=workspace.id, event_type="loop.event", payload={}
        )
    deadline = asyncio.get_event_loop().time() + 5
    while not seen and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert seen
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_pump_error_is_contained_and_subscriber_closed():
    from mesh.realtime.session import RealtimeSession
    from tests.unit.test_gateway_session import (
        PRINCIPAL,
        TOKEN,
        AllowAuthorizer,
        FakeTransport,
        FixedAuthenticator,
    )

    closed = []

    class ErroringSubscriber:
        async def start(self):
            raise RuntimeError("redis exploded")

        async def frames(self):
            yield ("", {})  # pragma: no cover

        async def close(self):
            closed.append(True)

    transport = FakeTransport([{"op": "auth", "token": TOKEN}, {"op": "ping"}])
    session = RealtimeSession(
        transport,
        session_factory=None,
        authenticator=FixedAuthenticator(PRINCIPAL),
        authorizer=AllowAuthorizer(),
        subscriber_factory=ErroringSubscriber,
        ping_interval=3600,
    )
    # A crashing fan-out subscriber is contained and always closed...
    await session._pump()
    assert closed == [True]
    # ...and the connection keeps serving: ping is still answered.
    await session.run()
    ops = [frame.get("op") for frame in transport.sent]
    assert "ping" in ops
