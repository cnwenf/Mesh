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


async def test_scan_loop_processes_quarantine(
    session_factory, object_storage, settings, service, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    attachment_id = await _seed_pending_upload(service, member, workspace)

    stop = asyncio.Event()
    loop_task = asyncio.create_task(
        attachment_scan_loop(
            session_factory, storage=object_storage, settings=settings, stop=stop
        )
    )

    async def _is_clean() -> bool:
        async with session_factory() as session:
            blob = await session.scalar(select(AttachmentBlob))
            return blob is not None and blob.scan_status == "clean"

    try:
        await _stop_when(_is_clean, stop)
        await asyncio.wait_for(loop_task, timeout=10)
    finally:
        stop.set()
        loop_task.cancel()

    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "clean"
        attachment = await session.get(Attachment, uuid.UUID(attachment_id))
        assert attachment.upload_status == "completed"


async def test_maintenance_loop_sweeps_orphans_and_refreshes_quota(
    session_factory, object_storage, settings, service, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="stale.png",
        file_size=len(PNG), mime_type="image/png",
    )
    # Backdate expires_at so the orphan sweep claims it on the first tick.
    async with session_factory() as session, session.begin():
        attachment = await session.scalar(select(Attachment))
        attachment.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(AttachmentQuota(
            workspace_id=workspace.id,
            max_file_bytes=100 * 1024 * 1024,
            total_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        ))

    stop = asyncio.Event()
    loop_task = asyncio.create_task(
        attachment_maintenance_loop(
            session_factory, storage=object_storage, settings=settings, stop=stop
        )
    )

    async def _expired() -> bool:
        async with session_factory() as session:
            row = await session.scalar(select(Attachment))
            return row.upload_status == "expired"

    try:
        await _stop_when(_expired, stop)
        await asyncio.wait_for(loop_task, timeout=10)
    finally:
        stop.set()
        loop_task.cancel()

    async with session_factory() as session:
        row = await session.scalar(select(Attachment))
        assert row.upload_status == "expired"
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0
        assert await session.scalar(select(func.count()).select_from(AttachmentQuota)) == 1


def test_storage_config_fallback_endpoint():
    config = StorageConfig(
        endpoint="http://internal:9000",
        public_endpoint="http://internal:9000",
        region="us-east-1",
        access_key="a",
        secret_key="b",
        bucket="c",
    )
    storage = ObjectStorage(config)
    assert storage.bucket == "c"
