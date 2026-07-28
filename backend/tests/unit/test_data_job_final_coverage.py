"""Final coverage push: labels/custom-fields import path, service filter
estimates, permission branches, transform edges."""

import hashlib
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select, text

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings
from mesh.data_jobs import transforms as tf
from mesh.data_jobs.schemas import CreateExportJobRequest, CreateImportJobRequest
from mesh.data_jobs.service import DataJobService
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.issue import Issue, IssueStatus
from mesh.db.models.label import CustomFieldDef, IssueCustomFieldValue, IssueLabel, Label
from mesh.db.models.project import Cycle, Milestone, Project, ProjectMember
from mesh.errors import (
    BusinessRuleError,
    ForbiddenError,
    NotFoundError,
    PayloadTooLargeError,
    ValidationError,
)
from mesh.issue.statuses import ensure_scope_seeded

pytestmark = pytest.mark.unit


class StubStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket = "stub-bucket"

    async def download_to_path(self, key, dest_path, *, max_bytes):
        data = self.objects[key]
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return len(data), hashlib.sha256(data).hexdigest()

    async def put_fileobj(self, key, fileobj, *, content_type, content_length):
        self.objects[key] = fileobj.read()

    async def presign_get(self, key, *, expires_in, content_disposition=None, content_type=None):
        return f"https://stub.example/{key}"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/unused",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _service(session_factory, storage, **overrides):
    settings = _settings(**overrides)
    return DataJobService(session_factory, settings, storage)


async def _seed_source(
    session_factory,
    storage,
    workspace,
    member,
    *,
    content=b"Title,Key\na,K1\n",
    upload_status="completed",
    scan_status="skipped",
):
    # per-call salt keeps UNIQUE(workspace_id, content_hash) happy across calls
    content = content + b"\nsalt-" + uuid.uuid4().hex.encode() + b",x\n"
    blob_key = f"ws/{workspace.id}/00/{uuid.uuid4().hex}"
    storage.objects[blob_key] = content
    async with session_factory() as session, session.begin():
        blob = AttachmentBlob(
            workspace_id=workspace.id,
            content_hash=hashlib.sha256(content).hexdigest(),
            storage_provider="s3",
            storage_bucket="stub-bucket",
            storage_key=blob_key,
            file_size=len(content),
            scan_status=scan_status,
            ref_count=1,
        )
        session.add(blob)
        await session.flush()
        attachment = Attachment(
            workspace_id=workspace.id,
            uploader_id=member.id,
            blob_id=blob.id,
            file_name="src.csv",
            file_size=len(content),
            upload_status=upload_status,
        )
        session.add(attachment)
        await session.flush()
        return attachment.id


_MAPPING = {
    "columns": [
        {"source": "Title", "target": "title", "transform": {"type": "direct"}},
    ]
}


class TestLabelsAndCustomFieldsImport:
    LABEL_MAPPING = {
        "columns": [
            {"source": "Title", "target": "title", "transform": {"type": "direct"}},
            {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
            {
                "source": "Labels",
                "target": "labels",
                "transform": {"type": "list_split", "delimiter": ";", "create_missing": True},
            },
            {"source": "Sev", "target": "custom_field_values.severity", "transform": {"type": "direct"}},
        ]
    }

    async def test_labels_created_and_attached_and_external_ref_written(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            # one pre-existing label (hit the known-label branch)
            session.add(Label(workspace_id=workspace.id, project_id=None, name="existing", color="#123456"))
            session.add(
                CustomFieldDef(
                    workspace_id=workspace.id,
                    project_id=None,
                    name="Severity",
                    field_key="severity",
                    type="text",
                )
            )
        from mesh.data_jobs.runner import DataJobWorker

        storage = StubStorage()
        content = b"Title,Key,Labels,Sev\nOne,X-1,existing;brand-new,critical\nTwo,X-2,brand-new,low\n"
        blob_key = f"ws/{workspace.id}/00/{uuid.uuid4().hex}"
        storage.objects[blob_key] = content
        async with session_factory() as session, session.begin():
            blob = AttachmentBlob(
                workspace_id=workspace.id,
                content_hash=hashlib.sha256(content).hexdigest(),
                storage_provider="s3",
                storage_bucket="stub-bucket",
                storage_key=blob_key,
                file_size=len(content),
                scan_status="skipped",
                ref_count=1,
            )
            session.add(blob)
            await session.flush()
            att = Attachment(
                workspace_id=workspace.id,
                uploader_id=member.id,
                blob_id=blob.id,
                file_name="s.csv",
                file_size=len(content),
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            job = DataJob(
                workspace_id=workspace.id,
                kind="import",
                entity_type="issues",
                format="csv",
                mapping=self.LABEL_MAPPING,
                source_attachment_id=att.id,
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        settings = _settings()
        worker = DataJobWorker(
            session_factory,
            settings,
            storage,
            AttachmentService(session_factory, settings, storage),
            worker_id="worker-lbl",
        )
        async with session_factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET status='running', started_at=now() WHERE id=:id"),
                {"id": job_id},
            )
        claim = await worker._claim(job_id, "import-run")
        await worker.run_import(claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed"
            # the new label was created once and attached twice
            brand_new = (await session.execute(select(Label).where(Label.name == "brand-new"))).scalar_one()
            attachments = (
                await session.execute(
                    select(func.count()).select_from(IssueLabel).where(IssueLabel.label_id == brand_new.id)
                )
            ).scalar_one()
            assert attachments == 2
            existing = (await session.execute(select(Label).where(Label.name == "existing"))).scalar_one()
            assert (
                await session.execute(
                    select(func.count()).select_from(IssueLabel).where(IssueLabel.label_id == existing.id)
                )
            ).scalar_one() == 1
            # custom values written: severity ×2 + external_ref ×2
            assert (await session.scalar(select(func.count()).select_from(IssueCustomFieldValue))) == 4
            ext_def = (
                await session.execute(
                    select(CustomFieldDef).where(CustomFieldDef.field_key == "external_ref")
                )
            ).scalar_one()
            ext_values = (
                (
                    await session.execute(
                        select(IssueCustomFieldValue.value_text).where(
                            IssueCustomFieldValue.field_def_id == ext_def.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert set(ext_values) == {"X-1", "X-2"}


class TestServiceFilterEstimates:
    async def _workspace_with_issues(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            status = (
                (await session.execute(select(IssueStatus).where(IssueStatus.workspace_id == workspace.id)))
                .scalars()
                .first()
            )
            project = Project(workspace_id=workspace.id, name="P", key="FILT")
            session.add(project)
            await session.flush()
            session.add(Milestone(workspace_id=workspace.id, project_id=project.id, title="M"))
            session.add(
                Cycle(
                    workspace_id=workspace.id,
                    project_id=project.id,
                    name="C1",
                    starts_at=date(2026, 1, 1),
                    ends_at=date(2026, 1, 14),
                )
            )
            await session.flush()
            milestone = (await session.execute(select(Milestone))).scalar_one()
            cycle = (await session.execute(select(Cycle))).scalar_one()
            for i in range(4):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        project_id=project.id if i < 2 else None,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"i{i}",
                        status_id=status.id,
                        state_category="todo" if i % 2 == 0 else "done",
                        priority="high" if i == 0 else "none",
                        assignee_id=admin.id if i == 1 else None,
                        reporter_id=admin.id,
                        milestone_id=milestone.id if i == 0 else None,
                        cycle_id=cycle.id if i == 1 else None,
                    )
                )
            ids = {
                "status": status.id,
                "project": project.id,
                "milestone": milestone.id,
                "cycle": cycle.id,
                "admin": admin.id,
            }
        return workspace, admin, ids

    async def test_each_filter_key_narrows_estimate(self, session_factory, workspace_factory, member_factory):
        workspace, admin, ids = await self._workspace_with_issues(
            session_factory, workspace_factory, member_factory
        )
        storage = StubStorage()
        service = await _service(session_factory, storage, data_job_export_max_rows=2)
        # 4 issues total > 2 ceiling → unfiltered export is refused
        with pytest.raises(PayloadTooLargeError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
            )
        # each narrowing filter brings the estimate under the ceiling
        for filters in (
            {"state_category": ["todo"]},
            {"priority": "high"},
            {"status_id": str(ids["status"]), "state_category": ["done"]},
            {"assignee_id": str(ids["admin"])},
            {"reporter_id": str(ids["admin"]), "state_category": ["todo"]},
            {"milestone_id": str(ids["milestone"])},
            {"cycle_id": str(ids["cycle"])},
            {"project_id": str(ids["project"])},
        ):
            created = await service.create_export_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="workspace", filters=filters
                ),
            )
            assert created["status"] == "pending", f"filters={filters}"

    async def test_unknown_filter_field_400(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service = await _service(session_factory, StubStorage())
        with pytest.raises(ValidationError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="workspace", filters={"bogus_key": "x"}
                ),
            )


class TestServicePermissionBranches:
    async def test_project_lead_can_import_to_project(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        lead = await member_factory(workspace, role="member")
        outsider = await member_factory(workspace, role="member")
        async with session_factory() as session, session.begin():
            project = Project(workspace_id=workspace.id, name="T", key="IMPT")
            session.add(project)
            await session.flush()
            session.add(
                ProjectMember(
                    workspace_id=workspace.id, project_id=project.id, member_id=lead.id, role="lead"
                )
            )
        storage = StubStorage()
        service = await _service(session_factory, storage)
        source_id = await _seed_source(session_factory, storage, workspace, lead)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=lead,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping=_MAPPING,
                target_project_id=str(project.id),
            ),
        )
        assert created["params"]["target_project_id"] == str(project.id)
        # outsider (no membership) → 403
        source_id2 = await _seed_source(session_factory, storage, workspace, outsider)
        with pytest.raises(ForbiddenError):
            await service.create_import_job(
                workspace_id=workspace.id,
                member=outsider,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(source_id2),
                    mapping=_MAPPING,
                    target_project_id=str(project.id),
                ),
            )

    async def test_source_gates_not_ready(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        storage = StubStorage()
        service = await _service(session_factory, storage)
        # upload not complete
        pending_id = await _seed_source(session_factory, storage, workspace, admin, upload_status="pending")
        with pytest.raises(BusinessRuleError) as exc:
            await service.create_import_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(pending_id),
                    mapping=_MAPPING,
                ),
            )
        assert exc.value.code == "source_not_ready"
        # scan not released
        quarantined_id = await _seed_source(session_factory, storage, workspace, admin, scan_status="pending")
        with pytest.raises(BusinessRuleError) as exc:
            await service.create_import_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(quarantined_id),
                    mapping=_MAPPING,
                ),
            )
        assert exc.value.code == "source_not_ready"
        # missing attachment → 404
        with pytest.raises(NotFoundError):
            await service.create_import_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(uuid.uuid4()),
                    mapping=_MAPPING,
                ),
            )

    async def test_auto_infer_no_columns_400(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        storage = StubStorage()
        service = await _service(session_factory, storage)
        source_id = await _seed_source(
            session_factory,
            storage,
            workspace,
            admin,
            content=b"Xyzzy,Frobnicator\n1,2\n",
        )
        with pytest.raises(ValidationError) as exc:
            await service.create_import_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(source_id),
                    auto_infer=True,
                ),
            )
        assert exc.value.code == "mapping_invalid"

    async def test_list_jobs_admin_can_target_others(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        other = await member_factory(workspace, role="member")
        async with session_factory() as session, session.begin():
            project = Project(workspace_id=workspace.id, name="L", key="LSTJ")
            session.add(project)
            await session.flush()
            session.add(
                ProjectMember(
                    workspace_id=workspace.id, project_id=project.id, member_id=other.id, role="lead"
                )
            )
        storage = StubStorage()
        service = await _service(session_factory, storage)
        source_id = await _seed_source(session_factory, storage, workspace, other)
        await service.create_import_job(
            workspace_id=workspace.id,
            member=other,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping=_MAPPING,
                target_project_id=str(project.id),
            ),
        )
        # admin lists the other member's jobs
        result = await service.list_jobs(workspace_id=workspace.id, member=admin, requested_by=other.id)
        assert len(result["data"]) == 1
        # non-admin member cannot target someone else
        with pytest.raises(ForbiddenError):
            await service.list_jobs(workspace_id=workspace.id, member=other, requested_by=admin.id)
        # non-admin sees only their own
        own = await service.list_jobs(workspace_id=workspace.id, member=other)
        assert len(own["data"]) == 1

    async def test_download_via_service_signs(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        storage = StubStorage()
        service = await _service(session_factory, storage)
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
        )
        job_id = uuid.UUID(created["id"])
        storage.objects["k1"] = b"x"
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace.id)
            attachment = await AttachmentService(
                session_factory, _settings(), storage
            ).register_server_attachment(
                session,
                workspace_id=workspace.id,
                uploader_member_id=admin.id,
                file_name="e.csv",
                mime_type="text/csv",
                extension="csv",
                storage_key="k1",
                file_size=1,
                content_hash=hashlib.sha256(b"x").hexdigest(),
            )
            await session.execute(
                text("UPDATE data_jobs SET result_attachment_id=:a, status='completed' WHERE id=:id"),
                {"a": attachment.id, "id": job_id},
            )
        result = await service.download_job(workspace_id=workspace.id, member=admin, job_id=job_id)
        assert result["data"]["url"].startswith("https://stub.example/k1")


class TestTransformEdges:
    def test_value_map_case_insensitive_and_status_no_default(self):
        context = tf.TransformContext(entity_type="issues")  # no statuses at all
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {
                    "source": "S",
                    "target": "status",
                    "transform": {"type": "status_by_name", "fallback": "default"},
                },
                {
                    "source": "P",
                    "target": "priority",
                    "transform": {"type": "value_map", "map": {"HIGH": "high"}},
                },
            ]
        }
        # case-insensitive map match
        values, errors, _w = tf.transform_row(1, {"T": "x", "P": "high"}, mapping, context)
        assert values["priority"] == "high"
        # status with no default in scope → error
        _v, errors, _w = tf.transform_row(1, {"T": "x", "S": "Whatever"}, mapping, context)
        assert any(e.code == "unknown_status" for e in errors)
        # value_map with no entry and no default → invalid_value
        mapping2 = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {
                    "source": "P",
                    "target": "priority",
                    "transform": {"type": "value_map", "map": {"X": "high"}},
                },
            ]
        }
        _v, errors, _w = tf.transform_row(1, {"T": "x", "P": "zzz"}, mapping2, context)
        assert [e.code for e in errors] == ["invalid_value"]

    def test_project_targets_edges(self):
        context = tf.TransformContext(entity_type="projects")
        mapping = {
            "columns": [
                {"source": "N", "target": "name", "transform": {"type": "direct"}},
                {"source": "K", "target": "key", "transform": {"type": "direct"}},
                {"source": "D", "target": "description", "transform": {"type": "direct"}},
                {"source": "S", "target": "start_date", "transform": {"type": "date_parse"}},
            ]
        }
        values, errors, _w = tf.transform_row(
            1, {"N": "P", "K": "ABC", "D": "desc", "S": "2026-01-01"}, mapping, context
        )
        assert errors == []
        assert values["description"] == "desc"
        assert values["start_date"] == date(2026, 1, 1)
        # invalid date on a project target
        _v, errors, _w = tf.transform_row(2, {"N": "P", "K": "ABC", "D": "", "S": "nope"}, mapping, context)
        assert [e.code for e in errors] == ["invalid_date"]

    def test_labels_default_create_missing_from_options(self):
        context = tf.TransformContext(entity_type="issues", options={"create_missing_labels": False})
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                # no explicit create_missing → falls back to options (false)
                {"source": "L", "target": "labels", "transform": {"type": "list_split"}},
            ]
        }
        _v, errors, _w = tf.transform_row(1, {"T": "x", "L": "ghost"}, mapping, context)
        assert [e.code for e in errors] == ["unknown_label"]

    def test_list_split_default_delimiter(self):
        context = tf.TransformContext(entity_type="issues")
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {"source": "L", "target": "labels", "transform": {"type": "list_split"}},
            ]
        }
        values, errors, _w = tf.transform_row(1, {"T": "x", "L": "a,b,c"}, mapping, context)
        assert errors == [] and values["labels"] == ["a", "b", "c"]

    def test_member_by_email_default_on_missing_null(self):
        context = tf.TransformContext(entity_type="issues")
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {"source": "A", "target": "assignee", "transform": {"type": "member_by_email"}},
            ]
        }
        values, errors, _w = tf.transform_row(1, {"T": "x", "A": "nobody@mesh.example"}, mapping, context)
        assert errors == [] and values["assignee_id"] is None

    def test_direct_assignee_invalid_uuid(self):
        context = tf.TransformContext(entity_type="issues")
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {"source": "A", "target": "assignee", "transform": {"type": "direct"}},
            ]
        }
        _v, errors, _w = tf.transform_row(1, {"T": "x", "A": "not-a-uuid"}, mapping, context)
        assert [e.code for e in errors] == ["unknown_member"]

    def test_project_scope_status_mapping(self):
        # project import: lead unknown with on_missing null → None
        context = tf.TransformContext(entity_type="projects")
        mapping = {
            "columns": [
                {"source": "N", "target": "name", "transform": {"type": "direct"}},
                {"source": "K", "target": "key", "transform": {"type": "direct"}},
                {
                    "source": "L",
                    "target": "lead",
                    "transform": {"type": "member_by_email", "on_missing": "null"},
                },
            ]
        }
        values, errors, _w = tf.transform_row(
            1, {"N": "P", "K": "KK", "L": "ghost@mesh.example"}, mapping, context
        )
        assert errors == [] and values["lead_member_id"] is None
