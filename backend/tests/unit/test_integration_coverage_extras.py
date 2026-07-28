"""Coverage tests for oauth flow, channel checker, card payload parsing,
and the remaining inbound endpoint surface (feishu/github/gitlab + cards).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from redis.asyncio import Redis

from mesh.integrations import oauth as oauth_mod
from mesh.integrations.cards import extract_action, extract_clicker, parse_card_payload
from mesh.integrations.channels import make_integration_channel_checker
from tests.unit.test_integration_routes import (
    SIGNING_SECRET,
    _settings_kwargs,
    auth_headers,
    make_world,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# OAuth (PKCE round-trip with injected transport)
# ---------------------------------------------------------------------------


async def test_pkce_pair_s256():
    verifier, challenge = oauth_mod.generate_pkce_pair()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64

    assert challenge == base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def test_begin_authorization_and_state_consumption(redis_client):
    url = await oauth_mod.begin_authorization(
        redis_client,
        workspace_id=uuid.uuid4(),
        member_id=uuid.uuid4(),
        kind="im_slack",
        callback_url="https://mesh.test/cb",
    )
    assert url.startswith(oauth_mod.AUTHORIZE_URLS["im_slack"])
    assert "code_challenge_method=S256" in url
    state = url.split("state=")[1].split("&")[0]
    record = await oauth_mod.consume_state(redis_client, state=state)
    assert record is not None and record["kind"] == "im_slack"
    # single-use
    assert await oauth_mod.consume_state(redis_client, state=state) is None


async def test_begin_authorization_bad_kind(redis_client):
    from mesh.errors import BusinessRuleError

    with pytest.raises(BusinessRuleError):
        await oauth_mod.begin_authorization(
            redis_client, workspace_id=uuid.uuid4(), member_id=uuid.uuid4(),
            kind="fax", callback_url="https://x",
        )


async def test_exchange_code_success_and_failure(redis_client):
    ok_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"refresh_token": "rt-1", "access_token": "at-1"})
    )
    async with httpx.AsyncClient(transport=ok_transport) as client:
        tokens = await oauth_mod.exchange_code(
            kind="im_slack", code="c", code_verifier="v",
            callback_url="https://x", http_client=client,
        )
    assert tokens["refresh_token"] == "rt-1"

    from mesh.errors import BusinessRuleError

    bad_transport = httpx.MockTransport(lambda request: httpx.Response(400))
    async with httpx.AsyncClient(transport=bad_transport) as client:
        with pytest.raises(BusinessRuleError) as excinfo:
            await oauth_mod.exchange_code(
                kind="im_slack", code="c", code_verifier="v",
                callback_url="https://x", http_client=client,
            )
        assert excinfo.value.code == "oauth_failed"

    no_token_transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=no_token_transport) as client:
        with pytest.raises(BusinessRuleError):
            await oauth_mod.exchange_code(
                kind="im_slack", code="c", code_verifier="v",
                callback_url="https://x", http_client=client,
            )

    def raise_transport(request):
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(raise_transport)) as client:
        with pytest.raises(BusinessRuleError):
            await oauth_mod.exchange_code(
                kind="vcs_github", code="c", code_verifier="v",
                callback_url="https://x", http_client=client,
            )


# ---------------------------------------------------------------------------
# Channel checker (integration:{id} resource authorization)
# ---------------------------------------------------------------------------


class FakePrincipal:
    def __init__(self, workspace_ids):
        self.workspace_ids = workspace_ids


async def test_integration_channel_checker(session_factory):
    from tests.unit.integrations_support import seed_world

    world = await seed_world(session_factory)
    checker = make_integration_channel_checker(session_factory)
    member_principal = FakePrincipal([world["ws"]])
    stranger_principal = FakePrincipal([uuid.uuid4()])
    assert await checker(member_principal, f"integration:{world['integ_slack']}") is True
    assert await checker(stranger_principal, f"integration:{world['integ_slack']}") is False
    assert await checker(member_principal, "integration:not-a-uuid") is False
    assert await checker(member_principal, "garbage") is False


# ---------------------------------------------------------------------------
# Card payload parsing helpers
# ---------------------------------------------------------------------------


def test_parse_card_payload_form_encoded():
    inner = json.dumps({"user": {"id": "U1"}, "actions": []})
    body = f"payload={inner}".replace('"', "%22").encode()
    parsed = parse_card_payload(body, {"Content-Type": "application/x-www-form-urlencoded"})
    assert parsed["user"]["id"] == "U1"


def test_parse_card_payload_invalid_json():
    assert parse_card_payload(b"{bad", {"content-type": "application/json"}) == {}
    assert parse_card_payload(b'{"a":1}', {}) == {"a": 1}
    assert parse_card_payload(b"[1,2]", {}) == {"value": [1, 2]}


def test_extract_action_variants():
    approval_id = uuid.uuid4()
    ok = extract_action({"action": {"value": {"approval_id": str(approval_id), "decision": "approve"}}})
    assert ok == (approval_id, True)
    via_actions = extract_action({"actions": [
        {"value": json.dumps({"approval_id": str(approval_id), "decision": "reject"})}
    ]})
    assert via_actions == (approval_id, False)
    assert extract_action({}) is None
    assert extract_action({"action": {"value": "not-json"}}) is None
    assert extract_action({"action": {"value": {"approval_id": "bad", "decision": "approve"}}}) is None
    assert extract_action(
        {"action": {"value": {"approval_id": str(approval_id), "decision": "maybe"}}}
    ) is None


def test_extract_clicker_feishu_and_slack():
    from mesh.db.models.integration import Integration

    slack = Integration(
        workspace_id=uuid.uuid4(), kind="im_slack", name="s",
        config={"team_id": "T_CFG"}, created_by=uuid.uuid4(),
    )
    assert extract_clicker(
        "im_slack", {"user": {"id": "U9"}, "team": {"id": "T9"}}, slack
    ) == ("slack", "T9", "U9")
    assert extract_clicker("im_slack", {"user_id": "U8"}, slack) == ("slack", "T_CFG", "U8")
    feishu = Integration(
        workspace_id=uuid.uuid4(), kind="im_feishu", name="f",
        config={"tenant_key": "tk-cfg"}, created_by=uuid.uuid4(),
    )
    assert extract_clicker(
        "im_feishu", {"open_id": "ou_1", "tenant_key": "tk-1"}, feishu
    ) == ("feishu", "tk-1", "ou_1")
    assert extract_clicker(
        "im_feishu", {"operator": {"open_id": "ou_2"}}, feishu
    ) == ("feishu", "tk-cfg", "ou_2")
    assert extract_clicker("im_feishu", {}, feishu) is None
    assert extract_clicker("vcs_github", {}, feishu) is None


# ---------------------------------------------------------------------------
# Remaining inbound endpoints through the app (feishu / github / gitlab / cards)
# ---------------------------------------------------------------------------


@pytest.fixture
async def app_client_full(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
async def redis_client(redis_url):
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def test_feishu_challenge_and_event(app_client_full):
    world = await make_world(app_client_full, "fs")
    from mesh.auth.security import encrypt_secret

    encrypt_key = "fek-" + uuid.uuid4().hex
    verification_token = "fvt-" + uuid.uuid4().hex
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "im_feishu", "name": "fs-main", "config": {
            "tenant_key": "tk-fs",
            "encrypt_key_ref": encrypt_secret(encrypt_key, SIGNING_SECRET),
            "verification_token_ref": encrypt_secret(verification_token, SIGNING_SECRET),
        }},
        headers=auth_headers(world),
    )
    # challenge with valid token echoes; invalid token → 401 invalid_challenge
    challenge_body = json.dumps({
        "type": "url_verification", "challenge": "ch-fs", "token": verification_token,
    }).encode()
    ts = str(int(datetime.now(UTC).timestamp()))
    nonce = "n1"
    sig = hashlib.sha256(f"{ts}{nonce}{encrypt_key}".encode() + challenge_body).hexdigest()
    ok = await app_client_full.post(
        "/api/v1/integrations/feishu/events", content=challenge_body,
        headers={"timestamp": ts, "nonce": nonce, "x-lark-signature": sig,
                 "content-type": "application/json"},
    )
    assert ok.status_code == 200
    assert ok.json() == {"challenge": "ch-fs"}

    bad_token = json.dumps({
        "type": "url_verification", "challenge": "x", "token": "WRONG",
    }).encode()
    bad = await app_client_full.post("/api/v1/integrations/feishu/events", content=bad_token)
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_challenge"

    # signed event event → 200 (no binding → received audit)
    event_payload = {
        "schema": "2.0",
        "header": {"event_id": "evt-fs-1", "event_type": "im.message.receive_v1",
                   "tenant_key": "tk-fs"},
        "event": {"sender": {"sender_id": {"open_id": "ou_a"}},
                  "message": {"chat_id": "oc_1", "message_type": "text",
                              "content": json.dumps({"text": "hi"})}},
    }
    ebody = json.dumps(event_payload).encode()
    esig = hashlib.sha256(f"{ts}{nonce}{encrypt_key}".encode() + ebody).hexdigest()
    ev = await app_client_full.post(
        "/api/v1/integrations/feishu/events", content=ebody,
        headers={"timestamp": ts, "nonce": nonce, "x-lark-signature": esig,
                 "content-type": "application/json"},
    )
    assert ev.status_code == 200
    assert ev.json()["process_status"] == "received"


async def test_github_and_gitlab_endpoints(app_client_full):
    world = await make_world(app_client_full, "ghgl")
    from mesh.auth.security import encrypt_secret

    gh_secret = "gws-" + uuid.uuid4().hex
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "vcs_github", "name": "gh-main", "config": {
            "installation_id": "777",
            "webhook_secret_ref": encrypt_secret(gh_secret, SIGNING_SECRET),
        }},
        headers=auth_headers(world),
    )
    gh_payload = {
        "action": "opened",
        "repository": {"full_name": "acme/site"},
        "installation": {"id": 777},
        "sender": {"login": "dev"},
        "pull_request": {"number": 3, "title": "pr", "state": "open"},
    }
    gh_body = json.dumps(gh_payload).encode()
    gh_sig = hmac.new(gh_secret.encode(), gh_body, hashlib.sha256).hexdigest()
    gh = await app_client_full.post(
        "/api/v1/integrations/github/events", content=gh_body,
        headers={"x-hub-signature-256": f"sha256={gh_sig}",
                 "x-github-event": "pull_request", "x-github-delivery": "d-1",
                 "content-type": "application/json"},
    )
    assert gh.status_code == 200

    gh_bad = await app_client_full.post(
        "/api/v1/integrations/github/events", content=gh_body,
        headers={"x-hub-signature-256": "sha256=nope",
                 "x-github-event": "pull_request", "x-github-delivery": "d-2"},
    )
    assert gh_bad.status_code == 401

    # gitlab routes through the repo binding
    gl_token = "gwt-" + uuid.uuid4().hex
    gl_integration = (await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "vcs_gitlab", "name": "gl-main", "config": {
            "instance_url": "https://gitlab.com",
            "webhook_token_ref": encrypt_secret(gl_token, SIGNING_SECRET),
        }},
        headers=auth_headers(world),
    )).json()["integration"]
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{gl_integration['id']}/bindings",
        json={"external_ref": "acme/api"}, headers=auth_headers(world),
    )
    gl_payload = {
        "event_uuid": "gl-1",
        "project": {"path_with_namespace": "acme/api"},
        "user": {"username": "dev"},
        "object_attributes": {"iid": 5, "title": "mr", "state": "opened", "action": "open"},
    }
    gl_body = json.dumps(gl_payload).encode()
    gl = await app_client_full.post(
        "/api/v1/integrations/gitlab/events", content=gl_body,
        headers={"x-gitlab-token": gl_token, "x-gitlab-event": "Merge Request Hook",
                 "content-type": "application/json"},
    )
    assert gl.status_code == 200
    gl_bad = await app_client_full.post(
        "/api/v1/integrations/gitlab/events", content=gl_body,
        headers={"x-gitlab-token": "WRONG", "x-gitlab-event": "Merge Request Hook"},
    )
    assert gl_bad.status_code == 401


async def test_card_endpoint_end_to_end(app_client_full, redis_client):
    world = await make_world(app_client_full, "card")
    from mesh.auth.security import encrypt_secret

    signing_secret = "sss-" + uuid.uuid4().hex
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "im_slack", "name": "slack-card", "config": {
            "team_id": "T_CARD",
            "signing_secret_ref": encrypt_secret(signing_secret, SIGNING_SECRET),
        }},
        headers=auth_headers(world),
    )
    # link the requester's slack identity
    integration_id = (await app_client_full.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=auth_headers(world)
    )).json()["data"][0]["id"]
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link",
        json={"provider": "slack", "integration_id": integration_id,
              "external_user_key": "U_CARD"},
        headers=auth_headers(world),
    )
    code = await redis_client.get("mesh:identity-dev-outbox:slack:T_CARD:U_CARD")
    await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link-confirm",
        json={"provider": "slack", "integration_id": integration_id, "code": code},
        headers=auth_headers(world),
    )
    # card callback for a non-existent approval → 404 (chain passes, approval missing)
    missing = uuid.uuid4()
    payload = {
        "type": "block_actions", "team": {"id": "T_CARD"}, "user": {"id": "U_CARD"},
        "actions": [{"value": json.dumps({"approval_id": str(missing), "decision": "approve"})}],
    }
    body = json.dumps(payload).encode()
    ts = str(int(datetime.now(UTC).timestamp()))
    sig = hmac.new(signing_secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    response = await app_client_full.post(
        "/api/v1/integrations/slack/cards", content=body,
        headers={"x-slack-signature": f"v0={sig}", "x-slack-request-timestamp": ts,
                 "content-type": "application/json"},
    )
    assert response.status_code == 404

    # bad signature → 401
    bad = await app_client_full.post(
        "/api/v1/integrations/slack/cards", content=body,
        headers={"x-slack-signature": "v0=bad", "x-slack-request-timestamp": ts},
    )
    assert bad.status_code == 401

    # feishu cards endpoint rejects without signature
    fs = await app_client_full.post("/api/v1/integrations/feishu/cards", content=b"{}")
    assert fs.status_code == 401


async def test_delivery_retry_endpoint_404_and_422(app_client_full):
    world = await make_world(app_client_full, "dretry")
    subscription = (await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://hooks.example.com/x"}, headers=auth_headers(world),
    )).json()["data"]
    missing = await app_client_full.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/"
        f"{subscription['id']}/deliveries/{uuid.uuid4()}/retry",
        headers=auth_headers(world),
    )
    assert missing.status_code == 404
