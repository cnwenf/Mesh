"""Binding service — agent ↔ installed skill versions (skill.md §2.5 / §3.1 / §3.2).

The fourth decoupling layer. A binding pins an agent to one installed
version — which may differ from the installation's current version
(canary / rollback, §5.1). The database's overlapping composite FK chain
guarantees the bound installation AND the bound version belong to the SAME
skill (README §6.2 rule 7); the service adds the business checks on top
(installation alive, version published).

``collect_bound_versions`` is the §6.11 producer: the agent enqueue path
(agent/triggers.py) freezes its result into
``task_executions.config_snapshot.skill_versions`` so in-flight runs keep
running their enqueue-time versions no matter what rebinds afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.audit import write_audit
from mesh.db.constraints import violates
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillInstallation,
    SkillSource,
    SkillVersion,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.skill.service import SkillService, skills_channel

BINDING_NOT_FOUND = "skill binding not found"
AGENT_NOT_FOUND = "agent not found"


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


class BindingService:
    """Stateless orchestrator over agent_skills."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # -- loading -------------------------------------------------------------------

    @staticmethod
    async def load_agent(
        session: AsyncSession, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Agent:
        agent = await session.scalar(
            select(Agent).where(
                Agent.workspace_id == workspace_id,
                Agent.id == agent_id,
                Agent.deleted_at.is_(None),
            )
        )
        if agent is None:
            raise NotFoundError(AGENT_NOT_FOUND)
        return agent

    @staticmethod
    async def load_binding(
        session: AsyncSession, workspace_id: uuid.UUID, binding_id: uuid.UUID
    ) -> AgentSkill:
        binding = await session.scalar(
            select(AgentSkill).where(
                AgentSkill.workspace_id == workspace_id,
                AgentSkill.id == binding_id,
            )
        )
        if binding is None:
            raise NotFoundError(BINDING_NOT_FOUND)
        return binding

    # -- serialization -----------------------------------------------------------------

    @staticmethod
    def render_binding(binding: AgentSkill) -> dict:
        return {
            "id": str(binding.id),
            "agent_id": str(binding.agent_id),
            "skill_id": str(binding.skill_id),
            "skill_installation_id": str(binding.skill_installation_id),
            "skill_version_id": str(binding.skill_version_id),
            "enabled": binding.enabled,
            "auto_trigger": binding.auto_trigger,
            "priority": binding.priority,
            "created_at": binding.created_at.isoformat(),
            "updated_at": binding.updated_at.isoformat(),
        }

    # -- bind ------------------------------------------------------------------------------

    async def bind(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        skill_installation_id: uuid.UUID,
        skill_version_id: uuid.UUID | None = None,
        auto_trigger: bool = True,
        priority: int = 100,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Bind an agent to an installed skill version (K5).

        ``skill_version_id`` defaults to the installation's current version;
        pinning another historic version supports canary / rollback (§5.1).
        """
        SkillService.require_manage(actor)
        if not 0 <= priority <= 1000:
            raise ValidationError(
                "priority out of range",
                details={"fields": [{"field": "priority", "issue": "out_of_range"}]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self.load_agent(session, workspace_id, agent_id)
            installation = await session.scalar(
                select(SkillInstallation).where(
                    SkillInstallation.workspace_id == workspace_id,
                    SkillInstallation.id == skill_installation_id,
                    SkillInstallation.deleted_at.is_(None),
                )
            )
            if installation is None:
                raise NotFoundError("skill installation not found")
            skill = await SkillService.load_skill(
                session, workspace_id, installation.skill_id
            )
            version_id = skill_version_id or installation.skill_version_id
            version = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.id == version_id,
                )
            )
            if version is None:
                raise NotFoundError("skill version not found")
            if version.status != "published":
                raise ConflictError(
                    "only published versions can be bound",
                    code="conflict",
                    details={"version_status": version.status},
                )

            now = _now(self._clock)
            binding = AgentSkill(
                workspace_id=workspace_id,
                agent_id=agent.id,
                skill_id=skill.id,
                skill_installation_id=installation.id,
                skill_version_id=version.id,
                enabled=True,
                auto_trigger=auto_trigger,
                priority=priority,
                created_at=now,
                updated_at=now,
            )
            session.add(binding)
            # Capture plain values BEFORE the flush: after a failed flush
            # the transaction is dead and ORM attribute access would raise.
            agent_id_value = agent.id
            installation_id_value = installation.id
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_agent_skills"):
                    raise ConflictError(
                        "this installation is already bound to the agent",
                        code="conflict",
                        details={
                            "agent_id": str(agent_id_value),
                            "skill_installation_id": str(installation_id_value),
                        },
                    ) from exc
                raise

            rendered = self.render_binding(binding)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": str(installation.id),
                    "change_type": "bound",
                    "agent_id": str(agent.id),
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.bound",
                resource_type="agent_skill",
                resource_id=binding.id,
                metadata={
                    "agent_id": str(agent.id),
                    "skill_id": str(skill.id),
                    "skill_version_id": str(version.id),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- list (agent skills, §3.2 example shape) ----------------------------------------------

    async def list_agent_skills(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        from sqlalchemy.sql.expression import tuple_

        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            await self.load_agent(session, workspace_id, agent_id)
            stmt = (
                select(AgentSkill, Skill, SkillVersion, SkillInstallation, SkillSource)
                .join(
                    Skill,
                    (Skill.workspace_id == AgentSkill.workspace_id)
                    & (Skill.id == AgentSkill.skill_id),
                )
                .join(
                    SkillVersion,
                    (SkillVersion.workspace_id == AgentSkill.workspace_id)
                    & (SkillVersion.id == AgentSkill.skill_version_id),
                )
                .join(
                    SkillInstallation,
                    (SkillInstallation.workspace_id == AgentSkill.workspace_id)
                    & (SkillInstallation.id == AgentSkill.skill_installation_id),
                )
                .outerjoin(
                    SkillSource,
                    (SkillSource.workspace_id == Skill.workspace_id)
                    & (SkillSource.id == Skill.source_id),
                )
                .where(
                    AgentSkill.workspace_id == workspace_id,
                    AgentSkill.agent_id == agent_id,
                )
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(AgentSkill.created_at, AgentSkill.id)
                    > (position.sort_value, position.id)
                )
            stmt = stmt.order_by(
                AgentSkill.created_at.asc(), AgentSkill.id.asc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).all()

        items = []
        for binding, skill, version, installation, source in rows[:limit]:
            items.append(
                {
                    "binding_id": str(binding.id),
                    "skill": {
                        "id": str(skill.id),
                        "name": skill.name,
                        "slug": skill.slug,
                        "summary": skill.summary,
                        "source_type": source.source_type if source else None,
                        "trust_level": source.trust_level if source else None,
                        "status": skill.status,
                    },
                    "skill_version_id": str(version.id),
                    "version": version.version,
                    "install_status": installation.install_status,
                    "enabled": binding.enabled,
                    "auto_trigger": binding.auto_trigger,
                    "priority": binding.priority,
                }
            )
        next_cursor = None
        if len(rows) > limit:
            last_binding = rows[limit - 1][0]
            next_cursor = encode_cursor(last_binding.created_at, last_binding.id)
        return items, next_cursor

    # -- update / unbind ------------------------------------------------------------------------

    async def update_binding(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        enabled: bool | None = None,
        auto_trigger: bool | None = None,
        priority: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        SkillService.require_manage(actor)
        if priority is not None and not 0 <= priority <= 1000:
            raise ValidationError(
                "priority out of range",
                details={"fields": [{"field": "priority", "issue": "out_of_range"}]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self.load_agent(session, workspace_id, agent_id)
            binding = await self.load_binding(session, workspace_id, binding_id)
            if binding.agent_id != agent_id:
                raise NotFoundError(BINDING_NOT_FOUND)
            now = _now(self._clock)
            changed: list[str] = []
            if enabled is not None and enabled != binding.enabled:
                binding.enabled = enabled
                changed.append("enabled")
            if auto_trigger is not None and auto_trigger != binding.auto_trigger:
                binding.auto_trigger = auto_trigger
                changed.append("auto_trigger")
            if priority is not None and priority != binding.priority:
                binding.priority = priority
                changed.append("priority")
            if not changed:
                return self.render_binding(binding)
            binding.updated_at = now
            await session.flush()
            rendered = self.render_binding(binding)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(binding.skill_id),
                    "installation_id": str(binding.skill_installation_id),
                    "change_type": "binding_updated",
                    "agent_id": str(agent_id),
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.binding_updated",
                resource_type="agent_skill",
                resource_id=binding.id,
                metadata={"changed": sorted(changed)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def unbind(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        SkillService.require_manage(actor)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self.load_agent(session, workspace_id, agent_id)
            binding = await self.load_binding(session, workspace_id, binding_id)
            if binding.agent_id != agent_id:
                raise NotFoundError(BINDING_NOT_FOUND)
            skill_id = binding.skill_id
            installation_id = binding.skill_installation_id
            await session.delete(binding)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill_id),
                    "installation_id": str(installation_id),
                    "change_type": "unbound",
                    "agent_id": str(agent_id),
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.unbound",
                resource_type="agent_skill",
                resource_id=binding.id,
                metadata={"agent_id": str(agent_id), "skill_id": str(skill_id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )

    # -- §6.11 snapshot producer ---------------------------------------------------------------

    async def collect_bound_versions(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> dict[str, str]:
        """``{skill_id: skill_version_id}`` for the agent's ENABLED bindings.

        The enqueue path freezes this into
        ``task_executions.config_snapshot.skill_versions`` (README §6.11) —
        later rebinds / rollbacks only affect SUBSEQUENT enqueues; in-flight
        executions keep running their snapshot's versions. Disabled
        bindings and disabled installations / skills are excluded so a
        kill-switch takes effect on the next enqueue (三档停用, §4.4).
        """
        rows = (
            await session.execute(
                select(AgentSkill.skill_id, AgentSkill.skill_version_id)
                .join(
                    SkillInstallation,
                    (SkillInstallation.workspace_id == AgentSkill.workspace_id)
                    & (SkillInstallation.id == AgentSkill.skill_installation_id)
                    & (SkillInstallation.deleted_at.is_(None)),
                )
                .join(
                    Skill,
                    (Skill.workspace_id == AgentSkill.workspace_id)
                    & (Skill.id == AgentSkill.skill_id)
                    & (Skill.deleted_at.is_(None)),
                )
                .where(
                    AgentSkill.workspace_id == workspace_id,
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.enabled.is_(True),
                    SkillInstallation.install_status != "disabled",
                    Skill.status.notin_(["disabled"]),
                )
            )
        ).all()
        return {str(skill_id): str(version_id) for skill_id, version_id in rows}

    async def collect_enqueue_context(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> dict:
        """The §6.11 enqueue inputs derived from this module's state.

        Returns ``{"skill_versions": {skill_id: version_id},
        "declared_capabilities": [...]}`` — the frozen version map plus the
        union of the granted capability declarations of every counted
        binding. The enqueue path (agent/triggers.py) runs the §3.3
        normalization over ``declared_capabilities`` to derive the strict
        scheduling / authorization fields; this module only supplies the
        declaration-layer inputs (R3 separation).
        """
        rows = (
            await session.execute(
                select(
                    AgentSkill.skill_id,
                    AgentSkill.skill_version_id,
                    SkillInstallation.granted_capabilities,
                )
                .join(
                    SkillInstallation,
                    (SkillInstallation.workspace_id == AgentSkill.workspace_id)
                    & (SkillInstallation.id == AgentSkill.skill_installation_id)
                    & (SkillInstallation.deleted_at.is_(None)),
                )
                .join(
                    Skill,
                    (Skill.workspace_id == AgentSkill.workspace_id)
                    & (Skill.id == AgentSkill.skill_id)
                    & (Skill.deleted_at.is_(None)),
                )
                .where(
                    AgentSkill.workspace_id == workspace_id,
                    AgentSkill.agent_id == agent_id,
                    AgentSkill.enabled.is_(True),
                    SkillInstallation.install_status != "disabled",
                    Skill.status.notin_(["disabled"]),
                )
            )
        ).all()
        skill_versions = {
            str(skill_id): str(version_id) for skill_id, version_id, _ in rows
        }
        declared: list = []
        for _, _, granted in rows:
            declared.extend(granted or [])
        return {"skill_versions": skill_versions, "declared_capabilities": declared}


__all__ = ["AGENT_NOT_FOUND", "BINDING_NOT_FOUND", "BindingService"]
