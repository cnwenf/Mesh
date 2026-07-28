"""Reaper / channel-checker / exporter unit tests (import-export.md §3.5/§3.8/§3.11)."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mesh.attachment.service import AttachmentService
from mesh.config import load_settings
from mesh.data_jobs.channels import make_data_job_channel_checker
from mesh.data_jobs.reaper import run_reaper_pass
from mesh.data_jobs.runner import DataJobWorker
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob
from mesh.db.models.issue import Issue
from mesh.db.models.outbox import OutboxEvent
from mesh.issue.statuses import ensure_scope_seeded
from mesh.realtime.auth import Principal

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


async def _make_worker(session_factory, storage, **overrides):
    settings = _settings(**overrides)
    return DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-test",
    )


class TestReaper:
    async def test_expired_lease_reclaimed_and_resume_emitted(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="running",
                lease_owner="dead-worker",
                lease_seq=3,
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=1),
                requested_by=member.id,
                checkpoint={"last_committed_batch": 2},
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        reclaimed = await run_reaper_pass(session_factory, settings=_settings())
        assert reclaimed == 1
        async with session_factory() as session:
            refreshed = await session.get(DataJob, job_id)
            assert refreshed.lease_owner is None  # owner cleared…
            assert refreshed.lease_seq == 3  # …seq PRESERVED (never reset, R4)
            assert refreshed.status == "running"  # status stays
            event = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "data_job.resume"))
            ).scalar_one()
            assert event.payload["data_job_id"] == str(job_id)

    async def test_unexpired_lease_untouched(self, session_factory, workspace_factory, member_factory):
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
                    lease_owner="live-worker",
                    lease_seq=1,
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                    requested_by=member.id,
                )
            )
        assert await run_reaper_pass(session_factory, settings=_settings()) == 0

    async def test_stuck_pending_export_re_enqueued(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        settings = _settings(data_job_stuck_grace=timedelta(seconds=0))
        async with session_factory() as session, session.begin():
            session.add(
                DataJob(
                    workspace_id=workspace.id,
                    kind="export",
                    entity_type="issues",
                    format="csv",
                    status="pending",
                    requested_by=member.id,
                    created_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await session.flush()
        reclaimed = await run_reaper_pass(session_factory, settings=settings)
        assert reclaimed == 1
        async with session_factory() as session:
            event = (
                await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "data_job.enqueue"))
            ).scalar_one()
            assert event.payload["action"] == "export"

    async def test_unclaimed_running_redispatched(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        settings = _settings(data_job_stuck_grace=timedelta(seconds=0))
        async with session_factory() as session, session.begin():
            session.add(
                DataJob(
                    workspace_id=workspace.id,
                    kind="export",
                    entity_type="issues",
                    format="csv",
                    status="running",
                    lease_owner=None,
                    requested_by=member.id,
                    updated_at=datetime.now(UTC) - timedelta(minutes=10),
                )
            )
            await session.flush()
        reclaimed = await run_reaper_pass(session_factory, settings=settings)
        assert reclaimed >= 1


class TestChannelChecker:
    async def test_only_requester_or_admin_can_subscribe(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        requester = await member_factory(workspace, role="member")
        admin = await member_factory(workspace, role="admin")
        stranger = await member_factory(workspace, role="member")
        async with session_factory() as session, session.begin():
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="pending",
                requested_by=requester.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        checker = make_data_job_channel_checker(session_factory)
        channel = f"data_job:{job_id}"

        async def _user_id(member):
            from mesh.db.models.member import Member

            async with session_factory() as session:
                row = await session.get(Member, member.id)
                return str(row.user_id)

        requester_principal = Principal(
            subject=await _user_id(requester), workspace_ids=frozenset({workspace.id})
        )
        admin_principal = Principal(subject=await _user_id(admin), workspace_ids=frozenset({workspace.id}))
        stranger_principal = Principal(
            subject=await _user_id(stranger), workspace_ids=frozenset({workspace.id})
        )
        assert await checker(requester_principal, channel) is True
        assert await checker(admin_principal, channel) is True
        assert await checker(stranger_principal, channel) is False

    async def test_unknown_job_denied(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace, role="admin")
        checker = make_data_job_channel_checker(session_factory)
        from mesh.db.models.member import Member

        async with session_factory() as session:
            row = await session.get(Member, member.id)
            user_id = str(row.user_id)
        principal = Principal(subject=user_id, workspace_ids=frozenset({workspace.id}))
        assert await checker(principal, f"data_job:{uuid.uuid4()}") is False
        assert await checker(principal, "data_job:not-a-uuid") is False


class TestExportPipeline:
    async def test_export_streams_csv_and_registers_product(
        self, session_factory, workspace_factory, member_factory
    ):
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
            for i in range(3):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"export me {i}",
                        status_id=status_id,
                        state_category="todo",
                        priority="high",
                    )
                )
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="pending",
                mapping={
                    "columns": [
                        {"target": "identifier", "source": "编号"},
                        {"target": "title", "source": "标题"},
                        {"target": "priority", "source": "Priority"},
                    ]
                },
                params={"scope": "workspace"},
                requested_by=admin.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        storage = StubStorage()
        worker = await _make_worker(session_factory, storage)
        claim = await worker._claim(job_id, "export")
        assert claim is not None and claim.status == "running"
        from mesh.data_jobs.exporter import run_export_pipeline

        await run_export_pipeline(worker, claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "completed"
            assert job.total_rows == 3 and job.succeeded_rows == 3
            assert job.result_attachment_id is not None
            attachment = await session.get(Attachment, job.result_attachment_id)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            assert blob.scan_status == "skipped"  # text whitelist → immediately downloadable
            assert attachment.uploader_id == admin.id  # attributed to the requester
        content = storage.objects[blob.storage_key].decode()
        assert "编号,标题,Priority" in content.splitlines()[0]
        assert "WS-1" in content and "export me 2" in content

    async def test_export_json_format(self, session_factory, workspace_factory, member_factory):
        import json

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
                    title="json row",
                    status_id=status_id,
                    state_category="todo",
                )
            )
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="json",
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
            assert job.status == "completed"
            attachment = await session.get(Attachment, job.result_attachment_id)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
        payload = json.loads(storage.objects[blob.storage_key].decode())
        assert isinstance(payload, list) and payload[0]["identifier"] == "WS-1"

    async def test_export_too_large_fails_job(self, session_factory, workspace_factory, member_factory):
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
            for i in range(4):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"x{i}",
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
        worker = await _make_worker(session_factory, storage, data_job_export_max_rows=3)
        claim = await worker._claim(job_id, "export")
        from mesh.data_jobs.exporter import run_export_pipeline

        await run_export_pipeline(worker, claim)
        async with session_factory() as session:
            job = await session.get(DataJob, job_id)
            assert job.status == "failed"
            assert job.failure_reason == "export_too_large"


class TestReaperRearmBucketH2:
    async def test_stale_published_resume_row_does_not_wedge_rearm(
        self, session_factory, workspace_factory, member_factory
    ):
        """H2: a 'wasted' published resume row from a prior crash (old bucket)
        must NOT dedup the reaper's re-emit in a later bucket — so the job is
        re-armed instead of wedged for the outbox retention window."""
        from mesh.data_jobs.reaper import _rearm_bucket, _reclaim_expired_leases
        from mesh.data_jobs.runner import resume_idempotency_key
        from mesh.outbox.service import scope_idempotency_key

        workspace = await workspace_factory()
        member = await member_factory(workspace)
        settings = _settings()
        async with session_factory() as session, session.begin():
            job = DataJob(
                workspace_id=workspace.id,
                kind="export",
                entity_type="issues",
                format="csv",
                status="running",
                lease_owner="dead-worker",
                lease_seq=1,
                lease_expires_at=datetime(2020, 1, 1, tzinfo=UTC),
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        # Simulate the wasted published row from a previous crash: emit_event
        # stores keys workspace-scoped, and WITHOUT bucketing the inner key
        # would be resume_idempotency_key(job_id, 0) — exactly what a non-
        # bucketed reaper would re-emit, wedging the job. Store that scoped key.
        stale_inner = resume_idempotency_key(job_id, 0)  # non-bucketed (old behaviour)
        stale_key = scope_idempotency_key(workspace.id, stale_inner)
        async with session_factory() as session, session.begin():
            session.add(
                OutboxEvent(
                    workspace_id=workspace.id,
                    event_type="data_job.resume",
                    payload={"data_job_id": str(job_id), "action": "resume"},
                    idempotency_key=stale_key,
                    status="published",
                )
            )
        # Reaper runs at a 'now' that falls in a DIFFERENT bucket → its emit
        # uses a fresh key and is inserted (NOT deduped against stale_key).
        now = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
        n = await _reclaim_expired_leases(session_factory, settings=settings, now=now)
        assert n == 1
        async with session_factory() as session:
            keys = set(
                (
                    await session.execute(
                        select(OutboxEvent.idempotency_key).where(
                            OutboxEvent.event_type == "data_job.resume"
                        )
                    )
                ).scalars().all()
            )
        # WITH bucketing the reaper's inner key carries the new bucket, so its
        # scoped key differs from the stale row and the re-arm is inserted.
        fresh_inner = resume_idempotency_key(job_id, 0, bucket=_rearm_bucket(now, settings))
        fresh_key = scope_idempotency_key(workspace.id, fresh_inner)
        assert fresh_inner != stale_inner  # bucketing changes the inner key
        assert fresh_key in keys  # new pending re-arm landed despite the stale row
        # …whereas the non-bucketed key would have collided with the stale row.
        assert scope_idempotency_key(workspace.id, stale_inner) == stale_key
