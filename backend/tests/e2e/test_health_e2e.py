"""Health endpoints over real HTTP against real PostgreSQL + Redis."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def test_healthz_liveness(api_client):
    response = await api_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}}


async def test_readyz_checks_database_and_redis(api_client):
    response = await api_client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "ready"
    assert body["data"]["checks"] == {"database": "ok", "redis": "ok"}


async def test_ping_success_envelope(api_client):
    response = await api_client.get("/api/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"data": {"pong": True}}
