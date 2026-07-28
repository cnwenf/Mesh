"""Data-jobs worker pipeline (import-export.md §3.8 — R3 recovery + R4 fencing).

Outbox handlers (``data_job.enqueue`` / ``data_job.resume``) claim the job
in a short transaction and run the pipeline inline; every batch is ONE
database transaction carrying the fencing check, entity creation, ledger
rows, counters/checkpoint advance, lease renewal and the progress event
— a crash between batches leaves ``checkpoint.last_committed_batch``
behind and the reaper's fresh claim (``lease_seq + 1``) resumes after it.

Fencing (R4, README §6.4 paradigm): each claim increments the monotonic
``lease_seq``; every write transaction locks the job row and validates
``lease_owner + lease_seq + unexpired`` first — a resurrected stale
worker's batch (INCLUDING its terminal ``fail_job``) is rejected
wholesale, so old and new workers can never commit concurrently.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.attachment.service import AttachmentService
from mesh.attachment.storage import ObjectStorage, generate_storage_key
from mesh.comment_inbox.notifications import emit_notification_fanout
from mesh.config import Settings
from mesh.data_jobs import transforms
from mesh.data_jobs.parser import (
    RowKeyAllocator,
    SourceParseError,
    iter_source_rows,
)
from mesh.data_jobs.report import ErrorReportWriter
from mesh.data_jobs.transforms import RowError, StatusInfo, build_context, transform_row
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DATA_JOB_TERMINAL_STATUSES, DataJob, DataJobRow
from mesh.db.models.issue import (
    TITLE_MAX_LENGTH,
    Issue,
)
from mesh.db.models.label import (
    CustomFieldDef,
    IssueCustomFieldValue,
    Label,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project, ProjectMember
from mesh.db.models.workspace import IdentifierPrefixRegistry, Workspace
from mesh.db.tenant import set_tenant_context
from mesh.errors import ConflictError, MeshError
from mesh.issue.graph import detect_parent_cycle, lock_issue_graph
from mesh.issue.statuses import ensure_scope_seeded
from mesh.outbox.service import emit_realtime
from mesh.project.service import ProjectService
from mesh.workspace.service import DEFAULT_INBOX_PREFIX, next_inbox_issue_number, occupy_project_prefix

logger = logging.getLogger("mesh.data_jobs.runner")

ENQUEUE_EVENT_TYPE = "data_job.enqueue"
RESUME_EVENT_TYPE = "data_job.resume"

EXTERNAL_REF_FIELD_KEY = "external_ref"
EXTERNAL_REF_FIELD_NAME = "External Reference"

# Deterministic label palette for labels created on import.
_LABEL_PALETTE = ("#4f46e5", "#0891b2", "#059669", "#d97706", "#dc2626", "#7c3aed", "#db2777")


class FenceLostError(Exception):
    """The lease no longer belongs to this worker — roll back and stop."""


def _count_source_rows(path: str, format: str) -> int:
    """Cheap streaming row count (no transforms) — total_rows for the run."""
    total = 0
    for _row_number, _raw in iter_source_rows(path, format):
        total += 1
    return total


@dataclass(frozen=True)
class Claim:
    """A successful job claim — the worker's fencing identity for this run."""

    job_id: uuid.UUID
    workspace_id: uuid.UUID
    kind: str
    entity_type: str
    format: str
    status: str
    lease_seq: int
    resumed: bool


def enqueue_idempotency_key(job_id: uuid.UUID, action: str) -> str:
    """§6.5 convention: sha256(data_job_id | action)."""
    return hashlib.sha256(f"{job_id}|{action}".encode()).hexdigest()


def resume_idempotency_key(job_id: uuid.UUID, last_batch: int, *, bucket: int = 0) -> str:
    """§6.5: sha256(data_job_id | 'resume' | batch | rearm-bucket).

    The rearm bucket (a sub-lease-ttl time window) guarantees a reaper re-emit
    after a lease expiry never collides with an outbox row left over from an
    earlier recovery episode — so a hard crash at a deterministic poison batch
    can never wedge the job behind a stale published/failed row (T31⑤; H2)."""
    return hashlib.sha256(f"{job_id}|resume|{last_batch}|{bucket}".encode()).hexdigest()


def _rearm_bucket(now: datetime, settings: Settings) -> int:
    """Sub-lease-ttl window so re-arms re-key across recovery episodes (H2)."""
    window = max(1, int(settings.data_job_lease_ttl.total_seconds()) // 5)
    return int(now.timestamp() // window)


class DataJobWorker:
    """Runs import/export pipelines on claimed jobs (worker-process resident)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        storage: ObjectStorage,
        attachment_service: AttachmentService,
        *,
        worker_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._storage = storage
        self._attachments = attachment_service
        self._worker_id = worker_id or uuid.uuid4().hex
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------
    # entry points (relay handlers + tests drive these directly)
    # ------------------------------------------------------------------

    async def handle_enqueue(self, session: AsyncSession, event: OutboxEvent) -> None:
        """Outbox handler for ``data_job.enqueue`` — claim + run inline."""
        payload = event.payload or {}
        job_id = _uuid_or_none(payload.get("data_job_id"))
        if job_id is None:
            return None
        await self.process(job_id, str(payload.get("action") or "export"))

    async def handle_resume(self, session: AsyncSession, event: OutboxEvent) -> None:
        """Outbox handler for ``data_job.resume`` — reaper-driven recovery."""
        payload = event.payload or {}
        job_id = _uuid_or_none(payload.get("data_job_id"))
        if job_id is None:
            return None
        await self.process(job_id, "resume")

    async def process(self, job_id: uuid.UUID, action: str) -> None:
        """Claim the job and run its pipeline (never raises for job-level faults)."""
        try:
            claim = await self._claim(job_id, action)
        except Exception:  # noqa: BLE001 — claim failure → relay retry/backoff
            logger.exception("data job %s claim failed", job_id)
            raise
        if claim is None:
            return  # not claimable (wrong state / leased) — idempotent no-op
        try:
            if claim.kind == "export":
                from mesh.data_jobs.exporter import run_export_pipeline

                await run_export_pipeline(self, claim)
            elif action == "import-validate" or claim.status == "validating":
                await self.run_validate(claim)
            else:
                await self.run_import(claim)
        except FenceLostError:
            logger.info("data job %s fence lost — a newer worker owns it", job_id)
        except Exception:  # noqa: BLE001 — task-level fault → failed (fenced)
            logger.exception("data job %s failed", job_id)
            await self.fail_job(claim, "internal_error")

    # ------------------------------------------------------------------
    # claim (short transaction; the ONLY writer of lease identity)
    # ------------------------------------------------------------------

    async def _claim(self, job_id: uuid.UUID, action: str) -> Claim | None:
        now = self._clock()
        ttl = self._settings.data_job_lease_ttl
        async with self._factory() as session, session.begin():
            stmt = select(DataJob).where(DataJob.id == job_id).with_for_update(skip_locked=True)
            job = (await session.execute(stmt)).scalar_one_or_none()
            if job is None or job.status in DATA_JOB_TERMINAL_STATUSES:
                return None
            if action == "import-validate":
                if job.status != "validating":
                    return None
            elif action == "import-run":
                if job.status != "running" or job.lease_owner is not None:
                    return None
            elif action == "export":
                if job.kind != "export" or job.status != "pending":
                    return None
            else:  # resume — lease-expired reclaim re-dispatched by the reaper
                if job.status not in ("running", "validating") or job.lease_owner is not None:
                    return None
            old_seq = job.lease_seq
            # A genuine crash-recovery resume is signalled by the reaper's
            # action — NOT by old_seq>0 (a normal validate->run also bumps the
            # seq; L4).
            resumed = action == "resume"
            if resumed:
                checkpoint = dict(job.checkpoint or {})
                new_count = int(checkpoint.get("resumed_count") or 0) + 1
                if new_count > self._settings.data_job_max_resumes:
                    # Poison-batch crash-loop guard: terminate instead of
                    # looping forever (T31⑤).
                    job.status = "failed"
                    job.failure_reason = "resume_limit_exceeded"
                    job.finished_at = now
                    job.lease_owner = None
                    job.lease_expires_at = None
                    job.updated_at = now
                    await self._emit_job_event(session, job, transition="failed")
                    await self._emit_terminal_notification(session, job)
                    return None
                checkpoint["resumed_count"] = new_count
                checkpoint["resumed_at"] = now.isoformat()
                job.checkpoint = checkpoint
            job.lease_owner = self._worker_id
            job.lease_seq = old_seq + 1
            job.lease_expires_at = now + ttl
            if job.kind == "export" and job.status == "pending":
                job.status = "running"
                job.started_at = now
            params = dict(job.params or {})
            params.pop("validate_requested_at", None)
            params.pop("run_requested_at", None)
            job.params = params
            job.updated_at = now
            claim = Claim(
                job_id=job.id,
                workspace_id=job.workspace_id,
                kind=job.kind,
                entity_type=job.entity_type,
                format=job.format,
                status=job.status,
                lease_seq=job.lease_seq,
                resumed=resumed,
            )
            await self._emit_job_event(session, job, transition=f"claimed:{claim.lease_seq}")
            return claim

    # ------------------------------------------------------------------
    # validate pipeline (dry-run — never writes entities, §3.3)
    # ------------------------------------------------------------------

    async def run_validate(self, claim: Claim) -> None:
        job = await self._load_job(claim.job_id)
        if job is None:
            return
        scratch_dir = tempfile.mkdtemp(prefix="mesh-datajob-")
        source_path = os.path.join(scratch_dir, "source")
        report = ErrorReportWriter()
        try:
            size, content_hash = await self._fetch_source(job, source_path)
            frozen = job.source_content_hash
            # A FRESH (re-)validate re-establishes the snapshot, so a changed
            # source is simply re-frozen at finalize (§3.4 "re-validate" stays
            # reachable; M3). Only a crash-RESUME of a validate must refuse a
            # swapped source (the in-flight dry-run assumed the old bytes).
            if frozen and frozen != content_hash and claim.resumed:
                await self.fail_job(claim, "source_changed")
                return
            context = await self._build_context(job)
            preview: list[dict[str, Any]] = []
            total_rows = 0
            failed_rows = 0
            errors_preview: list[dict[str, Any]] = []
            warnings_preview: list[dict[str, Any]] = []
            preview_cap = self._settings.data_job_preview_rows
            error_cap = self._settings.data_job_error_preview_max
            renew_every = max(self._settings.data_job_batch_size, 100)
            allocator = RowKeyAllocator()
            try:
                for row_number, raw in iter_source_rows(source_path, claim.format):
                    total_rows += 1
                    values, errors, warnings = transform_row(row_number, raw, job.mapping or {}, context)
                    _row_key, is_duplicate = allocator.key_for(row_number, raw, values.get("external_ref"))
                    if is_duplicate:
                        errors.append(
                            RowError(
                                row_number,
                                "external_ref",
                                "duplicate_within_file",
                                f"duplicate external_ref {values.get('external_ref')!r}",
                            )
                        )
                    if errors:
                        failed_rows += 1
                        for error in errors:
                            report.add(error.as_dict())
                        if len(errors_preview) < error_cap:
                            errors_preview.append(errors[0].as_dict())
                    elif len(preview) < preview_cap:
                        preview.append({"row": row_number, "values": _preview_values(values)})
                    # §2.4 / §5.1: non-fatal transform warnings (e.g. status
                    # fell back to default) are recorded, not silently dropped —
                    # the row still succeeds, but the user can review them.
                    for warning in warnings:
                        if len(warnings_preview) < error_cap:
                            warnings_preview.append(warning.as_dict())
                    if total_rows % renew_every == 0:
                        await self._renew_lease(claim, transition=f"validating:{total_rows}")
            except SourceParseError as exc:
                await self.fail_job(claim, "source_unparseable", message=str(exc))
                return
            # Error report attachment (archive — generated even with 0 failures).
            report_path = report.finish()
            result_attachment_id = await self._upload_product(
                job,
                report_path,
                file_name=f"{claim.entity_type}-import-errors.csv",
                mime_type="text/csv",
                extension="csv",
            )
            # Final transaction (fenced): dry-run results + back to pending.
            async with self._factory() as session, session.begin():
                await set_tenant_context(session, claim.workspace_id)
                current = await self._lock_and_fence(session, claim)
                if current is None:
                    return
                current.status = "pending"
                current.total_rows = total_rows
                current.failed_rows = 0  # prediction lives in error_report/preview
                current.succeeded_rows = 0
                current.error_report = errors_preview
                current.source_content_hash = content_hash  # frozen (§2.2 R3)
                current.result_attachment_id = result_attachment_id
                current.lease_owner = None
                current.lease_expires_at = None
                params = dict(current.params or {})
                params["validated_at"] = self._clock().isoformat()
                params["predicted_failed_rows"] = failed_rows
                # §3.3: persist the mapping preview (first N rows, capped) so
                # GET /data-jobs/{id} and the wizard can show it — previously it
                # only lived in the realtime event extra and was lost (B6).
                params["preview"] = preview
                params["warnings"] = warnings_preview
                # validate is explicitly repeatable (§2.3); bump a round so each
                # finalize emits a fresh realtime frame — a static key would be
                # deduped by the outbox and re-validates would push nothing (B5).
                validate_round = int(params.get("validate_round") or 0) + 1
                params["validate_round"] = validate_round
                current.params = params
                current.updated_at = self._clock()
                await self._emit_job_event(
                    session,
                    current,
                    transition=f"validated:{validate_round}",
                    extra={"preview": preview, "predicted_failed_rows": failed_rows},
                )
        finally:
            report.cleanup()
            _cleanup_dir(scratch_dir)

    # ------------------------------------------------------------------
    # import run pipeline (partial success, fenced batches, §3.4)
    # ------------------------------------------------------------------

    async def run_import(self, claim: Claim) -> None:
        job = await self._load_job(claim.job_id)
        if job is None:
            return
        scratch_dir = tempfile.mkdtemp(prefix="mesh-datajob-")
        source_path = os.path.join(scratch_dir, "source")
        try:
            _size, content_hash = await self._fetch_source(job, source_path)
            if job.source_content_hash and job.source_content_hash != content_hash:
                await self.fail_job(claim, "source_changed")
                return
            context = await self._build_context(job)
            external_ref_def_id: uuid.UUID | None = None
            if claim.entity_type == "issues":
                external_ref_def_id = await self._ensure_external_ref_field(claim)
            total_expected = _count_source_rows(source_path, claim.format)
            checkpoint = job.checkpoint or {}
            # Honour the batch size frozen at first commit so a config change
            # between crashes cannot misalign the skip boundary (L3).
            batch_size = int(checkpoint.get("batch_size") or self._settings.data_job_batch_size)
            last_committed = int(checkpoint.get("last_committed_batch") or 0)
            allocator = RowKeyAllocator()
            batch: list[tuple[int, dict[str, Any]]] = []
            batch_index = 0
            try:
                for row_number, raw in iter_source_rows(source_path, claim.format):
                    batch_index = (row_number - 1) // batch_size + 1
                    batch.append((row_number, raw))
                    if len(batch) >= batch_size:
                        await self._run_batch(
                            claim,
                            batch,
                            batch_index,
                            context,
                            external_ref_def_id,
                            allocator,
                            total_expected,
                            skip=batch_index <= last_committed,
                        )
                        batch = []
                if batch:
                    await self._run_batch(
                        claim,
                        batch,
                        batch_index,
                        context,
                        external_ref_def_id,
                        allocator,
                        total_expected,
                        skip=batch_index <= last_committed,
                    )
            except SourceParseError as exc:
                await self.fail_job(claim, "source_unparseable", message=str(exc))
                return
            await self._resolve_parents(claim, context, source_path, external_ref_def_id)
            await self._finalize_import(claim, source_path)
        finally:
            _cleanup_dir(scratch_dir)

    async def _run_batch(
        self,
        claim: Claim,
        batch: list[tuple[int, dict[str, Any]]],
        batch_index: int,
        context: transforms.TransformContext,
        external_ref_def_id: uuid.UUID | None,
        allocator: RowKeyAllocator,
        total_expected: int = 0,
        *,
        skip: bool,
    ) -> None:
        """One batch = one transaction: fence → entities → ledger → counts →
        checkpoint → lease renewal → progress event (§3.4 / §3.8)."""
        job = await self._load_job(claim.job_id)
        mapping = (job.mapping if job else {}) or {}
        target_project_id = _uuid_or_none((job.params or {}).get("target_project_id")) if job else None
        requested_by = job.requested_by if job else None
        if skip:
            # Keep the row-key allocator state deterministic across a resume:
            # replay the (already committed) batch through the pure transform
            # so duplicate-ref counts match the original run.
            for row_number, raw in batch:
                values, _errors, _warnings = transform_row(row_number, raw, mapping, context)
                allocator.key_for(row_number, raw, values.get("external_ref"))
            return
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            current = await self._lock_and_fence(session, claim)
            if current is None:
                raise FenceLostError()
            if current.total_rows == 0 and total_expected:
                current.total_rows = total_expected  # written at run time (§2.2)
            succeeded_delta = 0
            failed_delta = 0
            last_key = ""
            for row_number, raw in batch:
                values, errors, warnings = transform_row(row_number, raw, mapping, context)
                row_key, is_duplicate = allocator.key_for(row_number, raw, values.get("external_ref"))
                if is_duplicate:
                    errors.append(
                        RowError(
                            row_number,
                            "external_ref",
                            "duplicate_within_file",
                            f"duplicate external_ref {values.get('external_ref')!r}",
                        )
                    )
                last_key = row_key
                if errors:
                    inserted = await self._insert_failed_row(
                        session,
                        claim=claim,
                        row_number=row_number,
                        row_key=row_key,
                        error=errors[0].as_dict(),
                    )
                    if inserted:
                        failed_delta += 1
                    continue
                target_id = uuid.uuid4()
                target_type = "issue" if claim.entity_type == "issues" else "project"
                claimed_row = await self._claim_ledger_row(
                    session,
                    claim=claim,
                    row_number=row_number,
                    row_key=row_key,
                    target_type=target_type,
                    target_id=target_id,
                )
                if claimed_row is None:
                    continue  # replay: the row was created by a committed batch
                try:
                    async with session.begin_nested():
                        if target_type == "issue":
                            await self._create_issue_row(
                                session,
                                claim=claim,
                                context=context,
                                target_id=target_id,
                                values=values,
                                target_project_id=target_project_id,
                                requested_by=requested_by,
                                external_ref_def_id=external_ref_def_id,
                            )
                        else:
                            await self._create_project_row(
                                session,
                                claim=claim,
                                target_id=target_id,
                                values=values,
                                requested_by=requested_by,
                            )
                except (FenceLostError, asyncio.CancelledError):
                    raise
                except Exception as exc:  # noqa: BLE001 — row-level -> failed row (§5.1)
                    # NEVER let one bad row (title too long, due<start, key
                    # collision, ...) bubble to a job-level failed (H1).
                    # Neutral message for driver/constraint errors (L2).
                    code, message = _row_error_from_exc(exc)
                    await self._record_row_failure(
                        session,
                        claim=claim,
                        row_number=row_number,
                        row_key=row_key,
                        error={"field": "", "code": code, "message": message},
                    )
                    failed_delta += 1
                    continue
                await session.execute(
                    update(DataJobRow)
                    .where(
                        DataJobRow.job_id == claim.job_id,
                        DataJobRow.row_key == row_key,
                    )
                    .values(status="created", updated_at=self._clock())
                )
                succeeded_delta += 1
            current.succeeded_rows = current.succeeded_rows + succeeded_delta
            current.failed_rows = current.failed_rows + failed_delta
            checkpoint = dict(current.checkpoint or {})
            checkpoint["last_committed_batch"] = batch_index
            checkpoint["last_row_key"] = last_key
            checkpoint.setdefault("batch_size", self._settings.data_job_batch_size)
            current.checkpoint = checkpoint
            current.lease_expires_at = self._clock() + self._settings.data_job_lease_ttl
            current.updated_at = self._clock()
            await self._emit_job_event(session, current, transition=f"progress:{batch_index}")

    # ------------------------------------------------------------------
    # entity creation (claim-before-create; numbering via the normal path)
    # ------------------------------------------------------------------

    async def _create_issue_row(
        self,
        session: AsyncSession,
        *,
        claim: Claim,
        context: transforms.TransformContext,
        target_id: uuid.UUID,
        values: dict[str, Any],
        target_project_id: uuid.UUID | None,
        requested_by: uuid.UUID | None,
        external_ref_def_id: uuid.UUID | None,
    ) -> None:
        title = (values.get("title") or "").strip()
        if not title or len(title) > TITLE_MAX_LENGTH:
            raise ValueError("title missing or too long")
        project_id = values.get("project_id") or target_project_id
        project: Project | None = None
        if project_id is not None:
            project = await session.get(Project, project_id)
            if project is None or project.deleted_at is not None:
                raise ValueError("target project not found")
        status: StatusInfo | None = values.get("status") or context.default_status
        if status is None:
            raise ValueError("no status resolvable in scope")
        due_date = values.get("due_date")
        start_date = values.get("start_date")
        if due_date and start_date and due_date < start_date:
            raise ValueError("due_date before start_date")
        if project is not None:
            number = await ProjectService(self._factory).next_issue_number(session, project_id=project.id)
            namespace_key = project.key
        else:
            number = await next_inbox_issue_number(session, workspace_id=claim.workspace_id)
            # §3.7 「与人工新建语义完全一致」: the inbox namespace key honors the
            # workspace's configurable ``inbox_issue_prefix`` exactly like the
            # manual create path (issue/service.py), not a hardcoded default.
            workspace = await session.scalar(
                select(Workspace).where(Workspace.id == claim.workspace_id)
            )
            ws_settings = (workspace.settings if workspace is not None else None) or {}
            namespace_key = ws_settings.get("inbox_issue_prefix") or DEFAULT_INBOX_PREFIX
        issue = Issue(
            id=target_id,
            workspace_id=claim.workspace_id,
            project_id=project.id if project else None,
            identifier_namespace_key=namespace_key,
            number=number,
            identifier=f"{namespace_key}-{number}",
            title=title,
            description=values.get("description"),
            status_id=status.id,
            state_category=status.category,
            priority=values.get("priority") or "none",
            assignee_id=values.get("assignee_id"),
            reporter_id=values.get("reporter_id") or requested_by,
            estimate=values.get("estimate"),
            due_date=due_date,
            start_date=start_date,
            milestone_id=values.get("milestone_id"),
            cycle_id=values.get("cycle_id"),
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        session.add(issue)
        await session.flush()
        # Labels — workspace-scoped get-or-create, then attach (idempotent).
        for name in values.get("labels") or ():
            label_id = context.labels_by_name.get(name)
            if label_id is None:
                label = Label(
                    workspace_id=claim.workspace_id,
                    project_id=project.id if project else None,
                    name=name[:50],
                    color=_label_color(name),
                )
                session.add(label)
                try:
                    async with session.begin_nested():
                        await session.flush()
                    label_id = label.id
                    context.labels_by_name[name] = label_id
                except IntegrityError:
                    label_id = await session.scalar(
                        select(Label.id).where(
                            Label.workspace_id == claim.workspace_id,
                            Label.project_id.is_(None) if project is None else Label.project_id == project.id,
                            Label.name == name[:50],
                        )
                    )
            if label_id is not None:
                await session.execute(
                    text(
                        "INSERT INTO issue_labels (workspace_id, issue_id, label_id) "
                        "VALUES (:ws, :issue, :label) ON CONFLICT DO NOTHING"
                    ),
                    {"ws": claim.workspace_id, "issue": target_id, "label": label_id},
                )
        # Custom field values (incl. the external_ref system field).
        custom_values = dict(values.get("custom_field_values") or {})
        external_ref = values.get("external_ref")
        if external_ref and external_ref_def_id is not None:
            custom_values.setdefault(EXTERNAL_REF_FIELD_KEY, str(external_ref)[:2000])
        for key, value in custom_values.items():
            field_info = context.custom_fields_by_key.get(key)
            if field_info is None:
                if key == EXTERNAL_REF_FIELD_KEY and external_ref_def_id is not None:
                    field_info = transforms.CustomFieldInfo(id=external_ref_def_id, type="text")
                else:
                    continue
            column, coerced = _custom_value_column(field_info.type, value)
            if column is None:
                continue
            await session.execute(
                text(
                    f"INSERT INTO issue_custom_field_values "
                    f"(id, workspace_id, issue_id, field_def_id, {column}, created_at, updated_at) "
                    f"VALUES (gen_random_uuid(), :ws, :issue, :def, :value, now(), now()) "
                    f"ON CONFLICT (issue_id, field_def_id) DO NOTHING"
                ),
                {"ws": claim.workspace_id, "issue": target_id, "def": field_info.id, "value": coerced},
            )

    async def _create_project_row(
        self,
        session: AsyncSession,
        *,
        claim: Claim,
        target_id: uuid.UUID,
        values: dict[str, Any],
        requested_by: uuid.UUID | None,
    ) -> None:
        name = (values.get("name") or "").strip()
        key = (values.get("key") or "").strip().upper()
        if not name or not key:
            raise ValueError("project name/key required")
        lead_id = values.get("lead_member_id")
        # M2: pre-check the prefix registry so a taken/retired/inbox key is a
        # row-level project_key_taken, reported BEFORE the project INSERT.
        occupied = await session.scalar(
            select(IdentifierPrefixRegistry.id).where(
                IdentifierPrefixRegistry.workspace_id == claim.workspace_id,
                IdentifierPrefixRegistry.key == key,
            )
        )
        if occupied is not None:
            raise ConflictError(
                "project key is already taken in this workspace",
                code="project_key_taken",
                details={"key": key},
            )
        project = Project(
            id=target_id,
            workspace_id=claim.workspace_id,
            name=name[:120],
            key=key,
            description=values.get("description"),
            status=values.get("status") or "planning",
            health=values.get("health") or "on_track",
            lead_member_id=lead_id,
            start_date=values.get("start_date"),
            target_date=values.get("target_date"),
            created_at=self._clock(),
            updated_at=self._clock(),
        )
        session.add(project)
        await session.flush()
        # Prefix registry exclusivity (README §6.3 — permanent reservation).
        await occupy_project_prefix(session, workspace_id=claim.workspace_id, key=key, project_id=target_id)
        await ensure_scope_seeded(session, workspace_id=claim.workspace_id, project_id=target_id)
        for member_id, role in (
            (lead_id, "lead") if lead_id else (None, ""),
            (requested_by, "member") if requested_by else (None, ""),
        ):
            if member_id is None:
                continue
            member_row = ProjectMember(
                workspace_id=claim.workspace_id,
                project_id=target_id,
                member_id=member_id,
                role=role,
            )
            session.add(member_row)
            try:
                async with session.begin_nested():
                    await session.flush()
            except IntegrityError:
                pass  # duplicate membership is harmless

    async def _ensure_external_ref_field(self, claim: Claim) -> uuid.UUID | None:
        """Idempotently ensure the workspace-level ``external_ref`` system field."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            existing = await session.scalar(
                select(CustomFieldDef.id).where(
                    CustomFieldDef.workspace_id == claim.workspace_id,
                    CustomFieldDef.project_id.is_(None),
                    CustomFieldDef.field_key == EXTERNAL_REF_FIELD_KEY,
                )
            )
            if existing is not None:
                return existing
            definition = CustomFieldDef(
                workspace_id=claim.workspace_id,
                project_id=None,
                name=EXTERNAL_REF_FIELD_NAME,
                field_key=EXTERNAL_REF_FIELD_KEY,
                type="text",
                position=0,
                is_active=True,
            )
            session.add(definition)
            try:
                async with session.begin_nested():
                    await session.flush()
                return definition.id
            except IntegrityError:
                return await session.scalar(
                    select(CustomFieldDef.id).where(
                        CustomFieldDef.workspace_id == claim.workspace_id,
                        CustomFieldDef.project_id.is_(None),
                        CustomFieldDef.field_key == EXTERNAL_REF_FIELD_KEY,
                    )
                )

    # ------------------------------------------------------------------
    # parent second pass (external_ref resolution + cycle safety, §3.7)
    # ------------------------------------------------------------------

    async def _resolve_parents(
        self,
        claim: Claim,
        context: transforms.TransformContext,
        source_path: str,
        external_ref_def_id: uuid.UUID | None,
    ) -> None:
        if claim.entity_type != "issues":
            return
        if external_ref_def_id is None:
            # Without the system field definition there is no authoritative
            # source for parent keys (§3.7) — nothing to resolve.
            return
        job = await self._load_job(claim.job_id)
        mapping = (job.mapping if job else {}) or {}
        has_parent_map = any(
            (column.get("transform") or {}).get("type") == "parent_by_external_ref"
            for column in mapping.get("columns") or ()
        )
        if not has_parent_map:
            return
        # Rebuild (row_key → parent_ref) from the same source (hash-verified
        # identical) — committed batches are included, so a resume does not
        # lose pre-crash parent links (review fix).
        allocator = RowKeyAllocator()
        parent_refs: dict[str, str] = {}
        for row_number, raw in iter_source_rows(source_path, claim.format):
            values, _errors, _warnings = transform_row(row_number, raw, mapping, context)
            row_key, _dup = allocator.key_for(row_number, raw, values.get("external_ref"))
            parent_ref = values.get("parent_external_ref")
            if parent_ref:
                parent_refs[row_key] = parent_ref
        if not parent_refs:
            return
        warnings: list[dict[str, Any]] = []
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            current = await self._lock_and_fence(session, claim)
            if current is None:
                raise FenceLostError()
            ledger_rows = (
                (
                    await session.execute(
                        select(DataJobRow).where(
                            DataJobRow.job_id == claim.job_id,
                            DataJobRow.status == "created",
                            DataJobRow.target_type == "issue",
                        )
                    )
                )
                .scalars()
                .all()
            )
            external_rows = (
                await session.execute(
                    select(IssueCustomFieldValue.issue_id, IssueCustomFieldValue.value_text)
                    .where(IssueCustomFieldValue.workspace_id == claim.workspace_id)
                    # §3.7: resolve parents ONLY through the ``external_ref``
                    # system field — any other text custom-field value that
                    # happens to equal a source parent key must not link.
                    .where(IssueCustomFieldValue.field_def_id == external_ref_def_id)
                    .where(IssueCustomFieldValue.issue_id.in_([r.target_id for r in ledger_rows]))
                )
            ).all()
            ref_to_issue = {ref: issue_id for issue_id, ref in external_rows if ref}
            await lock_issue_graph(session, claim.workspace_id)
            for row in ledger_rows:
                parent_ref = parent_refs.get(row.row_key)
                if not parent_ref:
                    continue
                parent_issue_id = ref_to_issue.get(parent_ref)
                if parent_issue_id is None or parent_issue_id == row.target_id:
                    warnings.append(
                        {
                            "row": row.row_number,
                            "field": "parent",
                            "code": "parent_not_found",
                            "message": f"parent {parent_ref!r} not found; kept top-level",
                        }
                    )
                    continue
                cycle = await detect_parent_cycle(
                    session,
                    workspace_id=claim.workspace_id,
                    issue_id=row.target_id,
                    new_parent_id=parent_issue_id,
                )
                if cycle is not None:
                    warnings.append(
                        {
                            "row": row.row_number,
                            "field": "parent",
                            "code": "parent_not_found",
                            "message": f"parent {parent_ref!r} would create a cycle; kept top-level",
                        }
                    )
                    continue
                await session.execute(
                    update(Issue)
                    .where(Issue.id == row.target_id, Issue.workspace_id == claim.workspace_id)
                    .values(parent_id=parent_issue_id, updated_at=self._clock())
                )
            if warnings:
                # Persist warnings as ledger rows so the final error_report can
                # be REBUILT idempotently from the ledger (M1). ON CONFLICT
                # guards a resume that re-runs this pass after a crash between
                # here and _finalize_import; the per-row key is stable + short
                # (no message in the key).
                await session.execute(
                    text(
                        "INSERT INTO data_job_rows "
                        "(id, workspace_id, job_id, row_number, row_key, status, "
                        "error, created_at, updated_at) "
                        "SELECT gen_random_uuid(), :ws, :job, (w->>'row')::int, "
                        "'warning:' || (w->>'row')::int, 'skipped', w, now(), now() "
                        "FROM jsonb_array_elements(CAST(:warnings AS jsonb)) w "
                        "ON CONFLICT (job_id, row_key) DO NOTHING"
                    ),
                    {"ws": claim.workspace_id, "job": claim.job_id, "warnings": _json(warnings)},
                )

    # ------------------------------------------------------------------
    # terminal paths
    # ------------------------------------------------------------------

    async def _finalize_import(self, claim: Claim, source_path: str) -> None:
        """Rebuild the full error report from the ledger, then terminate."""
        report = ErrorReportWriter()
        preview: list[dict[str, Any]] = []
        try:
            error_cap = self._settings.data_job_error_preview_max
            async with self._factory() as session:
                await set_tenant_context(session, claim.workspace_id)
                # Rebuild the preview ENTIRELY from the ledger (failed rows +
                # parent-resolution warnings stored as skipped-with-error rows)
                # so a resume yields an identical, de-duplicated report (M1).
                ledger_rows = (
                    await session.execute(
                        select(DataJobRow.row_number, DataJobRow.error)
                        .where(
                            DataJobRow.job_id == claim.job_id,
                            or_(
                                DataJobRow.status == "failed",
                                (DataJobRow.status == "skipped") & (DataJobRow.error.is_not(None)),
                            ),
                        )
                        .order_by(DataJobRow.row_number.asc())
                    )
                ).all()
                for row_number, error in ledger_rows:
                    entry = {
                        "row": row_number,
                        "field": (error or {}).get("field", ""),
                        "code": (error or {}).get("code", ""),
                        "message": (error or {}).get("message", ""),
                    }
                    report.add(entry)
                    if len(preview) < error_cap:
                        preview.append(entry)
            report_path = report.finish()
            result_attachment_id = await self._upload_product(
                await self._require_job(claim.job_id),
                report_path,
                file_name=f"{claim.entity_type}-import-errors.csv",
                mime_type="text/csv",
                extension="csv",
            )
            async with self._factory() as session, session.begin():
                await set_tenant_context(session, claim.workspace_id)
                job = await self._lock_and_fence(session, claim)
                if job is None:
                    return
                status = "completed" if job.failed_rows == 0 else "completed_with_errors"
                job.status = status
                job.finished_at = self._clock()
                job.error_report = preview  # failures + carried-over warnings
                job.result_attachment_id = result_attachment_id
                job.lease_owner = None
                job.lease_expires_at = None
                job.updated_at = self._clock()
                await self._emit_job_event(session, job, transition="finished")
                await self._emit_terminal_notification(session, job)
        finally:
            report.cleanup()

    async def fail_job(self, claim: Claim, reason: str, *, message: str | None = None) -> None:
        """Task-level failure — FENCED (review fix: a stale worker can never
        fail a job a newer worker owns)."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            job = await self._lock_and_fence(session, claim)
            if job is None:
                return  # fence mismatch or terminal — give up silently
            job.status = "failed"
            job.failure_reason = reason
            job.finished_at = self._clock()
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = self._clock()
            await self._emit_job_event(session, job, transition="failed")
            await self._emit_terminal_notification(session, job, extra_message=message)
        logger.warning("data job %s failed: %s", claim.job_id, reason)

    # ------------------------------------------------------------------
    # fencing / lease helpers
    # ------------------------------------------------------------------

    async def _lock_and_fence(self, session: AsyncSession, claim: Claim) -> DataJob | None:
        """Lock the job row and validate the fencing token (R4).

        Returns the locked row, or None when the lease no longer belongs to
        this worker (owner / seq mismatch, or expired) — the caller's
        transaction then rolls back wholesale.
        """
        job = (
            await session.execute(select(DataJob).where(DataJob.id == claim.job_id).with_for_update())
        ).scalar_one_or_none()
        if job is None or job.status in DATA_JOB_TERMINAL_STATUSES:
            return None
        if (
            job.lease_owner != self._worker_id
            or job.lease_seq != claim.lease_seq
            or job.lease_expires_at is None
            or job.lease_expires_at <= self._clock()
        ):
            return None
        return job

    async def _renew_lease(self, claim: Claim, *, transition: str) -> None:
        """Extend the lease in its own (fenced) transaction — keeps long
        streaming phases (validate/export) ahead of the reaper."""
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            job = await self._lock_and_fence(session, claim)
            if job is None:
                raise FenceLostError()
            job.lease_expires_at = self._clock() + self._settings.data_job_lease_ttl
            job.updated_at = self._clock()
            await self._emit_job_event(session, job, transition=transition)

    # ------------------------------------------------------------------
    # ledger helpers
    # ------------------------------------------------------------------

    async def _claim_ledger_row(
        self,
        session: AsyncSession,
        *,
        claim: Claim,
        row_number: int,
        row_key: str,
        target_type: str,
        target_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Atomic row_key occupation BEFORE entity creation (R4, §2.5).

        Returns the ledger row id when claimed (0 rows → the row was
        already created by a committed batch — the caller skips creation).
        """
        result = await session.execute(
            text(
                "INSERT INTO data_job_rows "
                "(id, workspace_id, job_id, row_number, row_key, status, target_type, "
                " target_id, attempts, created_at, updated_at) "
                "VALUES (:id, :ws, :job, :row_number, :row_key, 'pending', :target_type, "
                "        :target_id, 1, now(), now()) "
                "ON CONFLICT (job_id, row_key) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "ws": claim.workspace_id,
                "job": claim.job_id,
                "row_number": row_number,
                "row_key": row_key,
                "target_type": target_type,
                "target_id": target_id,
            },
        )
        row = result.first()
        return row[0] if row else None

    async def _insert_failed_row(
        self,
        session: AsyncSession,
        *,
        claim: Claim,
        row_number: int,
        row_key: str,
        error: dict[str, Any],
    ) -> bool:
        """Record a failed row (replay-safe via ON CONFLICT)."""
        result = await session.execute(
            text(
                "INSERT INTO data_job_rows "
                "(id, workspace_id, job_id, row_number, row_key, status, error, attempts, "
                " created_at, updated_at) "
                "VALUES (:id, :ws, :job, :row_number, :row_key, 'failed', :error, 1, now(), now()) "
                "ON CONFLICT (job_id, row_key) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": uuid.uuid4(),
                "ws": claim.workspace_id,
                "job": claim.job_id,
                "row_number": row_number,
                "row_key": row_key,
                "error": _json(error),
            },
        )
        return result.first() is not None

    async def _record_row_failure(
        self,
        session: AsyncSession,
        *,
        claim: Claim,
        row_number: int,
        row_key: str,
        error: dict[str, Any],
    ) -> None:
        """Convert a rolled-back claim (entity creation failed) into a failed row."""
        await session.execute(
            text(
                "INSERT INTO data_job_rows "
                "(id, workspace_id, job_id, row_number, row_key, status, error, attempts, "
                " created_at, updated_at) "
                "VALUES (:id, :ws, :job, :row_number, :row_key, 'failed', :error, 1, now(), now()) "
                "ON CONFLICT (job_id, row_key) DO UPDATE "
                "SET status = 'failed', error = :error, updated_at = now()"
            ),
            {
                "id": uuid.uuid4(),
                "ws": claim.workspace_id,
                "job": claim.job_id,
                "row_number": row_number,
                "row_key": row_key,
                "error": _json(error),
            },
        )

    # ------------------------------------------------------------------
    # source / product I/O (streamed — §5 RED LINE)
    # ------------------------------------------------------------------

    async def _fetch_source(self, job: DataJob, dest_path: str) -> tuple[int, str]:
        attachment, blob = await self._load_source_blob(job)
        return await self._storage.download_to_path(
            blob.storage_key, dest_path, max_bytes=self._settings.data_job_source_max_bytes
        )

    async def _load_source_blob(self, job: DataJob) -> tuple[Attachment, AttachmentBlob]:
        async with self._factory() as session:
            await set_tenant_context(session, job.workspace_id)
            attachment = await session.get(Attachment, job.source_attachment_id)
            if attachment is None or attachment.deleted_at is not None:
                raise SourceParseError("source attachment missing")
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            if blob is None:
                raise SourceParseError("source blob missing")
            return attachment, blob

    async def _upload_product(
        self,
        job: DataJob,
        path: str,
        *,
        file_name: str,
        mime_type: str,
        extension: str,
    ) -> uuid.UUID:
        """Stream a worker-generated file into the attachment channel (§3.9)."""
        from mesh.data_jobs.parser import hash_file

        content_hash = await asyncio.to_thread(hash_file, path)
        size = os.path.getsize(path)
        storage_key = generate_storage_key(job.workspace_id, content_hash)
        with open(path, "rb") as handle:
            await self._storage.put_fileobj(storage_key, handle, content_type=mime_type, content_length=size)
        idem_key = f"data-job-product:{job.id}:{file_name}:{content_hash}"
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, job.workspace_id)
            existing = await session.scalar(
                select(Attachment.id).where(
                    Attachment.workspace_id == job.workspace_id,
                    Attachment.uploader_id == job.requested_by,
                    Attachment.idempotency_key == idem_key,
                )
            )
            if existing is not None:
                return existing
            attachment = await self._attachments.register_server_attachment(
                session,
                workspace_id=job.workspace_id,
                uploader_member_id=job.requested_by,
                file_name=file_name,
                mime_type=mime_type,
                extension=extension,
                storage_key=storage_key,
                file_size=size,
                content_hash=content_hash,
                idempotency_key=idem_key,
            )
            return attachment.id

    # ------------------------------------------------------------------
    # events / notifications
    # ------------------------------------------------------------------

    async def _emit_job_event(
        self,
        session: AsyncSession,
        job: DataJob,
        *,
        transition: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """``data_job.updated`` on ``data_job:{id}`` — the ONLY realtime path."""
        data: dict[str, Any] = {
            "id": str(job.id),
            "status": job.status,
            "total_rows": job.total_rows,
            "succeeded_rows": job.succeeded_rows,
            "failed_rows": job.failed_rows,
            "result_attachment_id": str(job.result_attachment_id) if job.result_attachment_id else None,
            "failure_reason": job.failure_reason,
            "updated_at": (job.updated_at or self._clock()).isoformat(),
            "visibility": {"workspace_id": str(job.workspace_id)},
        }
        if extra:
            data.update(extra)
        await emit_realtime(
            session,
            workspace_id=job.workspace_id,
            channel=f"data_job:{job.id}",
            event="data_job.updated",
            data=data,
            idempotency_key=f"data-job:{job.id}:{transition}",
        )

    async def _emit_terminal_notification(
        self,
        session: AsyncSession,
        job: DataJob,
        *,
        extra_message: str | None = None,
    ) -> None:
        """§6.13 data-job three rows via the canonical fan-out (no local tiers)."""
        preview = (
            f"{job.kind} {job.entity_type} {job.status}: "
            f"succeeded={job.succeeded_rows} failed={job.failed_rows} total={job.total_rows}"
        )
        if extra_message:
            preview = f"{preview} ({extra_message})"
        # §3.10 failed row: the critical failure notification carries the
        # task-level reason (source_changed / export_too_large / …) so the
        # inbox preview is actionable, not a generic "failed".
        if job.status == "failed" and job.failure_reason and job.failure_reason not in preview:
            preview = f"{preview} reason={job.failure_reason}"
        await emit_notification_fanout(
            session,
            workspace_id=job.workspace_id,
            notification_type="data_job_finished",
            actor_kind="system",
            recipient_ids=[job.requested_by],
            group_key=f"data_job:{job.id}:finished",
            preview=preview,
            extra={
                "data_job_status": job.status,
                "data_job_id": str(job.id),
                "data_job_kind": job.kind,
                "failure_reason": job.failure_reason,
                "succeeded_rows": job.succeeded_rows,
                "failed_rows": job.failed_rows,
                "total_rows": job.total_rows,
            },
            idempotency_key=f"data-job:{job.id}:notify:{job.status}",
        )

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------

    async def _load_job(self, job_id: uuid.UUID) -> DataJob | None:
        async with self._factory() as session:
            return await session.get(DataJob, job_id)

    async def _require_job(self, job_id: uuid.UUID) -> DataJob:
        job = await self._load_job(job_id)
        if job is None:
            raise ValueError(f"data job {job_id} missing")
        return job

    async def _build_context(self, job: DataJob) -> transforms.TransformContext:
        target_project_id = _uuid_or_none((job.params or {}).get("target_project_id"))
        async with self._factory() as session:
            await set_tenant_context(session, job.workspace_id)
            return await build_context(
                session,
                workspace_id=job.workspace_id,
                entity_type=job.entity_type,
                project_id=target_project_id,
                mapping=job.mapping or {},
            )


# ---------------------------------------------------------------------------
# module-level helpers (pure)
# ---------------------------------------------------------------------------


def _uuid_or_none(raw: Any) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _short(exc: Exception, limit: int = 120) -> str:
    text = str(exc).split("\n")[0]
    return text[:limit]


def _label_color(name: str) -> str:
    return _LABEL_PALETTE[hash(name) % len(_LABEL_PALETTE)]


def _row_error_from_exc(exc: Exception) -> tuple[str, str]:
    """Map a row-creation exception to a (code, neutral message) pair.

    MeshError carries a crafted code/message (e.g. project_key_taken); our own
    ValueError text is safe to surface; anything else (IntegrityError / driver)
    is reduced to a neutral message so SQL / constraint / driver names never
    reach the downloadable error report (§5.4; L2)."""
    if isinstance(exc, MeshError):
        return (getattr(exc, "code", None) or "invalid_value"), str(exc) or "row rejected"
    if isinstance(exc, ValueError):
        return "invalid_value", str(exc) or "invalid value"
    return "invalid_value", "row could not be imported"


def _cleanup_dir(path: str) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _custom_value_column(field_type: str, value: Any) -> tuple[str | None, Any]:
    mapping = {
        "text": "value_text",
        "textarea": "value_text",
        "url": "value_text",
        "number": "value_number",
        "date": "value_date",
        "datetime": "value_date",
        "boolean": "value_boolean",
        "member": "value_member_id",
        "single_select": "value_json",
        "multi_select": "value_json",
    }
    column = mapping.get(field_type)
    if column == "value_date" and isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return column, value


def _preview_values(values: dict[str, Any]) -> dict[str, Any]:
    """Render transformed values for the dry-run mapping preview (§3.3)."""
    rendered: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, StatusInfo):
            rendered[key] = f"status:{value.category}"
        elif isinstance(value, (uuid.UUID, Decimal)):
            rendered[key] = str(value)
        elif isinstance(value, (datetime, date)):
            rendered[key] = value.isoformat()
        else:
            rendered[key] = value
    return rendered
