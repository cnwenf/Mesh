"""Skill service — definitions, sources and immutable versions (skill.md §3.1/§3.2/§4.4).

Implements the first two layers of the four-layer decoupling:

* ``skills`` — CRUD + lifecycle state machine (§4.4: draft → published →
  deprecated / disabled, recoverable soft states);
* ``skill_sources`` — provenance + trust level (auto-provisioned: one
  ``user`` source per workspace for self-created skills; imports mint their
  own source rows);
* ``skill_versions`` — immutable snapshots; publishing a version moves
  ``skills.current_version_id`` (the overlapping composite FK guarantees it
  belongs to THIS skill, README §6.2 rule 7). Published versions are frozen
  — updates always mint a new version.

Writes require workspace ``admin`` / ``owner`` (skill.md §3.4 "admin /
skill:manage"; the fixed role enum has no custom roles, so the manage-level
built-in roles satisfy it). Reads need plain membership.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.expression import tuple_

from mesh.agent.capabilities import CapabilityInvalidError, normalize_capability_declarations
from mesh.auth.audit import write_audit
from mesh.auth.rbac import role_satisfies
from mesh.db.models.member import Member
from mesh.db.models.skill import (
    SKILL_STATUS_VALUES,
    TRUST_LEVEL_BY_SOURCE_TYPE,
    Skill,
    SkillInstallation,
    SkillReference,
    SkillScript,
    SkillSource,
    SkillTrigger,
    SkillVersion,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.skill.manifest import SEMVER_PATTERN, validate_manifest

WORKSPACE_SKILLS_CHANNEL = "workspace:{workspace_id}:skills"

SKILL_NOT_FOUND = "skill not found"
VERSION_NOT_FOUND = "skill version not found"
SOURCE_NOT_FOUND = "skill source not found"

USER_SOURCE_NAME = "user-uploads"

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_SLUG_LENGTH = 96

# Lifecycle transitions (§4.4): draft→published happens on first version
# publish; PATCH may then move among published / deprecated / disabled and
# recover disabled back to published.
SKILL_LIFECYCLE_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"published"}),
    "published": frozenset({"deprecated", "disabled"}),
    "deprecated": frozenset({"disabled"}),
    "disabled": frozenset({"published", "deprecated"}),
}


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


def skills_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_SKILLS_CHANNEL.format(workspace_id=workspace_id)


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a display name (ASCII fallback)."""
    raw = name.strip().lower()
    ascii_name = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    slug = ascii_name or f"skill-{uuid.uuid4().hex[:8]}"
    return slug[:MAX_SLUG_LENGTH]


def compute_version_content_hash(instructions: str, scripts: list[dict]) -> str:
    """The §2.3 content hash: instructions + every script path/content hash.

    Used for de-duplication (skip re-publishing unchanged sources) and the
    auto-update gate (any script hash change forces re-approval, §4.4).
    """
    digest = hashlib.sha256()
    digest.update(instructions.encode("utf-8"))
    for script in sorted(scripts, key=lambda item: item["path"]):
        digest.update(b"\x00")
        digest.update(script["path"].encode("utf-8"))
        digest.update(b"\x00")
        digest.update(script.get("content_hash", "").encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class SkillPatch:
    """A PATCH skills/{id} request — unset fields keep their value."""

    name: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    icon: str | None = None
    status: str | None = None
    required_capabilities: list | None = None


class SkillService:
    """Stateless orchestrator over the skill definition tables."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._clock = clock

    # -- authorization ---------------------------------------------------------

    @staticmethod
    def require_manage(actor: Member) -> None:
        """skill.md §3.4: create / write / install / bind = admin-level role."""
        if not role_satisfies(actor.role, "agent:manage"):
            raise ForbiddenError("skill management requires an admin role")

    @staticmethod
    def validate_required_capabilities(required: list | None) -> None:
        """Validate declaration-only shape before persisting a draft skill."""
        try:
            normalize_capability_declarations(required or [])
        except CapabilityInvalidError as exc:
            raise BusinessRuleError(
                "required capabilities are malformed",
                code="capability_invalid",
                details={"reason": str(exc)},
            ) from exc

    # -- loading -----------------------------------------------------------------

    @staticmethod
    async def load_skill(
        session: AsyncSession, workspace_id: uuid.UUID, skill_id: uuid.UUID
    ) -> Skill:
        skill = await session.scalar(
            select(Skill).where(
                Skill.workspace_id == workspace_id,
                Skill.id == skill_id,
                Skill.deleted_at.is_(None),
            )
        )
        if skill is None:
            raise NotFoundError(SKILL_NOT_FOUND)
        return skill

    async def _load_source(
        self, session: AsyncSession, workspace_id: uuid.UUID, source_id: uuid.UUID
    ) -> SkillSource:
        source = await session.scalar(
            select(SkillSource).where(
                SkillSource.workspace_id == workspace_id,
                SkillSource.id == source_id,
                SkillSource.deleted_at.is_(None),
            )
        )
        if source is None:
            raise NotFoundError(SOURCE_NOT_FOUND)
        return source

    async def ensure_user_source(
        self, session: AsyncSession, workspace_id: uuid.UUID, now: datetime
    ) -> SkillSource:
        """The per-workspace ``user`` source for self-created skills (K2)."""
        source = await session.scalar(
            select(SkillSource).where(
                SkillSource.workspace_id == workspace_id,
                SkillSource.source_type == "user",
                SkillSource.name == USER_SOURCE_NAME,
                SkillSource.deleted_at.is_(None),
            )
        )
        if source is not None:
            return source
        source = SkillSource(
            workspace_id=workspace_id,
            source_type="user",
            name=USER_SOURCE_NAME,
            trust_level=TRUST_LEVEL_BY_SOURCE_TYPE["user"],
            created_at=now,
            updated_at=now,
        )
        session.add(source)
        await session.flush()
        return source

    async def _unique_slug(
        self, session: AsyncSession, workspace_id: uuid.UUID, desired: str
    ) -> str:
        """Suffix a slug until it is unique within the workspace (live rows)."""
        candidate = desired
        counter = 1
        while True:
            taken = await session.scalar(
                select(Skill.id).where(
                    Skill.workspace_id == workspace_id,
                    Skill.slug == candidate,
                    Skill.deleted_at.is_(None),
                )
            )
            if taken is None:
                return candidate
            counter += 1
            suffix = f"-{counter}"
            candidate = desired[: MAX_SLUG_LENGTH - len(suffix)] + suffix

    # -- serialization -------------------------------------------------------------

    def render_skill(
        self,
        skill: Skill,
        source: SkillSource | None,
        *,
        current_version: str | None = None,
        has_scripts: bool | None = None,
        install_status: str | None = None,
    ) -> dict:
        """Render the §3.2 skill shape (JSON-safe strings for outbox reuse).

        ``current_version`` / ``has_scripts`` / ``install_status`` are the
        §4.1 library-card fields (current version string, the ⚠ has-scripts
        flag, the ↻ install status). They are optional so the outbox/event
        callers that don't carry them stay valid; the list + detail readers
        supply them.
        """
        return {
            "id": str(skill.id),
            "workspace_id": str(skill.workspace_id),
            "source_id": str(skill.source_id),
            "source_type": source.source_type if source is not None else None,
            "trust_level": source.trust_level if source is not None else None,
            "name": skill.name,
            "slug": skill.slug,
            "summary": skill.summary,
            "status": skill.status,
            "current_version_id": (
                str(skill.current_version_id) if skill.current_version_id is not None else None
            ),
            "current_version": current_version,
            "has_scripts": has_scripts,
            "install_status": install_status,
            "required_capabilities": skill.required_capabilities,
            "tags": list(skill.tags or []),
            "icon": skill.icon,
            "created_by": str(skill.created_by),
            "created_at": skill.created_at.isoformat(),
            "updated_at": skill.updated_at.isoformat(),
        }

    def render_version(self, version: SkillVersion) -> dict:
        return {
            "id": str(version.id),
            "skill_id": str(version.skill_id),
            "version": version.version,
            "instructions": version.instructions,
            "status": version.status,
            "changelog": version.changelog,
            "io_contract": version.io_contract,
            "required_capabilities": version.required_capabilities,
            "content_hash": version.content_hash,
            "created_by": str(version.created_by),
            "created_at": version.created_at.isoformat(),
        }

    @staticmethod
    def render_script(script: SkillScript) -> dict:
        return {
            "id": str(script.id),
            "path": script.path,
            "runtime": script.runtime,
            "entrypoint": script.entrypoint,
            "content_ref": script.content_ref,
            "content_hash": script.content_hash,
            "required_capabilities": script.required_capabilities,
        }

    @staticmethod
    def render_reference(reference: SkillReference) -> dict:
        return {
            "id": str(reference.id),
            "path": reference.path,
            "media_type": reference.media_type,
            "content_ref": reference.content_ref,
            "summary": reference.summary,
        }

    @staticmethod
    def render_trigger(trigger: SkillTrigger) -> dict:
        return {
            "id": str(trigger.id),
            "trigger_type": trigger.trigger_type,
            "pattern": trigger.pattern,
            "weight": float(trigger.weight),
        }

    # -- create ---------------------------------------------------------------------

    async def create_skill(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        name: str,
        summary: str,
        slug: str | None = None,
        tags: list[str] | None = None,
        icon: str | None = None,
        required_capabilities: list | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create a user-sourced skill definition (status=draft, no version)."""
        self.require_manage(actor)
        self.validate_required_capabilities(required_capabilities)
        if slug is not None and not SLUG_PATTERN.match(slug):
            raise ValidationError(
                "invalid slug",
                details={"fields": [{"field": "slug", "issue": "invalid_format"}]},
            )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _now(self._clock)
            source = await self.ensure_user_source(session, workspace_id, now)
            final_slug = await self._unique_slug(
                session, workspace_id, slug or slugify(name)
            )
            skill = Skill(
                workspace_id=workspace_id,
                source_id=source.id,
                name=name.strip(),
                slug=final_slug,
                summary=summary.strip(),
                status="draft",
                required_capabilities=required_capabilities or [],
                tags=tags or [],
                icon=icon,
                created_by=actor.id,
                created_at=now,
                updated_at=now,
            )
            session.add(skill)
            await session.flush()
            rendered = self.render_skill(skill, source)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": None,
                    "change_type": "created",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.created",
                resource_type="skill",
                resource_id=skill.id,
                metadata={"slug": final_slug},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    # -- list / detail ------------------------------------------------------------------

    async def list_skills(
        self,
        *,
        workspace_id: uuid.UUID,
        status: str = "all",
        source_type: str = "all",
        q: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Keyset pagination over (created_at, id) — §3.4 cursor contract."""
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = (
                select(Skill, SkillSource)
                .outerjoin(
                    SkillSource,
                    (SkillSource.workspace_id == Skill.workspace_id)
                    & (SkillSource.id == Skill.source_id),
                )
                .where(Skill.workspace_id == workspace_id, Skill.deleted_at.is_(None))
            )
            if status != "all":
                if status not in SKILL_STATUS_VALUES:
                    raise ValidationError(
                        "invalid status filter",
                        details={"status": status, "allowed": list(SKILL_STATUS_VALUES)},
                    )
                stmt = stmt.where(Skill.status == status)
            if source_type != "all":
                stmt = stmt.where(SkillSource.source_type == source_type)
            if q:
                pattern = f"%{q.strip()}%"
                stmt = stmt.where(Skill.name.ilike(pattern) | Skill.summary.ilike(pattern))
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(Skill.created_at, Skill.id)
                    > (position.sort_value, position.id)
                )
            stmt = stmt.order_by(Skill.created_at.asc(), Skill.id.asc()).limit(limit + 1)
            rows = (await session.execute(stmt)).all()
            page = rows[:limit]
            # Card extras MUST run inside the session block: executing on the
            # closed session silently re-acquires a connection and abandons an
            # open transaction (idle-in-transaction lock starvation).
            extras = await self._card_extras(
                session, workspace_id, [skill.id for skill, _ in page]
            )

        items = [
            self.render_skill(skill, source, **extras.get(skill.id, {}))
            for skill, source in page
        ]
        next_cursor = None
        if len(rows) > limit:
            last_skill = rows[limit - 1][0]
            next_cursor = encode_cursor(last_skill.created_at, last_skill.id)
        return items, next_cursor

    async def _card_extras(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        skill_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, dict]:
        """Batch the §4.1 card fields (current version / has_scripts / status).

        Three bounded queries over the page's skill ids (no per-card N+1):
        current-version strings, a has-scripts flag (exists over the current
        version's scripts), and the workspace-scope installation status.
        """
        result: dict[uuid.UUID, dict] = {
            sid: {"current_version": None, "has_scripts": False, "install_status": None}
            for sid in skill_ids
        }
        if not skill_ids:
            return result
        version_rows = (
            await session.execute(
                select(Skill.id, SkillVersion.version)
                .join(SkillVersion, SkillVersion.id == Skill.current_version_id)
                .where(Skill.id.in_(skill_ids))
            )
        ).all()
        for sid, ver in version_rows:
            result[sid]["current_version"] = ver
        script_rows = (
            await session.execute(
                select(Skill.id)
                .join(SkillVersion, SkillVersion.skill_id == Skill.id)
                .join(SkillScript, SkillScript.skill_version_id == SkillVersion.id)
                .where(Skill.id.in_(skill_ids))
                .distinct()
            )
        ).scalars().all()
        for sid in script_rows:
            result[sid]["has_scripts"] = True
        install_rows = (
            await session.execute(
                select(SkillInstallation.skill_id, SkillInstallation.install_status)
                .where(
                    SkillInstallation.workspace_id == workspace_id,
                    SkillInstallation.skill_id.in_(skill_ids),
                    SkillInstallation.scope == "workspace",
                    SkillInstallation.deleted_at.is_(None),
                )
            )
        ).all()
        for sid, status in install_rows:
            result[sid]["install_status"] = status
        return result

    async def get_skill(
        self, *, workspace_id: uuid.UUID, skill_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            source = await self._load_source(session, workspace_id, skill.source_id)
            extras = await self._card_extras(session, workspace_id, [skill.id])
            return self.render_skill(skill, source, **extras.get(skill.id, {}))

    # -- update ------------------------------------------------------------------------

    async def update_skill(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        patch: SkillPatch,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Update metadata and/or run a lifecycle transition (§4.4)."""
        self.require_manage(actor)
        if patch.required_capabilities is not None:
            self.validate_required_capabilities(patch.required_capabilities)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            now = _now(self._clock)
            changed: list[str] = []

            if patch.name is not None and patch.name.strip() != skill.name:
                skill.name = patch.name.strip()
                changed.append("name")
            if patch.summary is not None and patch.summary.strip() != skill.summary:
                skill.summary = patch.summary.strip()
                changed.append("summary")
            if patch.tags is not None and list(patch.tags) != list(skill.tags or []):
                skill.tags = list(patch.tags)
                changed.append("tags")
            if patch.icon is not None and patch.icon != skill.icon:
                skill.icon = patch.icon
                changed.append("icon")
            if (
                patch.required_capabilities is not None
                and patch.required_capabilities != skill.required_capabilities
            ):
                skill.required_capabilities = patch.required_capabilities
                changed.append("required_capabilities")
            if patch.status is not None and patch.status != skill.status:
                if patch.status not in SKILL_STATUS_VALUES:
                    raise ValidationError(
                        "invalid status",
                        details={"status": patch.status, "allowed": list(SKILL_STATUS_VALUES)},
                    )
                allowed = SKILL_LIFECYCLE_TRANSITIONS.get(skill.status, frozenset())
                if patch.status not in allowed:
                    raise ConflictError(
                        f"cannot move skill from '{skill.status}' to '{patch.status}'",
                        code="conflict",
                        details={
                            "from": skill.status,
                            "to": patch.status,
                            "allowed": sorted(allowed),
                        },
                    )
                skill.status = patch.status
                changed.append("status")

            if not changed:
                source = await self._load_source(session, workspace_id, skill.source_id)
                return self.render_skill(skill, source)

            skill.updated_at = now
            await session.flush()
            source = await self._load_source(session, workspace_id, skill.source_id)
            rendered = self.render_skill(skill, source)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": None,
                    "change_type": "status_changed" if "status" in changed else "updated",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.updated",
                resource_type="skill",
                resource_id=skill.id,
                metadata={"changed": sorted(changed)},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered

    async def delete_skill(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Soft-delete a definition (§4.4: deprecated / disabled only)."""
        self.require_manage(actor)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            if skill.status not in ("deprecated", "disabled"):
                raise LockedError(
                    "only deprecated or disabled skills can be deleted",
                    details={"status": skill.status},
                )
            now = _now(self._clock)
            skill.deleted_at = now
            skill.updated_at = now
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": None,
                    "change_type": "deleted",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.deleted",
                resource_type="skill",
                resource_id=skill.id,
                metadata={},
                ip_address=ip_address,
                user_agent=user_agent,
            )

    # -- versions ------------------------------------------------------------------------

    async def create_version(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        manifest: dict,
        script_bodies: dict[str, bytes] | None = None,
        reference_bodies: dict[str, bytes] | None = None,
        content_store: object | None = None,
        publish: bool = False,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Mint a new immutable version from a VALIDATED manifest.

        ``script_bodies`` / ``reference_bodies`` carry the inline content for
        user-created versions; each body is stored through ``content_store``
        and referenced by ``content_ref`` (§2.7). ``publish=True`` freezes the
        version as ``published`` and moves the skill's current pointer — the
        skill itself becomes ``published`` on its first published version.
        """
        self.require_manage(actor)
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            # Versions inherit the definition's name / summary when the
            # caller omits them (the version payload centers on content).
            filled = dict(manifest)
            filled.setdefault("name", skill.name)
            filled.setdefault("summary", skill.summary)
            normalized = validate_manifest(filled)
            now = _now(self._clock)

            existing = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == normalized["version"],
                )
            )
            if existing is not None:
                raise ConflictError(
                    "version number already exists (versions are immutable)",
                    code="version_conflict",
                    details={"version": normalized["version"]},
                )

            version, rendered_version = await self._write_version_rows(
                session,
                workspace_id=workspace_id,
                skill=skill,
                normalized=normalized,
                script_bodies=script_bodies or {},
                reference_bodies=reference_bodies or {},
                content_store=content_store,
                actor=actor,
                now=now,
                publish=publish,
            )

            if publish:
                skill.current_version_id = version.id
                if skill.status == "draft":
                    skill.status = "published"
                if normalized["required_capabilities"]:
                    skill.required_capabilities = normalized["required_capabilities"]
                skill.updated_at = now
                await session.flush()

            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": None,
                    "change_type": "version_published" if publish else "version_created",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.version_created",
                resource_type="skill_version",
                resource_id=version.id,
                metadata={"skill_id": str(skill.id), "version": version.version,
                          "published": publish},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return rendered_version

    async def _write_version_rows(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        skill: Skill,
        normalized: dict,
        script_bodies: dict[str, bytes],
        reference_bodies: dict[str, bytes],
        content_store: object | None,
        actor: Member,
        now: datetime,
        publish: bool,
    ) -> tuple[SkillVersion, dict]:
        """Insert the version + leaf rows; returns (version, rendered)."""
        script_metas: list[dict] = []
        script_contents: list[tuple[dict, bytes]] = []
        for script in normalized["scripts"]:
            body = script_bodies.get(script["path"], b"")
            content_hash = hashlib.sha256(body).hexdigest()
            content_ref = ""
            if content_store is not None:
                content_ref = await content_store.put(  # type: ignore[union-attr]
                    f"{workspace_id}/{skill.id}/{normalized['version']}/{script['path']}",
                    body,
                )
            script_metas.append({"path": script["path"], "content_hash": content_hash})
            script_contents.append(
                (
                    {
                        "path": script["path"],
                        "runtime": script["runtime"],
                        "entrypoint": script["entrypoint"],
                        "content_ref": content_ref,
                        "content_hash": content_hash,
                        "required_capabilities": script["required_capabilities"],
                    },
                    body,
                )
            )
        reference_contents: list[dict] = []
        for reference in normalized["references"]:
            body = reference_bodies.get(reference["path"], b"")
            content_ref = ""
            if content_store is not None:
                content_ref = await content_store.put(  # type: ignore[union-attr]
                    f"{workspace_id}/{skill.id}/{normalized['version']}/"
                    f"refs/{reference['path']}",
                    body,
                )
            reference_contents.append(
                {
                    "path": reference["path"],
                    "media_type": reference["media_type"],
                    "content_ref": content_ref,
                    "summary": reference["summary"],
                }
            )

        content_hash = compute_version_content_hash(
            normalized["instructions"], script_metas
        )
        version = SkillVersion(
            workspace_id=workspace_id,
            skill_id=skill.id,
            version=normalized["version"],
            instructions=normalized["instructions"],
            status="published" if publish else "draft",
            changelog=normalized["changelog"],
            io_contract=normalized["io_contract"],
            required_capabilities=normalized["required_capabilities"],
            manifest={
                key: normalized[key]
                for key in (
                    "name", "version", "summary", "scripts", "references",
                    "triggers", "tags", "required_capabilities", "changelog",
                )
            },
            content_hash=content_hash,
            created_by=actor.id,
            created_at=now,
        )
        session.add(version)
        await session.flush()

        for meta, _body in script_contents:
            session.add(SkillScript(skill_version_id=version.id, created_at=now, **meta))
        for meta in reference_contents:
            session.add(
                SkillReference(skill_version_id=version.id, created_at=now, **meta)
            )
        for trigger in normalized["triggers"]:
            session.add(
                SkillTrigger(
                    skill_version_id=version.id,
                    trigger_type=trigger["trigger_type"],
                    pattern=trigger["pattern"],
                    weight=trigger["weight"],
                    created_at=now,
                )
            )
        if script_contents or reference_contents or normalized["triggers"]:
            await session.flush()
        return version, self.render_version(version)

    async def list_versions(
        self,
        *,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        limit: int = 20,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Newest-first version history — history is never deleted (§4.2)."""
        from mesh.api.pagination import decode_cursor, encode_cursor

        limit = max(1, min(limit, 100))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            stmt = select(SkillVersion).where(
                SkillVersion.workspace_id == workspace_id,
                SkillVersion.skill_id == skill.id,
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(SkillVersion.created_at, SkillVersion.id)
                    < (position.sort_value, position.id)
                )
            stmt = stmt.order_by(
                SkillVersion.created_at.desc(), SkillVersion.id.desc()
            ).limit(limit + 1)
            rows = (await session.execute(stmt)).scalars().all()

        items = []
        for version in rows[:limit]:
            rendered = self.render_version(version)
            rendered["is_current"] = version.id == skill.current_version_id
            items.append(rendered)
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.created_at, last.id)
        return items, next_cursor

    async def get_version(
        self,
        *,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        version_id: uuid.UUID,
        include_content: bool = False,
        content_store: object | None = None,
    ) -> dict:
        """Version detail with its scripts / references / triggers."""
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            skill = await self.load_skill(session, workspace_id, skill_id)
            version = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.id == version_id,
                )
            )
            if version is None:
                raise NotFoundError(VERSION_NOT_FOUND)
            scripts = (
                await session.execute(
                    select(SkillScript)
                    .where(SkillScript.skill_version_id == version.id)
                    .order_by(SkillScript.path.asc())
                )
            ).scalars().all()
            references = (
                await session.execute(
                    select(SkillReference)
                    .where(SkillReference.skill_version_id == version.id)
                    .order_by(SkillReference.path.asc())
                )
            ).scalars().all()
            triggers = (
                await session.execute(
                    select(SkillTrigger)
                    .where(SkillTrigger.skill_version_id == version.id)
                    .order_by(SkillTrigger.created_at.asc())
                )
            ).scalars().all()

            rendered = self.render_version(version)
            rendered["is_current"] = version.id == skill.current_version_id
            script_items = [self.render_script(script) for script in scripts]
            if include_content and content_store is not None:
                for script, item in zip(scripts, script_items, strict=True):
                    body = await content_store.get(script.content_ref)  # type: ignore[union-attr]
                    item["content"] = body.decode("utf-8", errors="replace")
            rendered["scripts"] = script_items
            rendered["references"] = [self.render_reference(r) for r in references]
            rendered["triggers"] = [self.render_trigger(t) for t in triggers]
            return rendered


def require_valid_semver(version: str) -> None:
    """400 when the version string is not SemVer (route-level guard)."""
    if not SEMVER_PATTERN.match(version or ""):
        raise ValidationError(
            "invalid version",
            details={"fields": [{"field": "version", "issue": "invalid_semver"}]},
        )


__all__ = [
    "SKILL_LIFECYCLE_TRANSITIONS",
    "SOURCE_NOT_FOUND",
    "VERSION_NOT_FOUND",
    "SkillPatch",
    "SkillService",
    "USER_SOURCE_NAME",
    "WORKSPACE_SKILLS_CHANNEL",
    "compute_version_content_hash",
    "require_valid_semver",
    "skills_channel",
    "slugify",
]
