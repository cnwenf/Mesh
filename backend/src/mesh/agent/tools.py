"""Per-agent capability grant service (agent.md §2.5 / §3.1).

The public ``/agents/{id}/tools`` surface is deliberately a thin wrapper
over ``skill_installations.granted_capabilities``.  There is no tool catalog
or tool-binding table.  When an agent mutates a grant inherited from a shared
workspace installation, the service creates/reuses an agent-scoped
installation and repoints only that agent's skill binding.  This copy-on-write
keeps another agent's grants unchanged while preserving skill.md's four-layer
model.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.agent.capabilities import normalize_capability_declarations
from mesh.agent.service import AgentService
from mesh.auth.audit import write_audit
from mesh.auth.deps import AuthenticatedPrincipal
from mesh.auth.rbac import assert_scope, resolve_workspace_context
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.skill import (
    AgentSkill,
    Skill,
    SkillImportTask,
    SkillInstallation,
    SkillVersion,
    installation_matches_binding_agent,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ConflictError, NotFoundError, ValidationError
from mesh.outbox.service import emit_realtime
from mesh.skill.capabilities import assert_grants_subset_of_required

TOOL_NOT_FOUND = "agent capability grant not found"
_PERMISSIONS = frozenset({"read_only", "write", "confirm_required"})
_PERMISSION_RANK = {"read_only": 1, "write": 2, "confirm_required": 3}


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def _validate_capability(value: str) -> str:
    key = value.strip()
    if not key or len(key) > 256 or "\x00" in key:
        raise ValidationError(
            "invalid capability key",
            details={"fields": [{"field": "capability", "issue": "invalid"}]},
        )
    return key


def _validate_permission(value: str) -> str:
    if value not in _PERMISSIONS:
        raise ValidationError(
            "invalid capability permission",
            details={"fields": [{"field": "permission", "issue": "invalid_enum"}]},
        )
    return value


class AgentToolService:
    """Read and mutate the grants reachable through one agent's bindings."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    async def resolve_actor(
        self, *, principal: AuthenticatedPrincipal, agent_id: uuid.UUID
    ) -> tuple[uuid.UUID, Member]:
        """Resolve the exact Spec route without weakening tenant RLS.

        ``mesh_agent_workspace`` is a SECURITY DEFINER lookup which returns a
        workspace only when ``user`` is an active member there.  The normal
        tenant GUC and visibility checks apply immediately afterwards.
        """
        async with self._factory() as session, session.begin():
            if principal.user_id is None:
                raise NotFoundError("agent not found")
            workspace_id = await session.scalar(
                select(func.mesh_agent_workspace(agent_id, principal.user_id))
            )
            if workspace_id is None:
                raise NotFoundError("agent not found")
            try:
                context = await resolve_workspace_context(
                    session,
                    principal=principal,
                    workspace_id=workspace_id,
                )
            except NotFoundError as exc:
                # Keep an unknown agent, a PAT outside its owning tenant and
                # a deleted workspace indistinguishable.
                raise NotFoundError("agent not found") from exc
            return workspace_id, context.member

    @staticmethod
    async def _load_agent(
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        write: bool,
    ) -> Agent:
        statement = select(Agent).where(
            Agent.workspace_id == workspace_id,
            Agent.id == agent_id,
            Agent.deleted_at.is_(None),
        )
        # Serialize all capability mutations for an agent. In particular, two
        # simultaneous first writes must not both attempt to create the one
        # agent-scoped installation allowed by ``uq_install_scope``.
        if write:
            statement = statement.with_for_update()
        agent = await session.scalar(statement)
        if agent is None:
            raise NotFoundError("agent not found")
        AgentService._assert_visible(actor, agent)
        if write:
            assert_scope(actor, "agent:manage")
            AgentService._assert_can_manage(actor, agent)
        return agent

    @staticmethod
    async def _bound_rows(
        session: AsyncSession, workspace_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[tuple[AgentSkill, SkillInstallation, SkillVersion]]:
        return list(
            (
                await session.execute(
                    select(AgentSkill, SkillInstallation, SkillVersion)
                    .join(
                        SkillInstallation,
                        (SkillInstallation.workspace_id == AgentSkill.workspace_id)
                        & (SkillInstallation.id == AgentSkill.skill_installation_id)
                        & (SkillInstallation.deleted_at.is_(None))
                        & installation_matches_binding_agent(),
                    )
                    .join(
                        SkillVersion,
                        (SkillVersion.workspace_id == AgentSkill.workspace_id)
                        & (SkillVersion.id == AgentSkill.skill_version_id),
                    )
                    .where(
                        AgentSkill.workspace_id == workspace_id,
                        AgentSkill.agent_id == agent_id,
                    )
                    .order_by(AgentSkill.priority.desc(), AgentSkill.created_at, AgentSkill.id)
                )
            ).all()
        )

    @staticmethod
    def _render_rows(
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]],
    ) -> list[dict]:
        enabled_permissions: dict[str, str] = {}
        disabled_permissions: dict[str, str] = {}
        for _binding, installation, _version in rows:
            grants = list(installation.granted_capabilities or [])
            for raw in grants:
                item = normalize_capability_declarations([raw], allow_enabled=True)["grants"][0]
                key = item["capability"]
                grant_enabled = not isinstance(raw, dict) or raw.get("enabled") is not False
                target = enabled_permissions if grant_enabled else disabled_permissions
                current = target.get(key)
                if current is None or _PERMISSION_RANK[current] < _PERMISSION_RANK[item["permission"]]:
                    target[key] = item["permission"]
        keys = sorted(set(enabled_permissions) | set(disabled_permissions))
        return [
            {
                "capability": key,
                "permission": (
                    enabled_permissions[key] if key in enabled_permissions else disabled_permissions[key]
                ),
                "enabled": key in enabled_permissions,
            }
            for key in keys
        ]

    @staticmethod
    def _assert_unambiguous_skill_bindings(
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]],
    ) -> None:
        """Reject a legacy state that copy-on-write cannot safely merge.

        The binding service prevents duplicate installation bindings, but an
        agent can still have workspace- and agent-scoped installations for the
        same skill. Repointing the former to the latter would violate
        ``uq_agent_skills`` and silently choosing one version would be
        ambiguous, so fail before mutating either installation.
        """
        seen: set[uuid.UUID] = set()
        duplicates: set[uuid.UUID] = set()
        for binding, _installation, _version in rows:
            if binding.skill_id in seen:
                duplicates.add(binding.skill_id)
            seen.add(binding.skill_id)
        if duplicates:
            raise ConflictError(
                "agent has multiple bindings for the same skill",
                details={"skill_ids": sorted(str(skill_id) for skill_id in duplicates)},
            )

    async def list_tools(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> list[dict]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self._load_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                write=False,
            )
            return self._render_rows(await self._bound_rows(session, workspace_id, agent_id))

    @staticmethod
    def _grant_map(installation: SkillInstallation) -> dict[str, dict[str, object]]:
        return {
            item["capability"]: {
                "permission": item["permission"],
                "enabled": item["enabled"],
            }
            for item in AgentToolService._render_rows([(None, installation, None)])
        }

    @staticmethod
    def _declares(required: list, capability: str, permission: str) -> bool:
        try:
            assert_grants_subset_of_required(
                [{"capability": capability, "permission": permission}],
                required,
            )
        except BusinessRuleError:
            return False
        return capability in {
            item["capability"]
            for item in normalize_capability_declarations(list(required or []), allow_enabled=True)["grants"]
        }

    @classmethod
    def _safe_grants(cls, installation: SkillInstallation, ceiling: list) -> list[dict]:
        safe: list[dict] = []
        for capability, grant in cls._grant_map(installation).items():
            permission = str(grant["permission"])
            if cls._declares(ceiling, capability, permission):
                safe.append(
                    {
                        "capability": capability,
                        "permission": permission,
                        "enabled": bool(grant["enabled"]),
                    }
                )
        return sorted(safe, key=lambda item: item["capability"])

    @staticmethod
    async def _approved_ceiling(session: AsyncSession, version: SkillVersion) -> list:
        """Return the immutable authorization ceiling for a bound version.

        Untrusted scripted imports freeze the approver's minimized grants on
        their import task.  A tool edit may tighten or disable that set, but
        must never re-add an omitted key or escalate its permission back to
        the manifest's broader declaration. Trusted/non-scripted versions use
        the immutable required declarations as their ceiling.
        """
        approval = await session.scalar(
            select(SkillImportTask)
            .where(
                SkillImportTask.workspace_id == version.workspace_id,
                SkillImportTask.skill_id == version.skill_id,
                SkillImportTask.skill_version_id == version.id,
                SkillImportTask.requires_approval.is_(True),
                SkillImportTask.reviewed_at.is_not(None),
                SkillImportTask.status.in_(("ready", "installing", "installed")),
            )
            .order_by(
                SkillImportTask.reviewed_at.desc(),
                SkillImportTask.created_at.desc(),
                SkillImportTask.id.desc(),
            )
            .limit(1)
        )
        if approval is not None:
            return list(approval.granted_capabilities or [])
        pending_approval = await session.scalar(
            select(SkillImportTask.id)
            .where(
                SkillImportTask.workspace_id == version.workspace_id,
                SkillImportTask.skill_id == version.skill_id,
                SkillImportTask.skill_version_id == version.id,
                SkillImportTask.requires_approval.is_(True),
            )
            .limit(1)
        )
        if pending_approval is not None:
            # Corrupt/legacy state: a version that required review has no
            # durable approval. Fail closed rather than restoring manifest
            # permissions that no human accepted.
            return []
        return list(version.required_capabilities or [])

    @classmethod
    async def _approved_ceilings(
        cls,
        session: AsyncSession,
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]],
    ) -> dict[uuid.UUID, list]:
        ceilings: dict[uuid.UUID, list] = {}
        for _binding, _installation, version in rows:
            if version.id not in ceilings:
                ceilings[version.id] = await cls._approved_ceiling(session, version)
        return ceilings

    async def _private_installation(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        agent_id: uuid.UUID,
        binding: AgentSkill,
        installation: SkillInstallation,
        version: SkillVersion,
        ceiling: list,
    ) -> SkillInstallation:
        now = _now(self._clock)
        if installation.scope == "agent" and installation.agent_id == agent_id:
            foreign_binding = await session.scalar(
                select(AgentSkill.id).where(
                    AgentSkill.workspace_id == binding.workspace_id,
                    AgentSkill.skill_installation_id == installation.id,
                    AgentSkill.agent_id != agent_id,
                )
            )
            if foreign_binding is not None:
                raise ConflictError("agent-scoped installation is bound to another agent")
            # Existing data can predate the binding's pinned version.  Every
            # mutation normalizes the private grants against that version so a
            # stale capability can never enter an enqueue snapshot.
            installation.skill_version_id = binding.skill_version_id
            installation.granted_capabilities = self._safe_grants(installation, ceiling)
            installation.updated_at = now
            return installation

        private = await session.scalar(
            select(SkillInstallation).where(
                SkillInstallation.workspace_id == binding.workspace_id,
                SkillInstallation.skill_id == binding.skill_id,
                SkillInstallation.scope == "agent",
                SkillInstallation.agent_id == agent_id,
                SkillInstallation.deleted_at.is_(None),
            )
        )
        if private is not None:
            foreign_binding = await session.scalar(
                select(AgentSkill.id).where(
                    AgentSkill.workspace_id == binding.workspace_id,
                    AgentSkill.skill_installation_id == private.id,
                    AgentSkill.agent_id != agent_id,
                )
            )
            if foreign_binding is not None:
                raise ConflictError("agent-scoped installation is bound to another agent")
        if private is None:
            private = SkillInstallation(
                workspace_id=binding.workspace_id,
                skill_id=binding.skill_id,
                skill_version_id=binding.skill_version_id,
                scope="agent",
                agent_id=agent_id,
                install_status=installation.install_status,
                auto_update=False,
                granted_capabilities=self._safe_grants(installation, ceiling),
                installed_by=actor.id,
                installed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(private)
            await session.flush()
        elif binding.skill_installation_id != private.id:
            # A stale, unbound private installation occupies the unique
            # agent+skill slot. Reconcile it from the live bound installation
            # before repointing instead of silently inheriting its old
            # version, disabled state or grants.
            private.skill_version_id = binding.skill_version_id
            private.install_status = installation.install_status
            private.auto_update = False
            private.granted_capabilities = self._safe_grants(installation, ceiling)
            private.updated_at = now
        else:
            # A previous item in this batch already repointed the binding.
            # Preserve that item's change while still dropping stale grants.
            private.skill_version_id = binding.skill_version_id
            private.granted_capabilities = self._safe_grants(private, ceiling)
            private.updated_at = now
        binding.skill_installation_id = private.id
        binding.updated_at = now
        return private

    @staticmethod
    def _replace_grant(
        installation: SkillInstallation,
        capability: str,
        permission: str | None,
        *,
        enabled: bool = True,
    ) -> None:
        grants = AgentToolService._grant_map(installation)
        if permission is None:
            grants.pop(capability, None)
        else:
            grants[capability] = {"permission": permission, "enabled": enabled}
        installation.granted_capabilities = [
            {
                "capability": key,
                "permission": grants[key]["permission"],
                "enabled": grants[key]["enabled"],
            }
            for key in sorted(grants)
        ]

    async def _emit_change(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        action: str,
        capability: str,
        permission: str | None,
        enabled: bool,
        visibility: str,
        updated_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        await emit_realtime(
            session,
            workspace_id=workspace_id,
            # Capability details for a private agent must never be broadcast
            # on a workspace-membership channel. ``agent:{id}`` has a
            # resource checker that admits only the owner/admin for private
            # agents, while remaining subscribable by members for workspace
            # agents.
            channel=f"agent:{agent_id}",
            event="agent.updated",
            data={
                "change_type": "tool_grant_changed",
                "id": str(agent_id),
                "agent_id": str(agent_id),
                "capability": capability,
                "permission": permission,
                "enabled": enabled,
                "visibility": visibility,
                "updated_at": updated_at.isoformat(),
            },
        )
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=actor.id,
            actor_kind="member",
            action=action,
            resource_type="agent",
            resource_id=agent_id,
            metadata={
                "capability": capability,
                "permission": permission,
                "enabled": enabled,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

    @staticmethod
    def _existing_tool(
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]], capability: str
    ) -> dict | None:
        rendered = AgentToolService._render_rows(rows)
        return next((item for item in rendered if item["capability"] == capability), None)

    async def _remove_from_all(
        self,
        session: AsyncSession,
        *,
        actor: Member,
        agent_id: uuid.UUID,
        capability: str,
        rows: list[tuple[AgentSkill, SkillInstallation, SkillVersion]],
        ceilings: dict[uuid.UUID, list],
    ) -> None:
        for binding, installation, version in rows:
            if capability not in self._grant_map(installation):
                continue
            private = await self._private_installation(
                session,
                actor=actor,
                agent_id=agent_id,
                binding=binding,
                installation=installation,
                version=version,
                ceiling=ceilings[version.id],
            )
            self._replace_grant(private, capability, None)
            assert_grants_subset_of_required(private.granted_capabilities, ceilings[version.id])
            private.updated_at = _now(self._clock)

    @staticmethod
    def _candidate(
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]],
        ceilings: dict[uuid.UUID, list],
        active_skill_ids: set[uuid.UUID],
        capability: str,
        permission: str,
    ) -> tuple[AgentSkill, SkillInstallation, SkillVersion] | None:
        eligible = [
            row
            for row in rows
            if AgentToolService._declares(
                ceilings[row[2].id], capability, permission
            )
        ]

        def is_runtime_active(
            row: tuple[AgentSkill, SkillInstallation, SkillVersion],
        ) -> bool:
            binding, installation, _version = row
            return (
                binding.enabled
                and installation.install_status != "disabled"
                and binding.skill_id in active_skill_ids
            )

        def currently_grants(
            row: tuple[AgentSkill, SkillInstallation, SkillVersion],
        ) -> bool:
            return capability in AgentToolService._grant_map(row[1])

        return next(
            (
                row
                for rank in (
                    lambda item: is_runtime_active(item) and currently_grants(item),
                    is_runtime_active,
                    currently_grants,
                    lambda _item: True,
                )
                for row in eligible
                if rank(row)
            ),
            None,
        )

    @staticmethod
    async def _active_skill_ids(
        session: AsyncSession,
        workspace_id: uuid.UUID,
        rows: Iterable[tuple[AgentSkill, SkillInstallation, SkillVersion]],
    ) -> set[uuid.UUID]:
        skill_ids = {binding.skill_id for binding, _installation, _version in rows}
        if not skill_ids:
            return set()
        return set(
            (
                await session.scalars(
                    select(Skill.id).where(
                        Skill.workspace_id == workspace_id,
                        Skill.id.in_(skill_ids),
                        Skill.deleted_at.is_(None),
                        Skill.status.notin_(["disabled"]),
                    )
                )
            ).all()
        )

    async def bind_tools(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        grants: list[dict[str, str]],
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> list[dict]:
        normalized_input = [
            {
                "capability": _validate_capability(item["capability"]),
                "permission": _validate_permission(item["permission"]),
            }
            for item in grants
        ]
        keys = [item["capability"] for item in normalized_input]
        if len(set(keys)) != len(keys):
            raise ValidationError(
                "duplicate capability in request",
                details={"fields": [{"field": "capabilities", "issue": "duplicate"}]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                write=True,
            )
            rows = await self._bound_rows(session, workspace_id, agent_id)
            self._assert_unambiguous_skill_bindings(rows)
            ceilings = await self._approved_ceilings(session, rows)
            active_skill_ids = await self._active_skill_ids(session, workspace_id, rows)
            existing = {item["capability"] for item in self._render_rows(rows)}
            duplicate = sorted(existing.intersection(keys))
            if duplicate:
                raise ConflictError(
                    "capability is already bound",
                    details={"capabilities": duplicate},
                )
            candidates: list[tuple[dict[str, str], tuple[AgentSkill, SkillInstallation, SkillVersion]]] = []
            for grant in normalized_input:
                candidate = self._candidate(
                    rows,
                    ceilings,
                    active_skill_ids,
                    grant["capability"],
                    grant["permission"],
                )
                if candidate is None:
                    raise BusinessRuleError(
                        "capability is not declared by a bound skill",
                        code="capability_not_declared",
                        details={"undeclared": [grant["capability"]]},
                    )
                candidates.append((grant, candidate))
            changed_at = _now(self._clock)
            agent.updated_at = changed_at
            result: list[dict] = []
            for grant, (binding, installation, version) in candidates:
                private = await self._private_installation(
                    session,
                    actor=actor,
                    agent_id=agent_id,
                    binding=binding,
                    installation=installation,
                    version=version,
                    ceiling=ceilings[version.id],
                )
                self._replace_grant(private, grant["capability"], grant["permission"])
                assert_grants_subset_of_required(private.granted_capabilities, ceilings[version.id])
                private.updated_at = _now(self._clock)
                result.append({**grant, "enabled": True})
                await self._emit_change(
                    session,
                    actor=actor,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    action="agent.tool_bound",
                    capability=grant["capability"],
                    permission=grant["permission"],
                    enabled=True,
                    visibility=agent.visibility,
                    updated_at=changed_at,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            return result

    async def update_tool(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        capability: str,
        permission: str | None,
        enabled: bool | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        capability = _validate_capability(capability)
        if permission is not None:
            permission = _validate_permission(permission)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                write=True,
            )
            rows = await self._bound_rows(session, workspace_id, agent_id)
            self._assert_unambiguous_skill_bindings(rows)
            ceilings = await self._approved_ceilings(session, rows)
            active_skill_ids = await self._active_skill_ids(session, workspace_id, rows)
            current = self._existing_tool(rows, capability)
            if current is None:
                raise NotFoundError(TOOL_NOT_FOUND)
            desired_permission = permission or current["permission"]
            desired_enabled = current["enabled"] if enabled is None else enabled
            candidate = self._candidate(
                rows,
                ceilings,
                active_skill_ids,
                capability,
                desired_permission,
            )
            if candidate is None:
                raise BusinessRuleError(
                    "granted permission exceeds the declared permission",
                    code="capability_not_declared",
                    details={"capability": capability, "permission": desired_permission},
                )
            await self._remove_from_all(
                session,
                actor=actor,
                agent_id=agent_id,
                capability=capability,
                rows=rows,
                ceilings=ceilings,
            )
            binding, installation, version = candidate
            private = await self._private_installation(
                session,
                actor=actor,
                agent_id=agent_id,
                binding=binding,
                installation=installation,
                version=version,
                ceiling=ceilings[version.id],
            )
            self._replace_grant(
                private,
                capability,
                desired_permission,
                enabled=bool(desired_enabled),
            )
            assert_grants_subset_of_required(private.granted_capabilities, ceilings[version.id])
            private.updated_at = _now(self._clock)
            result = {
                "capability": capability,
                "permission": desired_permission,
                "enabled": bool(desired_enabled),
            }
            changed_at = _now(self._clock)
            agent.updated_at = changed_at
            await self._emit_change(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                action="agent.tool_updated",
                capability=capability,
                permission=desired_permission,
                enabled=result["enabled"],
                visibility=agent.visibility,
                updated_at=changed_at,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return result

    async def unbind_tool(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        capability: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        capability = _validate_capability(capability)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            agent = await self._load_agent(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                write=True,
            )
            rows = await self._bound_rows(session, workspace_id, agent_id)
            self._assert_unambiguous_skill_bindings(rows)
            ceilings = await self._approved_ceilings(session, rows)
            existing = self._existing_tool(rows, capability)
            if existing is None:
                raise NotFoundError(TOOL_NOT_FOUND)
            await self._remove_from_all(
                session,
                actor=actor,
                agent_id=agent_id,
                capability=capability,
                rows=rows,
                ceilings=ceilings,
            )
            changed_at = _now(self._clock)
            agent.updated_at = changed_at
            await self._emit_change(
                session,
                actor=actor,
                workspace_id=workspace_id,
                agent_id=agent_id,
                action="agent.tool_unbound",
                capability=capability,
                permission=existing["permission"],
                enabled=False,
                visibility=agent.visibility,
                updated_at=changed_at,
                ip_address=ip_address,
                user_agent=user_agent,
            )


__all__ = ["AgentToolService", "TOOL_NOT_FOUND"]
