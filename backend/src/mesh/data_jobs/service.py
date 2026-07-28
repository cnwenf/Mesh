"""Data-jobs service — API-side orchestration (import-export.md §3).

Creates import/export jobs inside a business transaction that also writes
the ``data_job.enqueue`` outbox event (README §6.6 — nothing is parsed or
written outside the worker), guards the two-phase import contract
(validate → run), prechecks the frozen source hash (§3.4 R3), enforces
scope permissions (§3.0) and the export size ceiling (§3.5), and serves
job queries / signed downloads (§3.6). All parsing / entity writes happen
in the worker (runner.py / exporter.py).
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, paginate
from mesh.attachment.storage import ObjectStorage
from mesh.data_jobs.mapping import infer_mapping, validate_export_mapping, validate_import_mapping
from mesh.data_jobs.parser import read_headers
from mesh.data_jobs.runner import ENQUEUE_EVENT_TYPE, enqueue_idempotency_key
from mesh.data_jobs.schemas import CreateExportJobRequest, CreateImportJobRequest
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member
from mesh.db.models.project import Project, ProjectMember
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from mesh.issue.filters import validate_combined_condition_count
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.data_jobs.service")

_MANAGER_ROLES = frozenset({"admin", "owner"})

# Flat export filter keys → issue columns (reuse the list-query contract,
# §3.5/E3). Nesting/condition budget via validate_combined_condition_count.
_FLAT_FILTER_KEYS = frozenset(
    {
        "state_category",
        "status_id",
        "priority",
        "assignee_id",
        "reporter_id",
        "project_id",
        "cycle_id",
        "milestone_id",
        "parent_id",
        "due_before",
        "due_after",
        "q",
    }
)


class DataJobService:
    """Stateless orchestrator over a session factory (house pattern)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Any,
        storage: ObjectStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._storage = storage
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # workspace resolution (workspace-less paths, SECURITY DEFINER 0020)
    # ------------------------------------------------------------------

    async def resolve_job_workspace(self, job_id: uuid.UUID) -> uuid.UUID | None:
        async with self._factory() as session:
            return await session.scalar(
                select(
                    func.mesh_data_job_workspace_id(job_id)  # type: ignore[arg-type]
                )
            )

    # ------------------------------------------------------------------
    # create import job (§3.2)
    # ------------------------------------------------------------------

    async def create_import_job(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        body: CreateImportJobRequest,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        source_attachment_id = _parse_uuid(body.source_attachment_id, "source attachment not found")
        target_project_id = (
            _parse_uuid(body.target_project_id, "project not found") if body.target_project_id else None
        )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self._assert_import_permission(session, member=member, project_id=target_project_id)
            source = await self._load_ready_source(session, member=member, attachment_id=source_attachment_id)
            mapping = body.mapping
            if body.auto_infer or not mapping:
                mapping = await self._infer_mapping(workspace_id, source, body.format)
            validate_import_mapping(mapping, entity_type=body.entity_type)
            # Idempotency: SELECT-first (duplicate key → first result, §6.14).
            if idempotency_key:
                existing = await session.scalar(
                    select(DataJob).where(
                        DataJob.workspace_id == workspace_id,
                        DataJob.requested_by == member.id,
                        DataJob.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return self._render_job(existing)
            job = DataJob(
                workspace_id=workspace_id,
                kind="import",
                entity_type=body.entity_type,
                format=body.format,
                status="pending",
                mapping=mapping,
                params={
                    "target_project_id": str(target_project_id) if target_project_id else None,
                    "options": (mapping.get("options") or {}),
                },
                source_attachment_id=source_attachment_id,
                requested_by=member.id,
                idempotency_key=idempotency_key,
                created_at=self._clock(),
                updated_at=self._clock(),
            )
            session.add(job)
            try:
                async with session.begin_nested():
                    await session.flush()
            except IntegrityError:
                if idempotency_key is None:
                    raise
                # Lost the idempotency race: the outer transaction is
                # poisoned, so re-select the winner on a FRESH session.
                async with self._factory() as lookup:
                    # Fresh session → set tenant GUC (data_jobs is RLS-protected).
                    await set_tenant_context(lookup, workspace_id)
                    winner = await lookup.scalar(
                        select(DataJob).where(
                            DataJob.workspace_id == workspace_id,
                            DataJob.requested_by == member.id,
                            DataJob.idempotency_key == idempotency_key,
                        )
                    )
                if winner is None:
                    raise
                return self._render_job(winner)
            await emit_event(
                session,
                workspace_id=workspace_id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "kind": "import", "action": "created"},
                idempotency_key=enqueue_idempotency_key(job.id, "created"),
            )
            return self._render_job(job)

    # ------------------------------------------------------------------
    # validate (§3.3) / run (§3.4)
    # ------------------------------------------------------------------

    async def validate_import(
        self, *, workspace_id: uuid.UUID, member: Member, job_id: uuid.UUID
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            if job.kind != "import":
                raise ConflictError("export jobs cannot be validated")
            if job.status != "pending":
                raise ConflictError("job is not in a validatable state")
            await self._assert_source_released(session, job.source_attachment_id)
            job.status = "validating"
            params = dict(job.params or {})
            params["validate_requested_at"] = self._clock().isoformat()
            job.params = params
            job.updated_at = self._clock()
            await emit_event(
                session,
                workspace_id=workspace_id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "kind": "import", "action": "import-validate"},
                idempotency_key=enqueue_idempotency_key(job.id, "import-validate"),
            )
            return self._render_job(job)

    async def run_import(
        self, *, workspace_id: uuid.UUID, member: Member, job_id: uuid.UUID
    ) -> dict[str, Any]:
        # Source integrity precheck OUTSIDE the write transaction (streamed
        # hash; no row locks held across storage I/O — review fix).
        frozen_hash = await self._frozen_source_hash(workspace_id, job_id, member)
        if frozen_hash is not None:
            current_hash = await self._current_source_hash(workspace_id, job_id, member)
            if current_hash != frozen_hash:
                raise BusinessRuleError(
                    "source file changed after validation; re-validate first",
                    code="source_changed",
                )
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            if job.kind != "import":
                raise ConflictError("export jobs cannot be run")
            if not (job.params or {}).get("validated_at"):
                raise BusinessRuleError("dry-run validation required before run", code="validation_required")
            if job.status != "pending":
                raise ConflictError("job is not in a runnable state")
            await self._assert_source_released(session, job.source_attachment_id)
            job.status = "running"
            job.started_at = self._clock()
            params = dict(job.params or {})
            params["run_requested_at"] = self._clock().isoformat()
            job.params = params
            job.updated_at = self._clock()
            await emit_event(
                session,
                workspace_id=workspace_id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "kind": "import", "action": "import-run"},
                idempotency_key=enqueue_idempotency_key(job.id, "import-run"),
            )
            return self._render_job(job)

    # ------------------------------------------------------------------
    # create export job (§3.5)
    # ------------------------------------------------------------------

    async def create_export_job(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        body: CreateExportJobRequest,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        project_id = _parse_uuid(body.project_id, "project not found") if body.project_id else None
        filters = self._validate_export_filters(body.filters)
        columns = validate_export_mapping(body.mapping, entity_type=body.entity_type)
        if body.scope == "project" and project_id is None:
            raise ValidationError("scope 'project' requires project_id")
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await self._assert_export_permission(
                session, member=member, scope=body.scope, project_id=project_id
            )
            await self._assert_export_size(
                session,
                workspace_id=workspace_id,
                member=member,
                entity_type=body.entity_type,
                project_id=project_id if body.scope == "project" else None,
                filters=filters,
            )
            if idempotency_key:
                existing = await session.scalar(
                    select(DataJob).where(
                        DataJob.workspace_id == workspace_id,
                        DataJob.requested_by == member.id,
                        DataJob.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return self._render_job(existing)
            job = DataJob(
                workspace_id=workspace_id,
                kind="export",
                entity_type=body.entity_type,
                format=body.format,
                status="pending",
                mapping={"columns": columns},
                params={
                    "scope": body.scope,
                    "project_id": str(project_id) if project_id else None,
                    "filters": filters,
                    "locale": body.locale,
                },
                requested_by=member.id,
                idempotency_key=idempotency_key,
                created_at=self._clock(),
                updated_at=self._clock(),
            )
            session.add(job)
            try:
                async with session.begin_nested():
                    await session.flush()
            except IntegrityError:
                if idempotency_key is None:
                    raise
                # Lost the idempotency race: the outer transaction is
                # poisoned, so re-select the winner on a FRESH session.
                async with self._factory() as lookup:
                    # Fresh session → set tenant GUC (data_jobs is RLS-protected).
                    await set_tenant_context(lookup, workspace_id)
                    winner = await lookup.scalar(
                        select(DataJob).where(
                            DataJob.workspace_id == workspace_id,
                            DataJob.requested_by == member.id,
                            DataJob.idempotency_key == idempotency_key,
                        )
                    )
                if winner is None:
                    raise
                return self._render_job(winner)
            await emit_event(
                session,
                workspace_id=workspace_id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "kind": "export", "action": "export"},
                idempotency_key=enqueue_idempotency_key(job.id, "export"),
            )
            return self._render_job(job)

    # ------------------------------------------------------------------
    # query / list / download (§3.6)
    # ------------------------------------------------------------------

    async def get_job(self, *, workspace_id: uuid.UUID, member: Member, job_id: uuid.UUID) -> dict[str, Any]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            return self._render_job(job, with_preview=True)

    async def list_jobs(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        kind: str | None = None,
        status: str | None = None,
        requested_by: uuid.UUID | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> dict[str, Any]:
        if kind not in (None, "import", "export"):
            raise ValidationError("invalid kind filter")
        if status is not None and status not in (
            "pending",
            "validating",
            "running",
            "completed",
            "completed_with_errors",
            "failed",
        ):
            raise ValidationError("invalid status filter")
        limit = max(1, min(limit, MAX_PAGE_LIMIT))
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            stmt = select(DataJob).where(DataJob.workspace_id == workspace_id)
            if requested_by is not None:
                if requested_by != member.id and member.role not in _MANAGER_ROLES:
                    raise ForbiddenError("only admins can list other members' jobs")
                stmt = stmt.where(DataJob.requested_by == requested_by)
            elif member.role not in _MANAGER_ROLES:
                stmt = stmt.where(DataJob.requested_by == member.id)
            if kind:
                stmt = stmt.where(DataJob.kind == kind)
            if status:
                stmt = stmt.where(DataJob.status == status)
            page = await paginate(
                session,
                stmt,
                sort_column=DataJob.created_at,
                id_column=DataJob.id,
                sort_value_of=lambda job: job.created_at,
                id_of=lambda job: job.id,
                cursor=cursor,
                limit=limit,
                descending=True,
            )
            return {
                "data": [self._render_job(job) for job in page.items],
                "next_cursor": page.next_cursor,
            }

    async def download_job(
        self,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        job_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Signed download via the attachment channel (§3.6 / §3.9)."""
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            if job.result_attachment_id is None:
                raise NotFoundError("product not ready")
            attachment = await session.get(Attachment, job.result_attachment_id)
            if attachment is None or attachment.deleted_at is not None:
                raise NotFoundError("product not found")
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            if blob is None or blob.scan_status not in ("clean", "skipped"):
                raise ForbiddenError("product not released", code="scan_pending")
            ttl = int(self._settings.attachment_download_url_ttl.total_seconds())
            url = await self._storage.presign_get(
                blob.storage_key,
                expires_in=ttl,
                content_disposition=f"attachment; filename*=UTF-8''{quote(attachment.file_name)}",
                content_type=blob.mime_type,
            )
            return {
                "data": {
                    "url": url,
                    "file_name": attachment.file_name,
                    "expires_at": (self._clock() + self._settings.attachment_download_url_ttl).isoformat(),
                }
            }

    # ------------------------------------------------------------------
    # permission gates (§3.0)
    # ------------------------------------------------------------------

    async def _assert_import_permission(
        self, session: AsyncSession, *, member: Member, project_id: uuid.UUID | None
    ) -> None:
        if member.role in _MANAGER_ROLES:
            return
        if project_id is None:
            raise ForbiddenError("workspace import requires admin/owner")
        membership = await session.scalar(
            select(ProjectMember).where(
                ProjectMember.workspace_id == member.workspace_id,
                ProjectMember.project_id == project_id,
                ProjectMember.member_id == member.id,
            )
        )
        if membership is None or membership.role not in ("lead", "member"):
            raise ForbiddenError("project import requires write access to the target project")

    async def _assert_export_permission(
        self,
        session: AsyncSession,
        *,
        member: Member,
        scope: str,
        project_id: uuid.UUID | None,
    ) -> None:
        if scope == "workspace":
            if member.role not in _MANAGER_ROLES:
                raise ForbiddenError("workspace export requires admin/owner")
            return
        if scope == "project" and project_id is not None:
            project = await session.scalar(
                select(Project).where(
                    Project.workspace_id == member.workspace_id,
                    Project.id == project_id,
                    Project.deleted_at.is_(None),
                )
            )
            if project is None:
                raise NotFoundError("project not found")
            if member.role in _MANAGER_ROLES or project.visibility == "public":
                return
            membership = await session.scalar(
                select(ProjectMember).where(
                    ProjectMember.workspace_id == member.workspace_id,
                    ProjectMember.project_id == project_id,
                    ProjectMember.member_id == member.id,
                )
            )
            if membership is None:
                raise ForbiddenError("private project export requires membership")
            return
        # scope='view' (L5 / §3.0): we do not re-check the named view's read
        # ACL here; instead the worker runs the export through
        # IssueService.list_issues(viewer=requested_by), so the result is
        # bounded by the requester's own list visibility — no data can leak
        # beyond what the requester may already see. Any member may therefore
        # start a view-scoped export; the visibility boundary is the list
        # query, not this gate.

    def _validate_export_filters(self, filters: dict[str, Any] | None) -> dict[str, Any] | None:
        if not filters:
            return None
        if not isinstance(filters, dict):
            raise ValidationError("filters must be an object", code="validation_error")
        unknown = sorted(set(filters) - _FLAT_FILTER_KEYS)
        if unknown:
            raise ValidationError(f"unknown filter field(s): {', '.join(unknown)}", code="validation_error")
        # §6.14 condition budget (≤20) → 400 filter_too_complex.
        try:
            validate_combined_condition_count(len(filters), None)
        except ValidationError as exc:
            raise ValidationError("filters too complex", code="filter_too_complex") from exc
        return filters

    async def _assert_export_size(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member: Member,
        entity_type: str,
        project_id: uuid.UUID | None,
        filters: dict[str, Any] | None,
    ) -> None:
        """Create-time row estimate WITH filters applied (review fix: filtered
        exports are no longer killed by the unfiltered workspace total)."""
        max_rows = self._settings.data_job_export_max_rows
        if entity_type == "projects":
            stmt = (
                select(func.count())
                .select_from(Project)
                .where(Project.workspace_id == workspace_id, Project.deleted_at.is_(None))
            )
            if project_id is not None:
                stmt = stmt.where(Project.id == project_id)
        else:
            stmt = (
                select(func.count())
                .select_from(Issue)
                .where(Issue.workspace_id == workspace_id, Issue.deleted_at.is_(None))
            )
            if project_id is not None:
                stmt = stmt.where(Issue.project_id == project_id)
            if member.role not in _MANAGER_ROLES:
                # Non-managers export under their own visibility; a rough
                # upper bound is enough for the 413 precheck.
                member_projects = select(ProjectMember.project_id).where(
                    ProjectMember.workspace_id == workspace_id,
                    ProjectMember.member_id == member.id,
                )
                stmt = stmt.where(or_(Issue.project_id.in_(member_projects), Issue.project_id.is_(None)))
            for key, value in (filters or {}).items():
                if key == "state_category" and isinstance(value, list):
                    stmt = stmt.where(Issue.state_category.in_(value))
                elif key == "priority":
                    stmt = stmt.where(Issue.priority == value)
                elif key == "status_id":
                    stmt = stmt.where(Issue.status_id == _parse_uuid(value, "invalid status_id"))
                elif key == "assignee_id":
                    stmt = stmt.where(Issue.assignee_id == _parse_uuid(value, "invalid assignee_id"))
                elif key == "reporter_id":
                    stmt = stmt.where(Issue.reporter_id == _parse_uuid(value, "invalid reporter_id"))
                elif key == "cycle_id":
                    stmt = stmt.where(Issue.cycle_id == _parse_uuid(value, "invalid cycle_id"))
                elif key == "milestone_id":
                    stmt = stmt.where(Issue.milestone_id == _parse_uuid(value, "invalid milestone_id"))
                elif key == "project_id":
                    stmt = stmt.where(Issue.project_id == _parse_uuid(value, "invalid project_id"))
            # q / due_before / due_after / parent_id refine further but never
            # widen the estimate — safe to ignore for the ceiling check.
        estimate = await session.scalar(stmt)
        if (estimate or 0) > max_rows:
            raise PayloadTooLargeError(
                "export estimate exceeds the row ceiling",
                code="export_too_large",
                details={"estimate": estimate, "max_rows": max_rows},
            )

    # ------------------------------------------------------------------
    # source attachment gates (§3.2 / §3.4 / §5.4 M-2)
    # ------------------------------------------------------------------

    async def _load_ready_source(
        self, session: AsyncSession, *, member: Member, attachment_id: uuid.UUID
    ) -> Attachment:
        attachment = await session.scalar(
            select(Attachment).where(
                Attachment.workspace_id == member.workspace_id,
                Attachment.id == attachment_id,
                Attachment.deleted_at.is_(None),
            )
        )
        if attachment is None:
            raise NotFoundError("source attachment not found")
        # M-2: the source must be the caller's own upload (no borrowing
        # other members' files by id).
        if attachment.uploader_id != member.id:
            raise ForbiddenError("source attachment must be uploaded by the caller")
        if attachment.upload_status != "completed":
            raise BusinessRuleError("source upload is not complete", code="source_not_ready")
        blob = await session.get(AttachmentBlob, attachment.blob_id)
        if blob is None or blob.scan_status not in ("clean", "skipped"):
            raise BusinessRuleError("source attachment is not released", code="source_not_ready")
        return attachment

    async def _assert_source_released(self, session: AsyncSession, attachment_id: uuid.UUID | None) -> None:
        if attachment_id is None:
            raise BusinessRuleError("import job has no source", code="source_not_ready")
        attachment = await session.get(Attachment, attachment_id)
        if attachment is None or attachment.deleted_at is not None:
            raise BusinessRuleError("source attachment unavailable", code="source_not_ready")
        blob = await session.get(AttachmentBlob, attachment.blob_id)
        if blob is None or blob.scan_status not in ("clean", "skipped"):
            raise BusinessRuleError("source attachment is not released", code="source_not_ready")

    async def _source_blob_key(self, workspace_id: uuid.UUID, job_id: uuid.UUID, member: Member) -> str:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            if job.source_attachment_id is None:
                raise BusinessRuleError("import job has no source", code="source_not_ready")
            attachment = await session.get(Attachment, job.source_attachment_id)
            if attachment is None:
                raise BusinessRuleError("source attachment unavailable", code="source_not_ready")
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            if blob is None:
                raise BusinessRuleError("source blob unavailable", code="source_not_ready")
            return blob.storage_key

    async def _frozen_source_hash(
        self, workspace_id: uuid.UUID, job_id: uuid.UUID, member: Member
    ) -> str | None:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            job = await self._load_owned_job(session, workspace_id=workspace_id, member=member, job_id=job_id)
            return job.source_content_hash

    async def _current_source_hash(self, workspace_id: uuid.UUID, job_id: uuid.UUID, member: Member) -> str:
        storage_key = await self._source_blob_key(workspace_id, job_id, member)
        directory = tempfile.mkdtemp(prefix="mesh-src-check-")
        path = os.path.join(directory, "source")
        try:
            _size, digest = await self._storage.download_to_path(
                storage_key, path, max_bytes=self._settings.data_job_source_max_bytes
            )
            return digest
        finally:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)

    async def _infer_mapping(
        self, workspace_id: uuid.UUID, source: Attachment, format: str
    ) -> dict[str, Any]:
        """auto_infer: stream the source out, read headers, draft a mapping."""
        blob = None
        async with self._factory() as session:
            # app role runs fail-closed RLS on attachment_blobs (§6.2) — the
            # tenant GUC must be set on this fresh session or the get 500s.
            await set_tenant_context(session, workspace_id)
            blob = await session.get(AttachmentBlob, source.blob_id)
        if blob is None:
            raise BusinessRuleError("source blob unavailable", code="source_not_ready")
        directory = tempfile.mkdtemp(prefix="mesh-infer-")
        path = os.path.join(directory, "source")
        try:
            await self._storage.download_to_path(
                blob.storage_key, path, max_bytes=self._settings.data_job_source_max_bytes
            )
            from mesh.data_jobs.parser import SourceParseError

            try:
                headers, _samples = read_headers(path, format)
            except SourceParseError as exc:
                raise BusinessRuleError(
                    "source file cannot be parsed",
                    code="source_not_ready",
                    details={"reason": str(exc)},
                ) from exc
        finally:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)
        mapping = infer_mapping(headers, entity_type="issues")
        if not mapping["columns"]:
            raise ValidationError("could not infer any mapping from source headers", code="mapping_invalid")
        return mapping

    # ------------------------------------------------------------------
    # ownership + rendering
    # ------------------------------------------------------------------

    async def _load_owned_job(
        self, session: AsyncSession, *, workspace_id: uuid.UUID, member: Member, job_id: uuid.UUID
    ) -> DataJob:
        """requested_by or workspace admin/owner — invisible = not found."""
        job = await session.scalar(
            select(DataJob).where(DataJob.workspace_id == workspace_id, DataJob.id == job_id)
        )
        if job is None:
            raise NotFoundError("data job not found")
        if job.requested_by != member.id and member.role not in _MANAGER_ROLES:
            raise NotFoundError("data job not found")
        return job

    def _render_job(self, job: DataJob, *, with_preview: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": str(job.id),
            "workspace_id": str(job.workspace_id),
            "kind": job.kind,
            "entity_type": job.entity_type,
            "format": job.format,
            "status": job.status,
            "total_rows": job.total_rows,
            "succeeded_rows": job.succeeded_rows,
            "failed_rows": job.failed_rows,
            "source_attachment_id": str(job.source_attachment_id) if job.source_attachment_id else None,
            "result_attachment_id": str(job.result_attachment_id) if job.result_attachment_id else None,
            "failure_reason": job.failure_reason,
            "requested_by": str(job.requested_by),
            "mapping": job.mapping,
            "params": job.params,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        }
        if with_preview:
            payload["error_report"] = job.error_report or []
        if job.result_attachment_id is not None:
            payload["download_url"] = f"/api/v1/data-jobs/{job.id}/download"
        return payload


def _parse_uuid(raw: str | None, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError) as exc:
        raise NotFoundError(message) from exc
