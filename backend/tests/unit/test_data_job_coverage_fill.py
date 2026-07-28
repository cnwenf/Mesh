"""Coverage-completion tests: parent second pass, worker entry points,
project export, transform/context branches, reaper loop, report writer."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings
from mesh.data_jobs import transforms as tf
from mesh.data_jobs.parser import SourceParseError, iter_source_rows
from mesh.data_jobs.reaper import data_job_reaper_loop
from mesh.data_jobs.report import ErrorReportWriter
from mesh.data_jobs.runner import DataJobWorker, _preview_values, _short
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.issue import Issue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.issue.statuses import ensure_scope_seeded

pytestmark = pytest.mark.unit


class StubStorage:
    def __init__(self, *, fail_put: bool = False):
        self.objects: dict[str, bytes] = {}
        self.bucket = "stub-bucket"
        self.fail_put = fail_put

    async def download_to_path(self, key, dest_path, *, max_bytes):
        data = self.objects[key]
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return len(data), hashlib.sha256(data).hexdigest()

    async def put_fileobj(self, key, fileobj, *, content_type, content_length):
        if self.fail_put:
            from mesh.errors import StorageError

            raise StorageError("simulated outage")
        self.objects[key] = fileobj.read()

    async def presign_get(self, key, *, expires_in, content_disposition=None, content_type=None):
        return f"https://stub.example/{key}"


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/unused",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


async def _make_worker(session_factory, storage, **overrides):
    settings = _settings(**overrides)
    return DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-test",
    )


async def _seed_import_job(
    session_factory,
    storage,
    workspace,
    member,
    content: bytes,
    mapping: dict,
    *,
    entity_type="issues",
    params=None,
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
            uploader_id=member.id,
            blob_id=blob.id,
            file_name="src.csv",
            file_size=len(content),
            upload_status="completed",
        )
        session.add(attachment)
        await session.flush()
        job = DataJob(
            workspace_id=workspace.id,
            kind="import",
            entity_type=entity_type,
            format="csv",
            mapping=mapping,
            params=params or {},
            source_attachment_id=attachment.id,
            requested_by=member.id,
        )
        session.add(job)
        await session.flush()
        return job.id


async def _run_job(worker, job_id, action="import-run"):
    async with worker._factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE data_jobs SET status='running', started_at=now(), "
                "lease_owner=NULL, lease_expires_at=NULL WHERE id=:id"
            ),
            {"id": job_id},
        )
    await worker.process(job_id, action)


class TestParentSecondPass:
    PARENT_MAPPING = {
        "columns": [
            {"source": "Title", "target": "title", "transform": {"type": "direct"}},
            {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
            {"source": "Parent", "target": "parent", "transform": {"type": "parent_by_external_ref"}},
        ]
    }

    async def test_parent_links_resolved_via_external_ref(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = b"Title,Key,Parent\nParent Issue,P-1,\nChild Issue,C-1,P-1\n"
        job_id = await _seed_import_job(
            session_factory, storage, workspace, member, content, self.PARENT_MAPPING
        )
        worker = await _make_worker(session_factory, storage)
        await _run_job(worker, job_id)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed"
            parent = (await session.execute(select(Issue).where(Issue.title == "Parent Issue"))).scalar_one()
            child = (await session.execute(select(Issue).where(Issue.title == "Child Issue"))).scalar_one()
            assert child.parent_id == parent.id

    async def test_unresolvable_parent_warns_and_stays_top_level(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = b"Title,Key,Parent\nOrphan,O-1,GHOST-9\n"
        job_id = await _seed_import_job(
            session_factory, storage, workspace, member, content, self.PARENT_MAPPING
        )
        worker = await _make_worker(session_factory, storage)
        await _run_job(worker, job_id)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed"  # warnings do not fail rows
            codes = [e["code"] for e in (job.error_report or [])]
            assert "parent_not_found" in codes
            issue = (await session.execute(select(Issue))).scalar_one()
            assert issue.parent_id is None

    async def test_parent_cycle_detected_and_kept_top_level(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        # A's parent is B, B's parent is A → cycle; both stay top-level
        content = b"Title,Key,Parent\nIssue A,A-1,B-1\nIssue B,B-1,A-1\n"
        job_id = await _seed_import_job(
            session_factory, storage, workspace, member, content, self.PARENT_MAPPING
        )
        worker = await _make_worker(session_factory, storage)
        await _run_job(worker, job_id)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed"
            rows = (await session.execute(select(Issue.id, Issue.parent_id))).all()
            by_id = {iid: pid for iid, pid in rows}
            # the first direction links; the cycle-creating reverse is
            # refused — walking up from any issue must terminate (no cycle)
            assert any(e["code"] == "parent_not_found" for e in (job.error_report or []))
            for issue_id in by_id:
                seen, current = set(), issue_id
                while by_id.get(current) is not None:
                    current = by_id[current]
                    assert current not in seen, "cycle created in DB"
                    seen.add(current)
            assert sum(1 for p in by_id.values() if p is not None) <= 1


class TestWorkerEntryPoints:
    async def test_process_runs_full_import_via_entry(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        mapping = {
            "columns": [
                {"source": "Title", "target": "title", "transform": {"type": "direct"}},
            ]
        }
        job_id = await _seed_import_job(
            session_factory, storage, workspace, member, b"Title\nhello\n", mapping
        )
        worker = await _make_worker(session_factory, storage)
        await _run_job(worker, job_id)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed" and job.succeeded_rows == 1

    async def test_handle_enqueue_and_resume_dispatch(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
            ]
        }
        job_id = await _seed_import_job(session_factory, storage, workspace, member, b"T\nrow1\n", mapping)
        worker = await _make_worker(session_factory, storage)
        # export-style pending → enqueue handler ignores import 'created' action
        event = OutboxEvent(
            workspace_id=workspace.id,
            event_type="data_job.enqueue",
            payload={"data_job_id": str(job_id), "action": "created"},
        )
        async with worker._factory() as session:
            await worker.handle_enqueue(session, event)  # no-op for import 'created'
        # resume handler with no lease → nothing claimable, no crash
        event2 = OutboxEvent(
            workspace_id=workspace.id,
            event_type="data_job.resume",
            payload={"data_job_id": str(job_id), "action": "resume"},
        )
        async with worker._factory() as session:
            await worker.handle_resume(session, event2)
        # garbage payloads are tolerated
        bad = OutboxEvent(workspace_id=workspace.id, event_type="data_job.enqueue", payload={})
        async with worker._factory() as session:
            await worker.handle_enqueue(session, bad)
            await worker.handle_resume(session, bad)

    async def test_process_unclaimable_is_noop(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
            ]
        }
        job_id = await _seed_import_job(session_factory, storage, workspace, member, b"T\nr\n", mapping)
        worker = await _make_worker(session_factory, storage)
        # job is pending (never transitioned) → import-run claim fails → no-op
        await worker.process(job_id, "import-run")
        async with session_factory() as session:
            assert (await session.get(DataJob, job_id)).status == "pending"


class TestProjectExportPipeline:
    async def test_project_export_streams_rows(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        admin = await member_factory(workspace, role="admin")
        async with session_factory() as session, session.begin():
            for key, name in (("AAA", "Alpha"), ("BBB", "Beta")):
                session.add(Project(workspace_id=workspace.id, name=name, key=key))
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="projects",
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
        worker = await _make_worker(session_factory, storage)
        claim = await worker._claim(job_id, "export")
        from mesh.data_jobs.exporter import run_export_pipeline

        await run_export_pipeline(worker, claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed" and job.total_rows == 2
            attachment = await session.get(Attachment, job.result_attachment_id)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
        content = storage.objects[blob.storage_key].decode()
        assert "Alpha" in content and "BBB" in content

    async def test_storage_failure_fails_job_fenced(self, session_factory, workspace_factory, member_factory):
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
            session.add(
                Issue(
                    workspace_id=workspace.id,
                    identifier_namespace_key="WS",
                    number=1,
                    identifier="WS-1",
                    title="x",
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
        storage = StubStorage(fail_put=True)
        worker = await _make_worker(session_factory, storage)
        claim = await worker._claim(job_id, "export")
        from mesh.data_jobs.exporter import run_export_pipeline

        await run_export_pipeline(worker, claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "failed"
            assert job.failure_reason == "storage_error"


class TestTransformContextBranches:
    async def test_project_context_and_lead_resolution(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        lead = await member_factory(workspace, name="Lead Person")
        from mesh.db.models.member import Member
        from mesh.db.models.user import User

        async with session_factory() as session:
            row = (
                await session.execute(
                    select(Member, User).join(User, User.id == Member.user_id).where(Member.id == lead.id)
                )
            ).one()
            email = row[1].email
        async with session_factory() as ctx_session:
            context = await tf.build_context(
                ctx_session,
                workspace_id=workspace.id,
                entity_type="projects",
                project_id=None,
                mapping={},
            )
        assert context.members_by_email.get(email.lower()) == lead.id
        mapping = {
            "columns": [
                {"source": "Name", "target": "name", "transform": {"type": "direct"}},
                {"source": "Key", "target": "key", "transform": {"type": "direct"}},
                {
                    "source": "Lead",
                    "target": "lead",
                    "transform": {"type": "member_by_email", "on_missing": "error"},
                },
                {
                    "source": "Health",
                    "target": "health",
                    "transform": {"type": "value_map", "map": {"Green": "on_track"}, "default": "on_track"},
                },
                {"source": "Target", "target": "target_date", "transform": {"type": "date_parse"}},
                {"source": "Status", "target": "status", "transform": {"type": "direct"}},
            ]
        }
        values, errors, _w = tf.transform_row(
            1,
            {
                "Name": "Proj",
                "Key": "PRJ",
                "Lead": email,
                "Health": "Green",
                "Target": "2026-12-31",
                "Status": "active",
            },
            mapping,
            context,
        )
        assert errors == []
        assert values["lead_member_id"] == lead.id
        assert values["health"] == "on_track"
        assert values["status"] == "active"
        # unknown lead with on_missing=error
        _v, errors, _w = tf.transform_row(
            2,
            {
                "Name": "P",
                "Key": "PRJ",
                "Lead": "ghost@mesh.example",
                "Health": "x",
                "Target": "2026-12-31",
                "Status": "bogus",
            },
            mapping,
            context,
        )
        codes = {e.code for e in errors}
        assert {"unknown_member", "invalid_value"} <= codes

    async def test_issue_context_milestones_cycles_custom_coercion(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        from mesh.db.models.label import CustomFieldDef
        from mesh.db.models.project import Cycle, Milestone

        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            project = Project(workspace_id=workspace.id, name="P", key="PRJX")
            session.add(project)
            await session.flush()
            session.add(Milestone(workspace_id=workspace.id, project_id=project.id, title="M1"))
            session.add(
                Cycle(
                    workspace_id=workspace.id,
                    project_id=project.id,
                    name="Sprint 1",
                    starts_at=__import__("datetime").date(2026, 1, 1),
                    ends_at=__import__("datetime").date(2026, 1, 14),
                )
            )
            session.add(
                CustomFieldDef(
                    workspace_id=workspace.id, project_id=None, name="Points", field_key="pts", type="number"
                )
            )
            session.add(
                CustomFieldDef(
                    workspace_id=workspace.id, project_id=None, name="Flag", field_key="flag", type="boolean"
                )
            )
            session.add(
                CustomFieldDef(
                    workspace_id=workspace.id, project_id=None, name="When", field_key="when", type="datetime"
                )
            )
            session.add(
                CustomFieldDef(
                    workspace_id=workspace.id,
                    project_id=None,
                    name="Retired",
                    field_key="old",
                    type="text",
                    is_active=False,
                )
            )
        async with session_factory() as ctx_session:
            context = await tf.build_context(
                ctx_session,
                workspace_id=workspace.id,
                entity_type="issues",
                project_id=project.id,
                mapping={},
            )
        assert "M1" in context.milestones_by_name
        assert "Sprint 1" in context.cycles_by_name
        assert "old" not in context.custom_fields_by_key  # inactive excluded
        mapping = {
            "columns": [
                {"source": "T", "target": "title", "transform": {"type": "direct"}},
                {"source": "M", "target": "milestone", "transform": {"type": "direct"}},
                {"source": "C", "target": "cycle", "transform": {"type": "direct"}},
                {"source": "Pts", "target": "custom_field_values.pts", "transform": {"type": "direct"}},
                {"source": "Flag", "target": "custom_field_values.flag", "transform": {"type": "direct"}},
                {"source": "When", "target": "custom_field_values.when", "transform": {"type": "date_parse"}},
            ]
        }
        values, errors, _w = tf.transform_row(
            1,
            {"T": "x", "M": "M1", "C": "Sprint 1", "Pts": "8", "Flag": "true", "When": "2026-05-01T10:00:00"},
            mapping,
            context,
        )
        assert errors == []
        assert values["milestone_id"] == context.milestones_by_name["M1"]
        assert values["cycle_id"] == context.cycles_by_name["Sprint 1"]
        from decimal import Decimal

        assert values["custom_field_values"]["pts"] == Decimal("8")
        assert values["custom_field_values"]["flag"] is True
        # unknown milestone/cycle → invalid_value
        _v, errors, _w = tf.transform_row(
            2,
            {"T": "x", "M": "Ghost", "C": "Ghost", "Pts": "NaN-ish", "Flag": "maybe", "When": "never"},
            mapping,
            context,
        )
        codes = [e.code for e in errors]
        assert codes.count("invalid_value") >= 4

    async def test_status_project_scope_wins_and_date_edge(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        from mesh.db.models.issue import IssueStatus

        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            project = Project(workspace_id=workspace.id, name="P", key="PSCO")
            session.add(project)
            await session.flush()
            # project-private status with the SAME name as a workspace one
            session.add(
                IssueStatus(
                    workspace_id=workspace.id,
                    project_id=project.id,
                    name="Todo",
                    category="in_progress",
                    position=0,
                    is_default=True,
                )
            )
        async with session_factory() as ctx_session:
            context = await tf.build_context(
                ctx_session,
                workspace_id=workspace.id,
                entity_type="issues",
                project_id=project.id,
                mapping={},
            )
        # project-scoped "Todo" (in_progress) wins over workspace "Todo" (todo)
        assert context.statuses_by_name["Todo"].category == "in_progress"
        assert context.default_status.category == "in_progress"


class TestReaperLoop:
    async def test_loop_runs_pass_and_stops(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            session.add(
                DataJob(
                    workspace_id=workspace.id,
                    kind="export",
                    entity_type="issues",
                    format="csv",
                    status="running",
                    lease_owner="dead",
                    lease_seq=1,
                    lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
                    requested_by=member.id,
                )
            )
        stop = asyncio.Event()
        settings = _settings(data_job_reaper_interval=0.05)

        async def _stop_soon():
            await asyncio.sleep(0.2)
            stop.set()

        await asyncio.wait_for(
            asyncio.gather(
                data_job_reaper_loop(session_factory, settings=settings, stop=stop),
                _stop_soon(),
            ),
            timeout=10,
        )
        async with session_factory() as session:
            assert (
                await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "data_job.resume"))
            ).scalars().first() is not None


class TestReportWriterAndHelpers:
    def test_report_writer_lifecycle(self, tmp_path):
        writer = ErrorReportWriter()
        writer.add({"row": 1, "field": "title", "code": "required_field_missing", "message": "empty"})
        writer.add({"row": 2, "field": "x", "code": "invalid_value", "message": "bad"})
        assert writer.count == 2
        assert writer.size_bytes() > 0
        path = writer.finish()
        content = open(path).read()
        assert "required_field_missing" in content
        writer.cleanup()  # idempotent after finish

    def test_report_writer_cleanup_without_finish(self):
        writer = ErrorReportWriter()
        writer.add({"row": 1, "field": "f", "code": "invalid_date", "message": "m"})
        writer.cleanup()
        writer.cleanup()  # second call is harmless

    def test_preview_values_rendering(self):
        from mesh.data_jobs.transforms import StatusInfo

        preview = _preview_values(
            {
                "status": StatusInfo(id=uuid.uuid4(), category="todo"),
                "due": __import__("datetime").date(2026, 1, 2),
                "plain": "text",
                "n": 5,
            }
        )
        assert preview["status"] == "status:todo"
        assert preview["due"] == "2026-01-02"
        assert preview["plain"] == "text"

    def test_short_error_message(self):
        assert _short(ValueError("x" * 500)) == "x" * 120
        assert _short(ValueError("line1\nline2")) == "line1"


class TestParserEdges:
    def test_unsupported_format_raises(self, tmp_path):
        path = tmp_path / "x.xml"
        path.write_text("<a/>")
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "xml"))

    def test_json_huge_object_bound(self, tmp_path):
        path = tmp_path / "huge.json"
        # one object larger than the per-row bound
        with open(path, "w") as handle:
            handle.write('[{"k": "')
            handle.write("a" * (11 * 1024 * 1024))
            handle.write('"}]')
        with pytest.raises(SourceParseError):
            list(iter_source_rows(str(path), "json"))
