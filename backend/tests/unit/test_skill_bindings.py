"""Binding service tests — agent ↔ installed versions (skill.md §2.5) + §6.11 producer.

Real DB. Covers bind/unbind/update, duplicate 409, canary pinning of a
historic version, and ``collect_enqueue_context`` — the map frozen into
``task_executions.config_snapshot.skill_versions`` at enqueue time.
"""

from __future__ import annotations

import uuid

import pytest
from skill_test_support import make_agent, make_member, make_workspace
from sqlalchemy import select

from mesh.db.models.skill import Skill, SkillSource, SkillVersion
from mesh.errors import ConflictError, NotFoundError
from mesh.skill.bindings import BindingService
from mesh.skill.installations import InstallationService


@pytest.fixture
def binding_service(session_factory) -> BindingService:
    return BindingService(session_factory)


@pytest.fixture
def installation_service(session_factory) -> InstallationService:
    return InstallationService(session_factory)


async def _installed_world(session_factory, installation_service) -> dict:
    """Workspace + admin + agent + published skill + one installation."""
    workspace = await make_workspace(session_factory)
    admin = await make_member(session_factory, workspace, role="admin")
    agent = await make_agent(session_factory, workspace, admin.user_id)
    async with session_factory() as session, session.begin():
        source = SkillSource(
            workspace_id=workspace.id, source_type="user", name="u", trust_level="reviewed"
        )
        session.add(source)
        await session.flush()
        skill = Skill(
            workspace_id=workspace.id, source_id=source.id, name="N",
            slug=f"sk-{uuid.uuid4().hex[:8]}", summary="s", status="published",
            created_by=admin.id,
        )
        session.add(skill)
        await session.flush()
        v1 = SkillVersion(
            workspace_id=workspace.id, skill_id=skill.id, version="1.0.0",
            instructions="v1", status="published", content_hash="a" * 64,
            created_by=admin.id,
        )
        v2 = SkillVersion(
            workspace_id=workspace.id, skill_id=skill.id, version="1.1.0",
            instructions="v2", status="published", content_hash="b" * 64,
            created_by=admin.id,
        )
        session.add_all([v1, v2])
        await session.flush()
        skill.current_version_id = v2.id
    installation = await installation_service.install(
        actor=admin, workspace_id=workspace.id, skill_id=skill.id,
        skill_version_id=v2.id,
    )
    return {
        "workspace": workspace, "admin": admin, "agent": agent, "skill": skill,
        "v1": v1, "v2": v2, "installation_id": uuid.UUID(installation["id"]),
        "granted": installation["granted_capabilities"],
    }


class TestBind:
    async def test_bind_defaults_to_installation_version(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        assert binding["skill_version_id"] == str(world["v2"].id)
        assert binding["enabled"] is True
        assert binding["auto_trigger"] is True

    async def test_bind_can_pin_historic_version(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
            skill_version_id=world["v1"].id,  # canary on the OLD version
        )
        assert binding["skill_version_id"] == str(world["v1"].id)

    async def test_duplicate_binding_conflict(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        with pytest.raises(ConflictError):
            await binding_service.bind(
                actor=world["admin"], workspace_id=world["workspace"].id,
                agent_id=world["agent"].id,
                skill_installation_id=world["installation_id"],
            )

    async def test_bind_unknown_installation(self, binding_service, session_factory) -> None:
        workspace = await make_workspace(session_factory)
        admin = await make_member(session_factory, workspace, role="admin")
        agent = await make_agent(session_factory, workspace, admin.user_id)
        with pytest.raises(NotFoundError):
            await binding_service.bind(
                actor=admin, workspace_id=workspace.id, agent_id=agent.id,
                skill_installation_id=uuid.uuid4(),
            )


class TestBulkBind:
    """L247 批量操作:one installed skill → many agents, per-item isolation."""

    async def test_bulk_bind_many_agents(self, binding_service, session_factory) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        extra_agents = [
            await make_agent(session_factory, world["workspace"], world["admin"].user_id)
            for _ in range(2)
        ]
        agent_ids = [world["agent"].id] + [a.id for a in extra_agents]

        result = await binding_service.bulk_bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_ids=agent_ids, skill_installation_id=world["installation_id"],
        )

        assert len(result["bound"]) == 3
        assert result["errors"] == []
        assert {b["agent_id"] for b in result["bound"]} == {str(a) for a in agent_ids}
        assert all(b["skill_version_id"] == str(world["v2"].id) for b in result["bound"])

    async def test_bulk_bind_partial_failure_isolates_conflict(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        other = await make_agent(session_factory, world["workspace"], world["admin"].user_id)
        # Pre-bind the first agent so the bulk hit reports a conflict marker
        # for it while the second agent still binds.
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id, skill_installation_id=world["installation_id"],
        )

        result = await binding_service.bulk_bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_ids=[world["agent"].id, other.id],
            skill_installation_id=world["installation_id"],
        )

        assert [b["agent_id"] for b in result["bound"]] == [str(other.id)]
        assert len(result["errors"]) == 1
        marker = result["errors"][0]
        assert marker["agent_id"] == str(world["agent"].id)
        assert marker["code"] == "conflict"

    async def test_bulk_bind_unknown_agent_reports_marker_not_500(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))

        result = await binding_service.bulk_bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_ids=[uuid.uuid4(), world["agent"].id],
            skill_installation_id=world["installation_id"],
        )

        assert [b["agent_id"] for b in result["bound"]] == [str(world["agent"].id)]
        assert result["errors"][0]["code"] == "not_found"

    async def test_bulk_bind_dedupes_repeated_agent_ids(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))

        result = await binding_service.bulk_bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_ids=[world["agent"].id, world["agent"].id],
            skill_installation_id=world["installation_id"],
        )

        assert len(result["bound"]) == 1
        assert result["errors"] == []


class TestUpdateUnbind:
    async def test_update_binding_flags(self, binding_service, session_factory) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        updated = await binding_service.update_binding(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id, binding_id=uuid.UUID(binding["id"]),
            enabled=False, auto_trigger=False, priority=500,
        )
        assert updated["enabled"] is False
        assert updated["auto_trigger"] is False
        assert updated["priority"] == 500

    async def test_unbind_removes_row(self, binding_service, session_factory) -> None:
        from mesh.db.models.skill import AgentSkill

        world = await _installed_world(session_factory, InstallationService(session_factory))
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        await binding_service.unbind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id, binding_id=uuid.UUID(binding["id"]),
        )
        async with session_factory() as session:
            assert await session.scalar(select(AgentSkill)) is None

    async def test_binding_of_other_agent_not_found(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        other = await make_agent(session_factory, world["workspace"], world["admin"].user_id)
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        with pytest.raises(NotFoundError):
            await binding_service.update_binding(
                actor=world["admin"], workspace_id=world["workspace"].id,
                agent_id=other.id, binding_id=uuid.UUID(binding["id"]), enabled=False,
            )


class TestListAgentSkills:
    async def test_list_shape(self, binding_service, session_factory) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
            priority=120,
        )
        items, cursor = await binding_service.list_agent_skills(
            workspace_id=world["workspace"].id, agent_id=world["agent"].id
        )
        assert cursor is None
        assert len(items) == 1
        item = items[0]
        assert item["skill"]["name"] == "N"
        assert item["version"] == "1.1.0"
        assert item["install_status"] == "installed"
        assert item["priority"] == 120


class TestEnqueueContext:
    async def test_collect_bound_versions_and_grants(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        async with session_factory() as session:
            context = await binding_service.collect_enqueue_context(
                session, world["workspace"].id, world["agent"].id
            )
        assert context["skill_versions"] == {
            str(world["skill"].id): str(world["v2"].id)
        }
        assert len(context["declared_capabilities"]) == len(world["granted"])

    async def test_disabled_binding_excluded(
        self, binding_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, InstallationService(session_factory))
        binding = await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        await binding_service.update_binding(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id, binding_id=uuid.UUID(binding["id"]),
            enabled=False,
        )
        async with session_factory() as session:
            context = await binding_service.collect_enqueue_context(
                session, world["workspace"].id, world["agent"].id
            )
        assert context["skill_versions"] == {}

    async def test_disabled_installation_excluded(
        self, binding_service, installation_service, session_factory
    ) -> None:
        world = await _installed_world(session_factory, installation_service)
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        await installation_service.update_installation(
            actor=world["admin"], workspace_id=world["workspace"].id,
            installation_id=world["installation_id"], install_status="disabled",
        )
        async with session_factory() as session:
            context = await binding_service.collect_enqueue_context(
                session, world["workspace"].id, world["agent"].id
            )
        assert context["skill_versions"] == {}

    async def test_resolver_hook_integration(
        self, binding_service, session_factory
    ) -> None:
        """The agent trigger seam: register → handler-shaped call works."""
        from mesh.agent import triggers

        world = await _installed_world(session_factory, InstallationService(session_factory))
        await binding_service.bind(
            actor=world["admin"], workspace_id=world["workspace"].id,
            agent_id=world["agent"].id,
            skill_installation_id=world["installation_id"],
        )
        previous = triggers._SKILL_CONTEXT_RESOLVER
        triggers.register_skill_context_resolver(binding_service.collect_enqueue_context)
        try:
            async with session_factory() as session:
                context = await triggers._SKILL_CONTEXT_RESOLVER(
                    session, world["workspace"].id, world["agent"].id
                )
            assert str(world["skill"].id) in context["skill_versions"]
        finally:
            triggers.register_skill_context_resolver(previous)
