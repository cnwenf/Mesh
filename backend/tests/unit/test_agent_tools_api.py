"""Agent tool grants HTTP contract (agent.md §3.1/§3.2).

The tests use the in-process ASGI transport with the real PostgreSQL and Redis
fixtures.  A separate e2e module exercises the same contract through uvicorn.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from mesh.agent.capabilities import normalize_capability_declarations
from mesh.agent.channels import make_agent_channel_checker
from mesh.agent.service import AgentService
from mesh.agent.tools import AgentToolService
from mesh.auth import jwt as jwt_mod
from mesh.auth.deps import AuthenticatedPrincipal
from mesh.config import load_settings
from mesh.db.models.audit import AuditLog
from mesh.db.models.member import Member
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.skill import AgentSkill, SkillImportTask, SkillInstallation
from mesh.db.models.user import User
from mesh.db.models.workspace import Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.realtime.auth import DefaultChannelAuthorizer, Principal

pytestmark = pytest.mark.unit

PASSWORD = "S3cure-agent-tools-passw0rd!"


def test_tool_render_prefers_enabled_permissions_over_stricter_disabled() -> None:
    binding = SimpleNamespace(enabled=True)
    disabled = SimpleNamespace(
        install_status="installed",
        granted_capabilities=[
            {
                "capability": "repo:read",
                "permission": "confirm_required",
                "enabled": False,
            }
        ],
    )
    enabled = SimpleNamespace(
        install_status="installed",
        granted_capabilities=[{"capability": "repo:read", "permission": "read_only", "enabled": True}],
    )
    assert AgentToolService._render_rows([(binding, disabled, None), (binding, enabled, None)]) == [
        {"capability": "repo:read", "permission": "read_only", "enabled": True}
    ]
    assert AgentToolService._render_rows([(binding, enabled, None), (binding, enabled, None)]) == [
        {"capability": "repo:read", "permission": "read_only", "enabled": True}
    ]
    assert AgentToolService._render_rows([(binding, disabled, None)]) == [
        {
            "capability": "repo:read",
            "permission": "confirm_required",
            "enabled": False,
        }
    ]


def test_tool_render_reports_the_persisted_grant_switch_not_parent_state() -> None:
    disabled_binding = SimpleNamespace(enabled=False)
    disabled_installation = SimpleNamespace(
        install_status="disabled",
        granted_capabilities=[{"capability": "repo:read", "permission": "read_only", "enabled": True}],
    )

    assert AgentToolService._render_rows([(disabled_binding, disabled_installation, None)]) == [
        {"capability": "repo:read", "permission": "read_only", "enabled": True}
    ]


def test_grant_map_uses_canonical_duplicate_permission_and_enabled_precedence() -> None:
    installation = SimpleNamespace(
        granted_capabilities=[
            {
                "capability": "repo:read",
                "permission": "confirm_required",
                "enabled": True,
            },
            {"capability": "repo:read", "permission": "write", "enabled": False},
            {"capability": "repo:read", "permission": "read_only", "enabled": True},
        ]
    )

    assert AgentToolService._grant_map(installation) == {
        "repo:read": {"permission": "confirm_required", "enabled": True}
    }


def test_tool_mutations_reject_multiple_bindings_for_the_same_skill() -> None:
    skill_id = uuid.uuid4()
    duplicate_rows = [
        (SimpleNamespace(skill_id=skill_id), None, None),
        (SimpleNamespace(skill_id=skill_id), None, None),
    ]

    with pytest.raises(ConflictError) as raised:
        AgentToolService._assert_unambiguous_skill_bindings(duplicate_rows)

    assert raised.value.details == {"skill_ids": [str(skill_id)]}

    AgentToolService._assert_unambiguous_skill_bindings(
        [(SimpleNamespace(skill_id=uuid.uuid4()), None, None)]
    )


def test_copy_on_write_filters_grants_not_declared_by_the_bound_version() -> None:
    installation = SimpleNamespace(
        granted_capabilities=[{"capability": "root:all", "permission": "read_only"}]
    )
    ceiling = [{"capability": "repo:read", "permission": "read_only"}]

    assert AgentToolService._safe_grants(installation, ceiling) == []


async def test_tool_write_locks_agent_but_read_does_not(monkeypatch) -> None:
    session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(AgentService, "_assert_visible", lambda *_args: None)
    monkeypatch.setattr(AgentService, "_assert_can_manage", lambda *_args: None)
    workspace_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    actor = SimpleNamespace()

    await AgentToolService._load_agent(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
        write=False,
    )
    read_statement = session.scalar.await_args.args[0]
    assert "FOR UPDATE" not in str(read_statement)

    await AgentToolService._load_agent(
        session,
        actor=actor,
        workspace_id=workspace_id,
        agent_id=agent_id,
        write=True,
    )
    write_statement = session.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(write_statement)


@pytest.fixture
def app(db_url, redis_url, attachment_settings_kwargs):
    from mesh.api.app import create_app

    return create_app(load_settings(**attachment_settings_kwargs))


@pytest_asyncio.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        yield http
    await app.state.redis.aclose()
    await app.state.engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_login(client: httpx.AsyncClient, email: str) -> str:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Tool Tester"},
    )
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


async def _workspace(client: httpx.AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/v1/workspaces",
        json={"name": "Agent tools", "slug": f"tools-{uuid.uuid4().hex[:10]}"},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def _pat(
    client: httpx.AsyncClient,
    token: str,
    workspace_id: str,
    *,
    scopes: list[str],
) -> str:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/api-tokens",
        json={"name": f"tools-{uuid.uuid4().hex[:8]}", "scopes": scopes},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["token"]


async def _agent(client: httpx.AsyncClient, token: str, workspace_id: str, name: str) -> str:
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents",
        json={"name": name},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def _invite_member(client: httpx.AsyncClient, owner: str, workspace_id: str) -> str:
    email = f"member-{uuid.uuid4().hex[:8]}@example.com"
    invitation = await client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations",
        json={"emails": [email], "role": "member"},
        headers=_auth(owner),
    )
    assert invitation.status_code in (200, 201), invitation.text
    invite_token = invitation.json()["data"][0]["invite_link"].rsplit("/", 1)[1]
    member = await _register_login(client, email)
    accepted = await client.post(
        "/api/v1/invitations/accept",
        json={"token": invite_token},
        headers=_auth(member),
    )
    assert accepted.status_code == 200, accepted.text
    return member


async def _install_and_bind(
    client: httpx.AsyncClient,
    token: str,
    workspace_id: str,
    agent_ids: list[str],
) -> tuple[str, list[str]]:
    capabilities = [
        {"capability": "repo:read", "permission": "read_only"},
        {"capability": "issue:write", "permission": "write"},
        "exec:shell",
    ]
    skill_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skills",
        json={
            "name": "Tool suite",
            "summary": "Capabilities used by the agent tools contract",
            "required_capabilities": capabilities,
        },
        headers=_auth(token),
    )
    assert skill_response.status_code == 201, skill_response.text
    skill_id = skill_response.json()["data"]["id"]
    version_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skills/{skill_id}/versions",
        json={
            "version": "1.0.0",
            "instructions": "Use the granted capabilities only.",
            "required_capabilities": capabilities,
            "publish": True,
        },
        headers=_auth(token),
    )
    assert version_response.status_code == 201, version_response.text
    version_id = version_response.json()["data"]["id"]
    install_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skill-installations",
        json={"skill_id": skill_id, "skill_version_id": version_id, "scope": "workspace"},
        headers=_auth(token),
    )
    assert install_response.status_code == 201, install_response.text
    installation_id = install_response.json()["data"]["id"]
    binding_ids: list[str] = []
    for agent_id in agent_ids:
        binding_response = await client.post(
            f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/skills",
            json={"skill_installation_id": installation_id},
            headers=_auth(token),
        )
        assert binding_response.status_code == 201, binding_response.text
        binding_ids.append(binding_response.json()["data"]["id"])
    return installation_id, binding_ids


async def test_tools_crud_isolated_with_default_risk_and_audit(client, app) -> None:
    token = await _register_login(client, f"owner-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    first_agent = await _agent(client, token, workspace_id, "First")
    second_agent = await _agent(client, token, workspace_id, "Second")
    shared_installation_id, binding_ids = await _install_and_bind(
        client, token, workspace_id, [first_agent, second_agent]
    )

    listed = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools",
        headers=_auth(token),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"] == [
        {"capability": "exec:shell", "permission": "confirm_required", "enabled": True},
        {"capability": "issue:write", "permission": "write", "enabled": True},
        {"capability": "repo:read", "permission": "read_only", "enabled": True},
    ]

    removed = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools/exec:shell",
        headers=_auth(token),
    )
    assert removed.status_code == 204, removed.text
    first_after = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools",
        headers=_auth(token),
    )
    assert {item["capability"] for item in first_after.json()["data"]} == {
        "issue:write",
        "repo:read",
    }
    second_after = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{second_agent}/tools",
        headers=_auth(token),
    )
    assert {item["capability"] for item in second_after.json()["data"]} == {
        "exec:shell",
        "issue:write",
        "repo:read",
    }

    rebound = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools",
        json={"capability": "exec:shell"},
        headers=_auth(token),
    )
    assert rebound.status_code == 201, rebound.text
    assert rebound.json()["data"] == {
        "capability": "exec:shell",
        "permission": "confirm_required",
        "enabled": True,
    }

    patched = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools/issue:write",
        json={"permission": "read_only"},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["permission"] == "read_only"

    disabled = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools/repo:read",
        json={"enabled": False},
        headers=_auth(token),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["data"] == {
        "capability": "repo:read",
        "permission": "read_only",
        "enabled": False,
    }
    disabled_permission_change = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools/repo:read",
        json={"permission": "read_only"},
        headers=_auth(token),
    )
    assert disabled_permission_change.status_code == 200, disabled_permission_change.text
    assert disabled_permission_change.json()["data"]["enabled"] is False
    listed_disabled = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools",
        headers=_auth(token),
    )
    repo_disabled = next(item for item in listed_disabled.json()["data"] if item["capability"] == "repo:read")
    assert repo_disabled["enabled"] is False

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        context = await app.state.skill_binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(first_agent)
        )
    normalized = normalize_capability_declarations(context["declared_capabilities"])
    assert "repo:read" not in normalized["required"]

    restored = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools/repo:read",
        json={"enabled": True},
        headers=_auth(token),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["enabled"] is True
    listed_restored = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{first_agent}/tools",
        headers=_auth(token),
    )
    assert (
        next(item for item in listed_restored.json()["data"] if item["capability"] == "repo:read")["enabled"]
        is True
    )
    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        context = await app.state.skill_binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(first_agent)
        )
    normalized = normalize_capability_declarations(context["declared_capabilities"])
    assert "repo:read" in normalized["required"]

    async with app.state.session_factory() as session:
        first_binding = await session.get(AgentSkill, uuid.UUID(binding_ids[0]))
        second_binding = await session.get(AgentSkill, uuid.UUID(binding_ids[1]))
        assert str(first_binding.skill_installation_id) != shared_installation_id
        assert str(second_binding.skill_installation_id) == shared_installation_id
        isolated = await session.get(SkillInstallation, first_binding.skill_installation_id)
        assert isolated.scope == "agent"
        assert str(isolated.agent_id) == first_agent

        audit_actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.resource_id == uuid.UUID(first_agent))
                )
            )
            .scalars()
            .all()
        )
        tool_frames = [
            row.payload
            for row in (
                (
                    await session.execute(
                        select(OutboxEvent).where(
                            OutboxEvent.workspace_id == uuid.UUID(workspace_id),
                            OutboxEvent.event_type == "realtime.publish",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if row.payload.get("data", {}).get("change_type") == "tool_grant_changed"
        ]
    assert {"agent.tool_bound", "agent.tool_updated", "agent.tool_unbound"} <= set(audit_actions)
    assert tool_frames
    assert all(frame["channel"] == f"agent:{first_agent}" for frame in tool_frames)
    assert all(frame["event"] == "agent.updated" for frame in tool_frames)
    assert all(frame["data"]["visibility"] == "workspace" for frame in tool_frames)
    assert all(frame["data"]["updated_at"] for frame in tool_frames)


async def test_tool_update_prefers_an_active_binding_for_duplicate_capabilities(client, app) -> None:
    token = await _register_login(client, f"active-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    agent_id = await _agent(client, token, workspace_id, "Active grant target")
    _, first_bindings = await _install_and_bind(client, token, workspace_id, [agent_id])
    _, second_bindings = await _install_and_bind(client, token, workspace_id, [agent_id])

    disabled_binding = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/skills/{first_bindings[0]}",
        json={"enabled": False, "priority": 500},
        headers=_auth(token),
    )
    assert disabled_binding.status_code == 200, disabled_binding.text
    active_binding = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/skills/{second_bindings[0]}",
        json={"enabled": True, "priority": 100},
        headers=_auth(token),
    )
    assert active_binding.status_code == 200, active_binding.text

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        before = await app.state.skill_binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(agent_id)
        )
    assert "repo:read" in normalize_capability_declarations(
        before["declared_capabilities"]
    )["required"]

    updated = await client.patch(
        f"/api/v1/agents/{agent_id}/tools/repo:read",
        json={"permission": "read_only", "enabled": True},
        headers=_auth(token),
    )
    assert updated.status_code == 200, updated.text

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        after = await app.state.skill_binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(agent_id)
        )
    assert "repo:read" in normalize_capability_declarations(
        after["declared_capabilities"]
    )["required"]


async def test_tools_validation_visibility_and_authorization(client, app) -> None:
    owner = await _register_login(client, f"owner-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, owner)
    agent_id = await _agent(client, owner, workspace_id, "Private")
    await _install_and_bind(client, owner, workspace_id, [agent_id])
    member = await _invite_member(client, owner, workspace_id)

    missing_auth = await client.get(f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools")
    assert missing_auth.status_code == 401

    readable = await client.get(f"/api/v1/agents/{agent_id}/tools", headers=_auth(member))
    assert readable.status_code == 200, readable.text
    forbidden = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools/repo:read",
        json={"enabled": False},
        headers=_auth(member),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "forbidden"

    made_private = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}",
        json={"visibility": "private"},
        headers=_auth(owner),
    )
    assert made_private.status_code == 200, made_private.text

    owner_roster = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members",
        params={"member_type": "agent"},
        headers=_auth(owner),
    )
    assert owner_roster.status_code == 200, owner_roster.text
    private_member = next(
        row
        for row in owner_roster.json()["data"]
        if row["profile"]["id"] == agent_id
    )
    hidden_roster = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members",
        params={"member_type": "agent"},
        headers=_auth(member),
    )
    assert hidden_roster.status_code == 200, hidden_roster.text
    assert all(row["profile"]["id"] != agent_id for row in hidden_roster.json()["data"])
    hidden_member_detail = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members/{private_member['id']}",
        headers=_auth(member),
    )
    assert hidden_member_detail.status_code == 404
    visible_member_detail = await client.get(
        f"/api/v1/workspaces/{workspace_id}/members/{private_member['id']}",
        headers=_auth(owner),
    )
    assert visible_member_detail.status_code == 200, visible_member_detail.text

    hidden = await client.get(f"/api/v1/agents/{agent_id}/tools", headers=_auth(member))
    assert hidden.status_code == 404
    checker = make_agent_channel_checker(app.state.session_factory)
    owner_claims = jwt_mod.decode_access_token(
        owner,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
    )
    member_claims = jwt_mod.decode_access_token(
        member,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
    )
    assert await checker(
        Principal(
            subject=str(owner_claims.subject),
            workspace_ids=frozenset({uuid.UUID(workspace_id)}),
        ),
        f"agent:{agent_id}",
    )
    assert not await checker(
        Principal(
            subject=str(member_claims.subject),
            workspace_ids=frozenset({uuid.UUID(workspace_id)}),
        ),
        f"agent:{agent_id}",
    )
    authorizer = DefaultChannelAuthorizer(app.state.session_factory)
    authorizer.register_prefix_checker("agent", checker)
    owner_principal = Principal(
        subject=str(owner_claims.subject),
        workspace_ids=frozenset({uuid.UUID(workspace_id)}),
    )
    member_principal = Principal(
        subject=str(member_claims.subject),
        workspace_ids=frozenset({uuid.UUID(workspace_id)}),
    )
    # No projector-created realtime_channels row exists yet: the fresh
    # resource must still be subscribable by its owner, while a plain member
    # remains denied by the private-agent checker.
    assert await authorizer.authorize(owner_principal, f"agent:{agent_id}") == uuid.UUID(workspace_id)
    assert await authorizer.authorize(member_principal, f"agent:{agent_id}") is None

    undeclared = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools",
        json={"capability": "root:all", "permission": "write"},
        headers=_auth(owner),
    )
    assert undeclared.status_code == 422
    assert undeclared.json()["error"]["code"] == "capability_not_declared"

    escalated = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools/repo:read",
        json={"permission": "write"},
        headers=_auth(owner),
    )
    assert escalated.status_code == 422
    assert escalated.json()["error"]["code"] == "capability_not_declared"

    invalid_permission = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools/repo:read",
        json={"permission": "superuser"},
        headers=_auth(owner),
    )
    assert invalid_permission.status_code == 400
    assert invalid_permission.json()["error"]["code"] == "validation_error"

    invalid_post_shape = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools",
        json={
            "capability": "repo:read",
            "capabilities": [{"capability": "repo:read"}],
        },
        headers=_auth(owner),
    )
    assert invalid_post_shape.status_code == 400
    assert invalid_post_shape.json()["error"]["code"] == "validation_error"

    empty_patch = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools/repo:read",
        json={},
        headers=_auth(owner),
    )
    assert empty_patch.status_code == 400
    assert empty_patch.json()["error"]["code"] == "validation_error"

    duplicate = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools",
        json={"capability": "repo:read", "permission": "read_only"},
        headers=_auth(owner),
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    not_found = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools/missing:tool",
        headers=_auth(owner),
    )
    assert not_found.status_code == 404

    deleted = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}",
        headers=_auth(owner),
    )
    assert deleted.status_code == 204, deleted.text
    assert await authorizer.authorize(owner_principal, f"agent:{agent_id}") == uuid.UUID(
        workspace_id
    )
    assert await authorizer.authorize(member_principal, f"agent:{agent_id}") is None
    assert await authorizer.authorize(owner_principal, f"agent:{agent_id}:presence") is None


async def test_exact_spec_routes_and_batch_body(client) -> None:
    token = await _register_login(client, f"exact-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    agent_id = await _agent(client, token, workspace_id, "Exact route")
    await _install_and_bind(client, token, workspace_id, [agent_id])

    exact = await client.get(f"/api/v1/agents/{agent_id}/tools", headers=_auth(token))
    assert exact.status_code == 200, exact.text
    assert exact.json()["next_cursor"] is None
    assert len(exact.json()["data"]) == 3

    for capability in ("exec:shell", "issue:write"):
        deleted = await client.delete(f"/api/v1/agents/{agent_id}/tools/{capability}", headers=_auth(token))
        assert deleted.status_code == 204, deleted.text

    batch = await client.post(
        f"/api/v1/agents/{agent_id}/tools",
        json={
            "capabilities": [
                {"capability": "exec:shell"},
                {"capability": "issue:write", "permission": "read_only"},
            ]
        },
        headers=_auth(token),
    )
    assert batch.status_code == 201, batch.text
    assert batch.json()["data"] == [
        {"capability": "exec:shell", "permission": "confirm_required", "enabled": True},
        {"capability": "issue:write", "permission": "read_only", "enabled": True},
    ]

    patched = await client.patch(
        f"/api/v1/agents/{agent_id}/tools/issue:write",
        json={"permission": "write", "enabled": True},
        headers=_auth(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["permission"] == "write"


def test_openapi_exposes_typed_tool_grants_and_runtime_error_statuses(app) -> None:
    spec = app.openapi()
    for prefix in (
        "/api/v1/agents/{agent_id}/tools",
        "/api/v1/workspaces/{workspace_id}/agents/{agent_id}/tools",
    ):
        collection = spec["paths"][prefix]
        list_schema = collection["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        bind_schema = collection["post"]["responses"]["201"]["content"]["application/json"]["schema"]
        assert list_schema["$ref"].endswith("/AgentToolListResponse")
        assert bind_schema["$ref"].endswith("/AgentToolBindResponse")
        assert {"400", "401", "403", "404", "409", "422", "429"} <= set(collection["post"]["responses"])

        item = spec["paths"][f"{prefix}/{{capability_key}}"]
        patch_schema = item["patch"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert patch_schema["$ref"].endswith("/AgentToolResponse")
        assert "204" in item["delete"]["responses"]

    grant = spec["components"]["schemas"]["AgentToolGrant"]
    assert set(grant["required"]) == {"capability", "permission", "enabled"}
    assert grant["properties"]["permission"]["enum"] == [
        "read_only",
        "write",
        "confirm_required",
    ]


async def test_required_capabilities_reject_the_grant_only_enabled_field(client) -> None:
    token = await _register_login(client, f"required-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skills",
        json={
            "name": "Invalid required field",
            "summary": "Required declarations cannot be switched off.",
            "required_capabilities": [
                {
                    "capability": "repo:read",
                    "permission": "read_only",
                    "enabled": False,
                }
            ],
        },
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "capability_invalid"


async def test_member_can_manage_skill_bindings_for_their_own_agent(client) -> None:
    owner = await _register_login(client, f"binding-owner-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, owner)
    member = await _invite_member(client, owner, workspace_id)
    member_agent = await _agent(client, member, workspace_id, "Member-owned")
    installation_id, _ = await _install_and_bind(client, owner, workspace_id, [])

    bound = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/skills",
        json={"skill_installation_id": installation_id},
        headers=_auth(member),
    )
    assert bound.status_code == 201, bound.text
    binding_id = bound.json()["data"]["id"]

    made_private = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}",
        json={"visibility": "private"},
        headers=_auth(member),
    )
    assert made_private.status_code == 200, made_private.text

    # A first grant change creates the agent-scoped copy-on-write installation.
    private_grant = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/tools/repo:read",
        json={"enabled": False},
        headers=_auth(member),
    )
    assert private_grant.status_code == 200, private_grant.text

    outsider = await _invite_member(client, owner, workspace_id)
    hidden_bindings = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/skills",
        headers=_auth(outsider),
    )
    assert hidden_bindings.status_code == 404

    for visible_token in (member, owner):
        visible_bindings = await client.get(
            f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/skills",
            headers=_auth(visible_token),
        )
        assert visible_bindings.status_code == 200, visible_bindings.text
        assert visible_bindings.json()["data"][0]["binding_id"] == binding_id

        visible_installations = await client.get(
            f"/api/v1/workspaces/{workspace_id}/skill-installations",
            params={"scope": "agent"},
            headers=_auth(visible_token),
        )
        assert visible_installations.status_code == 200, visible_installations.text
        assert any(
            row["agent_id"] == member_agent
            for row in visible_installations.json()["data"]
        )

    hidden_installations = await client.get(
        f"/api/v1/workspaces/{workspace_id}/skill-installations",
        params={"scope": "agent"},
        headers=_auth(outsider),
    )
    assert hidden_installations.status_code == 200, hidden_installations.text
    assert all(
        row["agent_id"] != member_agent
        for row in hidden_installations.json()["data"]
    )

    updated = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/skills/{binding_id}",
        json={"enabled": False, "priority": 250},
        headers=_auth(member),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["enabled"] is False

    unbound = await client.delete(
        f"/api/v1/workspaces/{workspace_id}/agents/{member_agent}/skills/{binding_id}",
        headers=_auth(member),
    )
    assert unbound.status_code == 204, unbound.text


async def test_exact_routes_preserve_principal_workspace_scope_and_lifecycle(client, app) -> None:
    token = await _register_login(client, f"principal-{uuid.uuid4().hex[:8]}@example.com")
    first_workspace = await _workspace(client, token)
    second_workspace = await _workspace(client, token)
    first_agent = await _agent(client, token, first_workspace, "First tenant")
    second_agent = await _agent(client, token, second_workspace, "Second tenant")
    await _install_and_bind(client, token, first_workspace, [first_agent])
    await _install_and_bind(client, token, second_workspace, [second_agent])

    foreign_pat = await _pat(
        client,
        token,
        first_workspace,
        scopes=["agent:manage"],
    )
    foreign = await client.get(f"/api/v1/agents/{second_agent}/tools", headers=_auth(foreign_pat))
    assert foreign.status_code == 404

    scoped_pat = await _pat(
        client,
        token,
        second_workspace,
        scopes=["issue:read"],
    )
    denied = await client.patch(
        f"/api/v1/agents/{second_agent}/tools/repo:read",
        json={"enabled": False},
        headers=_auth(scoped_pat),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"required_scope": "agent:manage"}

    claims = jwt_mod.decode_access_token(
        token,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
    )
    device_token, _ = jwt_mod.encode_access_token(
        subject=claims.subject,
        secret=app.state.settings.jwt_secret,
        algorithm=app.state.settings.jwt_algorithm,
        ttl=timedelta(minutes=5),
        workspace_id=uuid.UUID(first_workspace),
        scopes=["agent:manage"],
    )
    device_foreign = await client.get(f"/api/v1/agents/{second_agent}/tools", headers=_auth(device_token))
    assert device_foreign.status_code == 403

    async with app.state.session_factory() as session, session.begin():
        workspace = await session.get(Workspace, uuid.UUID(second_workspace))
        workspace.deleted_at = datetime.now(UTC)
    deleted_workspace = await client.get(f"/api/v1/agents/{second_agent}/tools", headers=_auth(token))
    assert deleted_workspace.status_code == 404


async def test_first_tool_write_reconciles_an_existing_unbound_private_installation(client, app) -> None:
    token = await _register_login(client, f"cow-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    agent_id = await _agent(client, token, workspace_id, "COW reconciliation")
    shared_installation_id, binding_ids = await _install_and_bind(client, token, workspace_id, [agent_id])

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        shared = await session.get(SkillInstallation, uuid.UUID(shared_installation_id))
        skill_id = str(shared.skill_id)
        bound_version_id = str(shared.skill_version_id)

    stale_version = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skills/{skill_id}/versions",
        json={
            "version": "2.0.0",
            "instructions": "A stale private version.",
            "required_capabilities": [{"capability": "stale:only", "permission": "read_only"}],
            "publish": True,
        },
        headers=_auth(token),
    )
    assert stale_version.status_code == 201, stale_version.text
    stale_version_id = stale_version.json()["data"]["id"]
    stale_private = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skill-installations",
        json={
            "skill_id": skill_id,
            "skill_version_id": stale_version_id,
            "scope": "agent",
            "agent_id": agent_id,
        },
        headers=_auth(token),
    )
    assert stale_private.status_code == 201, stale_private.text
    private_id = stale_private.json()["data"]["id"]
    disabled = await client.patch(
        f"/api/v1/workspaces/{workspace_id}/skill-installations/{private_id}",
        json={"install_status": "disabled"},
        headers=_auth(token),
    )
    assert disabled.status_code == 200, disabled.text

    removed = await client.delete(f"/api/v1/agents/{agent_id}/tools/exec:shell", headers=_auth(token))
    assert removed.status_code == 204, removed.text

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        binding = await session.get(AgentSkill, uuid.UUID(binding_ids[0]))
        private = await session.get(SkillInstallation, uuid.UUID(private_id))
        assert binding.skill_installation_id == private.id
        assert str(private.skill_version_id) == bound_version_id
        assert private.install_status == "installed"
        grants = AgentToolService._grant_map(private)
        assert "stale:only" not in grants
        assert "exec:shell" not in grants
        assert set(grants) == {"issue:write", "repo:read"}


async def test_tool_write_rejects_private_installation_bound_to_another_agent(client, app) -> None:
    token = await _register_login(client, f"foreign-cow-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    target_agent = await _agent(client, token, workspace_id, "Target")
    foreign_agent = await _agent(client, token, workspace_id, "Foreign binding")
    shared_installation_id, binding_ids = await _install_and_bind(client, token, workspace_id, [target_agent])
    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        shared = await session.get(SkillInstallation, uuid.UUID(shared_installation_id))
        skill_id = str(shared.skill_id)
        version_id = str(shared.skill_version_id)

    private_response = await client.post(
        f"/api/v1/workspaces/{workspace_id}/skill-installations",
        json={
            "skill_id": skill_id,
            "skill_version_id": version_id,
            "scope": "agent",
            "agent_id": target_agent,
        },
        headers=_auth(token),
    )
    assert private_response.status_code == 201, private_response.text
    private_id = private_response.json()["data"]["id"]
    rejected_ingress = await client.post(
        f"/api/v1/workspaces/{workspace_id}/agents/{foreign_agent}/skills",
        json={"skill_installation_id": private_id},
        headers=_auth(token),
    )
    assert rejected_ingress.status_code == 404
    # Seed the legacy-invalid state directly: current bind ingress now rejects
    # an agent-scoped installation whose owner differs from the target agent.
    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        private = await session.get(SkillInstallation, uuid.UUID(private_id))
        private_skill_id = private.skill_id
        now = datetime.now(UTC)
        session.add(
            AgentSkill(
                workspace_id=uuid.UUID(workspace_id),
                agent_id=uuid.UUID(foreign_agent),
                skill_id=private.skill_id,
                skill_installation_id=private.id,
                skill_version_id=private.skill_version_id,
                enabled=True,
                auto_trigger=True,
                priority=100,
                created_at=now,
                updated_at=now,
            )
        )

    # Legacy-invalid rows must be inert in every read and execution consumer:
    # the foreign agent cannot inherit, list, snapshot or auto-match the
    # private installation that belongs to the target agent.
    foreign_tools = await client.get(
        f"/api/v1/agents/{foreign_agent}/tools", headers=_auth(token)
    )
    assert foreign_tools.status_code == 200, foreign_tools.text
    assert foreign_tools.json()["data"] == []
    foreign_skills = await client.get(
        f"/api/v1/workspaces/{workspace_id}/agents/{foreign_agent}/skills",
        headers=_auth(token),
    )
    assert foreign_skills.status_code == 200, foreign_skills.text
    assert foreign_skills.json()["data"] == []

    from mesh.skill.matching import match_skills_for_task

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        versions = await app.state.skill_binding_service.collect_bound_versions(
            session, uuid.UUID(workspace_id), uuid.UUID(foreign_agent)
        )
        context = await app.state.skill_binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(foreign_agent)
        )
        matched = await match_skills_for_task(
            session,
            workspace_id=uuid.UUID(workspace_id),
            agent_id=uuid.UUID(foreign_agent),
            explicit_skill_ids=[private_skill_id],
        )
    assert versions == {}
    assert context == {"skill_versions": {}, "declared_capabilities": []}
    assert matched == []

    rejected = await client.delete(f"/api/v1/agents/{target_agent}/tools/exec:shell", headers=_auth(token))
    assert rejected.status_code == 409

    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        target_binding = await session.get(AgentSkill, uuid.UUID(binding_ids[0]))
        private = await session.get(SkillInstallation, uuid.UUID(private_id))
        assert str(target_binding.skill_installation_id) == shared_installation_id
        assert "exec:shell" in AgentToolService._grant_map(private)


async def test_tool_mutations_cannot_exceed_an_import_approval_ceiling(client, app) -> None:
    token = await _register_login(client, f"approval-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    agent_id = await _agent(client, token, workspace_id, "Approved tools")
    shared_installation_id, _ = await _install_and_bind(client, token, workspace_id, [agent_id])
    now = datetime.now(UTC)
    approved = [
        {"capability": "issue:write", "permission": "read_only"},
        {"capability": "repo:read", "permission": "read_only"},
    ]
    async with app.state.session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        installation = await session.get(SkillInstallation, uuid.UUID(shared_installation_id))
        installation.granted_capabilities = approved
        session.add(
            SkillImportTask(
                workspace_id=uuid.UUID(workspace_id),
                created_by=installation.installed_by,
                source_type="url",
                uri="https://skills.example.test/minimized",
                status="ready",
                stage="ready",
                percent=100,
                requires_approval=True,
                skill_id=installation.skill_id,
                skill_version_id=installation.skill_version_id,
                granted_capabilities=approved,
                reviewed_by=installation.installed_by,
                reviewed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        # An identical later re-import can produce a ready task without a new
        # review. It must not shadow the durable approved ceiling above.
        session.add(
            SkillImportTask(
                workspace_id=uuid.UUID(workspace_id),
                created_by=installation.installed_by,
                source_type="url",
                uri="https://skills.example.test/minimized-reimport",
                status="ready",
                stage="ready",
                percent=100,
                requires_approval=True,
                skill_id=installation.skill_id,
                skill_version_id=installation.skill_version_id,
                granted_capabilities=[],
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )

    omitted = await client.post(
        f"/api/v1/agents/{agent_id}/tools",
        json={"capability": "exec:shell"},
        headers=_auth(token),
    )
    assert omitted.status_code == 422
    assert omitted.json()["error"]["code"] == "capability_not_declared"

    escalated = await client.patch(
        f"/api/v1/agents/{agent_id}/tools/issue:write",
        json={"permission": "write"},
        headers=_auth(token),
    )
    assert escalated.status_code == 422
    assert escalated.json()["error"]["code"] == "capability_not_declared"

    disabled = await client.patch(
        f"/api/v1/agents/{agent_id}/tools/issue:write",
        json={"enabled": False},
        headers=_auth(token),
    )
    assert disabled.status_code == 200, disabled.text
    restored = await client.patch(
        f"/api/v1/agents/{agent_id}/tools/issue:write",
        json={"enabled": True},
        headers=_auth(token),
    )
    assert restored.status_code == 200, restored.text


async def test_tool_service_direct_mutations_and_defensive_validation(client, app) -> None:
    token = await _register_login(client, f"direct-{uuid.uuid4().hex[:8]}@example.com")
    workspace_id = await _workspace(client, token)
    agent_id = await _agent(client, token, workspace_id, "Direct service")
    await _install_and_bind(client, token, workspace_id, [agent_id])
    workspace_uuid = uuid.UUID(workspace_id)
    agent_uuid = uuid.UUID(agent_id)
    async with app.state.session_factory() as session:
        actor = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_uuid,
                Member.member_type == "human",
            )
        )
        user = await session.get(User, actor.user_id)

    service = app.state.agent_tool_service
    listed = await service.list_tools(actor=actor, workspace_id=workspace_uuid, agent_id=agent_uuid)
    assert len(listed) == 3
    await service.unbind_tool(
        actor=actor,
        workspace_id=workspace_uuid,
        agent_id=agent_uuid,
        capability="exec:shell",
    )
    rebound = await service.bind_tools(
        actor=actor,
        workspace_id=workspace_uuid,
        agent_id=agent_uuid,
        grants=[{"capability": "exec:shell", "permission": "confirm_required"}],
    )
    assert rebound[0]["enabled"] is True
    disabled = await service.update_tool(
        actor=actor,
        workspace_id=workspace_uuid,
        agent_id=agent_uuid,
        capability="exec:shell",
        permission=None,
        enabled=False,
    )
    assert disabled["enabled"] is False
    restored = await service.update_tool(
        actor=actor,
        workspace_id=workspace_uuid,
        agent_id=agent_uuid,
        capability="exec:shell",
        permission="read_only",
        enabled=True,
    )
    assert restored["permission"] == "read_only"

    with pytest.raises(ConflictError):
        await service.bind_tools(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            grants=[{"capability": "exec:shell", "permission": "read_only"}],
        )
    with pytest.raises(BusinessRuleError):
        await service.bind_tools(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            grants=[{"capability": "root:all", "permission": "read_only"}],
        )
    with pytest.raises(NotFoundError):
        await service.update_tool(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            capability="missing:tool",
            permission=None,
            enabled=False,
        )
    with pytest.raises(BusinessRuleError):
        await service.update_tool(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            capability="repo:read",
            permission="write",
            enabled=True,
        )
    with pytest.raises(NotFoundError):
        await service.unbind_tool(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            capability="missing:tool",
        )

    with pytest.raises(ValidationError):
        await service.bind_tools(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            grants=[
                {"capability": "dup:key", "permission": "read_only"},
                {"capability": "dup:key", "permission": "read_only"},
            ],
        )
    with pytest.raises(ValidationError):
        await service.bind_tools(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            grants=[{"capability": " ", "permission": "read_only"}],
        )
    with pytest.raises(ValidationError):
        await service.update_tool(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=agent_uuid,
            capability="exec:shell",
            permission="invalid",
            enabled=True,
        )
    with pytest.raises(NotFoundError):
        await service.resolve_actor(
            principal=AuthenticatedPrincipal(kind="session", user_id=user.id),
            agent_id=uuid.uuid4(),
        )
    with pytest.raises(NotFoundError):
        await service.list_tools(
            actor=actor,
            workspace_id=workspace_uuid,
            agent_id=uuid.uuid4(),
        )
