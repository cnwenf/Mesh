"""data_jobs / data_job_rows schema behavior (import-export.md §2, T18/T31).

Real-DELETE semantics and constraint rejections are asserted by ACTUAL
writes, not by inspecting the DDL (README §9 T18 / T31).
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.data_job import DataJob, DataJobRow

pytestmark = pytest.mark.unit


async def _seed_attachment(session_factory, workspace, *, uploader, name="src.csv"):
    async with session_factory() as session, session.begin():
        blob = AttachmentBlob(
            workspace_id=workspace.id,
            content_hash=f"hash-{uuid.uuid4().hex}",
            storage_provider="s3",
            storage_bucket="test-bucket",
            storage_key=f"ws/{workspace.id}/00/{uuid.uuid4().hex}",
            file_size=10,
            scan_status="skipped",
            ref_count=1,
        )
        session.add(blob)
        await session.flush()
        attachment = Attachment(
            workspace_id=workspace.id,
            uploader_id=uploader.id,
            blob_id=blob.id,
            file_name=name,
            file_size=10,
            upload_status="completed",
        )
        session.add(attachment)
        await session.flush()
        return attachment.id, blob.id


async def _seed_job(session_factory, workspace, *, requester, source_id=None, kind="import", **overrides):
    async with session_factory() as session, session.begin():
        job = DataJob(
            workspace_id=workspace.id,
            kind=kind,
            entity_type="issues",
            format="csv",
            source_attachment_id=source_id,
            requested_by=requester.id,
            **overrides,
        )
        session.add(job)
        await session.flush()
        return job.id


class TestTableConstraints:
    async def test_import_requires_source(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        with pytest.raises(IntegrityError):
            await _seed_job(session_factory, workspace, requester=member)  # no source

    async def test_export_forbids_source(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        source_id, _blob = await _seed_attachment(session_factory, workspace, uploader=member)
        with pytest.raises(IntegrityError):
            await _seed_job(session_factory, workspace, requester=member, kind="export", source_id=source_id)

    async def test_counts_invariant(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        with pytest.raises(IntegrityError):
            await _seed_job(
                session_factory,
                workspace,
                requester=member,
                kind="export",
                total_rows=5,
                succeeded_rows=4,
                failed_rows=2,
            )

    async def test_cross_tenant_composite_fk_rejected(
        self, session_factory, workspace_factory, member_factory
    ):
        """T1 同类: a composite FK referencing another workspace is rejected."""
        workspace_a = await workspace_factory(name="A")
        workspace_b = await workspace_factory(name="B")
        member_a = await member_factory(workspace_a)
        member_b = await member_factory(workspace_b)
        source_id, _blob = await _seed_attachment(session_factory, workspace_a, uploader=member_a)
        async with session_factory() as session, session.begin():
            session.add(
                DataJob(
                    workspace_id=workspace_b.id,  # tenant B job…
                    kind="import",
                    entity_type="issues",
                    format="csv",
                    source_attachment_id=source_id,  # …tenant A attachment
                    requested_by=member_b.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_row_ledger_check_created_requires_target(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        job_id = await _seed_job(session_factory, workspace, requester=member, kind="export")
        async with session_factory() as session, session.begin():
            session.add(
                DataJobRow(
                    workspace_id=workspace.id,
                    job_id=job_id,
                    row_number=1,
                    row_key="ref:X-1",
                    status="created",  # CHECK demands target_type + target_id
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_row_ledger_check_failed_requires_error(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        job_id = await _seed_job(session_factory, workspace, requester=member, kind="export")
        async with session_factory() as session, session.begin():
            session.add(
                DataJobRow(
                    workspace_id=workspace.id,
                    job_id=job_id,
                    row_number=1,
                    row_key="ref:X-2",
                    status="failed",  # CHECK demands error
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()

    async def test_row_key_unique_per_job(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        job_id = await _seed_job(session_factory, workspace, requester=member, kind="export")
        async with session_factory() as session, session.begin():
            for _ in range(2):
                session.add(
                    DataJobRow(
                        workspace_id=workspace.id,
                        job_id=job_id,
                        row_number=1,
                        row_key="ref:DUP",
                        status="skipped",
                    )
                )
            with pytest.raises(IntegrityError):
                await session.flush()


class TestDeleteSemantics:
    """T18/T31: ACTUAL DELETE behavior of the composite FKs."""

    async def test_source_attachment_delete_restricted(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        source_id, _blob = await _seed_attachment(session_factory, workspace, uploader=member)
        await _seed_job(session_factory, workspace, requester=member, source_id=source_id)
        async with session_factory() as session, session.begin():
            with pytest.raises(IntegrityError):
                await session.execute(text("DELETE FROM attachments WHERE id = :id"), {"id": source_id})
                await session.flush()

    async def test_result_attachment_delete_sets_null_column_only(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        result_id, _blob = await _seed_attachment(
            session_factory, workspace, uploader=member, name="export.csv"
        )
        job_id = await _seed_job(
            session_factory,
            workspace,
            requester=member,
            kind="export",
            result_attachment_id=result_id,
        )
        async with session_factory() as session, session.begin():
            await session.execute(text("DELETE FROM attachments WHERE id = :id"), {"id": result_id})
            await session.flush()
        async with session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT result_attachment_id, workspace_id FROM data_jobs WHERE id = :id"),
                    {"id": job_id},
                )
            ).one()
            assert row[0] is None  # reference column nulled…
            assert row[1] == workspace.id  # …workspace_id untouched (PG16 column-level)

    async def test_job_delete_cascades_rows(self, session_factory, workspace_factory, member_factory):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        job_id = await _seed_job(session_factory, workspace, requester=member, kind="export")
        async with session_factory() as session, session.begin():
            session.add(
                DataJobRow(
                    workspace_id=workspace.id,
                    job_id=job_id,
                    row_number=1,
                    row_key="row:1:abc",
                    status="skipped",
                )
            )
        async with session_factory() as session, session.begin():
            await session.execute(text("DELETE FROM data_jobs WHERE id = :id"), {"id": job_id})
        async with session_factory() as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM data_job_rows WHERE job_id = :id"), {"id": job_id}
                )
            ).scalar_one()
            assert count == 0

    async def test_workspace_delete_cascades_everything(
        self, session_factory, workspace_factory, member_factory
    ):
        workspace = await workspace_factory()
        member = await member_factory(workspace)
        job_id = await _seed_job(session_factory, workspace, requester=member, kind="export")
        # The job references the requester — RESTRICT only blocks member
        # deletes; workspace CASCADE removes both in order.
        async with session_factory() as session, session.begin():
            await session.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace.id})
        async with session_factory() as session:
            assert (
                await session.execute(text("SELECT count(*) FROM data_jobs WHERE id = :id"), {"id": job_id})
            ).scalar_one() == 0
