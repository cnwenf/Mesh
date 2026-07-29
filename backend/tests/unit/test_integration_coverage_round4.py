"""Round-4 coverage: oauth callback paths, vcs_link_invalid on non-VCS
integrations, deliveries state filter."""

from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from mesh.integrations import oauth as oauth_mod
from tests.unit.test_integration_routes import (
    _settings_kwargs,
    auth_headers,
    make_world,
)

pytestmark = pytest.mark.unit


@pytest_asyncio.fixture
async def app_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app


@pytest_asyncio.fixture
async def redis_client(redis_url):
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def test_oauth_callback_unknown_state_redirects_error(app_client, redis_client):
    client, app = app_client
    response = await client.get(
        "/api/v1/integrations/oauth/im_slack/callback",
        params={"state": "never-issued", "code": "c"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "oauth=error" in response.headers["location"]


async def test_oauth_callback_kind_mismatch_redirects_error(app_client, redis_client):
    client, app = app_client
    url = await oauth_mod.begin_authorization(
        redis_client,
        workspace_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        kind="im_slack",
        callback_url="https://mesh.test/cb",
    )
    state = url.split("state=")[1].split("&")[0]
    # kind in the path does not match the issued state's kind
    response = await client.get(
        "/api/v1/integrations/oauth/vcs_github/callback",
        params={"state": state, "code": "c"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "oauth=error" in response.headers["location"]


async def test_oauth_callback_exchange_failure_redirects_error(app_client, redis_client):
    client, app = app_client
    url = await oauth_mod.begin_authorization(
        redis_client,
        workspace_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        kind="im_slack",
        callback_url="https://mesh.test/cb",
    )
    state = url.split("state=")[1].split("&")[0]
    # The token endpoint is the real Slack API — unreachable from the test
    # sandbox → exchange raises BusinessRuleError → error redirect.
    response = await client.get(
        "/api/v1/integrations/oauth/im_slack/callback",
        params={"state": state, "code": "bad-code"},
        follow_redirects=False,
        timeout=30,
    )
    assert response.status_code == 302
    assert "oauth=error" in response.headers["location"]


async def test_oauth_callback_creates_integration_with_encrypted_secret(
    app_client, redis_client, session_factory, monkeypatch
):
    """MEDIUM-1: the callback creates the integration itself, storing the
    refresh token as ciphertext ONLY (secret_ref, §6.16) under the name that
    rode through the state record (§3.1 line 523)."""
    client, _ = app_client
    world = await make_world(client, "oauthcreate")
    url = await oauth_mod.begin_authorization(
        redis_client,
        workspace_id=uuid.UUID(world["ws_id"]),
        member_id=uuid.UUID(world["member_id"]),
        kind="im_slack",
        callback_url="https://mesh.test/cb",
        name="Slack OAuth",
    )
    state = url.split("state=")[1].split("&")[0]

    async def fake_exchange_code(**_kwargs):
        return {"refresh_token": "rt-super-secret", "team_id": "T_OAUTH"}

    # The token exchange is the external-provider boundary; everything else
    # (state consumption, member lookup, integration creation) is real.
    monkeypatch.setattr(oauth_mod, "exchange_code", fake_exchange_code)

    response = await client.get(
        "/api/v1/integrations/oauth/im_slack/callback",
        params={"state": state, "code": "auth-code"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert "oauth=success" in location
    integration_id = uuid.UUID(location.split("id=")[1])

    from mesh.db.models.integration import Integration
    from mesh.runtime.credentials import decrypt_credential_value
    from tests.unit.test_integration_routes import SIGNING_SECRET

    async with session_factory() as session:
        row = await session.get(Integration, integration_id)
    assert row is not None
    assert row.name == "Slack OAuth", "name carried through the state record"
    assert row.kind == "im_slack"
    assert row.secret_ref is not None
    assert row.secret_ref != "rt-super-secret", "stored as ciphertext, never plaintext"
    assert decrypt_credential_value(row.secret_ref, SIGNING_SECRET) == "rt-super-secret"
    assert row.config.get("provider_tenant_id") == "T_OAUTH"


async def test_vcs_endpoints_reject_non_vcs_integration(app_client):
    client, _ = app_client
    world = await make_world(client, "nonvcs")
    slack_integration = (
        await client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-nonvcs", "config": {"team_id": "T_NV"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    issue = (
        await client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "i"},
            headers=auth_headers(world),
        )
    ).json()["data"]
    link = await client.post(
        "/api/v1/integrations/vcs/links",
        json={
            "integration_id": slack_integration["id"],
            "vcs_ref": {"type": "pull_request", "id": "a/b#1"},
            "issue_id": issue["id"],
        },
        headers=auth_headers(world),
    )
    assert link.status_code == 422
    assert link.json()["error"]["code"] == "vcs_link_invalid"
    resolve = await client.post(
        "/api/v1/integrations/vcs/resolve",
        json={
            "integration_id": slack_integration["id"],
            "source_text": "x",
            "vcs_ref": {"type": "commit", "id": "s"},
        },
        headers=auth_headers(world),
    )
    assert resolve.status_code == 422


async def test_deliveries_list_state_filter(app_client):
    client, _ = app_client
    world = await make_world(client, "dfilter")
    subscription = (
        await client.post(
            f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
            json={"url": "https://hooks.example.com/f"},
            headers=auth_headers(world),
        )
    ).json()["data"]
    filtered = await client.get(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription['id']}/deliveries",
        params={"state": "failed"},
        headers=auth_headers(world),
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"] == []
    listed = await client.get(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        params={"status": "active"},
        headers=auth_headers(world),
    )
    assert listed.status_code == 200
    assert any(s["id"] == subscription["id"] for s in listed.json()["data"])


async def test_vcs_link_on_foreign_integration_404(app_client):
    client, _ = app_client
    world = await make_world(client, "forvcs")
    issue = (
        await client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "i"},
            headers=auth_headers(world),
        )
    ).json()["data"]
    link = await client.post(
        "/api/v1/integrations/vcs/links",
        json={
            "integration_id": str(uuid.uuid4()),
            "vcs_ref": {"type": "pull_request", "id": "a/b#1"},
            "issue_id": issue["id"],
        },
        headers=auth_headers(world),
    )
    assert link.status_code == 404
    links = await client.get(f"/api/v1/issues/{uuid.uuid4()}/vcs-links", headers=auth_headers(world))
    assert links.status_code == 404
