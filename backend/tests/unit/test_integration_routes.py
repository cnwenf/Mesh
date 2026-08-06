"""In-process route coverage for the integrations HTTP surface (§3.1-§3.6).

Drives the REAL FastAPI app through httpx ASGITransport: management CRUD,
RBAC gates, the bare-JSON inbound contract, external-identity link flow
(code read from the Redis dev outbox — exactly where the external account's
DM would land), VCS link endpoints, subscriptions incl. one-time secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
SIGNING_SECRET = "integration-routes-signing-secret-0000000"


def _settings_kwargs(db_url: str, redis_url: str, **overrides) -> dict:
    base = {
        "database_url": db_url,
        "app_database_url": db_url.replace("mesh:mesh@", "mesh_app:mesh_app@"),
        "redis_url": redis_url,
        "auth_mode": "dev",
        "jwt_secret": SIGNING_SECRET,
        "daemon_tls_required": False,
        "storage_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        "storage_public_endpoint": os.environ.get("MESH_TEST_STORAGE_ENDPOINT", "http://127.0.0.1:9100"),
        "storage_access_key": os.environ.get("MESH_STORAGE_ACCESS_KEY", "mesh"),
        "storage_secret_key": os.environ.get("MESH_STORAGE_SECRET_KEY", "mesh_minio_secret"),
        "storage_bucket": "mesh-integration-routes-test",
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def app_client(db_url, redis_url):
    from mesh.api.app import create_app
    from mesh.config import load_settings

    app = create_app(load_settings(**_settings_kwargs(db_url, redis_url)))

    async def valid_dingtalk_credentials(_config, _secret):
        return "healthy", None

    app.state.integration_service._dingtalk_credential_verifier = valid_dingtalk_credentials
    try:
        await app.state.storage.ensure_bucket()
    except Exception:  # noqa: BLE001 — storage optional in unit context
        pass
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def redis_client(redis_url):
    client = Redis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def make_world(client: httpx.AsyncClient, suffix: str) -> dict:
    email = f"intg-routes-{suffix}@example.com"
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Routes-Test-12345", "display_name": "INTG Routes"},
    )
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": "Routes-Test-12345"})
    token = login.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    ws = (
        await client.post(
            "/api/v1/workspaces",
            json={"name": f"INTG {suffix}", "slug": f"intg-routes-{suffix}"},
            headers=headers,
        )
    ).json()["data"]
    agent = (
        await client.post(
            f"/api/v1/workspaces/{ws['id']}/agents",
            json={"name": f"intg-agent-{suffix}"},
            headers=headers,
        )
    ).json()["data"]
    members = (await client.get(f"/api/v1/workspaces/{ws['id']}/members", headers=headers)).json()["data"]
    human_member = next(m for m in members if m.get("member_type") == "human")
    return {
        "token": token,
        "headers": headers,
        "ws_id": ws["id"],
        "agent_id": agent["id"],
        "member_id": human_member["id"],
        "email": email,
    }


def auth_headers(world: dict) -> dict:
    return world["headers"]


# ---------------------------------------------------------------------------
# Integrations CRUD + RBAC
# ---------------------------------------------------------------------------


async def test_integration_crud_and_secret_contract(app_client):
    world = await make_world(app_client, "crud")
    created = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_slack",
            "name": "slack-main",
            "config": {"team_id": "T0X"},
            "secret": "xoxb-secret-plain",
        },
        headers=auth_headers(world),
    )
    assert created.status_code == 201
    data = created.json()
    # MEDIUM-4: the create endpoint wears the §6.14 {"data"} envelope; the
    # payload carries the rendered integration + the secret_accepted flag.
    assert data["data"]["secret_accepted"] is True
    integration = data["data"]["integration"]
    assert integration["has_secret"] is True
    assert "xoxb-secret-plain" not in json.dumps(data), "secret never echoed (§6.16)"

    listed = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=auth_headers(world)
    )
    assert listed.status_code == 200
    assert any(i["id"] == integration["id"] for i in listed.json()["data"])

    got = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}",
        headers=auth_headers(world),
    )
    assert got.status_code == 200
    assert "secret_ref" not in json.dumps(got.json())

    patched = await app_client.patch(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}",
        json={"status": "disabled"},
        headers=auth_headers(world),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["status"] == "disabled"

    rotated = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/rotate-secret",
        json={"secret": "new-secret"},
        headers=auth_headers(world),
    )
    assert rotated.status_code == 200

    deleted = await app_client.delete(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}",
        headers=auth_headers(world),
    )
    assert deleted.status_code == 204
    gone = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}",
        headers=auth_headers(world),
    )
    assert gone.status_code == 404


async def test_integration_create_validation_and_rbac(app_client, session_factory):
    world = await make_world(app_client, "rbac")
    bad_kind = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "smoke_signal", "name": "x"},
        headers=auth_headers(world),
    )
    assert bad_kind.status_code == 422
    secret_in_config = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "im_slack", "name": "y", "config": {"signing_secret": "plain"}},
        headers=auth_headers(world),
    )
    assert secret_in_config.status_code == 422

    # A plain (non-admin) member cannot write but can read.
    email2 = f"intg-member-{uuid.uuid4().hex[:6]}@example.com"
    await app_client.post(
        "/api/v1/auth/register",
        json={"email": email2, "password": "Routes-Test-12345", "display_name": "M2"},
    )
    token2 = (
        await app_client.post("/api/v1/auth/login", json={"email": email2, "password": "Routes-Test-12345"})
    ).json()["data"]["access_token"]
    from sqlalchemy import select

    from mesh.db.models.member import Member
    from mesh.db.models.user import User

    async with session_factory() as session, session.begin():
        user2 = await session.scalar(select(User).where(User.email == email2))
        session.add(
            Member(
                workspace_id=uuid.UUID(world["ws_id"]),
                member_type="human",
                user_id=user2.id,
                role="member",
                status="active",
            )
        )
    forbidden = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={"kind": "im_slack", "name": "z"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert forbidden.status_code == 403
    readable = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert readable.status_code == 200


async def test_dingtalk_api_rejects_config_that_bypasses_app_proof(app_client):
    world = await make_world(app_client, "dt-config")
    invalid_configs = [
        {"corp_id": "dingcorp", "receive_mode": "stream"},
        {"app_key": "dingapp", "receive_mode": "stream"},
        {"app_key": "dingapp", "corp_id": "dingcorp", "receive_mode": "socket"},
        {
            "app_key": "dingapp",
            "corp_id": "dingcorp",
            "receive_mode": "stream",
            "gateway_base": "https://attacker.example",
        },
        {
            "app_key": "dingapp",
            "corp_id": "dingcorp",
            "receive_mode": "stream",
            "app_secret_ref": "encrypted-bypass",
        },
    ]
    for index, config in enumerate(invalid_configs):
        response = await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={
                "kind": "im_dingtalk",
                "name": f"invalid-dingtalk-{index}",
                "config": config,
                "secret": "must-never-be-probed",
            },
            headers=auth_headers(world),
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_request"

    listed = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        headers=auth_headers(world),
    )
    assert listed.status_code == 200
    assert listed.json()["data"] == []


async def test_dingtalk_stream_reconnect_route_is_durable_and_manager_only(app_client, session_factory):
    from sqlalchemy import select

    from mesh.db.models.integration import Integration
    from mesh.db.models.member import Member
    from mesh.db.models.outbox import OutboxEvent
    from mesh.db.models.user import User

    world = await make_world(app_client, "dingtalk-reconnect")
    created = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_dingtalk",
            "name": "dingtalk-stream-reconnect",
            "config": {
                "app_key": "ding-reconnect-app-key",
                "corp_id": "ding-reconnect-corp",
                "robot_code": "ding-reconnect-robot",
                "receive_mode": "stream",
            },
            "secret": "dingtalk-reconnect-test-secret",
        },
        headers=auth_headers(world),
    )
    assert created.status_code == 201, created.text
    integration_id = created.json()["data"]["integration"]["id"]

    member_email = f"dingtalk-reconnect-member-{uuid.uuid4().hex[:8]}@example.com"
    await app_client.post(
        "/api/v1/auth/register",
        json={
            "email": member_email,
            "password": "Routes-Test-12345",
            "display_name": "Reconnect reader",
        },
    )
    member_token = (
        await app_client.post(
            "/api/v1/auth/login",
            json={"email": member_email, "password": "Routes-Test-12345"},
        )
    ).json()["data"]["access_token"]
    async with session_factory() as session, session.begin():
        member_user = await session.scalar(select(User).where(User.email == member_email))
        session.add(
            Member(
                workspace_id=uuid.UUID(world["ws_id"]),
                member_type="human",
                user_id=member_user.id,
                role="member",
                status="active",
            )
        )

    forbidden = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}:reconnect",
        json={},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert forbidden.status_code == 403

    accepted = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration_id}:reconnect",
        json={},
        headers=auth_headers(world),
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json() == {"data": {"accepted": True}}
    assert accepted.headers["X-RateLimit-Limit"] == "120"

    async with session_factory() as session:
        integration = await session.get(Integration, uuid.UUID(integration_id))
        events = (
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.workspace_id == uuid.UUID(world["ws_id"]),
                        OutboxEvent.event_type == "realtime.publish",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert integration.stream_state["state"] == "reconnecting"
    assert integration.stream_state["backoff_seconds"] == 0
    assert uuid.UUID(integration.stream_state["reconnect_request_id"])
    assert any(
        event.payload["data"].get("subject") == "stream_channel"
        and event.payload["data"].get("stream_state") == "reconnecting"
        for event in events
    )


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


async def test_binding_crud_and_conflict(app_client):
    world = await make_world(app_client, "bind")
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-b", "config": {"team_id": "T_B"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    ok = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        json={
            "external_ref": "C_ROOM",
            "match_config": {"trigger_on": ["mention"]},
            "bound_agent_id": world["agent_id"],
        },
        headers=auth_headers(world),
    )
    assert ok.status_code == 201
    binding = ok.json()["data"]
    assert binding["provider"] == "slack"
    assert binding["provider_tenant_key"] == "T_B"

    conflict = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        json={"external_ref": "C_ROOM"},
        headers=auth_headers(world),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "binding_conflict"

    xor = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        json={"external_ref": "C_X", "scope": "project"},
        headers=auth_headers(world),
    )
    assert xor.status_code in (400, 422)

    listed = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        headers=auth_headers(world),
    )
    assert len(listed.json()["data"]) == 1

    patched = await app_client.patch(
        f"/api/v1/workspaces/{world['ws_id']}/integration-bindings/{binding['id']}",
        json={"status": "disabled"},
        headers=auth_headers(world),
    )
    assert patched.status_code == 200

    deleted = await app_client.delete(
        f"/api/v1/workspaces/{world['ws_id']}/integration-bindings/{binding['id']}",
        headers=auth_headers(world),
    )
    assert deleted.status_code == 204


# ---------------------------------------------------------------------------
# Inbound endpoints through the real app
# ---------------------------------------------------------------------------


async def test_inbound_slack_event_and_bad_signature(app_client):
    world = await make_world(app_client, "inb")
    signing_secret = "sss-" + uuid.uuid4().hex
    from mesh.auth.security import encrypt_secret

    await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/integrations",
        json={
            "kind": "im_slack",
            "name": "slack-inb",
            "config": {
                "team_id": "T_INB",
                "signing_secret_ref": encrypt_secret(signing_secret, SIGNING_SECRET),
                "bot_user_id": "U_BOT",
            },
        },
        headers=auth_headers(world),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T_INB",
        "event": {"type": "message", "channel": "C_INB", "user": "U_H", "text": "hello", "event_ts": "1.1"},
    }
    body = json.dumps(payload).encode()
    ts = str(int(datetime.now(UTC).timestamp()))  # server verifies vs real time
    sig = hmac.new(signing_secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    good = await app_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers={
            "x-slack-signature": f"v0={sig}",
            "x-slack-request-timestamp": ts,
            "content-type": "application/json",
        },
    )
    assert good.status_code == 200
    assert good.json()["process_status"] == "received"  # no binding → audit only

    bad = await app_client.post(
        "/api/v1/integrations/slack/events",
        content=body,
        headers={
            "x-slack-signature": "v0=deadbeef",
            "x-slack-request-timestamp": ts,
            "content-type": "application/json",
        },
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_signature"

    # url_verification challenge echoes after signature verification.
    challenge_payload = {"type": "url_verification", "team_id": "T_INB", "challenge": "ch-1"}
    cbody = json.dumps(challenge_payload).encode()
    csig = hmac.new(signing_secret.encode(), f"v0:{ts}:".encode() + cbody, hashlib.sha256).hexdigest()
    challenge = await app_client.post(
        "/api/v1/integrations/slack/events",
        content=cbody,
        headers={
            "x-slack-signature": f"v0={csig}",
            "x-slack-request-timestamp": ts,
            "content-type": "application/json",
        },
    )
    assert challenge.status_code == 200
    assert challenge.json() == {"challenge": "ch-1"}


async def test_inbound_event_ledger_endpoint(app_client):
    world = await make_world(app_client, "ledg")
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-ledg", "config": {"team_id": "T_LEDG"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    events = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/events",
        headers=auth_headers(world),
    )
    assert events.status_code == 200
    assert events.json()["data"] == []


async def test_binding_and_event_reads_enforce_project_visibility_before_pagination(
    app_client, session_factory
):
    """P0: member reads never disclose a private project's external identity/payload.

    This is an API-layer regression over the real ASGI app + PostgreSQL. Hidden
    rows are deliberately interleaved with visible rows in ledger order so a
    render-time filter would produce short/empty pages and a leaking cursor.
    """
    from sqlalchemy import select

    from mesh.auth.security import encrypt_secret
    from mesh.db.models.integration import IntegrationEvent
    from mesh.db.models.member import Member, MemberProjectAccess
    from mesh.db.models.project import Project, ProjectMember
    from mesh.db.models.user import User
    from mesh.db.tenant import set_tenant_context

    world = await make_world(app_client, "project-read-rbac")
    member_email = f"integration-reader-{uuid.uuid4().hex[:8]}@example.com"
    await app_client.post(
        "/api/v1/auth/register",
        json={
            "email": member_email,
            "password": "Routes-Test-12345",
            "display_name": "Integration reader",
        },
    )
    member_token = (
        await app_client.post(
            "/api/v1/auth/login",
            json={"email": member_email, "password": "Routes-Test-12345"},
        )
    ).json()["data"]["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}
    guest_email = f"integration-guest-{uuid.uuid4().hex[:8]}@example.com"
    await app_client.post(
        "/api/v1/auth/register",
        json={
            "email": guest_email,
            "password": "Routes-Test-12345",
            "display_name": "Integration guest",
        },
    )
    guest_token = (
        await app_client.post(
            "/api/v1/auth/login",
            json={"email": guest_email, "password": "Routes-Test-12345"},
        )
    ).json()["data"]["access_token"]
    guest_headers = {"Authorization": f"Bearer {guest_token}"}

    workspace_id = uuid.UUID(world["ws_id"])
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        member_user = await session.scalar(select(User).where(User.email == member_email))
        guest_user = await session.scalar(select(User).where(User.email == guest_email))
        member = Member(
            workspace_id=workspace_id,
            member_type="human",
            user_id=member_user.id,
            role="member",
            status="active",
        )
        guest = Member(
            workspace_id=workspace_id,
            member_type="human",
            user_id=guest_user.id,
            role="guest",
            status="active",
        )
        session.add_all([member, guest])
        await session.flush()
        projects = {
            "public": Project(
                workspace_id=workspace_id,
                name="Public integration project",
                key=f"IP{uuid.uuid4().hex[:5].upper()}",
                visibility="public",
            ),
            "visible_private": Project(
                workspace_id=workspace_id,
                name="Visible private integration project",
                key=f"IV{uuid.uuid4().hex[:5].upper()}",
                visibility="private",
            ),
            "hidden_private": Project(
                workspace_id=workspace_id,
                name="Hidden private integration project",
                key=f"IH{uuid.uuid4().hex[:5].upper()}",
                visibility="private",
            ),
            "deleted_visible": Project(
                workspace_id=workspace_id,
                name="Deleted visible integration project",
                key=f"ID{uuid.uuid4().hex[:5].upper()}",
                visibility="private",
            ),
        }
        session.add_all(projects.values())
        await session.flush()
        for project_name in ("visible_private", "deleted_visible"):
            session.add(
                ProjectMember(
                    workspace_id=workspace_id,
                    project_id=projects[project_name].id,
                    member_id=member.id,
                    role="member",
                )
            )
            session.add(
                MemberProjectAccess(
                    workspace_id=workspace_id,
                    project_id=projects[project_name].id,
                    member_id=guest.id,
                    permission="read",
                )
            )

    signing_secret = "visibility-secret-" + uuid.uuid4().hex
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={
                "kind": "im_slack",
                "name": "project visibility ledger",
                "config": {
                    "team_id": "T_VISIBILITY",
                    "signing_secret_ref": encrypt_secret(signing_secret, SIGNING_SECRET),
                },
            },
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    channels = {
        "workspace": "C_WS_VISIBLE",
        "public": "C_PUBLIC_VISIBLE",
        "visible_private": "C_PRIVATE_VISIBLE",
        "hidden_private": "C_PRIVATE_HIDDEN",
        "deleted_visible": "C_PRIVATE_DELETED",
    }
    bindings: dict[str, dict] = {}
    for scope_name, external_ref in channels.items():
        project = projects.get(scope_name)
        response = await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
            json={
                "external_ref": external_ref,
                "scope": "project" if project is not None else "workspace",
                **({"project_id": str(project.id)} if project is not None else {}),
            },
            headers=auth_headers(world),
        )
        assert response.status_code == 201, response.text
        bindings[scope_name] = response.json()["data"]

    event_specs = [
        ("hidden-new", "hidden_private", "PRIVATE PAYLOAD NEW"),
        ("workspace-new", "workspace", "workspace payload new"),
        ("hidden-old", "hidden_private", "PRIVATE PAYLOAD OLD"),
        ("public", "public", "public project payload"),
        ("private-visible", "visible_private", "visible private payload"),
        ("deleted-visible", "deleted_visible", "DELETED PROJECT PAYLOAD"),
        ("workspace-old", "workspace", "workspace payload old"),
    ]
    timestamp = str(int(datetime.now(UTC).timestamp()))
    for index, (_event_id, scope_name, sentinel) in enumerate(event_specs, start=1):
        payload = {
            "type": "event_callback",
            "team_id": "T_VISIBILITY",
            "event": {
                "type": "message",
                "channel": channels[scope_name],
                "user": "U_READER",
                "text": sentinel,
                "event_ts": f"{index}.0001",
            },
        }
        body = json.dumps(payload).encode()
        signature = hmac.new(
            signing_secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256
        ).hexdigest()
        received = await app_client.post(
            "/api/v1/integrations/slack/events",
            content=body,
            headers={
                "x-slack-signature": f"v0={signature}",
                "x-slack-request-timestamp": timestamp,
                "content-type": "application/json",
            },
        )
        assert received.status_code == 200, received.text
        assert received.json()["event_id"] == f"T_VISIBILITY:{index}.0001"

    # A valid but unmatched event has no trustworthy authorization source.
    # It is snapshotted as unknown and therefore remains an owner/admin-only
    # audit row rather than being inferred from attacker-controlled payload.
    unknown_sentinel = "UNMATCHED ADMIN ONLY"
    unknown_payload = {
        "type": "event_callback",
        "team_id": "T_VISIBILITY",
        "event": {
            "type": "message",
            "channel": "C_UNMATCHED_ADMIN_ONLY",
            "user": "U_READER",
            "text": unknown_sentinel,
            "event_ts": "8.0001",
        },
    }
    unknown_body = json.dumps(unknown_payload).encode()
    unknown_signature = hmac.new(
        signing_secret.encode(),
        f"v0:{timestamp}:".encode() + unknown_body,
        hashlib.sha256,
    ).hexdigest()
    unknown_received = await app_client.post(
        "/api/v1/integrations/slack/events",
        content=unknown_body,
        headers={
            "x-slack-signature": f"v0={unknown_signature}",
            "x-slack-request-timestamp": timestamp,
            "content-type": "application/json",
        },
    )
    assert unknown_received.status_code == 200, unknown_received.text
    assert unknown_received.json()["event_id"] == "T_VISIBILITY:8.0001"

    # Make the hidden/visible rows deterministically interleave in descending
    # keyset order, independent of request wall-clock precision.
    # Keep the deterministic interleaving inside the route's real rolling
    # seven-day aggregate window. A fixed calendar instant makes this test
    # expire as wall time advances even though the visibility contract is
    # unchanged.
    base_time = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        rows = (
            (
                await session.execute(
                    select(IntegrationEvent).where(
                        IntegrationEvent.integration_id == uuid.UUID(integration["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        by_text = {row.payload["event"]["text"]: row for row in rows}
        for offset, (_event_id, _scope_name, sentinel) in enumerate(event_specs):
            by_text[sentinel].received_at = base_time - timedelta(minutes=offset)
        by_text[unknown_sentinel].received_at = base_time - timedelta(minutes=len(event_specs))
        # Snapshot events survive a later project deletion, but a deleted
        # project is no longer a visibility grant for non-managers.
        deleted_project = await session.get(Project, projects["deleted_visible"].id)
        assert deleted_project is not None
        deleted_project.deleted_at = base_time

    member_bindings = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        headers=member_headers,
    )
    assert member_bindings.status_code == 200
    member_refs = {item["external_ref"] for item in member_bindings.json()["data"]}
    assert member_refs == {
        channels["workspace"],
        channels["public"],
        channels["visible_private"],
    }
    assert channels["hidden_private"] not in json.dumps(member_bindings.json())
    assert channels["deleted_visible"] not in json.dumps(member_bindings.json())

    visible_events: list[dict] = []
    cursor: str | None = None
    page_count = 0
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        response = await app_client.get(
            f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/events",
            params=params,
            headers=member_headers,
        )
        assert response.status_code == 200, response.text
        page_count += 1
        page = response.json()
        assert len(page["data"]) == 2, "SQL filtering must fill each authorized page"
        visible_events.extend(page["data"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert page_count == 2
    assert {item["payload"]["event"]["text"] for item in visible_events} == {
        "workspace payload new",
        "public project payload",
        "visible private payload",
        "workspace payload old",
    }
    member_event_json = json.dumps(visible_events)
    assert "PRIVATE PAYLOAD" not in member_event_json
    assert "DELETED PROJECT PAYLOAD" not in member_event_json
    assert channels["hidden_private"] not in member_event_json
    assert unknown_sentinel not in member_event_json

    # The integration-list aggregate is authorization-filtered too: a plain
    # member sees four events, not hidden/unknown/deleted-project snapshots.
    member_integrations = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=member_headers
    )
    member_integration = next(
        item for item in member_integrations.json()["data"] if item["id"] == integration["id"]
    )
    assert member_integration["events_7d"] == 4

    # Guests do not inherit public-project visibility; they see only
    # workspace scope plus projects explicitly shared through access grants.
    guest_bindings = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        headers=guest_headers,
    )
    assert {item["external_ref"] for item in guest_bindings.json()["data"]} == {
        channels["workspace"],
        channels["visible_private"],
    }
    guest_events = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/events",
        params={"limit": 200},
        headers=guest_headers,
    )
    assert {item["payload"]["event"]["text"] for item in guest_events.json()["data"]} == {
        "workspace payload new",
        "visible private payload",
        "workspace payload old",
    }
    guest_integrations = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=guest_headers
    )
    guest_integration = next(
        item for item in guest_integrations.json()["data"] if item["id"] == integration["id"]
    )
    assert guest_integration["events_7d"] == 3

    # Owner/admin retains the complete operational view.
    owner_bindings = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/bindings",
        headers=auth_headers(world),
    )
    assert {item["id"] for item in owner_bindings.json()["data"]} == {
        item["id"] for item in bindings.values()
    }
    owner_events = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/{integration['id']}/events",
        params={"limit": 200},
        headers=auth_headers(world),
    )
    assert len(owner_events.json()["data"]) == 8
    owner_integrations = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations", headers=auth_headers(world)
    )
    owner_integration = next(
        item for item in owner_integrations.json()["data"] if item["id"] == integration["id"]
    )
    assert owner_integration["events_7d"] == 8


# ---------------------------------------------------------------------------
# External identities (dev outbox = the external account's DM)
# ---------------------------------------------------------------------------


async def test_identity_link_confirm_unlink_flow(app_client, redis_client):
    world = await make_world(app_client, "ident")
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-id", "config": {"team_id": "T_ID"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    link = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link",
        json={"provider": "slack", "integration_id": integration["id"], "external_user_key": "U_LINKER"},
        headers=auth_headers(world),
    )
    assert link.status_code == 200
    assert "code" not in link.json()["data"]
    code = await redis_client.get("mesh:identity-dev-outbox:slack:T_ID:U_LINKER")
    assert code is not None, "code delivered to the external account (dev outbox)"

    confirm = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link-confirm",
        json={"provider": "slack", "integration_id": integration["id"], "code": code},
        headers=auth_headers(world),
    )
    assert confirm.status_code == 200
    identity = confirm.json()["data"]

    listed = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities",
        headers=auth_headers(world),
    )
    assert [i["id"] for i in listed.json()["data"]] == [identity["id"]]

    # Another user cannot unlink it (owner-only, no admin bypass).
    world2 = await make_world(app_client, "ident2")
    forbidden = await app_client.delete(
        f"/api/v1/workspaces/{world2['ws_id']}/external-identities/{identity['id']}",
        headers=auth_headers(world2),
    )
    assert forbidden.status_code in (403, 404)

    unlinked = await app_client.delete(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities/{identity['id']}",
        headers=auth_headers(world),
    )
    assert unlinked.status_code == 204


async def test_identity_confirm_wrong_code(app_client, redis_client):
    world = await make_world(app_client, "idc")
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "im_slack", "name": "slack-idc", "config": {"team_id": "T_IDC"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link",
        json={"provider": "slack", "integration_id": integration["id"], "external_user_key": "U_X"},
        headers=auth_headers(world),
    )
    bad = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/external-identities:link-confirm",
        json={"provider": "slack", "integration_id": integration["id"], "code": "999999"},
        headers=auth_headers(world),
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------------------
# Webhook subscriptions
# ---------------------------------------------------------------------------


async def test_subscription_crud_one_time_secret(app_client):
    world = await make_world(app_client, "sub")
    created = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://hooks.example.com/mesh", "event_types": ["issue.updated"]},
        headers=auth_headers(world),
    )
    assert created.status_code == 201
    data = created.json()["data"]
    assert data["secret"].startswith("whsec_"), "secret shown exactly once at create"
    subscription_id = data["id"]

    got = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}",
        headers=auth_headers(world),
    )
    assert "secret" not in got.json()["data"], "secret never echoed again (§6.16)"

    http_url = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "http://insecure.example.com/x"},
        headers=auth_headers(world),
    )
    assert http_url.status_code == 400
    assert http_url.json()["error"]["code"] == "invalid_url_scheme"

    ssrf = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions",
        json={"url": "https://169.254.169.254/meta"},
        headers=auth_headers(world),
    )
    assert ssrf.status_code == 422
    assert ssrf.json()["error"]["code"] == "ssrf_blocked"

    deliveries = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}/deliveries",
        headers=auth_headers(world),
    )
    assert deliveries.status_code == 200

    resumed = await app_client.post(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}/resume",
        headers=auth_headers(world),
    )
    assert resumed.status_code == 200

    patched = await app_client.patch(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}",
        json={"status": "paused"},
        headers=auth_headers(world),
    )
    assert patched.status_code == 200

    deleted = await app_client.delete(
        f"/api/v1/workspaces/{world['ws_id']}/webhook-subscriptions/{subscription_id}",
        headers=auth_headers(world),
    )
    assert deleted.status_code == 204


# ---------------------------------------------------------------------------
# VCS links
# ---------------------------------------------------------------------------


async def test_vcs_link_endpoints(app_client):
    world = await make_world(app_client, "vcs")
    integration = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/integrations",
            json={"kind": "vcs_github", "name": "gh-vcs", "config": {"installation_id": "42"}},
            headers=auth_headers(world),
        )
    ).json()["data"]["integration"]
    issue = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "link me"},
            headers=auth_headers(world),
        )
    ).json()["data"]

    link = await app_client.post(
        "/api/v1/integrations/vcs/links",
        json={
            "integration_id": integration["id"],
            "vcs_ref": {"type": "pull_request", "id": "acme/web#5"},
            "mesh_entity_type": "issue",
            "issue_id": issue["id"],
        },
        headers=auth_headers(world),
    )
    assert link.status_code == 201
    # LOW-1/§4.2: the rendered link carries a clickable deep link.
    assert link.json()["data"]["url"] == "https://github.com/acme/web/pull/5"

    listed = await app_client.get(f"/api/v1/issues/{issue['id']}/vcs-links", headers=auth_headers(world))
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1

    # LOW-1: claiming an external object already linked to ANOTHER issue → 409.
    issue2 = (
        await app_client.post(
            f"/api/v1/workspaces/{world['ws_id']}/issues",
            json={"title": "steal the link"},
            headers=auth_headers(world),
        )
    ).json()["data"]
    conflict = await app_client.post(
        "/api/v1/integrations/vcs/links",
        json={
            "integration_id": integration["id"],
            "vcs_ref": {"type": "pull_request", "id": "acme/web#5"},
            "mesh_entity_type": "issue",
            "issue_id": issue2["id"],
        },
        headers=auth_headers(world),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"

    resolved = await app_client.post(
        "/api/v1/integrations/vcs/resolve",
        json={
            "integration_id": integration["id"],
            "source_text": f"closes {issue['identifier']}",
            "vcs_ref": {"type": "commit", "id": "sha-1"},
        },
        headers=auth_headers(world),
    )
    assert resolved.status_code == 200
    assert issue["identifier"] in resolved.json()["data"]["identifiers"]

    unresolved = await app_client.post(
        "/api/v1/integrations/vcs/resolve",
        json={
            "integration_id": integration["id"],
            "source_text": "closes NOPE-999",
            "vcs_ref": {"type": "commit", "id": "sha-2"},
        },
        headers=auth_headers(world),
    )
    assert unresolved.status_code == 422
    assert unresolved.json()["error"]["code"] == "identifier_not_resolved"

    no_identifiers = await app_client.post(
        "/api/v1/integrations/vcs/resolve",
        json={
            "integration_id": integration["id"],
            "source_text": "no identifier here",
            "vcs_ref": {"type": "commit", "id": "sha-3"},
        },
        headers=auth_headers(world),
    )
    assert no_identifiers.status_code == 200
    assert no_identifiers.json()["data"]["identifiers"] == []

    deleted = await app_client.delete(
        f"/api/v1/integrations/vcs/links/{link.json()['data']['id']}",
        headers=auth_headers(world),
    )
    assert deleted.status_code == 204


# ---------------------------------------------------------------------------
# OAuth authorize (PKCE round-trip start)
# ---------------------------------------------------------------------------


async def test_oauth_authorize_redirects_with_pkce(app_client):
    world = await make_world(app_client, "oauth")
    response = await app_client.get(
        f"/api/v1/workspaces/{world['ws_id']}/integrations/oauth/im_slack/authorize",
        headers=auth_headers(world),
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    assert "state=" in location
