"""Edge-branch tests to complete the ≥90% gate: view-scope export, download
scan gates, project-scope export estimate, worker failure paths."""

import hashlib
import uuid

import pytest
from sqlalchemy import select, text

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings
from mesh.data_jobs.runner import DataJobWorker
from mesh.data_jobs.schemas import CreateExportJobRequest
from mesh.data_jobs.service import DataJobService
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.project import Project
from mesh.db.tenant import set_tenant_context
from mesh.errors import BusinessRuleError, ForbiddenError, NotFoundError
from mesh.issue.statuses import ensure_scope_seeded

pytestmark = pytest.mark.unit


class StubStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket = "stub-bucket"

    async def download_to_path(self, key, dest_path, *, max_bytes):
        data = self.objects[key]  # KeyError → internal_error path in tests
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


async def _worker(session_factory, storage, **overrides):
    settings = _settings(**overrides)
    return DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-edge",
    )


class TestExportScopes:
    async def test_view_scope_allowed_for_member_and_project_estimate(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="member")
        admin = await member_factory(workspace, role="admin")
        async with session_factory() as session, session.begin():
            project = Project(workspace_id=workspace.id, name="V", key="VIEW", visibility="public")
            session.add(project)
            await session.flush()
            project_id = project.id
        storage = StubStorage()
        service = DataJobService(session_factory, _settings(), storage)
        # view scope as a plain member — rows stay under their visibility
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=member,
            body=CreateExportJobRequest(
                workspace_id=str(workspace.id), scope="view", filters={"state_category": ["todo"]}
            ),
        )
        assert created["params"]["scope"] == "view"
        # project scope as member (public project) → project estimate branch
        created2 = await service.create_export_job(
            workspace_id=workspace.id,
            member=member,
            body=CreateExportJobRequest(
                workspace_id=str(workspace.id),
                scope="project",
                project_id=str(project_id),
                entity_type="projects",
            ),
        )
        assert created2["status"] == "pending"
        # projects export estimate with project_id (admin)
        created3 = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(
                workspace_id=str(workspace.id), scope="workspace", entity_type="projects"
            ),
        )
        assert created3["status"] == "pending"

    async def test_private_project_export_visibility(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="member")
        async with session_factory() as session, session.begin():
            project = Project(workspace_id=workspace.id, name="S", key="SECR", visibility="private")
            session.add(project)
            await session.flush()
            project_id = project.id
        service = DataJobService(session_factory, _settings(), StubStorage())
        with pytest.raises(ForbiddenError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="project", project_id=str(project_id)
                ),
            )
        # missing project → 404
        with pytest.raises(NotFoundError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="project", project_id=str(uuid.uuid4())
                ),
            )


class TestDownloadGates:
    async def _job_with_product(
        self, session_factory, workspace, member, *, scan_status="skipped", attachment_missing=False
    ):
        storage = StubStorage()
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, workspace.id)
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="completed",
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            if not attachment_missing:
                blob = AttachmentBlob(
                    workspace_id=workspace.id,
                    content_hash=hashlib.sha256(b"z").hexdigest(),
                    storage_provider="s3",
                    storage_bucket="stub-bucket",
                    storage_key="kz",
                    file_size=1,
                    scan_status=scan_status,
                    ref_count=1,
                )
                session.add(blob)
                await session.flush()
                att = Attachment(
                    workspace_id=workspace.id,
                    uploader_id=member.id,
                    blob_id=blob.id,
                    file_name="e.csv",
                    file_size=1,
                    upload_status="completed",
                )
                session.add(att)
                await session.flush()
                await session.execute(
                    text("UPDATE data_jobs SET result_attachment_id=:a WHERE id=:id"),
                    {"a": att.id, "id": job.id},
                )
            return job.id, storage

    async def test_scan_pending_blocks_download(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        job_id, storage = await self._job_with_product(
            session_factory, workspace, admin, scan_status="pending"
        )
        service = DataJobService(session_factory, _settings(), storage)
        with pytest.raises(ForbiddenError) as exc:
            await service.download_job(workspace_id=workspace.id, member=admin, job_id=job_id)
        assert exc.value.code == "scan_pending"

    async def test_deleted_product_404(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        job_id, storage = await self._job_with_product(session_factory, workspace, admin)
        async with session_factory() as session, session.begin():
            await session.execute(text("UPDATE attachments SET deleted_at=now()"))
        service = DataJobService(session_factory, _settings(), storage)
        with pytest.raises(NotFoundError):
            await service.download_job(workspace_id=workspace.id, member=admin, job_id=job_id)

    async def test_missing_job_404(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service = DataJobService(session_factory, _settings(), StubStorage())
        with pytest.raises(NotFoundError):
            await service.get_job(workspace_id=workspace.id, member=admin, job_id=uuid.uuid4())


class TestWorkerFailurePaths:
    async def test_process_internal_error_fails_job(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            blob = AttachmentBlob(
                workspace_id=workspace.id,
                content_hash="h" * 64,
                storage_provider="s3",
                storage_bucket="stub-bucket",
                storage_key="missing-key",
                file_size=5,
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
                file_size=5,
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            job = DataJob(
                workspace_id=workspace.id,
                kind="import",
                entity_type="issues",
                format="csv",
                status="running",
                mapping={"columns": [{"source": "T", "target": "title", "transform": {"type": "direct"}}]},
                source_attachment_id=att.id,
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        storage = StubStorage()  # 'missing-key' NOT in objects → KeyError
        worker = await _worker(session_factory, storage)
        async with session_factory() as session, session.begin():
            await session.execute(text("UPDATE data_jobs SET started_at=now() WHERE id=:id"), {"id": job_id})
        await worker.process(job_id, "import-run")
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "failed"
            assert job.failure_reason == "internal_error"

    async def test_validate_unparseable_source_fails(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        garbage = b"\xff\xfe\x00garbage"
        async with session_factory() as session, session.begin():
            blob = AttachmentBlob(
                workspace_id=workspace.id,
                content_hash=hashlib.sha256(garbage).hexdigest(),
                storage_provider="s3",
                storage_bucket="stub-bucket",
                storage_key="kg",
                file_size=len(garbage),
                scan_status="skipped",
                ref_count=1,
            )
            session.add(blob)
            await session.flush()
            att = Attachment(
                workspace_id=workspace.id,
                uploader_id=member.id,
                blob_id=blob.id,
                file_name="g.csv",
                file_size=len(garbage),
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            job = DataJob(
                workspace_id=workspace.id,
                kind="import",
                entity_type="issues",
                format="csv",
                status="validating",
                mapping={"columns": [{"source": "T", "target": "title", "transform": {"type": "direct"}}]},
                source_attachment_id=att.id,
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        storage = StubStorage()
        storage.objects["kg"] = garbage
        worker = await _worker(session_factory, storage)
        claim = await worker._claim(job_id, "import-validate")
        await worker.run_validate(claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "failed"
            assert job.failure_reason == "source_unparseable"

    async def test_validate_then_run_resume_flow_via_process(
        self, session_factory, workspace_factory, member_factory
    ):
        """process('resume') on a validating job re-runs the dry-run."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        content = b"Title\nhello\n"
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            blob = AttachmentBlob(
                workspace_id=workspace.id,
                content_hash=hashlib.sha256(content).hexdigest(),
                storage_provider="s3",
                storage_bucket="stub-bucket",
                storage_key="kr",
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
                file_name="r.csv",
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
                status="validating",
                lease_owner=None,
                lease_seq=1,  # resumed claim
                mapping={
                    "columns": [{"source": "Title", "target": "title", "transform": {"type": "direct"}}]
                },
                source_attachment_id=att.id,
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        storage = StubStorage()
        storage.objects["kr"] = content
        worker = await _worker(session_factory, storage)
        await worker.process(job_id, "resume")
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "pending"  # dry-run completed via resume path
            assert job.total_rows == 1


class TestIdempotencyRaceAndGuards:
    async def test_import_insert_integrity_error_with_idem_key_reraises(
        self, session_factory, workspace_factory, member_factory
    ):
        """IntegrityError on the create flush with an Idempotency-Key: the
        winner re-select finds nothing → the original error re-raises
        (covers the race-backstop branch deterministically via a composite
        FK violation)."""
        from sqlalchemy.exc import IntegrityError

        from mesh.data_jobs.schemas import CreateImportJobRequest

        workspace_a = await workspace_factory(name="A")
        workspace_b = await workspace_factory(name="B")
        member_a = await member_factory(workspace_a, role="admin")
        member_b = await member_factory(workspace_b, role="admin")
        storage = StubStorage()
        # source attachment in workspace B — cross-tenant composite FK fails
        content = b"Title,Key\na,K1\n"
        blob_key = f"ws/{workspace_b.id}/00/{uuid.uuid4().hex}"
        storage.objects[blob_key] = content
        async with session_factory() as session, session.begin():
            blob = AttachmentBlob(
                workspace_id=workspace_b.id,
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
                workspace_id=workspace_b.id,
                uploader_id=member_b.id,
                blob_id=blob.id,
                file_name="s.csv",
                file_size=len(content),
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            foreign_source_id = att.id
        service = DataJobService(session_factory, _settings(), storage)
        # uploader gate is workspace-scoped: the attachment is invisible in
        # workspace A → NotFoundError before the INSERT; instead bypass the
        # gate with a member of B creating into B's job table while the
        # source row vanishes mid-flight is impractical — so drive the
        # branch directly: source exists and is owned, but the composite FK
        # is broken by deleting the attachment's workspace row is cascaded…
        # Deterministic path: member_b creates with a source id that belongs
        # to workspace A (FK mismatch), owned-check fails first — so use the
        # service-internal path with a patched ownership check.

        async def _skip_ownership(session, *, member, attachment_id):
            return await session.get(Attachment, attachment_id)

        service._load_ready_source = _skip_ownership
        # re-point: use member_a's workspace with B's attachment → FK violation
        with pytest.raises(IntegrityError):
            await service.create_import_job(
                workspace_id=workspace_a.id,
                member=member_a,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace_a.id),
                    format="csv",
                    source_attachment_id=str(foreign_source_id),
                    mapping={
                        "columns": [{"source": "T", "target": "title", "transform": {"type": "direct"}}]
                    },
                ),
                idempotency_key="race-key-1",
            )

    async def test_validate_and_run_on_export_job_conflict(
        self, session_factory, workspace_factory, member_factory
    ):
        from mesh.data_jobs.schemas import CreateExportJobRequest
        from mesh.errors import ConflictError

        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service = DataJobService(session_factory, _settings(), StubStorage())
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
        )
        job_id = uuid.UUID(created["id"])
        with pytest.raises(ConflictError):
            await service.validate_import(workspace_id=workspace.id, member=admin, job_id=job_id)
        with pytest.raises(ConflictError):
            await service.run_import(workspace_id=workspace.id, member=admin, job_id=job_id)

    async def test_run_succeeds_when_source_hash_matches(
        self, session_factory, workspace_factory, member_factory
    ):
        """Frozen hash matches the current source → run transitions."""
        from mesh.data_jobs.schemas import CreateImportJobRequest

        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        storage = StubStorage()
        content = b"Title,Key\na,K1\n"
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
                uploader_id=admin.id,
                blob_id=blob.id,
                file_name="s.csv",
                file_size=len(content),
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            source_id = att.id
        service = DataJobService(session_factory, _settings(), storage)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping={
                    "columns": [{"source": "Title", "target": "title", "transform": {"type": "direct"}}]
                },
            ),
        )
        job_id = uuid.UUID(created["id"])
        # freeze the correct hash + validated_at (simulating dry-run done)
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET source_content_hash=:h, "
                    'params = params || \'{"validated_at": "2026-01-01T00:00:00Z"}\' '
                    "WHERE id=:id"
                ),
                {"h": hashlib.sha256(content).hexdigest(), "id": job_id},
            )
        result = await service.run_import(workspace_id=workspace.id, member=admin, job_id=job_id)
        assert result["status"] == "running"

    async def test_run_with_soft_deleted_source_not_ready(
        self, session_factory, workspace_factory, member_factory
    ):
        from mesh.data_jobs.schemas import CreateImportJobRequest

        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        storage = StubStorage()
        content = b"Title,Key\na,K1\n"
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
                uploader_id=admin.id,
                blob_id=blob.id,
                file_name="s.csv",
                file_size=len(content),
                upload_status="completed",
            )
            session.add(att)
            await session.flush()
            source_id = att.id
        service = DataJobService(session_factory, _settings(), storage)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping={
                    "columns": [{"source": "Title", "target": "title", "transform": {"type": "direct"}}]
                },
            ),
        )
        job_id = uuid.UUID(created["id"])
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET params = params || "
                    '\'{"validated_at": "2026-01-01T00:00:00Z"}\' WHERE id=:id'
                ),
                {"id": job_id},
            )
            await session.execute(text("UPDATE attachments SET deleted_at=now()"))
        with pytest.raises(BusinessRuleError) as exc:
            await service.run_import(workspace_id=workspace.id, member=admin, job_id=job_id)
        assert exc.value.code == "source_not_ready"


class TestExportPageCeiling:
    async def test_mid_stream_ceiling_fails_job(self, session_factory, workspace_factory, member_factory):
        """Row ceiling checked every page (200 rows) — a 250-row export with
        a 210-row ceiling fails mid-stream."""
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            status_id = (
                await session.execute(
                    select(__import__("mesh.db.models.issue", fromlist=["IssueStatus"]).IssueStatus.id).limit(
                        1
                    )
                )
            ).scalar_one()
            from mesh.db.models.issue import Issue

            for i in range(250):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"r{i}",
                        status_id=status_id,
                        state_category="todo",
                    )
                )
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="pending",
                mapping={},
                params={"scope": "workspace"},
                requested_by=admin.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        storage = StubStorage()
        worker = await _worker(session_factory, storage, data_job_export_max_rows=210)
        claim = await worker._claim(job_id, "export")
        from mesh.data_jobs.exporter import run_export_pipeline

        await run_export_pipeline(worker, claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "failed"
            assert job.failure_reason == "export_too_large"


class TestChannelBadSubject:
    async def test_non_uuid_subject_denied(self, session_factory, workspace_factory, member_factory):
        from mesh.data_jobs.channels import make_data_job_channel_checker
        from mesh.realtime.auth import Principal

        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        async with session_factory() as session, session.begin():
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="pending",
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        checker = make_data_job_channel_checker(session_factory)
        principal = Principal(subject="not-a-uuid", workspace_ids=frozenset({workspace.id}))
        assert await checker(principal, f"data_job:{job_id}") is False
        # job in a workspace the principal cannot access → False
        other_ws = await workspace_factory(name="Other")
        from mesh.db.models.member import Member
        from mesh.db.models.user import User

        async with session_factory() as session, session.begin():
            user = User(email="foreign@mesh.example", display_name="F", password_hash="x")
            session.add(user)
            await session.flush()
            foreign = Member(
                workspace_id=other_ws.id, member_type="human", user_id=user.id, role="admin", status="active"
            )
            session.add(foreign)
            await session.flush()
            foreign_user = str(user.id)
        principal2 = Principal(subject=foreign_user, workspace_ids=frozenset({other_ws.id}))
        assert await checker(principal2, f"data_job:{job_id}") is False
