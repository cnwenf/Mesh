"""DataJobService tests (import-export.md §3.0–§3.6 API-level behavior)."""

import hashlib
import uuid

import pytest
from sqlalchemy import func, select

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings
from mesh.data_jobs.schemas import CreateExportJobRequest, CreateImportJobRequest
from mesh.data_jobs.service import DataJobService
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.issue import Issue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
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
        return f"https://stub.example/{key}?sig={expires_in}"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/unused",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _make_service(session_factory, **overrides):
    storage = StubStorage()
    settings = _settings(**overrides)
    return (
        DataJobService(
            session_factory,
            settings,
            storage,
        ),
        AttachmentService(session_factory, settings, storage),
        storage,
    )


async def _seed_ready_source(
    session_factory, storage, workspace, member, *, content: bytes = b"Title,Key\na,K1\n", uploader=None
):
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
        attachment = Attachment(
            workspace_id=workspace.id,
            uploader_id=(uploader or member).id,
            blob_id=blob.id,
            file_name="issues.csv",
            file_size=len(content),
            upload_status="completed",
        )
        session.add(attachment)
        await session.flush()
        return attachment.id


_MAPPING = {
    "columns": [
        {"source": "Title", "target": "title", "transform": {"type": "direct"}},
        {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
    ]
}


class TestCreateImportJob:
    async def test_creates_job_and_outbox_event(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        source_id = await _seed_ready_source(session_factory, storage, workspace, member)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=member,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                entity_type="issues",
                format="csv",
                source_attachment_id=str(source_id),
                mapping=_MAPPING,
            ),
        )
        assert created["status"] == "pending"
        assert created["kind"] == "import"
        async with session_factory() as session:
            event = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "data_job.enqueue"))
            ).scalar_one()
            assert event.payload["action"] == "created"
            assert event.payload["data_job_id"] == created["id"]

    async def test_idempotency_key_returns_first_result(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        source_id = await _seed_ready_source(session_factory, storage, workspace, member)
        body = CreateImportJobRequest(
            workspace_id=str(workspace.id),
            format="csv",
            source_attachment_id=str(source_id),
            mapping=_MAPPING,
        )
        first = await service.create_import_job(
            workspace_id=workspace.id, member=member, body=body, idempotency_key="idem-1"
        )
        second = await service.create_import_job(
            workspace_id=workspace.id, member=member, body=body, idempotency_key="idem-1"
        )
        assert first["id"] == second["id"]
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(DataJob))) == 1

    async def test_rejects_other_members_attachment(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        other = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        # M-2: uploaded by `other` — even an admin cannot import from it.
        source_id = await _seed_ready_source(
            session_factory, storage, workspace, member=other, uploader=other
        )
        with pytest.raises(ForbiddenError):
            await service.create_import_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(source_id),
                    mapping=_MAPPING,
                ),
            )

    async def test_member_needs_project_write_for_import(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="member")
        service, _att, storage = await _make_service(session_factory)
        source_id = await _seed_ready_source(session_factory, storage, workspace, member)
        with pytest.raises(ForbiddenError):
            await service.create_import_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(source_id),
                    mapping=_MAPPING,
                ),
            )

    async def test_invalid_mapping_400(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        source_id = await _seed_ready_source(session_factory, storage, workspace, member)
        with pytest.raises(ValidationError) as exc:
            await service.create_import_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateImportJobRequest(
                    workspace_id=str(workspace.id),
                    format="csv",
                    source_attachment_id=str(source_id),
                    mapping={
                        "columns": [{"source": "T", "target": "bogus", "transform": {"type": "direct"}}]
                    },
                ),
            )
        assert exc.value.code == "mapping_invalid"

    async def test_auto_infer_drafts_mapping(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        source_id = await _seed_ready_source(
            session_factory,
            storage,
            workspace,
            member,
            content=b"Title,State,Key\na,Open,K1\n",
        )
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=member,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                auto_infer=True,
            ),
        )
        targets = {c["target"] for c in created["mapping"]["columns"]}
        assert "title" in targets


class TestTwoPhaseContract:
    async def _create(self, session_factory, workspace, member, service, storage):
        source_id = await _seed_ready_source(session_factory, storage, workspace, member)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=member,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping=_MAPPING,
            ),
        )
        return uuid.UUID(created["id"])

    async def test_run_without_validate_422(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        job_id = await self._create(session_factory, workspace, member, service, storage)
        with pytest.raises(BusinessRuleError) as exc:
            await service.run_import(workspace_id=workspace.id, member=member, job_id=job_id)
        assert exc.value.code == "validation_required"

    async def test_validate_transitions_and_run_conflicts(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        job_id = await self._create(session_factory, workspace, member, service, storage)
        result = await service.validate_import(workspace_id=workspace.id, member=member, job_id=job_id)
        assert result["status"] == "validating"
        # double validate while validating → conflict
        with pytest.raises(ConflictError):
            await service.validate_import(workspace_id=workspace.id, member=member, job_id=job_id)

    async def test_run_requires_pending_and_validated(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        job_id = await self._create(session_factory, workspace, member, service, storage)
        # mark validated + running by the worker, then run again → conflict
        async with session_factory() as session, session.begin():
            await session.execute(
                __import__("sqlalchemy").text(
                    "UPDATE data_jobs SET status='running', started_at=now(), "
                    'params = params || \'{"validated_at": "2026-01-01T00:00:00Z"}\' '
                    "WHERE id=:id"
                ),
                {"id": job_id},
            )
        with pytest.raises(ConflictError):
            await service.run_import(workspace_id=workspace.id, member=member, job_id=job_id)

    async def test_run_detects_source_change(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        job_id = await self._create(session_factory, workspace, member, service, storage)
        async with session_factory() as session, session.begin():
            await session.execute(
                __import__("sqlalchemy").text(
                    "UPDATE data_jobs SET params = params || "
                    '\'{"validated_at": "2026-01-01T00:00:00Z"}\', '
                    "source_content_hash = :h WHERE id=:id"
                ),
                {"h": "0" * 64, "id": job_id},
            )
        with pytest.raises(BusinessRuleError) as exc:
            await service.run_import(workspace_id=workspace.id, member=member, job_id=job_id)
        assert exc.value.code == "source_changed"

    async def test_owner_gate_403_for_stranger(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        stranger = await member_factory(workspace, role="member")
        admin = await member_factory(workspace, role="admin")
        service, _att, storage = await _make_service(session_factory)
        # admin creates (member role cannot workspace-import)
        source_id = await _seed_ready_source(session_factory, storage, workspace, admin)
        created = await service.create_import_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateImportJobRequest(
                workspace_id=str(workspace.id),
                format="csv",
                source_attachment_id=str(source_id),
                mapping=_MAPPING,
            ),
        )
        job_id = uuid.UUID(created["id"])
        # §3.12 / §5.4: a same-tenant non-owner/admin is forbidden (403), not
        # invisible — only a missing / cross-tenant job is 404.
        with pytest.raises(ForbiddenError):
            await service.get_job(workspace_id=workspace.id, member=stranger, job_id=job_id)
        # the owner and any admin can see it
        assert (await service.get_job(workspace_id=workspace.id, member=admin, job_id=job_id))["id"] == str(
            job_id
        )


class TestCreateExportJob:
    async def test_workspace_scope_requires_manager(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="member")
        service, _att, _storage = await _make_service(session_factory)
        with pytest.raises(ForbiddenError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
            )

    async def test_creates_export_pending_with_outbox(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service, _att, _storage = await _make_service(session_factory)
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace", format="csv"),
        )
        assert created["status"] == "pending" and created["kind"] == "export"
        async with session_factory() as session:
            event = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "data_job.enqueue"))
            ).scalar_one()
            assert event.payload["action"] == "export"

    async def test_filter_too_complex_400(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service, _att, _storage = await _make_service(session_factory)
        too_many = {f"key{i}": "x" for i in range(21)}
        with pytest.raises(ValidationError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="workspace", filters=too_many
                ),
            )

    async def test_export_too_large_413_precheck(self, session_factory, workspace_factory, member_factory):
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
            for i in range(6):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"issue {i}",
                        status_id=status_id,
                        state_category="todo",
                    )
                )
        service, _att, _storage = await _make_service(session_factory, data_job_export_max_rows=5)
        with pytest.raises(PayloadTooLargeError) as exc:
            await service.create_export_job(
                workspace_id=workspace.id,
                member=admin,
                body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
            )
        assert exc.value.code == "export_too_large"

    async def test_filters_reduce_estimate(self, session_factory, workspace_factory, member_factory):
        """Review MEDIUM-5: a filtered export is not killed by the unfiltered total."""
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
            for i in range(6):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"issue {i}",
                        status_id=status_id,
                        state_category="todo" if i < 2 else "done",
                    )
                )
        service, _att, _storage = await _make_service(session_factory, data_job_export_max_rows=5)
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(
                workspace_id=str(workspace.id),
                scope="workspace",
                filters={"state_category": ["todo"]},
            ),
        )
        assert created["status"] == "pending"  # 2 todo rows < ceiling of 5


class TestProjectExportPermission:
    async def test_private_project_requires_membership(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="member")
        async with session_factory() as session, session.begin():
            project = Project(workspace_id=workspace.id, name="Secret", key="SEC", visibility="private")
            session.add(project)
            await session.flush()
            project_id = project.id
        service, _att, _storage = await _make_service(session_factory)
        with pytest.raises(ForbiddenError):
            await service.create_export_job(
                workspace_id=workspace.id,
                member=member,
                body=CreateExportJobRequest(
                    workspace_id=str(workspace.id), scope="project", project_id=str(project_id)
                ),
            )


class TestDownload:
    async def test_download_without_product_404(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service, _att, _storage = await _make_service(session_factory)
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
        )
        with pytest.raises(NotFoundError):
            await service.download_job(
                workspace_id=workspace.id, member=admin, job_id=uuid.UUID(created["id"])
            )

    async def test_download_signs_product_url(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        service, attachment_service, storage = await _make_service(session_factory)
        created = await service.create_export_job(
            workspace_id=workspace.id,
            member=admin,
            body=CreateExportJobRequest(workspace_id=str(workspace.id), scope="workspace"),
        )
        job_id = uuid.UUID(created["id"])
        # simulate worker product registration
        storage.objects["product-key"] = b"a,b\n1,2\n"
        async with session_factory() as session, session.begin():
            from mesh.db.tenant import set_tenant_context

            await set_tenant_context(session, workspace.id)
            attachment = await attachment_service.register_server_attachment(
                session,
                workspace_id=workspace.id,
                uploader_member_id=admin.id,
                file_name="export.csv",
                mime_type="text/csv",
                extension="csv",
                storage_key="product-key",
                file_size=8,
                content_hash=hashlib.sha256(b"a,b\n1,2\n").hexdigest(),
            )
            from sqlalchemy import text as sql_text

            await session.execute(
                sql_text("UPDATE data_jobs SET result_attachment_id=:a, status='completed' WHERE id=:id"),
                {"a": attachment.id, "id": job_id},
            )
        result = await service.download_job(workspace_id=workspace.id, member=admin, job_id=job_id)
        assert result["data"]["url"].startswith("https://stub.example/product-key")
        assert result["data"]["file_name"] == "export.csv"
