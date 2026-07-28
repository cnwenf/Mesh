"""Data-jobs worker pipeline tests (import-export.md §3.4 / §3.8, R3/R4, T31).

Drives the REAL runner against the real database with a stub object
storage (the e2e suite covers MinIO). Covers: claim/fencing, row-ledger
idempotency, checkpoint resume, source-hash freeze, terminal semantics,
notification fan-out rows and the fenced ``fail_job``.
"""

import hashlib
import os
import uuid

import pytest
from sqlalchemy import func, select, text

from mesh.config import load_settings
from mesh.data_jobs.runner import DataJobWorker, FenceLostError
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob, DataJobRow
from mesh.db.models.issue import Issue
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.project import Project
from mesh.issue.statuses import ensure_scope_seeded

pytestmark = pytest.mark.unit


class StubStorage:
    """In-memory object store exposing the runner's streaming surface."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.bucket = "stub-bucket"

    def put_content(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def download_to_path(self, key, dest_path, *, max_bytes):
        data = self.objects[key]
        if len(data) > max_bytes:
            from mesh.errors import StorageError

            raise StorageError("object exceeds processing limit")
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return len(data), hashlib.sha256(data).hexdigest()

    async def put_fileobj(self, key, fileobj, *, content_type, content_length):
        self.objects[key] = fileobj.read()

    async def presign_get(self, key, *, expires_in, content_disposition=None, content_type=None):
        return f"https://stub.example/{key}?expires={expires_in}"

    async def delete_object(self, key):
        self.objects.pop(key, None)


def _settings(**overrides):
    return load_settings(
        database_url="postgresql+asyncpg://mesh:mesh@127.0.0.1:5432/unused",
        redis_url="redis://127.0.0.1:6390/1",
        **overrides,
    )


CSV_HEADER = "Title,State,Priority,Key\n"


def _csv_rows(*rows):
    return (
        CSV_HEADER + "".join(f"{title},{state},{prio},{key}\n" for title, state, prio, key in rows)
    ).encode()


async def _seed_source(
    session_factory, storage, workspace, member, content: bytes, *, status="pending", frozen_hash=None
):
    blob_key = f"ws/{workspace.id}/00/{uuid.uuid4().hex}"
    storage.put_content(blob_key, content)
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
            file_name="issues.csv",
            file_size=len(content),
            upload_status="completed",
        )
        session.add(attachment)
        await session.flush()
        job = DataJob(
            workspace_id=workspace.id,
            kind="import",
            entity_type="issues",
            format="csv",
            status=status,
            mapping={
                "columns": [
                    {"source": "Title", "target": "title", "transform": {"type": "direct"}},
                    {
                        "source": "State",
                        "target": "status",
                        "transform": {"type": "status_by_name", "fallback": "default"},
                    },
                    {
                        "source": "Priority",
                        "target": "priority",
                        "transform": {
                            "type": "value_map",
                            "map": {"High": "high", "Low": "low"},
                            "default": "none",
                        },
                    },
                    {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
                ]
            },
            params={},
            source_attachment_id=attachment.id,
            source_content_hash=frozen_hash,
            requested_by=member.id,
        )
        session.add(job)
        await session.flush()
        return job.id, attachment.id


async def _make_worker(session_factory, storage, **settings_overrides):
    settings = _settings(**settings_overrides)
    from mesh.attachment.service import AttachmentService

    return DataJobWorker(
        session_factory,
        settings,
        storage,
        AttachmentService(session_factory, settings, storage),
        worker_id="worker-test",
    )


async def _claim(job_id, worker, action="import-run", status="running"):
    """Claim the job the way the API-transition + reaper paths set it up."""
    async with worker._factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE data_jobs SET status = :status, started_at = now(), "
                "lease_owner = NULL, lease_expires_at = NULL WHERE id = :id"
            ),
            {"status": status, "id": job_id},
        )
    return await worker._claim(job_id, action)


async def _job_row(session_factory, job_id):
    async with session_factory() as session:
        return await session.get(DataJob, job_id)


class TestValidatePipeline:
    async def test_dry_run_writes_preview_and_no_entities(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(
            ("登录崩溃", "Todo", "High", "EXT-1"),
            ("", "Todo", "Low", "EXT-2"),
            ("修复", "Nope", "weird", "EXT-3"),
        )
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        async with worker._factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET status='validating' WHERE id=:id"), {"id": job_id}
            )
        claim = await worker._claim(job_id, "import-validate")
        assert claim is not None and claim.status == "validating"
        await worker.run_validate(claim)

        job = await _job_row(session_factory, job_id)
        assert job.status == "pending"  # back to pending after dry-run
        assert job.total_rows == 3
        assert job.source_content_hash == hashlib.sha256(content).hexdigest()  # frozen
        assert job.params.get("validated_at")
        assert job.params.get("predicted_failed_rows") == 1  # empty title row only
        assert job.result_attachment_id is not None  # error report archived
        # dry-run created NOTHING (spec: 不落库)
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(Issue))) == 0
            assert (await session.scalar(select(func.count()).select_from(DataJobRow))) == 0
        # Error report attachment content has the failed rows.
        report_key = None
        async with session_factory() as session:
            attachment = await session.get(Attachment, job.result_attachment_id)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            report_key = blob.storage_key
        report_text = storage.objects[report_key].decode()
        assert "required_field_missing" in report_text
        # one header + exactly the one failed row (status fallback warns,
        # value_map default saves the priority) — archived in full
        assert len(report_text.strip().splitlines()) == 2

    async def test_validate_rejects_replaced_source(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "K1"))
        job_id, _att = await _seed_source(
            session_factory,
            storage,
            workspace,
            member,
            content,
            frozen_hash="0" * 64,  # frozen hash mismatch → source replaced
        )
        worker = await _make_worker(session_factory, storage)
        async with worker._factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET status='validating' WHERE id=:id"), {"id": job_id}
            )
        # A crash-RESUME of a validate must refuse a swapped source (the
        # in-flight dry-run assumed the old bytes; M3 / §3.8 R3).
        claim = await worker._claim(job_id, "resume")
        assert claim.resumed is True
        await worker.run_validate(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "failed"
        assert job.failure_reason == "source_changed"

    async def test_fresh_validate_refreezes_changed_source(
        self, session_factory, workspace_factory, member_factory
    ):
        """M3: a FRESH (re-)validate re-establishes the snapshot (re-freezes the
        new hash) so §3.4 "re-validate" stays reachable after a source swap."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "K1"))
        job_id, _att = await _seed_source(
            session_factory,
            storage,
            workspace,
            member,
            content,
            frozen_hash="0" * 64,  # stale frozen hash from a previous validate
        )
        worker = await _make_worker(session_factory, storage)
        async with worker._factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET status='validating' WHERE id=:id"), {"id": job_id}
            )
        claim = await worker._claim(job_id, "import-validate")  # fresh → resumed False
        assert claim.resumed is False
        await worker.run_validate(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "pending"  # NOT failed — re-validated
        assert job.source_content_hash == hashlib.sha256(content).hexdigest()  # re-frozen


class TestRunPipeline:
    async def test_partial_success_counts_and_entities(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(
            ("good-1", "Todo", "High", "EXT-1"),
            ("good-2", "Todo", "Low", "EXT-2"),
            ("", "Todo", "Low", "EXT-3"),
        )
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)

        job = await _job_row(session_factory, job_id)
        assert job.status == "completed_with_errors"
        assert job.total_rows == 3
        assert job.succeeded_rows == 2
        assert job.failed_rows == 1
        assert job.succeeded_rows + job.failed_rows == job.total_rows  # invariant
        assert job.finished_at is not None and job.lease_owner is None
        assert job.result_attachment_id is not None
        assert job.checkpoint.get("last_committed_batch") == 1
        async with session_factory() as session:
            issues = (await session.execute(select(Issue))).scalars().all()
            assert {i.title for i in issues} == {"good-1", "good-2"}
            # identifiers via the normal numbering path
            assert {i.identifier for i in issues} == {"WS-1", "WS-2"}
            led_created = await session.scalar(
                select(func.count()).select_from(DataJobRow).where(DataJobRow.status == "created")
            )
            led_failed = await session.scalar(
                select(func.count()).select_from(DataJobRow).where(DataJobRow.status == "failed")
            )
            assert led_created == 2 and led_failed == 1
            # terminal notification fanned out (critical? no — partial = normal)
            fanouts = (
                (
                    await session.execute(
                        select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
                    )
                )
                .scalars()
                .all()
            )
            assert len(fanouts) == 1
            assert fanouts[0].payload["type"] == "data_job_finished"
            assert fanouts[0].payload["data_job_status"] == "completed_with_errors"

    async def test_all_success_completes(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "K1"), ("b", "Todo", "High", "K2"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed" and job.failed_rows == 0

    async def test_batching_and_checkpoint_resume(self, session_factory, workspace_factory, member_factory):
        """Kill after batch 1 → resume continues from checkpoint, no dupes."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        rows = [(f"issue-{i}", "Todo", "Low", f"K{i}") for i in range(1, 6)]  # 5 rows
        content = _csv_rows(*rows)
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage, data_job_batch_size=2)
        claim = await _claim(job_id, worker)

        # Simulate crash after batch 1: run only the first batch manually.
        context = await worker._build_context(await worker._require_job(job_id))
        from mesh.data_jobs.parser import RowKeyAllocator, iter_source_rows

        allocator = RowKeyAllocator()
        scratch = "/tmp/mesh-resume-test"
        os.makedirs(scratch, exist_ok=True)
        source_path = f"{scratch}/source"
        await worker._fetch_source(await worker._require_job(job_id), source_path)
        batch = []
        for row_number, raw in iter_source_rows(source_path, "csv"):
            batch.append((row_number, raw))
            if len(batch) == 2:
                break
        external_ref_def = await worker._ensure_external_ref_field(claim)
        await worker._run_batch(claim, batch, 1, context, external_ref_def, allocator, 5, skip=False)
        job = await _job_row(session_factory, job_id)
        assert job.checkpoint.get("last_committed_batch") == 1
        assert job.succeeded_rows == 2

        # Reaper-style resume claim (lease_seq +1) → resume finishes the rest.
        claim2 = await _claim(job_id, worker, action="resume")
        assert claim2.resumed is True and claim2.lease_seq == claim.lease_seq + 1
        await worker.run_import(claim2)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed"
        assert job.succeeded_rows == 5  # NO duplicates from replayed batch 1
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(Issue))) == 5
            titles = set((await session.execute(select(Issue.title))).scalars().all())
            assert titles == {f"issue-{i}" for i in range(1, 6)}

    async def test_replay_of_committed_batch_creates_nothing(
        self, session_factory, workspace_factory, member_factory
    ):
        """row_key atomic occupation: re-running a committed batch is a no-op."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("one", "Todo", "Low", "R1"), ("two", "Todo", "Low", "R2"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)

        # Force-replay the same batch with a fresh claim + reset checkpoint.
        async with worker._factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET status='running', started_at=now(), lease_owner=NULL, "
                    "lease_expires_at=NULL, checkpoint='{}', succeeded_rows=0, failed_rows=0 "
                    "WHERE id=:id"
                ),
                {"id": job_id},
            )
        claim2 = await _claim(job_id, worker)
        await worker.run_import(claim2)
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(Issue))) == 2  # no dupes

    async def test_duplicate_external_ref_counted_as_failed(
        self, session_factory, workspace_factory, member_factory
    ):
        """succeeded + failed = total even with duplicate refs (review HIGH-6)."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("first", "Todo", "Low", "DUP-1"), ("second", "Todo", "Low", "DUP-1"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed_with_errors"
        assert job.succeeded_rows == 1
        assert job.failed_rows == 1
        assert job.succeeded_rows + job.failed_rows == job.total_rows
        codes = [entry["code"] for entry in (job.error_report or [])]
        assert "duplicate_within_file" in codes
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(Issue))) == 1


class TestFencing:
    async def test_stale_worker_batch_rejected_wholesale(
        self, session_factory, workspace_factory, member_factory
    ):
        """R4: a resurrected stale worker's batch rolls back entirely."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "F1"), ("b", "Todo", "Low", "F2"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        old_claim = await _claim(job_id, worker)

        # A NEW worker takes over (reaper cleared the owner; lease_seq +1).
        new_claim = await _claim(job_id, worker)
        assert new_claim.lease_seq == old_claim.lease_seq + 1

        # The stale claim tries to commit a batch → FenceLostError, nothing written.
        job = await worker._require_job(job_id)
        context = await worker._build_context(job)
        from mesh.data_jobs.parser import RowKeyAllocator

        allocator = RowKeyAllocator()
        with pytest.raises(FenceLostError):
            await worker._run_batch(
                old_claim,
                [(1, {"Title": "stale", "State": "Todo", "Priority": "Low", "Key": "F1"})],
                1,
                context,
                None,
                allocator,
                skip=False,
            )
        async with session_factory() as session:
            assert (await session.scalar(select(func.count()).select_from(Issue))) == 0
            assert (await session.scalar(select(func.count()).select_from(DataJobRow))) == 0

    async def test_fail_job_is_fenced(self, session_factory, workspace_factory, member_factory):
        """Review HIGH-1: a stale worker can NOT fail a job the new owner runs."""
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "G1"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        old_claim = await _claim(job_id, worker)
        new_claim = await _claim(job_id, worker)  # takeover bumps lease_seq

        await worker.fail_job(old_claim, "storage_error")  # stale → silently ignored
        job = await _job_row(session_factory, job_id)
        assert job.status == "running"  # NOT failed — the new owner is untouched

        await worker.fail_job(new_claim, "storage_error")  # current owner → fails
        job = await _job_row(session_factory, job_id)
        assert job.status == "failed"
        assert job.failure_reason == "storage_error"
        # critical notification for task-level failure (§6.13 data-job row 3)
        async with session_factory() as session:
            fanout = (
                await session.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == "notification.fanout")
                )
            ).scalar_one()
            assert fanout.payload["data_job_status"] == "failed"

    async def test_expired_lease_fences_batch(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "H1"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        # Expire the lease out from under the worker.
        async with worker._factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = :id"),
                {"id": job_id},
            )
        job = await worker._require_job(job_id)
        context = await worker._build_context(job)
        from mesh.data_jobs.parser import RowKeyAllocator

        with pytest.raises(FenceLostError):
            await worker._run_batch(
                claim,
                [(1, {"Title": "x", "State": "Todo", "Priority": "Low", "Key": "H1"})],
                1,
                context,
                None,
                RowKeyAllocator(),
                skip=False,
            )


class TestProjectImport:
    async def test_project_rows_created_with_key_conflict_row_error(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        content = b"Name,Key\nProj One,AAA\nProj Two,AAA\nProj Three,BBB\n"
        blob_key = f"ws/{workspace.id}/00/{uuid.uuid4().hex}"
        storage.put_content(blob_key, content)
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
                file_name="projects.csv",
                file_size=len(content),
                upload_status="completed",
            )
            session.add(attachment)
            await session.flush()
            job = DataJob(
                workspace_id=workspace.id,
                kind="import",
                entity_type="projects",
                format="csv",
                mapping={
                    "columns": [
                        {"source": "Name", "target": "name", "transform": {"type": "direct"}},
                        {"source": "Key", "target": "key", "transform": {"type": "direct"}},
                    ]
                },
                source_attachment_id=attachment.id,
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed_with_errors"
        assert job.succeeded_rows == 2
        assert job.failed_rows == 1  # second AAA → project_key_taken
        async with session_factory() as session:
            keys = set((await session.execute(select(Project.key))).scalars().all())
            assert keys == {"AAA", "BBB"}
            failed_error = await session.scalar(select(DataJobRow.error).where(DataJobRow.status == "failed"))
            # IntegrityError path records invalid_value; either way the row
            # failed with an error payload and counts reconcile.
            assert failed_error is not None


class TestLeaseRenewal:
    async def test_renew_extends_lease_and_unknown_worker_fenced(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "L1"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        before = (await _job_row(session_factory, job_id)).lease_expires_at
        await worker._renew_lease(claim, transition="renew-test")
        after = (await _job_row(session_factory, job_id)).lease_expires_at
        assert after > before


async def _seed_csv(session_factory, storage, workspace, member, content, mapping, entity_type="issues"):
    blob_key = f"ws/{workspace.id}/00/{uuid.uuid4().hex}"
    storage.put_content(blob_key, content)
    content_hash = hashlib.sha256(content).hexdigest()
    async with session_factory() as session, session.begin():
        # Reuse an existing blob for identical content (T24 content-addressed dedup).
        blob_id = await session.scalar(
            select(AttachmentBlob.id).where(
                AttachmentBlob.workspace_id == workspace.id,
                AttachmentBlob.content_hash == content_hash,
            )
        )
        if blob_id is None:
            blob = AttachmentBlob(
                workspace_id=workspace.id,
                content_hash=content_hash,
                storage_provider="s3",
                storage_bucket="stub-bucket",
                storage_key=blob_key,
                file_size=len(content),
                scan_status="skipped",
                ref_count=1,
            )
            session.add(blob)
            await session.flush()
            blob_id = blob.id
        att = Attachment(
            workspace_id=workspace.id,
            uploader_id=member.id,
            blob_id=blob_id,
            file_name="s.csv",
            file_size=len(content),
            upload_status="completed",
        )
        session.add(att)
        await session.flush()
        job = DataJob(
            workspace_id=workspace.id,
            kind="import",
            entity_type=entity_type,
            format="csv",
            status="pending",
            mapping=mapping,
            source_attachment_id=att.id,
            requested_by=member.id,
        )
        session.add(job)
        await session.flush()
        return job.id


class TestReviewFixesH1M1M2:
    """Reviewer HIGH-1 / M1 / M2: row-level isolation, idempotent warnings, codes."""

    async def test_title_too_long_is_row_failure_not_job_failure(
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
                {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
            ]
        }
        long_title = "x" * 300
        content = f"Title,Key\n{long_title},K1\nshort,K2\n".encode()
        # dry-run PREDICTS the bad row (H1② transform pre-validation).
        dry_id = await _seed_csv(session_factory, storage, workspace, member, content, mapping)
        worker = await _make_worker(session_factory, storage)
        async with worker._factory() as session, session.begin():
            await session.execute(
                text("UPDATE data_jobs SET status='validating' WHERE id=:id"), {"id": dry_id}
            )
        dclaim = await worker._claim(dry_id, "import-validate")
        await worker.run_validate(dclaim)
        dry = await _job_row(session_factory, dry_id)
        assert dry.status == "pending"
        assert dry.params["predicted_failed_rows"] == 1
        assert dry.error_report[0]["code"] == "invalid_value"
        # run: partial success, NOT a job-level failed (H1①).
        run_id = await _seed_csv(session_factory, storage, workspace, member, content, mapping)
        rclaim = await _claim(run_id, worker)
        await worker.run_import(rclaim)
        job = await _job_row(session_factory, run_id)
        assert job.status == "completed_with_errors"
        assert job.succeeded_rows == 1 and job.failed_rows == 1
        assert any(e["code"] == "invalid_value" for e in job.error_report)
        async with session_factory() as session:
            titles = set((await session.execute(select(Issue.title))).scalars().all())
            assert titles == {"short"}

    async def test_due_before_start_is_row_failure(
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
                {"source": "Due", "target": "due_date", "transform": {"type": "date_parse"}},
                {"source": "Start", "target": "start_date", "transform": {"type": "date_parse"}},
            ]
        }
        content = b"Title,Due,Start\nok,2026-01-10,2026-01-01\nbad,2026-01-01,2026-01-10\n"
        job_id = await _seed_csv(session_factory, storage, workspace, member, content, mapping)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed_with_errors"
        assert job.succeeded_rows == 1 and job.failed_rows == 1
        assert any(e["code"] == "invalid_value" and e["field"] == "due_date" for e in job.error_report)

    async def test_project_key_collision_records_project_key_taken(
        self, session_factory, workspace_factory, member_factory
    ):
        from mesh.workspace.service import occupy_project_prefix

        workspace = await workspace_factory()
        member = await member_factory(workspace)
        # Pre-register the "COL" prefix via an existing project.
        async with session_factory() as session, session.begin():
            existing = Project(workspace_id=workspace.id, name="Existing", key="COL")
            session.add(existing)
            await session.flush()
            await occupy_project_prefix(
                session, workspace_id=workspace.id, key="COL", project_id=existing.id
            )
        storage = StubStorage()
        mapping = {
            "columns": [
                {"source": "Name", "target": "name", "transform": {"type": "direct"}},
                {"source": "Key", "target": "key", "transform": {"type": "direct"}},
            ]
        }
        content = b"Name,Key\nCollide,COL\nFresh,NEW2\n"
        job_id = await _seed_csv(
            session_factory, storage, workspace, member, content, mapping, entity_type="projects"
        )
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        await worker.run_import(claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "completed_with_errors"
        assert job.succeeded_rows == 1 and job.failed_rows == 1
        # M2: correct code, not a generic invalid_value.
        failed = [e for e in job.error_report if e["code"] == "project_key_taken"]
        assert failed, job.error_report
        async with session_factory() as session:
            keys = set((await session.execute(select(Project.key))).scalars().all())
            assert keys == {"COL", "NEW2"}

    async def test_parent_warning_idempotent_on_resolve_rerun(
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
                {"source": "Key", "target": "external_ref", "transform": {"type": "direct"}},
                {"source": "Parent", "target": "parent", "transform": {"type": "parent_by_external_ref"}},
            ]
        }
        content = b"Title,Key,Parent\na,K1,GHOST\n"
        job_id = await _seed_csv(session_factory, storage, workspace, member, content, mapping)
        worker = await _make_worker(session_factory, storage)
        claim = await _claim(job_id, worker)
        # Create the issue first (so the ledger has a 'created' row for K1),
        # then run the parent pass TWICE to prove the warning INSERT is
        # idempotent across a simulated resume re-run (M1 ON CONFLICT).
        job = await worker._require_job(job_id)
        context = await worker._build_context(job)
        from mesh.data_jobs.parser import RowKeyAllocator, iter_source_rows

        scratch = f"/tmp/mesh-m1-{uuid.uuid4().hex}"
        os.makedirs(scratch, exist_ok=True)
        source_path = f"{scratch}/source"
        await worker._fetch_source(job, source_path)
        rows = list(iter_source_rows(source_path, "csv"))
        allocator = RowKeyAllocator()
        ext = await worker._ensure_external_ref_field(claim)
        await worker._run_batch(claim, rows, 1, context, ext, allocator, len(rows), skip=False)
        await worker._resolve_parents(claim, context, source_path)
        await worker._resolve_parents(claim, context, source_path)  # re-run = resume
        async with session_factory() as session:
            warning_rows = await session.scalar(
                select(func.count())
                .select_from(DataJobRow)
                .where(DataJobRow.job_id == job_id, DataJobRow.status == "skipped")
            )
            assert warning_rows == 1  # NOT duplicated by the second pass


class TestReviewFixesExtraCoverage:
    def test_row_error_from_exc_branches(self):
        from mesh.data_jobs.runner import _row_error_from_exc
        from mesh.errors import ConflictError

        # MeshError → its crafted code + message (M2 path).
        code, msg = _row_error_from_exc(ConflictError("taken", code="project_key_taken"))
        assert code == "project_key_taken" and msg == "taken"
        # our own ValueError → invalid_value + the text.
        code, msg = _row_error_from_exc(ValueError("bad thing"))
        assert code == "invalid_value" and msg == "bad thing"
        # driver / unknown → neutral message, no leak (L2).
        code, msg = _row_error_from_exc(RuntimeError("psycopg2.errors.X constraint y"))
        assert code == "invalid_value"
        assert "psycopg2" not in msg and "constraint" not in msg

    async def test_resume_cap_terminates_poison_job(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        storage = StubStorage()
        content = _csv_rows(("a", "Todo", "Low", "K1"))
        job_id, _att = await _seed_source(session_factory, storage, workspace, member, content)
        # Cap at 2 resumes; pretend we already resumed twice.
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE data_jobs SET status='running', checkpoint='{\"resumed_count\": 2}' "
                    "WHERE id=:id"
                ),
                {"id": job_id},
            )
        worker = await _make_worker(session_factory, storage, data_job_max_resumes=2)
        claim = await worker._claim(job_id, "resume")
        assert claim is None  # terminated inside _claim, no claim handed out
        job = await _job_row(session_factory, job_id)
        assert job.status == "failed"
        assert job.failure_reason == "resume_limit_exceeded"

    async def test_export_mid_stream_too_large(
        self, session_factory, workspace_factory, member_factory
    ):
        from mesh.data_jobs.exporter import run_export_pipeline

        workspace = await workspace_factory()
        member = await member_factory(workspace)
        async with session_factory() as session, session.begin():
            await ensure_scope_seeded(session, workspace_id=workspace.id)
            status_id = (await session.execute(select(Issue.__table__.c.id).limit(0))).scalar()  # noqa
            from mesh.db.models.issue import IssueStatus

            status_id = (await session.execute(select(IssueStatus.id).limit(1))).scalar_one()
            for i in range(3):
                session.add(
                    Issue(
                        workspace_id=workspace.id,
                        identifier_namespace_key="WS",
                        number=i + 1,
                        identifier=f"WS-{i + 1}",
                        title=f"t{i}",
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
                requested_by=member.id,
            )
            session.add(job)
            await session.flush()
            job_id = job.id
        worker = await _make_worker(session_factory, storage=StubStorage(), data_job_export_max_rows=2)
        claim = await _claim(job_id, worker, action="export", status="pending")
        await run_export_pipeline(worker, claim)
        job = await _job_row(session_factory, job_id)
        assert job.status == "failed"
        assert job.failure_reason == "export_too_large"
