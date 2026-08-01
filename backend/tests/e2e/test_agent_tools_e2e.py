"""Real HTTP e2e for agent capability grants.

The uvicorn process connects as the restricted application role.  The test
hits both the exact agent.md route and the workspace-qualified compatibility
route, then verifies the committed PostgreSQL rows directly.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.agent.capabilities import normalize_capability_declarations
from mesh.db.models.audit import AuditLog
from mesh.db.models.skill import AgentSkill, SkillInstallation
from mesh.db.tenant import set_tenant_context
from mesh.skill.bindings import BindingService

pytestmark = pytest.mark.e2e

PASSWORD = "Agent-tools-e2e-passw0rd!"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(api_client) -> str:
    email = f"agent-tools-{uuid.uuid4().hex[:10]}@example.com"
    registered = await api_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "Tools E2E"},
    )
    assert registered.status_code == 201, registered.text
    response = await api_client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


async def _seed(api_client, token: str) -> tuple[str, str, str, str]:
    workspace = await api_client.post(
        "/api/v1/workspaces",
        json={"name": "Agent tools e2e", "slug": f"at-{uuid.uuid4().hex[:10]}"},
        headers=_auth(token),
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["data"]["id"]
    agent_ids: list[str] = []
    for name in ("Primary", "Control"):
        response = await api_client.post(
            f"/api/v1/workspaces/{workspace_id}/agents",
            json={"name": name},
            headers=_auth(token),
        )
        assert response.status_code == 201, response.text
        agent_ids.append(response.json()["data"]["id"])

    declared = [
        {"capability": "repo:read", "permission": "read_only"},
        "exec:shell",
    ]
    skill = await api_client.post(
        f"/api/v1/workspaces/{workspace_id}/skills",
        json={
            "name": "E2E capability suite",
            "summary": "Capability grant e2e fixture",
            "required_capabilities": declared,
        },
        headers=_auth(token),
    )
    assert skill.status_code == 201, skill.text
    skill_id = skill.json()["data"]["id"]
    version = await api_client.post(
        f"/api/v1/workspaces/{workspace_id}/skills/{skill_id}/versions",
        json={
            "version": "1.0.0",
            "instructions": "Use granted capabilities.",
            "required_capabilities": declared,
            "publish": True,
        },
        headers=_auth(token),
    )
    assert version.status_code == 201, version.text
    installation = await api_client.post(
        f"/api/v1/workspaces/{workspace_id}/skill-installations",
        json={
            "skill_id": skill_id,
            "skill_version_id": version.json()["data"]["id"],
            "scope": "workspace",
        },
        headers=_auth(token),
    )
    assert installation.status_code == 201, installation.text
    installation_id = installation.json()["data"]["id"]
    primary_binding_id = ""
    for index, agent_id in enumerate(agent_ids):
        binding = await api_client.post(
            f"/api/v1/workspaces/{workspace_id}/agents/{agent_id}/skills",
            json={"skill_installation_id": installation_id},
            headers=_auth(token),
        )
        assert binding.status_code == 201, binding.text
        if index == 0:
            primary_binding_id = binding.json()["data"]["id"]
    return workspace_id, agent_ids[0], agent_ids[1], primary_binding_id


async def test_agent_tools_exact_route_rls_and_persistence(api_client, session_factory) -> None:
    token = await _login(api_client)
    workspace_id, primary_id, control_id, binding_id = await _seed(api_client, token)

    exact = await api_client.get(f"/api/v1/agents/{primary_id}/tools", headers=_auth(token))
    assert exact.status_code == 200, exact.text
    assert exact.json()["data"] == [
        {"capability": "exec:shell", "permission": "confirm_required", "enabled": True},
        {"capability": "repo:read", "permission": "read_only", "enabled": True},
    ]

    removed = await api_client.delete(f"/api/v1/agents/{primary_id}/tools/exec:shell", headers=_auth(token))
    assert removed.status_code == 204, removed.text
    rebound = await api_client.post(
        f"/api/v1/agents/{primary_id}/tools",
        json={"capability": "exec:shell"},
        headers=_auth(token),
    )
    assert rebound.status_code == 201, rebound.text
    tightened = await api_client.patch(
        f"/api/v1/workspaces/{workspace_id}/agents/{primary_id}/tools/exec:shell",
        json={"permission": "read_only"},
        headers=_auth(token),
    )
    assert tightened.status_code == 200, tightened.text
    assert tightened.json()["data"]["permission"] == "read_only"

    disabled = await api_client.patch(
        f"/api/v1/agents/{primary_id}/tools/exec:shell",
        json={"enabled": False},
        headers=_auth(token),
    )
    assert disabled.status_code == 200, disabled.text
    still_listed = await api_client.get(f"/api/v1/agents/{primary_id}/tools", headers=_auth(token))
    assert (
        next(item for item in still_listed.json()["data"] if item["capability"] == "exec:shell")["enabled"]
        is False
    )

    binding_service = BindingService(session_factory)
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        enqueue_context = await binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(primary_id)
        )
    normalized = normalize_capability_declarations(enqueue_context["declared_capabilities"])
    assert "exec:shell" not in normalized["required"]

    restored = await api_client.patch(
        f"/api/v1/agents/{primary_id}/tools/exec:shell",
        json={"enabled": True},
        headers=_auth(token),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["enabled"] is True
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, uuid.UUID(workspace_id))
        enqueue_context = await binding_service.collect_enqueue_context(
            session, uuid.UUID(workspace_id), uuid.UUID(primary_id)
        )
    normalized = normalize_capability_declarations(enqueue_context["declared_capabilities"])
    assert "exec:shell" in normalized["required"]

    control = await api_client.get(f"/api/v1/agents/{control_id}/tools", headers=_auth(token))
    control_shell = next(item for item in control.json()["data"] if item["capability"] == "exec:shell")
    assert control_shell["permission"] == "confirm_required"

    async with session_factory() as session:
        binding = await session.get(AgentSkill, uuid.UUID(binding_id))
        private = await session.get(SkillInstallation, binding.skill_installation_id)
        audit_actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.workspace_id == uuid.UUID(workspace_id),
                        AuditLog.resource_id == uuid.UUID(primary_id),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert private.scope == "agent"
    assert str(private.agent_id) == primary_id
    assert private.granted_capabilities == [
        {"capability": "exec:shell", "permission": "read_only", "enabled": True},
        {"capability": "repo:read", "permission": "read_only", "enabled": True},
    ]
    assert {"agent.tool_bound", "agent.tool_updated", "agent.tool_unbound"} <= set(audit_actions)
