"""Skill import pipeline — fetch → parse → validate → preview → approval → ready.

skill.md §3.1 / §3.5 / §5.3.

The pipeline runs as a persisted state machine on ``skill_import_tasks``:

``parsing`` → ``validating`` → ``sandbox_preview`` → trust gate →
(``awaiting_review`` for untrusted sources WITH scripts — a human must
approve scripts and permissions, §5.3) → ``ready`` (skill + published
version rows exist, installable). ``failed`` / ``rejected`` are terminal.

Security boundaries:

* every fetch goes through the SSRF guard (ssrf.py) — private address
  space is refused, redirects are re-validated hop by hop;
* content is fetched ONCE at preview time and frozen in object storage —
  what the reviewer sees is exactly what installs (no TOCTOU swap);
* nothing executes — "sandbox preview" assembles the human review surface;
  execution belongs to runtime.md (skill.md §1.3 non-goal).

Every stage transition commits its own transaction and broadcasts
``skill_import.progress`` (skill.md §3.5); polling ``GET
/skills/import/{task_id}`` is the documented no-WebSocket fallback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.agent.capabilities import (
    CapabilityInvalidError,
    normalize_capability_declarations,
)
from mesh.auth.audit import write_audit
from mesh.db.models.member import Member
from mesh.db.models.skill import (
    TRUST_LEVEL_BY_SOURCE_TYPE,
    Skill,
    SkillImportTask,
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
    MeshError,
    NotFoundError,
    ValidationError,
)
from mesh.outbox.service import emit_realtime
from mesh.skill.capabilities import assert_grants_subset_of_required, capability_keys
from mesh.skill.content_store import MAX_CONTENT_BYTES, SkillContentStore
from mesh.skill.installations import InstallationService
from mesh.skill.manifest import validate_manifest
from mesh.skill.service import (
    SkillService,
    compute_version_content_hash,
    skills_channel,
    slugify,
)
from mesh.skill.ssrf import (
    Resolver,
    SourceUnreachableError,
    fetch_pinned,
    resolve_pinned,
    validate_source_uri,
)

FETCH_TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
INSTRUCTIONS_PREVIEW_CHARS = 500
IMPORTABLE_SOURCE_TYPES = ("marketplace", "url")

TASK_NOT_FOUND = "import task not found"

# Fetcher seam: ``async (url, allowlist) -> bytes``. Tests inject a local
# fixture server client; production uses the SSRF-guarded stdlib fetcher.
Fetcher = Callable[[str, frozenset[str]], Awaitable[bytes]]


def _now(clock: Callable[[], datetime] | None) -> datetime:
    return clock() if clock is not None else datetime.now(UTC)


async def guarded_fetch(url: str, allowlist: frozenset[str]) -> bytes:
    """Fetch ``url`` via the pinned-IP SSRF fetcher (redirect + rebind safe).

    The old implementation wrapped ``urllib.request.urlopen``, whose default
    ``HTTPRedirectHandler`` follows 3xx *inside a single call* — making any
    per-hop ``except HTTPError`` check dead code and letting a validated
    public URL 302-bounce into ``169.254.169.254`` / RFC1918 / loopback with
    zero second validation (CRITICAL-1). It also re-resolved the hostname at
    connect time, a textbook DNS-rebinding window (CRITICAL-2).

    Both are closed by :mod:`mesh.skill.ssrf`: :func:`resolve_pinned`
    resolves ONCE and pins the addresses; :func:`fetch_pinned` connects to a
    pinned IP and follows redirects MANUALLY, re-pinning each hop. The body
    size cap is enforced after the (bounded) read so an oversize source maps
    to ``manifest_invalid`` rather than the generic 502.
    """
    loop = asyncio.get_running_loop()
    target = await loop.run_in_executor(
        None, lambda: resolve_pinned(url, allowlist=allowlist)
    )
    body = await loop.run_in_executor(
        None, lambda: fetch_pinned(target, allowlist=allowlist, timeout=FETCH_TIMEOUT_SECONDS)
    )
    if len(body) > MAX_CONTENT_BYTES:
        raise BusinessRuleError(
            "skill source content is too large",
            code="manifest_invalid",
            details={"limit_bytes": MAX_CONTENT_BYTES},
        )
    return body


@dataclass(frozen=True)
class ImportSettings:
    """Per-deployment import policy knobs (config.py wires the values)."""

    host_allowlist: frozenset[str] = frozenset()
    marketplace_url: str | None = None
    fetcher: Fetcher | None = None
    # DNS seam for the fail-fast pre-check: production leaves it None (system
    # resolver); tests inject a stub so allowlisted fixture hosts validate
    # without touching real DNS (the injected ``fetcher`` replaces the wire).
    resolver: Resolver | None = None


class ImportService:
    """Orchestrates the asynchronous import state machine."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        content_store: SkillContentStore,
        settings: ImportSettings | None = None,
        installation_service: InstallationService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._content_store = content_store
        self._settings = settings or ImportSettings()
        self._installations = installation_service or InstallationService(
            session_factory, clock=clock
        )
        self._clock = clock

    @property
    def _fetch(self) -> Fetcher:
        return self._settings.fetcher or guarded_fetch

    # -- serialization ----------------------------------------------------------------

    @staticmethod
    def render_task(task: SkillImportTask) -> dict:
        return {
            "task_id": str(task.id),
            "source_type": task.source_type,
            "uri": task.uri,
            "ref": task.ref,
            "status": task.status,
            "stage": task.stage,
            "percent": task.percent,
            "preview": task.preview,
            "requires_approval": task.requires_approval,
            "skill_id": str(task.skill_id) if task.skill_id is not None else None,
            "skill_version_id": (
                str(task.skill_version_id) if task.skill_version_id is not None else None
            ),
            "installation_id": (
                str(task.installation_id) if task.installation_id is not None else None
            ),
            "granted_capabilities": task.granted_capabilities,
            "error": task.error,
            "decision_comment": task.decision_comment,
            "reviewed_by": str(task.reviewed_by) if task.reviewed_by is not None else None,
            "reviewed_at": task.reviewed_at.isoformat() if task.reviewed_at else None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    async def _emit_progress(
        self, session: AsyncSession, task: SkillImportTask
    ) -> None:
        await emit_realtime(
            session,
            workspace_id=task.workspace_id,
            channel=skills_channel(task.workspace_id),
            event="skill_import.progress",
            data={
                "task_id": str(task.id),
                "stage": task.stage,
                "status": task.status,
                "percent": task.percent,
            },
        )

    # -- start ----------------------------------------------------------------------------

    async def start_import(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        source_type: str,
        uri: str,
        ref: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Kick off an import from a marketplace entry or a URL (202)."""
        SkillService.require_manage(actor)
        if source_type not in IMPORTABLE_SOURCE_TYPES:
            raise ValidationError(
                "only marketplace / url sources can be imported",
                details={"source_type": source_type},
            )
        # Fail fast on obviously bad URIs — the pipeline re-validates per hop.
        validate_source_uri(
            uri,
            allowlist=self._settings.host_allowlist,
            resolver=self._settings.resolver,
        )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            now = _now(self._clock)
            task = SkillImportTask(
                workspace_id=workspace_id,
                created_by=actor.id,
                source_type=source_type,
                uri=uri,
                ref=ref,
                status="parsing",
                stage="manifest_parse",
                percent=0,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            await self._emit_progress(session, task)
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.import_started",
                resource_type="skill_import_task",
                resource_id=task.id,
                metadata={"source_type": source_type},
                ip_address=ip_address,
                user_agent=user_agent,
            )
        # Drive the pipeline inline (each stage commits on its own); the
        # worker sweep recovers tasks orphaned by a crash mid-pipeline.
        return await self.process_task(workspace_id=workspace_id, task_id=task.id)

    async def get_task(
        self, *, workspace_id: uuid.UUID, task_id: uuid.UUID
    ) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            task = await session.scalar(
                select(SkillImportTask).where(
                    SkillImportTask.workspace_id == workspace_id,
                    SkillImportTask.id == task_id,
                )
            )
            if task is None:
                raise NotFoundError(TASK_NOT_FOUND)
            return self.render_task(task)

    # -- pipeline ---------------------------------------------------------------------------

    async def process_task(
        self, *, workspace_id: uuid.UUID, task_id: uuid.UUID
    ) -> dict:
        """Advance a task through parsing → validating → preview → gate."""
        try:
            manifest = await self._stage_parse(workspace_id, task_id)
            if manifest is None:
                return await self._snapshot(workspace_id, task_id)
            normalized = await self._stage_validate(workspace_id, task_id, manifest)
            if normalized is None:
                return await self._snapshot(workspace_id, task_id)
            await self._stage_preview_and_gate(workspace_id, task_id, normalized)
        except MeshError as exc:
            await self._fail_task(workspace_id, task_id, code=exc.code, message=exc.message)
        except Exception as exc:  # noqa: BLE001 — pipeline must never wedge
            await self._fail_task(
                workspace_id, task_id, code="internal_error", message=str(exc)[:300]
            )
        return await self._snapshot(workspace_id, task_id)

    async def _snapshot(self, workspace_id: uuid.UUID, task_id: uuid.UUID) -> dict:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            task = await session.scalar(
                select(SkillImportTask).where(
                    SkillImportTask.workspace_id == workspace_id,
                    SkillImportTask.id == task_id,
                )
            )
            if task is None:
                raise NotFoundError(TASK_NOT_FOUND)
            return self.render_task(task)

    async def _transition(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        status: str | None = None,
        stage: str | None = None,
        percent: int | None = None,
        preview: dict | None = None,
        requires_approval: bool | None = None,
        error: str | None = None,
        skill_id: uuid.UUID | None = None,
        skill_version_id: uuid.UUID | None = None,
    ) -> SkillImportTask:
        """Commit one stage transition + its progress broadcast."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            task = await session.scalar(
                select(SkillImportTask)
                .where(
                    SkillImportTask.workspace_id == workspace_id,
                    SkillImportTask.id == task_id,
                )
                .with_for_update(skip_locked=True)
            )
            if task is None:
                raise NotFoundError(TASK_NOT_FOUND)
            if status is not None:
                task.status = status
            if stage is not None:
                task.stage = stage
            if percent is not None:
                task.percent = percent
            if preview is not None:
                task.preview = preview
            if requires_approval is not None:
                task.requires_approval = requires_approval
            if error is not None:
                task.error = error
            if skill_id is not None:
                task.skill_id = skill_id
            if skill_version_id is not None:
                task.skill_version_id = skill_version_id
            task.updated_at = _now(self._clock)
            await session.flush()
            await self._emit_progress(session, task)
            return task

    async def _fail_task(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, *, code: str, message: str
    ) -> None:
        try:
            await self._transition(
                workspace_id,
                task_id,
                status="failed",
                error=f"{code}: {message}"[:1000],
                percent=100,
            )
        except NotFoundError:
            pass

    async def _stage_parse(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID
    ) -> dict | None:
        """parsing: fetch + JSON-parse the manifest (10%)."""
        task = await self._transition(
            workspace_id, task_id, stage="manifest_parse", percent=10
        )
        body = await self._fetch(task.uri or "", self._settings.host_allowlist)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessRuleError(
                "manifest is not valid JSON",
                code="manifest_invalid",
            ) from exc

    async def _stage_validate(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, manifest: dict
    ) -> dict | None:
        """validating: schema + semantic manifest checks (35%)."""
        await self._transition(workspace_id, task_id, status="validating",
                               stage="validate", percent=35)
        return validate_manifest(manifest)

    async def _stage_preview_and_gate(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, normalized: dict
    ) -> None:
        """sandbox_preview → trust gate → rows → awaiting_review | ready."""
        task = await self._transition(
            workspace_id, task_id, status="sandbox_preview",
            stage="sandbox_preview", percent=55,
        )
        # Freeze EVERY body now — the reviewed content is the installed
        # content (no source-side swap between preview and approval).
        script_entries: list[dict] = []
        union_capabilities: set[str] = set(
            capability_keys(normalized["required_capabilities"])
        )
        for script in normalized["scripts"]:
            body = await self._fetch(
                urljoin(task.uri or "", script["path"]), self._settings.host_allowlist
            )
            content_hash = hashlib.sha256(body).hexdigest()
            content_ref = await self._content_store.put(
                f"{workspace_id}/imports/{task_id}/{script['path']}", body
            )
            union_capabilities |= set(capability_keys(script["required_capabilities"]))
            script_entries.append(
                {
                    "path": script["path"],
                    "runtime": script["runtime"],
                    "entrypoint": script["entrypoint"],
                    "required_capabilities": script["required_capabilities"],
                    "content_ref": content_ref,
                    "content_hash": content_hash,
                }
            )
        reference_entries: list[dict] = []
        for reference in normalized["references"]:
            body = await self._fetch(
                urljoin(task.uri or "", reference["path"]), self._settings.host_allowlist
            )
            content_ref = await self._content_store.put(
                f"{workspace_id}/imports/{task_id}/refs/{reference['path']}", body
            )
            reference_entries.append(
                {
                    "path": reference["path"],
                    "media_type": reference["media_type"],
                    "summary": reference["summary"],
                    "content_ref": content_ref,
                }
            )

        requested = sorted(union_capabilities)
        preview = {
            "name": normalized["name"],
            "version": normalized["version"],
            "summary": normalized["summary"],
            "instructions_preview": normalized["instructions"][
                :INSTRUCTIONS_PREVIEW_CHARS
            ],
            "scripts": [
                {
                    "path": entry["path"],
                    "runtime": entry["runtime"],
                    "entrypoint": entry["entrypoint"],
                    "required_capabilities": entry["required_capabilities"],
                }
                for entry in script_entries
            ],
            "references": [
                {"path": entry["path"], "media_type": entry["media_type"]}
                for entry in reference_entries
            ],
            "requested_capabilities": requested,
        }
        trust = TRUST_LEVEL_BY_SOURCE_TYPE.get(task.source_type, "untrusted")
        requires_approval = trust == "untrusted" and len(script_entries) > 0

        await self._transition(
            workspace_id, task_id, percent=80, preview=preview,
            requires_approval=requires_approval,
        )
        await self._materialize(
            workspace_id,
            task_id,
            normalized=normalized,
            script_entries=script_entries,
            reference_entries=reference_entries,
            requires_approval=requires_approval,
        )

    async def _materialize(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        normalized: dict,
        script_entries: list[dict],
        reference_entries: list[dict],
        requires_approval: bool,
    ) -> None:
        """Create source / skill / version rows; publish unless approval needed.

        Re-imports from the SAME uri reuse the existing source + skill and
        mint a NEW version — which also drives the update_available sweep
        across existing installations (§4.4).
        """
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            task = await session.scalar(
                select(SkillImportTask)
                .where(
                    SkillImportTask.workspace_id == workspace_id,
                    SkillImportTask.id == task_id,
                )
                .with_for_update()
            )
            if task is None or task.status != "sandbox_preview":
                return
            now = _now(self._clock)
            actor = await session.scalar(
                select(Member).where(
                    Member.workspace_id == workspace_id, Member.id == task.created_by
                )
            )
            if actor is None:
                raise ForbiddenError("import initiator is no longer a member")

            source = await session.scalar(
                select(SkillSource).where(
                    SkillSource.workspace_id == workspace_id,
                    SkillSource.uri == task.uri,
                    SkillSource.deleted_at.is_(None),
                )
            )
            if source is None:
                source = SkillSource(
                    workspace_id=workspace_id,
                    source_type=task.source_type,
                    name=(task.uri or normalized["name"])[:300],
                    uri=task.uri,
                    trust_level=TRUST_LEVEL_BY_SOURCE_TYPE.get(
                        task.source_type, "untrusted"
                    ),
                    created_at=now,
                    updated_at=now,
                )
                session.add(source)
                await session.flush()

            skill = await session.scalar(
                select(Skill)
                .join(
                    SkillSource,
                    (SkillSource.workspace_id == Skill.workspace_id)
                    & (SkillSource.id == Skill.source_id),
                )
                .where(
                    Skill.workspace_id == workspace_id,
                    Skill.source_id == source.id,
                    Skill.deleted_at.is_(None),
                )
                .order_by(Skill.created_at.asc())
            )
            if skill is None:
                base_slug = slugify(normalized["name"])
                slug = base_slug
                counter = 1
                while await session.scalar(
                    select(Skill.id).where(
                        Skill.workspace_id == workspace_id,
                        Skill.slug == slug,
                        Skill.deleted_at.is_(None),
                    )
                ):
                    counter += 1
                    slug = f"{base_slug[:90]}-{counter}"
                skill = Skill(
                    workspace_id=workspace_id,
                    source_id=source.id,
                    name=normalized["name"],
                    slug=slug,
                    summary=normalized["summary"] or normalized["name"],
                    status="draft",
                    required_capabilities=normalized["required_capabilities"],
                    tags=normalized["tags"],
                    created_by=actor.id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(skill)
                await session.flush()

            existing_version = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.skill_id == skill.id,
                    SkillVersion.version == normalized["version"],
                )
            )
            if existing_version is not None:
                expected = compute_version_content_hash(
                    normalized["instructions"],
                    [
                        {"path": e["path"], "content_hash": e["content_hash"]}
                        for e in script_entries
                    ],
                )
                if existing_version.content_hash == expected:
                    # Idempotent re-import of identical content (de-dup, §2.3).
                    task.skill_id = skill.id
                    task.skill_version_id = existing_version.id
                    task.status = "awaiting_review" if (
                        requires_approval and existing_version.status != "published"
                    ) else "ready"
                    if existing_version.status == "published":
                        task.status = "ready"
                    task.stage = "review" if task.status == "awaiting_review" else "ready"
                    task.percent = 100
                    task.updated_at = now
                    await session.flush()
                    await self._emit_progress(session, task)
                    return
                raise ConflictError(
                    "version number already exists with different content",
                    code="version_conflict",
                    details={"version": normalized["version"]},
                )

            publish = not requires_approval
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
                content_hash=compute_version_content_hash(
                    normalized["instructions"],
                    [
                        {"path": e["path"], "content_hash": e["content_hash"]}
                        for e in script_entries
                    ],
                ),
                created_by=actor.id,
                created_at=now,
            )
            session.add(version)
            await session.flush()
            for entry in script_entries:
                session.add(
                    SkillScript(
                        skill_version_id=version.id,
                        path=entry["path"],
                        runtime=entry["runtime"],
                        entrypoint=entry["entrypoint"],
                        content_ref=entry["content_ref"],
                        content_hash=entry["content_hash"],
                        required_capabilities=entry["required_capabilities"],
                        created_at=now,
                    )
                )
            for entry in reference_entries:
                session.add(
                    SkillReference(
                        skill_version_id=version.id,
                        path=entry["path"],
                        media_type=entry["media_type"],
                        content_ref=entry["content_ref"],
                        summary=entry["summary"],
                        created_at=now,
                    )
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

            task.skill_id = skill.id
            task.skill_version_id = version.id
            task.updated_at = now
            if requires_approval:
                task.status = "awaiting_review"
                task.stage = "review"
                task.percent = 100
                await session.flush()
                await self._emit_progress(session, task)
                await emit_realtime(
                    session,
                    workspace_id=workspace_id,
                    channel=skills_channel(workspace_id),
                    event="skill.approval_required",
                    data={"skill_id": str(skill.id), "task_id": str(task.id)},
                )
                # §4.3: update detection fires when the new version is
                # DISCOVERED (import time), not at approval — installations
                # surface updated_available immediately. The sweep cannot
                # auto-follow a still-draft version (guarded inside).
                await self._installations.notify_new_version(
                    session,
                    workspace_id=workspace_id,
                    skill=skill,
                    new_version=version,
                    now=now,
                )
            else:
                skill.current_version_id = version.id
                skill.status = "published"
                skill.updated_at = now
                task.status = "ready"
                task.stage = "ready"
                task.percent = 100
                await session.flush()
                await self._installations.notify_new_version(
                    session,
                    workspace_id=workspace_id,
                    skill=skill,
                    new_version=version,
                    now=now,
                )
                await self._emit_progress(session, task)

    # -- approval (§3.1 approve / §5.3) ----------------------------------------------------

    async def approve(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        skill_id: uuid.UUID,
        task_id: uuid.UUID,
        granted_capabilities: list,
        decision: str = "approve",
        comment: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Approve / reject a third-party skill awaiting review.

        Approval publishes the frozen version (skill → published, current
        pointer moves) with the approver's minimized capability subset;
        rejection terminates the task and leaves the skill in draft
        (uninstallable — 423 locked).
        """
        SkillService.require_manage(actor)
        if decision not in ("approve", "reject"):
            raise ValidationError("invalid decision", details={"decision": decision})
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            skill = await SkillService.load_skill(session, workspace_id, skill_id)
            task = await session.scalar(
                select(SkillImportTask)
                .where(
                    SkillImportTask.workspace_id == workspace_id,
                    SkillImportTask.id == task_id,
                    SkillImportTask.skill_id == skill.id,
                )
                .with_for_update()
            )
            if task is None:
                raise NotFoundError(TASK_NOT_FOUND)
            if task.status != "awaiting_review":
                raise ConflictError(
                    "import task is not awaiting review",
                    code="conflict",
                    details={"status": task.status},
                )
            version = await session.scalar(
                select(SkillVersion).where(
                    SkillVersion.workspace_id == workspace_id,
                    SkillVersion.id == task.skill_version_id,
                )
            )
            if version is None:
                raise NotFoundError("skill version not found")
            now = _now(self._clock)
            task.reviewed_by = actor.id
            task.reviewed_at = now
            task.decision_comment = comment
            task.updated_at = now

            if decision == "reject":
                task.status = "rejected"
                task.percent = 100
                await session.flush()
                await self._emit_progress(session, task)
                await write_audit(
                    session,
                    workspace_id=workspace_id,
                    actor_member_id=actor.id,
                    actor_kind="member",
                    action="skill.import_rejected",
                    resource_type="skill_import_task",
                    resource_id=task.id,
                    metadata={"skill_id": str(skill.id)},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return self.render_task(task)

            # HIGH-2: validate the grant SHAPE before the subset check so a
            # malformed authorization (e.g. [{"capability":..,"permission":"x"}]
            # or [123]) is rejected 422 here and can never be persisted into
            # skill_installations.granted_capabilities (which would later poison
            # the enqueue normalizer and stall the agent's dispatch).
            try:
                normalize_capability_declarations(granted_capabilities)
            except CapabilityInvalidError as exc:
                raise BusinessRuleError(
                    "granted capabilities are malformed",
                    code="capability_invalid",
                    details={"reason": str(exc)},
                ) from exc
            assert_grants_subset_of_required(
                granted_capabilities, version.required_capabilities
            )
            task.granted_capabilities = granted_capabilities
            task.status = "ready"
            task.stage = "ready"
            task.percent = 100
            version.status = "published"
            skill.current_version_id = version.id
            skill.status = "published"
            skill.required_capabilities = version.required_capabilities
            skill.updated_at = now
            await session.flush()
            await self._installations.notify_new_version(
                session,
                workspace_id=workspace_id,
                skill=skill,
                new_version=version,
                now=now,
            )
            await self._emit_progress(session, task)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=skills_channel(workspace_id),
                event="skill.changed",
                data={
                    "skill_id": str(skill.id),
                    "installation_id": None,
                    "change_type": "approved",
                },
            )
            await write_audit(
                session,
                workspace_id=workspace_id,
                actor_member_id=actor.id,
                actor_kind="member",
                action="skill.import_approved",
                resource_type="skill_import_task",
                resource_id=task.id,
                metadata={
                    "skill_id": str(skill.id),
                    "skill_version_id": str(version.id),
                    "granted_count": len(capability_keys(granted_capabilities)),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
            # M1: §3.2 approval RESULT shape (not the import task object).
            return {
                "skill_id": str(skill.id),
                "skill_version_id": str(version.id),
                "status": version.status,
                "granted_capabilities": list(task.granted_capabilities),
                "reviewed_by": str(task.reviewed_by),
                "reviewed_at": task.reviewed_at.isoformat() if task.reviewed_at else None,
            }

    # -- marketplace (§3.1 GET marketplace/skills, K10) ---------------------------------------

    async def list_marketplace(
        self,
        *,
        workspace_id: uuid.UUID,
        q: str | None = None,
        limit: int = 20,
    ) -> tuple[list[dict], str | None]:
        """List importable marketplace entries from the configured source.

        The marketplace is an EXTERNAL listing API (skill.md §1.3: Mesh only
        consumes it). Without a configured ``marketplace_url`` the surface is
        empty — never an error. Fetches are SSRF-guarded like every
        server-side outbound call.
        """
        limit = max(1, min(limit, 100))
        base = self._settings.marketplace_url
        if not base:
            return [], None
        body = await self._fetch(
            f"{base.rstrip('/')}/listings", self._settings.host_allowlist
        )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceUnreachableError("marketplace returned invalid JSON") from exc
        listings = payload.get("listings") if isinstance(payload, dict) else None
        if not isinstance(listings, list):
            return [], None
        items = []
        for entry in listings:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", ""))
            summary = str(entry.get("summary", ""))
            if q and q.strip().lower() not in f"{name} {summary}".lower():
                continue
            items.append(
                {
                    "id": str(entry.get("id", "")),
                    "name": name,
                    "summary": summary,
                    "version": str(entry.get("version", "")),
                    "manifest_url": str(entry.get("manifest_url", "")),
                    "downloads": int(entry.get("downloads", 0) or 0),
                    "rating": float(entry.get("rating", 0.0) or 0.0),
                    "certified": bool(entry.get("certified", False)),
                    "has_scripts": bool(entry.get("has_scripts", False)),
                    "tags": [str(t) for t in (entry.get("tags") or [])],
                }
            )
        items.sort(key=lambda item: item["downloads"], reverse=True)
        # The external API is not paginated; slice locally (next_cursor stays
        # null — one page, §6.14 "next_cursor=null 表示末页").
        return items[:limit], None


async def skill_import_sweep_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    content_store: SkillContentStore,
    settings: ImportSettings | None = None,
    interval: float = 1.0,
    stop: asyncio.Event | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Crash-recovery sweep over tasks stuck mid-pipeline.

    The API drives imports inline; this loop only picks up rows a crashed
    process left in a non-terminal in-flight state (worker pattern parity
    with attachment_scan_loop).
    """
    service = ImportService(
        session_factory, content_store=content_store, settings=settings, clock=clock
    )
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            SkillImportTask.workspace_id, SkillImportTask.id
                        ).where(
                            SkillImportTask.status.in_(
                                ("parsing", "validating", "sandbox_preview")
                            )
                        ).limit(10)
                    )
                ).all()
            for workspace_id, task_id in rows:
                await service.process_task(workspace_id=workspace_id, task_id=task_id)
        except Exception:  # noqa: BLE001 — loop must survive transient errors
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


__all__ = [
    "IMPORTABLE_SOURCE_TYPES",
    "ImportService",
    "ImportSettings",
    "guarded_fetch",
    "skill_import_sweep_loop",
]
