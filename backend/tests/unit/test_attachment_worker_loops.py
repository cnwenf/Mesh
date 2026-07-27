"""Worker loop integration tests (attachment.md §3.3 / README §2.2).

Runs the REAL supervised loops (one iteration each, stop event armed by a
watcher) against real PG + MinIO — the quarantine sweep and the maintenance
sweep (orphans / retention / GC / quota cache).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from mesh.attachment.storage import ObjectStorage, StorageConfig
from mesh.config import load_settings
from mesh.db.models.attachment import Attachment, AttachmentBlob, AttachmentQuota
from mesh.workers.attachment_processor import (
    attachment_maintenance_loop,
    attachment_scan_loop,
)
from tests.unit.attachment_support import build_service, make_png

pytestmark = pytest.mark.unit

PNG = make_png()


@pytest.fixture
def settings(attachment_settings_kwargs):
    return load_settings(**attachment_settings_kwargs)


@pytest.fixture
async def service(session_factory, object_storage, attachment_settings_kwargs):
    return build_service(session_factory, object_storage, attachment_settings_kwargs)


async def _seed_pending_upload(service, member, workspace) -> str:
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="pic.png",
        file_size=len(PNG), mime_type="image/png",
    )
    payload = response["data"]
    async with httpx.AsyncClient() as client:
        put = await client.put(
            payload["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
        )
        assert put.status_code == 200
    await service.complete_upload(
        actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
    )
    return payload["id"]


async def _stop_when(predicate, stop: asyncio.Event, *, timeout: float = 30.0):
    deadline = datetime.now(UTC) + timedelta(seconds=timeout)
    while datetime.now(UTC) < deadline:
        if await predicate():
            stop.set()
            return
        await asyncio.sleep(0.2)
    stop.set()
    raise AssertionError("loop did not reach the expected state in time")


async def test_scan_pass_processes_quarantine(
    session_factory, object_storage, settings, service, workspace_factory, member_factory
):
    """One synchronous scan pass releases a completed upload's blob."""
    from mesh.workers.attachment_processor import run_scan_pass

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    attachment_id = await _seed_pending_upload(service, member, workspace)

    processed = await run_scan_pass(
        session_factory, storage=object_storage, settings=settings
    )
    assert processed == 1
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "clean"
        attachment = await session.get(Attachment, uuid.UUID(attachment_id))
        assert attachment.upload_status == "completed"


async def test_maintenance_pass_sweeps_orphans_and_refreshes_quota(
    session_factory, object_storage, settings, service, workspace_factory, member_factory
):
    """One synchronous maintenance pass reaps an expired orphan upload."""
    from mesh.workers.attachment_processor import run_maintenance_pass

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="stale.png",
        file_size=len(PNG), mime_type="image/png",
    )
    # Backdate expires_at so the orphan sweep claims it on this pass.
    async with session_factory() as session, session.begin():
        attachment = await session.scalar(select(Attachment))
        attachment.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(AttachmentQuota(
            workspace_id=workspace.id,
            max_file_bytes=100 * 1024 * 1024,
            total_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        ))

    swept, _reaped, _collected = await run_maintenance_pass(service, run_gc=True)
    assert swept == 1
    async with session_factory() as session:
        row = await session.scalar(select(Attachment))
        assert row.upload_status == "expired"
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0
        assert await session.scalar(select(func.count()).select_from(AttachmentQuota)) == 1


# ----------------------------------------------------------------------
# F8 — worker pass robustness + loop-wrapper coverage
# ----------------------------------------------------------------------


async def test_scan_pass_errors_missing_object_without_dying(
    session_factory, object_storage, settings, service, workspace_factory, member_factory
):
    """A missing object (deleted behind the API's back) terminally errors the
    blob with OBJECT_MISSING — one poisoned blob must not wedge the pass."""
    from mesh.workers.attachment_processor import run_scan_pass

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    attachment_id = await _seed_pending_upload(service, member, workspace)

    async with session_factory() as session:
        row = await session.get(Attachment, uuid.UUID(attachment_id))
        blob = await session.get(AttachmentBlob, row.blob_id)
        storage_key = blob.storage_key
    await object_storage.delete_object(storage_key)

    processed = await run_scan_pass(
        session_factory, storage=object_storage, settings=settings
    )
    assert processed == 1
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "error"
        assert blob.scan_detail["error_code"] == "OBJECT_MISSING"


async def test_scan_pass_survives_storage_outage_as_pending(
    session_factory, settings, service, workspace_factory, member_factory
):
    """Storage UNREACHABLE (not object-missing): the pass raises (the loop
    catches and retries next tick); the blob stays pending, never terminal."""
    from mesh.errors import StorageError
    from mesh.workers.attachment_processor import run_scan_pass

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await _seed_pending_upload(service, member, workspace)

    dead = ObjectStorage(
        StorageConfig(
            endpoint="http://127.0.0.1:1",
            public_endpoint="http://127.0.0.1:1",
            region="us-east-1",
            access_key="x",
            secret_key="y",
            bucket="mesh-dead-bucket",
        )
    )
    with pytest.raises(StorageError):
        await run_scan_pass(session_factory, storage=dead, settings=settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "pending"  # retried on later passes


async def test_scan_loop_wrapper_runs_pass_until_stopped(
    session_factory, object_storage, settings, service, workspace_factory, member_factory,
    monkeypatch,
):
    """Loop wrapper coverage: processes one pass then exits when stop is set —
    driven deterministically (stop set right after the first pass)."""
    import mesh.workers.attachment_processor as processor

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await _seed_pending_upload(service, member, workspace)

    stop = asyncio.Event()
    real_pass = processor.run_scan_pass

    async def _one_pass_then_stop(*args, **kwargs):
        processed = await real_pass(*args, **kwargs)
        stop.set()
        return processed

    monkeypatch.setattr(processor, "run_scan_pass", _one_pass_then_stop)
    await asyncio.wait_for(
        attachment_scan_loop(
            session_factory, storage=object_storage, settings=settings, stop=stop
        ),
        timeout=15,
    )
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "clean"


async def test_maintenance_loop_wrapper_runs_pass_until_stopped(
    session_factory, object_storage, settings, service, workspace_factory, member_factory,
    monkeypatch,
):
    """Maintenance loop wrapper coverage: one pass then stop."""
    import mesh.workers.attachment_processor as processor

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="stale2.png",
        file_size=len(PNG), mime_type="image/png",
    )
    async with session_factory() as session, session.begin():
        attachment = await session.scalar(select(Attachment))
        attachment.expires_at = datetime.now(UTC) - timedelta(hours=1)

    stop = asyncio.Event()
    real_pass = processor.run_maintenance_pass

    async def _one_pass_then_stop(*args, **kwargs):
        result = await real_pass(*args, **kwargs)
        stop.set()
        return result

    monkeypatch.setattr(processor, "run_maintenance_pass", _one_pass_then_stop)
    await asyncio.wait_for(
        attachment_maintenance_loop(
            session_factory, storage=object_storage, settings=settings, stop=stop
        ),
        timeout=15,
    )
    async with session_factory() as session:
        row = await session.scalar(select(Attachment))
        assert row.upload_status == "expired"
