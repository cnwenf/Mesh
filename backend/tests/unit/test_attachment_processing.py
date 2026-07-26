"""Quarantine pipeline unit tests (attachment.md §3.3 / README §9 T14).

Runs the real worker brain (``process_blob``) against real PG + real MinIO:
magic-byte sniffing, SHA-256 verification, AV verdicts, thumbnails, post-dedup
and the ``attachment.processed`` outbox emission.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from mesh.attachment.processing import claim_pending_blobs, process_blob
from mesh.attachment.scanner import EICAR_SIGNATURE
from mesh.config import load_settings
from mesh.db.models.attachment import Attachment, AttachmentBlob
from mesh.db.models.audit import AuditLog
from mesh.db.models.outbox import OutboxEvent
from mesh.errors import StorageError
from tests.unit.attachment_support import build_service, make_png, seed_issue, sha256_hex

pytestmark = pytest.mark.unit

PNG = make_png()


@pytest.fixture
def settings(attachment_settings_kwargs):
    return load_settings(**attachment_settings_kwargs)


@pytest.fixture
async def service(session_factory, object_storage, attachment_settings_kwargs):
    return build_service(session_factory, object_storage, attachment_settings_kwargs)


async def _upload_and_complete(service, member, workspace, *, data: bytes, name: str,
                               mime: str, declared: str | None = None, link_to=None):
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name=name,
        file_size=len(data), mime_type=mime,
        content_hash=declared if declared is not None else sha256_hex(data),
        link_to=link_to,
    )
    payload = response["data"]
    if payload["upload"] and payload["upload"].get("method") == "PUT":
        import httpx

        async with httpx.AsyncClient() as client:
            put = await client.put(
                payload["upload"]["url"], content=data, headers={"Content-Type": mime}
            )
            assert put.status_code == 200
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )
    return payload


async def _process_all(session_factory, object_storage, settings) -> int:
    processed = 0
    async with session_factory() as session, session.begin():
        for blob in await claim_pending_blobs(session, batch=20):
            await process_blob(session, blob, storage=object_storage, settings=settings)
            processed += 1
    return processed


async def test_clean_image_pipeline(session_factory, service, tenant_factory, object_storage, settings):
    workspace, member = await tenant_factory()
    issue = await seed_issue(session_factory, workspace)
    payload = await _upload_and_complete(
        service, member, workspace, data=PNG, name="pic.png", mime="image/png",
        link_to={"type": "issue", "id": issue.id},
    )
    assert await _process_all(session_factory, object_storage, settings) == 1

    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "clean"
        assert blob.mime_type == "image/png"
        assert blob.extension == "png"
        assert blob.is_image is True
        assert blob.content_hash == sha256_hex(PNG)  # authoritative hash written back
        assert blob.scan_detail["hash_matches"] is True
        assert set(blob.thumbnail_keys) == {"sm", "md", "lg"}
        assert blob.image_width == 64 and blob.image_height == 48
        # attachment.processed queued via the outbox (unique write path, §6.6).
        events = (await session.scalars(select(OutboxEvent))).all()
        realtime_rows = [e for e in events if e.event_type == "realtime.publish"]
        assert len(realtime_rows) == 1
        processed_payload = realtime_rows[0].payload
        assert processed_payload["channel"] == f"issue:{issue.id}"
        assert processed_payload["event"] == "attachment.processed"
        data_field = processed_payload["data"]
        assert data_field["id"] == payload["id"]
        assert data_field["scan_status"] == "clean"
        assert data_field["thumbnail_url"].endswith("/thumbnail?size=md")


async def test_plain_text_is_skipped(session_factory, service, tenant_factory, object_storage, settings):
    workspace, member = await tenant_factory()
    await _upload_and_complete(
        service, member, workspace, data=b"hello\nworld\n", name="notes.txt", mime="text/plain"
    )
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "skipped"
        assert blob.scan_detail["av_result"] == "skipped-text-whitelist"
        assert blob.mime_type == "text/plain"


async def test_scan_skip_disabled_forces_full_scan(
    session_factory, object_storage, tenant_factory, attachment_settings_kwargs
):
    workspace, member = await tenant_factory()
    strict = build_service(
        session_factory, object_storage,
        {**attachment_settings_kwargs, "attachment_scan_skip_text": False},
    )
    await _upload_and_complete(
        strict, member, workspace, data=b"hello\nworld\n", name="notes.txt", mime="text/plain"
    )
    settings = load_settings(**attachment_settings_kwargs, attachment_scan_skip_text=False)
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "clean"  # scanned, not skipped
        assert blob.scan_detail["av_result"] == "clean"


async def test_eicar_is_infected_with_critical_audit(
    session_factory, service, tenant_factory, object_storage, settings
):
    workspace, member = await tenant_factory()
    await _upload_and_complete(
        service, member, workspace,
        data=b"report\n" + EICAR_SIGNATURE + b"\n",
        name="virus.txt", mime="text/plain",
        declared=sha256_hex(b"report\n" + EICAR_SIGNATURE + b"\n"),
    )
    # Text whitelist would skip AV — force a non-text sniff path? No: the EICAR
    # bytes live inside text, so disable skip to exercise the AV verdict.
    settings_strict = load_settings(
        **{**settings.model_dump(), "attachment_scan_skip_text": False}
    )
    await _process_all(session_factory, object_storage, settings_strict)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "infected"
        assert blob.scan_detail["av_result"] == "eicar-test-signature"
        audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "attachment.scan_infected")
        )
        assert audit is not None
        assert audit.metadata_["severity"] == "critical"


async def test_hash_mismatch_is_terminal_error(
    session_factory, service, tenant_factory, object_storage, settings
):
    workspace, member = await tenant_factory()
    real = b"clean csv content\n1,2,3\n"
    # Client lies about the hash — the worker must catch it (§3.5 hash_mismatch).
    await _upload_and_complete(
        service, member, workspace, data=real, name="data.csv", mime="text/csv",
        declared="0" * 64,
    )
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "error"
        assert blob.scan_detail["error_code"] == "HASH_MISMATCH"
        assert blob.scan_detail["hash_matches"] is False
        assert blob.scan_detail["sha256"] == sha256_hex(real)


async def test_missing_object_is_terminal_error(
    session_factory, service, tenant_factory, object_storage, settings
):
    workspace, member = await tenant_factory()
    payload = await _upload_and_complete(
        service, member, workspace, data=PNG, name="gone.png", mime="image/png"
    )
    # Delete the object out-of-band (simulates storage loss).
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        key = blob.storage_key
    await object_storage.delete_object(key)
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "error"
        assert blob.scan_detail["error_code"] == "OBJECT_MISSING"
    assert payload["id"]


async def test_transient_read_failure_retries_then_fails(
    session_factory, service, tenant_factory, object_storage, settings, monkeypatch
):
    workspace, member = await tenant_factory()
    await _upload_and_complete(
        service, member, workspace, data=PNG, name="retry.png", mime="image/png"
    )

    async def _boom(*args, **kwargs):
        raise StorageError("simulated outage")

    monkeypatch.setattr(object_storage, "get_bytes", _boom)
    # Attempts 1 + 2 stay pending with a counter; attempt 3 goes terminal.
    await _process_all(session_factory, object_storage, settings)
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "pending"
        assert blob.scan_detail["attempts"] == 2
    await _process_all(session_factory, object_storage, settings)
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.scan_status == "error"
        assert blob.scan_detail["error_code"] == "STORAGE_READ_FAILED"


async def test_post_dedup_converges_onto_truth_blob(
    session_factory, service, tenant_factory, object_storage, settings
):
    """Two uploads of identical bytes with NO declared hash → one blob (§3.2 后置去重)."""
    workspace, member = await tenant_factory()
    first = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="a.png",
        file_size=len(PNG), mime_type="image/png",
    )
    second = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="b.png",
        file_size=len(PNG), mime_type="image/png",
    )
    assert first["data"]["blob_id"] != second["data"]["blob_id"]  # distinct staging blobs
    import httpx

    for payload in (first["data"], second["data"]):
        async with httpx.AsyncClient() as client:
            put = await client.put(
                payload["upload"]["url"], content=PNG, headers={"Content-Type": "image/png"}
            )
            assert put.status_code == 200
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )

    await _process_all(session_factory, object_storage, settings)

    async with session_factory() as session:
        blobs = (await session.scalars(select(AttachmentBlob))).all()
        assert len(blobs) == 1, "staging blob must merge into the truth blob"
        truth = blobs[0]
        assert truth.content_hash == sha256_hex(PNG)
        assert truth.ref_count == 2
        attachments = (await session.scalars(select(Attachment))).all()
        assert {a.blob_id for a in attachments} == {truth.id}


async def test_staging_hash_collision_falls_back(
    session_factory, service, tenant_factory, object_storage, settings
):
    """A declared hash already claimed by an unreadable blob → staging key (T24)."""
    workspace, member = await tenant_factory()
    first = await _upload_and_complete(
        service, member, workspace, data=PNG, name="first.png", mime="image/png"
    )
    assert first["id"]
    # A second caller declares the SAME hash but cannot read the blob — the
    # request must still succeed (staging fallback), never 500 on the unique key.
    other = (await tenant_factory(workspace=workspace))[1]
    probe = await service.request_upload(
        actor=other, workspace_id=workspace.id, file_name="probe.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    assert probe["data"]["upload"] is not None
    assert probe["data"]["blob_id"] != first["blob_id"]


@pytest.fixture
async def tenant_factory(workspace_factory, member_factory):
    async def _make(workspace=None):
        workspace = workspace or await workspace_factory()
        member = await member_factory(workspace)
        return workspace, member

    return _make
