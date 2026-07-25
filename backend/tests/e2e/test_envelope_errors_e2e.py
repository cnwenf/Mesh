"""Error envelopes over real HTTP (§6.14), via the real server process."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "validation_error"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (409, "conflict"),
        (410, "gone"),
        (422, "business_rule_violation"),
        (429, "rate_limited"),
    ],
)
async def test_named_error_codes_render_envelope(api_client, status, code):
    response = await api_client.get("/_debug/error", params={"status": status})
    assert response.status_code == status
    body = response.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)


async def test_rate_limited_carries_retry_after(api_client):
    response = await api_client.get("/_debug/error", params={"status": 429})
    assert response.headers.get("Retry-After") == "30"


async def test_internal_error_is_sanitized_over_http(api_client):
    response = await api_client.get("/_debug/error", params={"status": 500})
    assert response.status_code == 500
    body = response.json()
    assert body == {"error": {"code": "internal_error", "message": "internal server error"}}
    assert "must-not-leak" not in response.text


async def test_unknown_route_maps_to_404_envelope(api_client):
    response = await api_client.get("/api/v1/definitely-missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_dev_principal_endpoint_respects_bearer_token(api_client, workspace_factory):
    workspace = await workspace_factory()
    # No token → 401 envelope.
    unauthenticated = await api_client.get("/_debug/principal")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "unauthorized"
    # Dev token → principal with that workspace.
    response = await api_client.get(
        "/_debug/principal", headers={"Authorization": f"Bearer mesh-dev:{workspace.id}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["subject"] == "dev-user"
    assert body["data"]["workspaces"] == [str(workspace.id)]
