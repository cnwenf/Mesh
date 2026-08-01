"""Installation service — the third layer: versions brought into scopes (skill.md §2.4 / §4.3 / §4.4).

Rules enforced here (spec anchors):

* only ``published`` skills / versions are installable — anything else 423
  ``locked`` (§3.3);
* ``scope='agent'`` requires ``agent_id`` — otherwise 400 (§5.1);
* duplicate installation in the same scope → 409 ``conflict`` (§5.1, DB
  partial unique index ``uq_install_scope``);
* untrusted sources WITH scripts need an approved import task — otherwise
  422 ``approval_required`` (§5.3); ``granted_capabilities`` comes from the
  approval and is always ⊆ ``required_capabilities`` (422
  ``capability_not_declared`` otherwise);
* upgrades need explicit confirmation (PATCH); ``auto_update`` follows only
  non-breaking PATCH versions with unchanged script hashes (§4.4) — anything
  else marks ``updated_available`` + ``skill.update_available``;
* rollback points the installation at ANY historic version (§4.2: history is
  never deleted); the overlapping composite FK guarantees the target version
  belongs to the same skill (README §6.2 rule 7).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.constraints import violates
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.models.skill import (
    Skill,
    SkillImportTask,
    SkillInstallation,
    SkillScript,
    SkillSource,
    SkillVersion,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    LockedError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.skill.capabilities import assert_grants_subset_of_required
from mesh.skill.semver import is_non_breaking_patch
from mesh.skill.service import SkillService, skills_channel

INSTALLATION_NOT_FOUND = "skill installation not found"

# Import task states that prove the review gate passed (no-approval imports
# land in 'ready' directly; approved ones carry reviewed_at).
_APPROVED_TASK_STATES = ("ready", "installing", "installed")


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


class InstallationService:
    """Stateless orchestrator over skill_installations."""

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
    async def load_installation(
        session: AsyncSession, workspace_id: uuid.UUID, installation_id: uuid.UUID
    ) -> SkillInstallation:
        installation = await session.scalar(
            select(SkillInstallation).where(
                SkillInstallation.workspace_id == workspace_id,
                SkillInstallation.id == installation_id,
                SkillInstallation.deleted_at.is_(None),
            )
        )
        if installation is None:
            raise NotFoundError(INSTALLATION_NOT_FOUND)
        return installation

    # -- serialization -----------------------------------------------------------------

    @staticmethod
    def render(installation: SkillInstallation) -> dict:
        return {
            "id": str(installation.id),
            "workspace_id": str(installation.workspace_id),
            "skill_id": str(installation.skill_id),
            "skill_version_id": str(installation.skill_version_id),
            "scope": installation.scope,
            "agent_id": (
                str(installation.agent_id) if installation.agent_id is not None else None
            ),
            "install_status": installation.install_status,
            "auto_update": installation.auto_update,
            "granted_capabilities": installation.granted_capabilities,
            "installed_by": str(installation.installed_by),
            "installed_at": installation.installed_at.isoformat(),
            "created_at": installation.created_at.isoformat(),
            "updated_at": installation.updated_at.isoformat(),
        }

    # -- install -----------------------------------------------------------------------

    async def install(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        skill_version_id: uuid.UUID,
        scope: str = "workspace",
        agent_id: uuid.UUID | None = None,
        auto_update: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Install a published version into a workspace / agent scope."""
        SkillService.require_manage(actor)
        if scope not in ("workspace", "agent"):
            raise ValidationError("invalid scope", details={"scope": scope})
        if scope == "agent" and agent_id is None:
            raise ValidationError(
                "agent-scoped installations require agent_id",
                details={"fields": [{"field": "agent_id", "issue": "required"}]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _now(self._clock)
            skill = await SkillService.load_skill(session, workspace_id, skill_id)
            version = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.id == skill_version_id,
                )
            )
            if version is None:
                raise NotFoundError("skill version not found")
            source = await session.scalar(
                select(SkillSource).where(
                    SkillSource.workspace_id == workspace_id,
                    SkillSource.id == skill.source_id,
                )
            )
            # Approval gate FIRST (§3.3): an unreviewed third-party skill
            # with scripts reports 422 approval_required — the more specific
            # cause — rather than the generic 423 locked for its draft state.
            granted = await self._resolve_grants(
                session,
                workspace_id=workspace_id,
                skill=skill,
                version=version,
                source=source,
            )
            if skill.status in ("draft", "disabled"):
                raise LockedError(
                    "skill is not installable in its current state",
                    details={"status": skill.status},
                )
            if version.status != "published":
                raise LockedError(
                    "only published versions are installable",
                    details={"version_status": version.status},
                )

            installation = SkillInstallation(
                workspace_id=workspace_id,
                skill_id=skill.id,
                skill_version_id=version.id,
                scope=scope,
                agent_id=agent_id,
                install_status="installed",
                auto_update=auto_update,
                granted_capabilities=granted,
                installed_by=actor.id,
                installed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(installation)
            # Capture plain values BEFORE the flush: after a failed flush
            # the transaction is dead and ORM attribute access would raise.
            skill_id_value = skill.id
            try:
                await session.flush()
            except IntegrityError as exc:
                if violates(exc, "uq_install_scope"):
                    raise ConflictError(
                        "this skill is already installed in this scope",
                        code="conflict",
                        details={"skill_id": str(skill_id_value), "scope": scope},
                    ) from exc
                raise

            # An approved import task (when present) advances to installed.
            task = await self._approval_task(session, workspace_id, skill.id, version.id)
            if task is not None and task.status in _APPROVED_TASK_STATES:
                task.status = "installed"
                task.installation_id = installation.id
                task.updated_at = now

            rendered = self.render(installation)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": str(installation.id),
                    "change_type": "installed",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.installed",
                resource_type="skill_installation",
                resource_id=installation.id,
                metadata={
                    "skill_id": str(skill.id),
                    "skill_version_id": str(version.id),
                    "scope": scope,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def _approval_task(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        version_id: uuid.UUID,
    ) -> SkillImportTask | None:
        """The import task that produced this skill version (if any)."""
        return await session.scalar(
            select(SkillImportTask).where(
                SkillImportTask.workspace_id == workspace_id,
                SkillImportTask.skill_id == skill_id,
                SkillImportTask.skill_version_id == version_id,
            )
        )

    async def _resolve_grants(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        skill: Skill,
        version: SkillVersion,
        source: SkillSource | None,
    ) -> list:
        """Derive granted_capabilities; enforce the approval gate (§5.3).

        Trusted / reviewed sources grant the full declared surface.
        Untrusted sources WITH scripts require an approved import task —
        its ``granted_capabilities`` (the approver's minimized subset)
        become the installation's grants. Without scripts there is nothing
        to review, so instructions-only imports install directly.
        """
        trust = source.trust_level if source is not None else "untrusted"
        scripts = (
            await session.execute(
                select(SkillScript.id).where(SkillScript.skill_version_id == version.id)
            )
        ).first()
        has_scripts = scripts is not None

        if trust == "untrusted" and has_scripts:
            task = await self._approval_task(session, workspace_id, skill.id, version.id)
            approved = task is not None and (
                task.status in _APPROVED_TASK_STATES
                and (not task.requires_approval or task.reviewed_at is not None)
            )
            if not approved:
                raise BusinessRuleError(
                    "third-party skill scripts require human approval before install",
                    code="approval_required",
                    details={"skill_id": str(skill.id)},
                )
            granted = list(task.granted_capabilities or [])
        else:
            granted = list(version.required_capabilities or [])

        assert_grants_subset_of_required(granted, version.required_capabilities)
        return granted

    # -- list -----------------------------------------------------------------------------

    async def list_installations(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID | None = None,
        scope: str = "all",
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        from sqlalchemy.sql.expression import tuple_

        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(SkillInstallation).where(
                SkillInstallation.workspace_id == workspace_id,
                SkillInstallation.deleted_at.is_(None),
            )
            if not role_satisfies(actor.role, "agent:manage"):
                stmt = stmt.outerjoin(
                    Agent,
                    and_(
                        SkillInstallation.scope == "agent",
                        Agent.workspace_id == SkillInstallation.workspace_id,
                        Agent.id == SkillInstallation.agent_id,
                        Agent.deleted_at.is_(None),
                    ),
                ).where(
                    or_(
                        SkillInstallation.scope == "workspace",
                        and_(
                            Agent.id.is_not(None),
                            or_(
                                Agent.visibility == "workspace",
                                Agent.owner_user_id == actor.user_id,
                            ),
                        ),
                    )
                )
            if skill_id is not None:
                stmt = stmt.where(SkillInstallation.skill_id == skill_id)
            if scope != "all":
                stmt = stmt.where(SkillInstallation.scope == scope)
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(
                        SkillInstallation.created_at, SkillInstallation.id
                    )
                    > (position.sort_value, position.id)
                )
            stmt = stmt.order_by(
                SkillInstallation.created_at.asc(), SkillInstallation.id.asc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()

        items = [self.render(installation) for installation in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return items, next_cursor

    # -- update (upgrade / enable / disable / auto_update) -------------------------------

    async def update_installation(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        installation_id: uuid.UUID,
        skill_version_id: uuid.UUID | None = None,
        install_status: str | None = None,
        auto_update: bool | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Explicit upgrade / enable / disable / auto_update toggle (§4.4)."""
        SkillService.require_manage(actor)
        if install_status is not None and install_status not in (
            "installed",
            "disabled",
        ):
            raise ValidationError(
                "invalid install_status",
                details={"install_status": install_status},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            installation = await self.load_installation(session, workspace_id, installation_id)
            skill = await SkillService.load_skill(
                session, workspace_id, installation.skill_id
            )
            now = _now(self._clock)
            changed: list[str] = []

            if skill_version_id is not None and (
                skill_version_id != installation.skill_version_id
            ):
                await self._switch_version(
                    session,
                    workspace_id=workspace_id,
                    installation=installation,
                    skill=skill,
                    target_version_id=skill_version_id,
                    change_label="upgraded",
                )
                changed.append("skill_version_id")

            if install_status is not None and install_status != installation.install_status:
                if install_status == "disabled":
                    installation.install_status = "disabled"
                else:
                    # Re-enable lands on 'installed'; a newer current version
                    # re-surfaces as an available update on the next sweep.
                    installation.install_status = "installed"
                changed.append("install_status")

            if auto_update is not None and auto_update != installation.auto_update:
                installation.auto_update = auto_update
                changed.append("auto_update")

            if not changed:
                return self.render(installation)

            installation.updated_at = now
            await session.flush()
            rendered = self.render(installation)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(installation.skill_id),
                    "installation_id": str(installation.id),
                    "change_type": (
                        "version_switched"
                        if "skill_version_id" in changed
                        else "status_changed"
                    ),
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.installation_updated",
                resource_type="skill_installation",
                resource_id=installation.id,
                metadata={"changed": sorted(changed)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def _switch_version(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        installation: SkillInstallation,
        skill: Skill,
        target_version_id: uuid.UUID,
        change_label: str,
    ) -> None:
        """Point the installation at another published version of the skill.

        Untrusted sources: if ANY script content hash differs between the
        currently installed version and the target, the switch is refused
        with 422 ``approval_required`` — a fresh import / approval round is
        required regardless of the SemVer bump (§4.4 anti-bypass).
        """
        target = await session.scalar(
            select(SkillVersion).where(
                SkillVersion.workspace_id == workspace_id,
                SkillVersion.skill_id == skill.id,
                SkillVersion.id == target_version_id,
            )
        )
        if target is None:
            raise NotFoundError("skill version not found")
        source = await session.scalar(
            select(SkillSource).where(
                SkillSource.workspace_id == workspace_id,
                SkillSource.id == skill.source_id,
            )
        )
        if source is not None and source.trust_level == "untrusted":
            if await self._script_hashes_differ(
                session, installation.skill_version_id, target.id
            ):
                # §4.4 anti-bypass: ANY script change re-enters human review
                # regardless of SemVer level — 422, even while the re-imported
                # version still sits in draft awaiting that review.
                raise BusinessRuleError(
                    "script changes require a new approval round",
                    code="approval_required",
                    details={
                        "skill_id": str(skill.id),
                        "target_version_id": str(target.id),
                    },
                )
            # Scripts identical (or none): an approved task must exist for
            # the TARGET version when scripts are present.
            has_scripts = (
                await session.execute(
                    select(SkillScript.id).where(SkillScript.skill_version_id == target.id)
                )
            ).first()
            if has_scripts is not None:
                task = await session.scalar(
                    select(SkillImportTask).where(
                        SkillImportTask.workspace_id == workspace_id,
                        SkillImportTask.skill_id == skill.id,
                        SkillImportTask.skill_version_id == target.id,
                    )
                )
                approved = task is not None and (
                    task.status in _APPROVED_TASK_STATES
                    and (not task.requires_approval or task.reviewed_at is not None)
                )
                if not approved:
                    raise BusinessRuleError(
                        "target version requires approval before switching",
                        code="approval_required",
                        details={"target_version_id": str(target.id)},
                    )
                installation.granted_capabilities = list(task.granted_capabilities or [])

        # Approval gate passed (or not applicable): the target must still be
        # a published version — draft/deprecated-freeze states stay 423.
        if target.status != "published":
            raise LockedError(
                "only published versions are installable",
                details={"version_status": target.status},
            )
        installation.skill_version_id = target.id
        installation.install_status = "installed"

    async def _script_hashes_differ(
        self,
        session: AsyncSession,
        version_a: uuid.UUID,
        version_b: uuid.UUID,
    ) -> bool:
        async def _hashes(version_id: uuid.UUID) -> set[tuple[str, str]]:
            rows = (
                await session.execute(
                    select(SkillScript.path, SkillScript.content_hash).where(
                        SkillScript.skill_version_id == version_id
                    )
                )
            ).all()
            return set(rows)

        return await _hashes(version_a) != await _hashes(version_b)

    # -- uninstall --------------------------------------------------------------------------

    async def uninstall(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        installation_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Soft-delete the installation (§4.4: 卸载 → deleted_at)."""
        SkillService.require_manage(actor)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            installation = await self.load_installation(session, workspace_id, installation_id)
            now = _now(self._clock)
            installation.deleted_at = now
            installation.updated_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(installation.skill_id),
                    "installation_id": str(installation.id),
                    "change_type": "uninstalled",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.uninstalled",
                resource_type="skill_installation",
                resource_id=installation.id,
                metadata={"skill_id": str(installation.skill_id)},
                ip_address=ip_address,
                user_agent=user_agent,
            )

    # -- rollback ------------------------------------------------------------------------------

    async def rollback(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        installation_id: uuid.UUID,
        target_version_id: uuid.UUID,
        reason: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Roll back to ANY historic version (§3.2 rollback example).

        Historic versions are never deleted, so rollback is always
        available; the overlapping composite FK guarantees the target
        belongs to the installation's skill (README §6.2 rule 7).
        """
        SkillService.require_manage(actor)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            installation = await self.load_installation(session, workspace_id, installation_id)
            skill = await SkillService.load_skill(
                session, workspace_id, installation.skill_id
            )
            target = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.id == target_version_id,
                )
            )
            if target is None:
                raise NotFoundError("skill version not found")
            if target.status != "published":
                raise LockedError(
                    "only published versions can be rolled back to",
                    details={"version_status": target.status},
                )
            previous_version_id = installation.skill_version_id
            installation.skill_version_id = target.id
            installation.install_status = "installed"
            installation.updated_at = _now(self._clock)
            await session.flush()

            rendered = self.render(installation)
            rendered["previous_version_id"] = str(previous_version_id)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": str(installation.id),
                    "change_type": "rollback",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.installation_rollback",
                resource_type="skill_installation",
                resource_id=installation.id,
                metadata={
                    "from_version_id": str(previous_version_id),
                    "to_version_id": str(target.id),
                    "reason": reason,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- update detection (§4.4 / §3.5 skill.update_available) -------------------------------

    async def notify_new_version(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        skill: Skill,
        new_version: SkillVersion,
        now: datetime,
    ) -> None:
        """Fan out a newly published version across active installations.

        Called inside the publisher's transaction (import pipeline): each
        installation either auto-follows (non-breaking PATCH, unchanged
        script hashes, ``auto_update=true``) or is marked
        ``updated_available`` with a ``skill.update_available`` broadcast.
        """
        installations = (
            await session.execute(
                select(SkillInstallation).where(
                    SkillInstallation.workspace_id == workspace_id,
                    SkillInstallation.skill_id == skill.id,
                    SkillInstallation.deleted_at.is_(None),
                )
            )
        ).scalars().all()
        for installation in installations:
            if installation.skill_version_id == new_version.id:
                continue
            current = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.id == installation.skill_version_id
                )
            )
            auto_follow = False
            if (
                installation.auto_update
                and installation.install_status != "disabled"
                and new_version.status == "published"
                and current is not None
                and is_non_breaking_patch(current.version, new_version.version)
                and not await self._script_hashes_differ(
                    session, current.id, new_version.id
                )
            ):
                auto_follow = True

            if auto_follow:
                installation.skill_version_id = new_version.id
                installation.install_status = "installed"
                installation.updated_at = now
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=skills_channel(workspace_id),
                    event="skill.changed",
                    data={
                        "skill_id": str(skill.id),
                        "installation_id": str(installation.id),
                        "change_type": "auto_updated",
                    },
                )
            else:
                if installation.install_status == "installed":
                    installation.install_status = "updated_available"
                    installation.updated_at = now
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=skills_channel(workspace_id),
                    event="skill.update_available",
                    data={
                        "skill_id": str(skill.id),
                        "installation_id": str(installation.id),
                        "new_version": new_version.version,
                    },
                )


__all__ = ["INSTALLATION_NOT_FOUND", "InstallationService"]
