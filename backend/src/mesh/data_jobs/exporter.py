"""Async export pipeline (import-export.md §3.5 / §3.9 / §5.2).

Streams the list query page-by-page (cursor pagination, never a full
load) into a scratch file — CSV via ``csv.writer``, JSON as an
incremental array — then registers the product through the unified
attachment channel (§3.9). Each page renews the lease inside its own
fenced transaction (review fix: long exports can no longer livelock
against the reaper) and emits a ``data_job.updated`` progress frame
(§3.11). Row/byte ceilings trip ``export_too_large`` (§3.12).
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from mesh.data_jobs.mapping import validate_export_mapping
from mesh.data_jobs.runner import Claim, FenceLostError, _uuid_or_none
from mesh.db.models.data_job import DataJob
from mesh.db.models.label import CustomFieldDef, IssueCustomFieldValue, IssueLabel, Label
from mesh.db.models.member import Member
from mesh.db.models.project import Project
from mesh.db.tenant import set_tenant_context
from mesh.issue.service import IssueService

_EXPORT_PAGE_LIMIT = 200


async def run_export_pipeline(worker: Any, claim: Claim) -> None:
    """Generate the export product, register it, terminate the job."""
    job: DataJob | None = await worker._load_job(claim.job_id)
    if job is None:
        return
    columns = validate_export_mapping(job.mapping, entity_type=claim.entity_type)
    scratch_dir = tempfile.mkdtemp(prefix="mesh-export-")
    extension = "csv" if claim.format == "csv" else "json"
    product_path = os.path.join(scratch_dir, f"export.{extension}")
    max_rows = worker._settings.data_job_export_max_rows
    max_bytes = worker._settings.data_job_export_max_bytes
    try:
        writer = _ProductWriter(product_path, claim.format, columns)
        total_rows = 0
        try:
            if claim.entity_type == "issues":
                async for row in _iter_issue_rows(worker, job, columns):
                    writer.write(row)
                    total_rows += 1
                    if total_rows % _EXPORT_PAGE_LIMIT == 0:
                        await worker._renew_lease(claim, transition=f"export:{total_rows}")
                        if writer.size_bytes() > max_bytes or total_rows > max_rows:
                            await worker.fail_job(claim, "export_too_large")
                            return
            else:
                async for row in _iter_project_rows(worker, job):
                    writer.write(row)
                    total_rows += 1
                    if total_rows % _EXPORT_PAGE_LIMIT == 0:
                        await worker._renew_lease(claim, transition=f"export:{total_rows}")
                        if writer.size_bytes() > max_bytes or total_rows > max_rows:
                            await worker.fail_job(claim, "export_too_large")
                            return
        except FenceLostError:
            raise
        except Exception as exc:  # noqa: BLE001 — storage/DB fault
            await worker.fail_job(claim, "storage_error", message=type(exc).__name__)
            return
        writer.close()
        try:
            file_name = _product_file_name(job)
            result_attachment_id = await worker._upload_product(
                job,
                product_path,
                file_name=file_name,
                mime_type="text/csv" if claim.format == "csv" else "application/json",
                extension=extension,
            )
        except FenceLostError:
            raise
        except Exception as exc:  # noqa: BLE001 — upload fault
            await worker.fail_job(claim, "storage_error", message=type(exc).__name__)
            return
        if writer.size_bytes() > max_bytes or total_rows > max_rows:
            await worker.fail_job(claim, "export_too_large")
            return
        async with worker._factory() as session, session.begin():
            await set_tenant_context(session, claim.workspace_id)
            current = await worker._lock_and_fence(session, claim)
            if current is None:
                return
            current.status = "completed"
            current.total_rows = total_rows
            current.succeeded_rows = total_rows
            current.finished_at = worker._clock()
            current.result_attachment_id = result_attachment_id
            current.lease_owner = None
            current.lease_expires_at = None
            current.updated_at = worker._clock()
            await worker._emit_job_event(session, current, transition="finished")
            await worker._emit_terminal_notification(session, current)
    finally:
        import shutil

        shutil.rmtree(scratch_dir, ignore_errors=True)


class _ProductWriter:
    """Streams export rows to disk (CSV rows / incremental JSON array)."""

    def __init__(self, path: str, format: str, columns: list[dict[str, str]]) -> None:
        self._path = path
        self._format = format
        self._columns = columns
        self._handle = open(path, "w", encoding="utf-8", newline="")
        self._closed = False
        if format == "csv":
            self._csv = csv.writer(self._handle)
            self._csv.writerow([column["source"] for column in columns])
        else:
            self._json_started = False
            self._handle.write("[\n")

    def write(self, values: dict[str, Any]) -> None:
        cells = [_cell_text(values.get(column["target"])) for column in self._columns]
        if self._format == "csv":
            self._csv.writerow(cells)
        else:
            if self._json_started:
                self._handle.write(",\n")
            self._json_started = True
            self._handle.write(
                json.dumps(
                    {column["source"]: _cell_text(values.get(column["target"])) for column in self._columns},
                    ensure_ascii=False,
                )
            )

    def size_bytes(self) -> int:
        if not self._closed:
            self._handle.flush()
        return os.path.getsize(self._path)

    def close(self) -> None:
        if self._closed:
            return
        if self._format == "json":
            self._handle.write("\n]\n")
        self._handle.flush()
        self._handle.close()
        self._closed = True


def _cell_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value)
    return str(value)


def _product_file_name(job: DataJob) -> str:
    stamp = (job.created_at or datetime.now()).strftime("%Y-%m-%d")
    return f"{job.entity_type}-{stamp}.{job.format}"


# Flat export filter keys (§2.4) routed to ``IssueService.list_issues`` typed
# flat keyword args; ``state_category`` may be a list and is expressed as an
# ``in`` structured-tree node because the flat parameter is scalar.
_UUID_FILTER_KEYS = ("status_id", "assignee_id", "reporter_id", "cycle_id", "milestone_id", "parent_id")
_DATE_FILTER_KEYS = ("due_before", "due_after")


def _coerce_filter_date(value: Any) -> date | None:
    """Parse an export filter date (ISO string / date / datetime) → ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _translate_export_filters(
    filters: dict[str, Any] | None, scope_project_id: uuid.UUID | None
) -> tuple[dict[str, Any], Any]:
    """Map the flat export filter dict onto ``list_issues`` inputs (§3.5/E3).

    ``list_issues`` accepts typed flat keyword args PLUS a structured
    ``filters`` tree (``{field, op}`` / ``{and/or/not}`` nodes). The export
    request uses a flat dict (§2.4), so each key is routed to the right input.
    Passing the raw flat dict straight through as ``filters=`` made
    ``compile_filter_tree`` reject every filtered export (no ``field``/``op``)
    and fail the job as ``storage_error`` — the bug this helper removes.

    Returns ``(flat_kwargs, filter_tree)``.
    """
    flat_kwargs: dict[str, Any] = {}
    if scope_project_id is not None:
        flat_kwargs["project_id"] = scope_project_id
    tree_conditions: list[dict[str, Any]] = []
    for key, value in (filters or {}).items():
        if value is None:
            continue
        if key == "state_category":
            categories = list(value) if isinstance(value, (list, tuple)) else [value]
            categories = [category for category in categories if category]
            if categories:
                tree_conditions.append({"field": "state_category", "op": "in", "value": categories})
        elif key in _UUID_FILTER_KEYS:
            parsed = _uuid_or_none(value)
            if parsed is not None:
                flat_kwargs[key] = parsed
        elif key == "project_id":
            if "project_id" not in flat_kwargs:
                parsed = _uuid_or_none(value)
                if parsed is not None:
                    flat_kwargs["project_id"] = parsed
        elif key in _DATE_FILTER_KEYS:
            parsed_date = _coerce_filter_date(value)
            if parsed_date is not None:
                flat_kwargs[key] = parsed_date
        elif key in ("priority", "q"):
            flat_kwargs[key] = value
    if not tree_conditions:
        filter_tree: Any = None
    elif len(tree_conditions) == 1:
        filter_tree = tree_conditions[0]
    else:
        filter_tree = {"and": tree_conditions}
    return flat_kwargs, filter_tree


async def _iter_issue_rows(worker: Any, job: DataJob, columns: list[dict[str, str]]):
    """Cursor through the issue list query (filters reuse, §3.5/E3)."""
    params = job.params or {}
    issue_service = IssueService(worker._factory)
    viewer = await _load_viewer(worker, job)
    if viewer is None:
        return
    scope_project_id = (
        _uuid_or_none(params.get("project_id")) if params.get("scope") == "project" else None
    )
    flat_kwargs, filter_tree = _translate_export_filters(params.get("filters"), scope_project_id)
    cursor: str | None = None
    while True:
        page = await issue_service.list_issues(
            viewer=viewer,
            workspace_id=job.workspace_id,
            filters=filter_tree,
            sort="created_at",
            order="asc",
            limit=_EXPORT_PAGE_LIMIT,
            cursor=cursor,
            **flat_kwargs,
        )
        items = page.get("data") or ()
        if items:
            issue_ids = [_uuid_or_none(item.get("id")) for item in items]
            labels_by_issue, refs_by_issue = await _issue_extras(
                worker, job.workspace_id, [iid for iid in issue_ids if iid]
            )
            for item in items:
                issue_id = _uuid_or_none(item.get("id"))
                enriched = dict(item)
                enriched["labels"] = labels_by_issue.get(issue_id, [])
                enriched["external_ref"] = refs_by_issue.get(issue_id)
                enriched["status"] = (item.get("status") or {}).get("name")
                enriched["status_category"] = item.get("state_category")
                enriched["assignee"] = (item.get("assignee") or {}).get("name")
                enriched["reporter"] = (item.get("reporter") or {}).get("name")
                enriched["project"] = (item.get("project") or {}).get("key")
                enriched["parent"] = item.get("parent_id")
                yield enriched
        cursor = page.get("next_cursor")
        if not cursor:
            return


async def _iter_project_rows(worker: Any, job: DataJob):
    """Cursor through projects (created_at keyset)."""
    last_created = None
    last_id: uuid.UUID | None = None
    while True:
        async with worker._factory() as session:
            await set_tenant_context(session, job.workspace_id)
            stmt = (
                select(Project)
                .where(Project.workspace_id == job.workspace_id, Project.deleted_at.is_(None))
                .order_by(Project.created_at.asc(), Project.id.asc())
                .limit(_EXPORT_PAGE_LIMIT)
            )
            if last_created is not None and last_id is not None:
                stmt = stmt.where(
                    (Project.created_at > last_created)
                    | ((Project.created_at == last_created) & (Project.id > last_id))
                )
            projects = list((await session.execute(stmt)).scalars().all())
        if not projects:
            return
        for project in projects:
            yield {
                "name": project.name,
                "key": project.key,
                "description": project.description,
                "status": project.status,
                "health": project.health,
                "lead": str(project.lead_member_id) if project.lead_member_id else None,
                "start_date": project.start_date,
                "target_date": project.target_date,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            }
        last_created = projects[-1].created_at
        last_id = projects[-1].id


async def _issue_extras(
    worker: Any, workspace_id: uuid.UUID, issue_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, list[str]], dict[uuid.UUID, str | None]]:
    """Bulk-load label names + external_ref values for one page."""
    labels_by_issue: dict[uuid.UUID, list[str]] = {}
    refs_by_issue: dict[uuid.UUID, str | None] = {}
    if not issue_ids:
        return labels_by_issue, refs_by_issue
    async with worker._factory() as session:
        await set_tenant_context(session, workspace_id)
        label_rows = (
            await session.execute(
                select(IssueLabel.issue_id, Label.name)
                .join(Label, Label.id == IssueLabel.label_id)
                .where(IssueLabel.workspace_id == workspace_id)
                .where(IssueLabel.issue_id.in_(issue_ids))
            )
        ).all()
        for issue_id, name in label_rows:
            labels_by_issue.setdefault(issue_id, []).append(name)
        ref_rows = (
            await session.execute(
                select(IssueCustomFieldValue.issue_id, IssueCustomFieldValue.value_text)
                .where(IssueCustomFieldValue.workspace_id == workspace_id)
                .where(IssueCustomFieldValue.issue_id.in_(issue_ids))
                .where(
                    IssueCustomFieldValue.field_def_id.in_(
                        select(CustomFieldDef.id).where(
                            CustomFieldDef.workspace_id == workspace_id,
                            CustomFieldDef.field_key == "external_ref",
                        )
                    )
                )
            )
        ).all()
        for issue_id, value in ref_rows:
            refs_by_issue[issue_id] = value
    return labels_by_issue, refs_by_issue


async def _load_viewer(worker: Any, job: DataJob) -> Member | None:
    async with worker._factory() as session:
        await set_tenant_context(session, job.workspace_id)
        return await session.get(Member, job.requested_by)
