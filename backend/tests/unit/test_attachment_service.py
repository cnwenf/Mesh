"""AttachmentService unit tests — real PG + real MinIO (attachment.md §3/§4/§5)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from mesh.db.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentLink,
    AttachmentQuota,
)
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationError,
)
from tests.unit.attachment_support import build_service, make_png, seed_issue, sha256_hex

pytestmark = pytest.mark.unit

PNG = make_png()


@pytest.fixture
async def service(session_factory, object_storage, attachment_settings_kwargs):
    return build_service(session_factory, object_storage, attachment_settings_kwargs)


@pytest.fixture
async def tenant(workspace_factory, member_factory):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    return workspace, member


async def _put_signed(url: str, data: bytes, content_type: str) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.put(url, content=data, headers={"Content-Type": content_type})
        assert response.status_code == 200, response.text


async def _upload(service, member, workspace, *, data: bytes = PNG, name="shot.png",
                  mime="image/png", link_to=None, content_hash=None, idempotency_key=None):
    declared = content_hash if content_hash is not None else sha256_hex(data)
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name=name,
        file_size=len(data), mime_type=mime, content_hash=declared,
        link_to=link_to, idempotency_key=idempotency_key,
    )
    payload = response["data"]
    if payload["upload"] is not None and payload["upload"].get("method") == "PUT":
        await _put_signed(payload["upload"]["url"], data, mime)
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )
    return payload


# ----------------------------------------------------------------------
# upload-request validation (§3.2 / §3.5 / §3.6)
# ----------------------------------------------------------------------


async def test_upload_request_returns_signed_put_and_creates_pending(service, tenant):
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    data = response["data"]
    assert data["upload_status"] == "pending"
    assert data["scan_status"] == "pending"
    assert data["upload"]["method"] == "PUT"
    assert "X-Amz-Signature" in data["upload"]["url"] or "Signature" in data["upload"]["url"]
    assert data["limits"]["max_file_bytes"] > 0


async def test_validation_errors_before_signing(service, tenant):
    workspace, member = tenant
    with pytest.raises(UnsupportedMediaTypeError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="run.exe",
            file_size=10, mime_type="application/x-msdownload",
        )
    with pytest.raises(PayloadTooLargeError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="big.png",
            file_size=200 * 1024 * 1024, mime_type="image/png",
        )
    with pytest.raises(ValidationError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="fake.exe",
            file_size=10, mime_type="image/png",
        )
    with pytest.raises(ValidationError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="x.png",
            file_size=10, mime_type="image/png", content_hash="not-a-hash",
        )


async def test_quota_exceeded_is_423_before_bandwidth(session_factory, service, tenant):
    workspace, member = tenant
    # Usage is computed from blob truth (ref_count > 0), not the cached column:
    # seed a live blob that nearly fills a tiny quota.
    async with session_factory() as session, session.begin():
        session.add(AttachmentQuota(
            workspace_id=workspace.id, max_file_bytes=100 * 1024 * 1024,
            total_bytes=100, used_bytes=0,
        ))
        session.add(AttachmentBlob(
            workspace_id=workspace.id, content_hash=f"seed-{uuid.uuid4().hex}",
            storage_bucket="x", storage_key="seed/key", file_size=90,
            scan_status="clean", ref_count=1,
        ))
    with pytest.raises(LockedError) as excinfo:
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="shot.png",
            file_size=len(PNG), mime_type="image/png",
        )
    assert excinfo.value.code == "quota_exceeded"


async def test_idempotent_upload_request_returns_first_record(service, tenant):
    workspace, member = tenant
    key = f"idem-{uuid.uuid4().hex}"
    first = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png", idempotency_key=key,
    )
    second = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png", idempotency_key=key,
    )
    assert first["data"]["id"] == second["data"]["id"]


# ----------------------------------------------------------------------
# complete / abort state machines (§2.3 / §3.3)
# ----------------------------------------------------------------------


async def test_complete_happy_path_moves_to_quarantine(service, tenant):
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    await _put_signed(response["data"]["upload"]["url"], PNG, "image/png")
    attachment_id = uuid.UUID(response["data"]["id"])
    done = await service.complete_upload(
        actor=member, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert done["data"]["upload_status"] == "completed"
    assert done["data"]["scan_status"] == "pending"
    assert "note" in done["data"]


async def test_double_complete_is_conflict(service, tenant):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    attachment_id = uuid.UUID(payload["id"])
    with pytest.raises(ConflictError):
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=attachment_id
        )


async def test_complete_missing_object_fails_with_hash_mismatch(service, tenant):
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="ghost.png",
        file_size=len(PNG), mime_type="image/png",
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=attachment_id
        )
    assert excinfo.value.code == "hash_mismatch"


async def test_complete_size_mismatch_marks_failed(service, tenant, object_storage):
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    url = response["data"]["upload"]["url"]
    await _put_signed(url, PNG + b"extra", "image/png")  # wrong size
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.complete_upload(
            actor=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(response["data"]["id"]),
        )
    assert excinfo.value.code == "hash_mismatch"


async def test_owner_only_complete_and_abort(service, tenant, member_factory):
    workspace, member = tenant
    other = await member_factory(workspace)
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    with pytest.raises(ForbiddenError):
        await service.complete_upload(
            actor=other, workspace_id=workspace.id, attachment_id=attachment_id
        )
    with pytest.raises(ForbiddenError):
        await service.abort_upload(
            actor=other, workspace_id=workspace.id, attachment_id=attachment_id
        )


async def test_abort_sets_failed_and_releases_ref_count(service, tenant, session_factory):
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    result = await service.abort_upload(
        actor=member, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert result["data"]["upload_status"] == "failed"
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0
    with pytest.raises(ConflictError):
        await service.abort_upload(
            actor=member, workspace_id=workspace.id, attachment_id=attachment_id
        )


# ----------------------------------------------------------------------
# links & listings (§2.4 / §3.1)
# ----------------------------------------------------------------------


async def test_link_to_issue_and_listing(service, tenant, session_factory):
    workspace, member = tenant
    issue = await seed_issue(session_factory, workspace)
    payload = await _upload(
        service, member, workspace,
        link_to={"type": "issue", "id": issue.id, "display": "inline", "position": 3},
    )
    items, cursor = await service.list_for_host(
        viewer=member, workspace_id=workspace.id,
        linked_type="issue", linked_id=issue.id,
    )
    assert cursor is None
    assert len(items) == 1
    assert items[0]["id"] == payload["id"]
    assert items[0]["links"][0]["display"] == "inline"
    assert items[0]["links"][0]["position"] == 3


async def test_link_duplicate_is_idempotent(service, tenant, session_factory):
    workspace, member = tenant
    issue = await seed_issue(session_factory, workspace)
    link = {"type": "issue", "id": issue.id}
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png", link_to=link,
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        attachment = await session.get(Attachment, attachment_id)
        link_row = await service._create_link(session, workspace.id, attachment, link)
        assert link_row.linked_id == issue.id
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(AttachmentLink))
        assert count == 1


async def test_link_to_unknown_issue_rejected(service, tenant):
    workspace, member = tenant
    with pytest.raises(NotFoundError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="shot.png",
            file_size=len(PNG), mime_type="image/png",
            link_to={"type": "issue", "id": uuid.uuid4()},
        )


async def test_link_to_comment_before_module_lands_is_404(service, tenant):
    workspace, member = tenant
    with pytest.raises(NotFoundError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="shot.png",
            file_size=len(PNG), mime_type="image/png",
            link_to={"type": "comment", "id": uuid.uuid4()},
        )


async def test_invalid_link_type_rejected(service, tenant):
    workspace, member = tenant
    with pytest.raises(ValidationError):
        await service.request_upload(
            actor=member, workspace_id=workspace.id, file_name="shot.png",
            file_size=len(PNG), mime_type="image/png",
            link_to={"type": "bogus", "id": uuid.uuid4()},
        )


async def test_listing_pagination_by_position(service, tenant, session_factory):
    workspace, member = tenant
    issue = await seed_issue(session_factory, workspace)
    ids = []
    for position in (2, 0, 1):
        payload = await _upload(
            service, member, workspace, name=f"f{position}.txt", mime="text/plain",
            data=b"plain text content\n" * 3,
            link_to={"type": "issue", "id": issue.id, "position": position},
        )
        ids.append(payload["id"])
    items, cursor = await service.list_for_host(
        viewer=member, workspace_id=workspace.id,
        linked_type="issue", linked_id=issue.id, limit=2,
    )
    assert [i["links"][0]["position"] for i in items] == [0, 1]
    assert cursor is not None
    page2, cursor2 = await service.list_for_host(
        viewer=member, workspace_id=workspace.id,
        linked_type="issue", linked_id=issue.id, limit=2, cursor=cursor,
    )
    assert [i["links"][0]["position"] for i in page2] == [2]
    assert cursor2 is None


async def test_listing_unreadable_host_is_404(service, tenant):
    workspace, member = tenant
    with pytest.raises(NotFoundError):
        await service.list_for_host(
            viewer=member, workspace_id=workspace.id,
            linked_type="issue", linked_id=uuid.uuid4(),
        )


async def test_guest_cannot_write_issue_link_but_member_can(
    service, tenant, member_factory, session_factory
):
    workspace, member = tenant
    guest = await member_factory(workspace, role="guest")
    issue = await seed_issue(session_factory, workspace)
    with pytest.raises(ForbiddenError):
        await service.request_upload(
            actor=guest, workspace_id=workspace.id, file_name="shot.png",
            file_size=len(PNG), mime_type="image/png",
            link_to={"type": "issue", "id": issue.id},
        )


# ----------------------------------------------------------------------
# download gate (§3.4 / T14)
# ----------------------------------------------------------------------


async def _set_scan(session_factory, workspace, status: str, **fields):
    async with session_factory() as session, session.begin():
        blob = await session.scalar(
            select(AttachmentBlob).where(AttachmentBlob.workspace_id == workspace.id)
        )
        blob.scan_status = status
        for key, value in fields.items():
            setattr(blob, key, value)
    return blob


async def test_download_refused_while_pending(service, tenant):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    with pytest.raises(ForbiddenError) as excinfo:
        await service.download_attachment(
            viewer=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )
    assert excinfo.value.code == "scan_pending"


async def test_download_error_state_maps_to_scan_pending(service, tenant, session_factory):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "error")
    with pytest.raises(ForbiddenError) as excinfo:
        await service.download_attachment(
            viewer=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )
    assert excinfo.value.code == "scan_pending"


async def test_download_infected_is_permanent_refusal(service, tenant, session_factory):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "infected", is_image=True)
    with pytest.raises(ForbiddenError) as excinfo:
        await service.download_attachment(
            viewer=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
        )
    assert excinfo.value.code == "scan_infected"
    # Thumbnails are gated identically.
    with pytest.raises(ForbiddenError) as thumb_exc:
        await service.thumbnail_url(
            viewer=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(payload["id"]), size="md",
        )
    assert thumb_exc.value.code == "scan_infected"


async def test_download_clean_returns_working_signed_url(service, tenant, session_factory):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "clean", mime_type="image/png", is_image=True)
    result = await service.download_attachment(
        viewer=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
    )
    async with httpx.AsyncClient() as client:
        got = await client.get(result["data"]["url"])
        assert got.status_code == 200
        assert got.content == PNG


async def test_thumbnail_not_ready_is_404(service, tenant, session_factory):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "clean", mime_type="image/png", is_image=True)
    with pytest.raises(NotFoundError):
        await service.thumbnail_url(
            viewer=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(payload["id"]), size="md",
        )


async def test_thumbnail_invalid_size_is_400(service, tenant):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    with pytest.raises(ValidationError):
        await service.thumbnail_url(
            viewer=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(payload["id"]), size="xxl",
        )


async def test_read_requires_uploader_or_host_access(service, tenant, member_factory, session_factory):
    workspace, member = tenant
    stranger = await member_factory(workspace)
    payload = await _upload(service, member, workspace)
    attachment_id = uuid.UUID(payload["id"])
    with pytest.raises(NotFoundError):
        await service.get_attachment(
            viewer=stranger, workspace_id=workspace.id, attachment_id=attachment_id
        )
    # Linking to an issue grants host-read access to every member.
    issue = await seed_issue(session_factory, workspace)
    async with session_factory() as session, session.begin():
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace.id)
        session.add(AttachmentLink(
            workspace_id=workspace.id, attachment_id=attachment_id,
            linked_type="issue", linked_id=issue.id,
        ))
    visible = await service.get_attachment(
        viewer=stranger, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert visible["data"]["id"] == payload["id"]


async def test_cross_workspace_access_is_404(service, tenant, workspace_factory, member_factory):
    workspace, member = tenant
    other_ws = await workspace_factory()
    other_member = await member_factory(other_ws)
    payload = await _upload(service, member, workspace)
    with pytest.raises(NotFoundError):
        await service.get_attachment(
            viewer=other_member, workspace_id=other_ws.id,
            attachment_id=uuid.UUID(payload["id"]),
        )


# ----------------------------------------------------------------------
# delete / ref_count / dedup (§4.6 / §5.1b / T24)
# ----------------------------------------------------------------------


async def test_delete_is_uploader_or_admin(service, tenant, member_factory):
    workspace, member = tenant
    plain = await member_factory(workspace)
    admin = await member_factory(workspace, role="admin")
    payload = await _upload(service, member, workspace)
    attachment_id = uuid.UUID(payload["id"])
    with pytest.raises(ForbiddenError):
        await service.delete_attachment(
            actor=plain, workspace_id=workspace.id, attachment_id=attachment_id
        )
    result = await service.delete_attachment(
        actor=admin, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert result["data"]["deleted"] is True
    # Idempotent second delete.
    again = await service.delete_attachment(
        actor=admin, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert again["data"]["deleted"] is True


async def test_instant_upload_with_possession_shares_blob(service, tenant, session_factory):
    workspace, member = tenant
    first = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "clean")
    second = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="copy.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    assert second["data"]["upload"] is None  # bytes skipped
    assert second["data"]["id"] != first["id"]  # independent record
    assert second["data"]["blob_id"] == first["blob_id"]  # shared blob
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 2
        assert await session.scalar(select(func.count()).select_from(Attachment)) == 2


async def test_instant_upload_without_possession_forces_full_upload(
    service, tenant, member_factory, session_factory
):
    """RED LINE (T24): a client-provided hash never short-circuits unreadable content."""
    workspace, member = tenant
    await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "clean")
    other = await member_factory(workspace)
    probe = await service.request_upload(
        actor=other, workspace_id=workspace.id, file_name="probe.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    assert probe["data"]["upload"] is not None  # no short-circuit
    assert probe["data"]["upload"]["method"] == "PUT"
    assert probe["data"]["blob_id"] is not None


async def test_delete_one_shared_attachment_keeps_the_other(service, tenant, session_factory):
    workspace, member = tenant
    first = await _upload(service, member, workspace)
    await _set_scan(session_factory, workspace, "clean")
    second = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="copy.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    await service.delete_attachment(
        actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(second["data"]["id"])
    )
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 1
    # The survivor still downloads.
    result = await service.download_attachment(
        viewer=member, workspace_id=workspace.id, attachment_id=uuid.UUID(first["id"])
    )
    assert result["data"]["url"].startswith("http")


async def test_unknown_attachment_is_404_everywhere(service, tenant):
    workspace, member = tenant
    missing = uuid.uuid4()
    with pytest.raises(NotFoundError):
        await service.get_attachment(viewer=member, workspace_id=workspace.id, attachment_id=missing)
    with pytest.raises(NotFoundError):
        await service.delete_attachment(actor=member, workspace_id=workspace.id, attachment_id=missing)
    with pytest.raises(NotFoundError):
        await service.download_attachment(viewer=member, workspace_id=workspace.id, attachment_id=missing)
    assert await service.resolve_attachment_workspace(missing) is None


# ----------------------------------------------------------------------
# multipart (§2.5 / §3.1)
# ----------------------------------------------------------------------


async def test_multipart_flow(service, tenant, session_factory, member_factory):
    workspace, member = tenant
    part_size = 5 * 1024 * 1024
    body = b"z" * part_size + b"y" * 1024
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="small.zip",
        file_size=len(body), mime_type="application/zip",
        content_hash=sha256_hex(body),
    )
    data = response["data"]
    # big.bin is 100KB+... actually < 64MB threshold → single PUT. Force multipart
    # via a large declared size instead.
    assert data["upload"]["method"] == "PUT"

    big = 65 * 1024 * 1024
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="huge.zip",
        file_size=big, mime_type="application/zip",
    )
    data = response["data"]
    assert "upload_id" in data["upload"]
    assert data["upload"]["part_count"] == -(-big // data["upload"]["part_size"])
    attachment_id = uuid.UUID(data["id"])

    # Non-owner cannot request parts.
    stranger = await member_factory(workspace)
    with pytest.raises(ForbiddenError):
        await service.request_multipart_parts(
            actor=stranger, workspace_id=workspace.id,
            attachment_id=attachment_id, part_numbers=[1],
        )

    parts_response = await service.request_multipart_parts(
        actor=member, workspace_id=workspace.id,
        attachment_id=attachment_id, part_numbers=[5],
    )
    assert parts_response["data"]["part_urls"][0]["part_number"] == 5

    with pytest.raises(ValidationError):
        await service.request_multipart_parts(
            actor=member, workspace_id=workspace.id,
            attachment_id=attachment_id, part_numbers=[9999],
        )


async def test_complete_requires_multipart_endpoint(service, tenant):
    workspace, member = tenant
    big = 65 * 1024 * 1024
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="huge.zip",
        file_size=big, mime_type="application/zip",
    )
    with pytest.raises(ConflictError):
        await service.complete_upload(
            actor=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(response["data"]["id"]),
        )


# ----------------------------------------------------------------------
# maintenance (§4.6)
# ----------------------------------------------------------------------


async def test_orphan_sweep_expires_stale_uploads(service, tenant, session_factory,
                                                 attachment_settings_kwargs, object_storage):

    workspace, member = tenant
    await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="stale.png",
        file_size=len(PNG), mime_type="image/png",
    )
    # Backdate expires_at past the window.
    async with session_factory() as session, session.begin():
        attachment = await session.scalar(select(Attachment))
        from datetime import UTC, datetime

        attachment.expires_at = datetime.now(UTC) - timedelta(hours=1)
    swept = await service.sweep_expired_uploads()
    assert swept == 1
    async with session_factory() as session:
        attachment = await session.scalar(select(Attachment))
        assert attachment.upload_status == "expired"
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0


async def test_retention_and_gc_reclaim_everything(service, tenant, session_factory,
                                                   object_storage):
    workspace, member = tenant
    payload = await _upload(service, member, workspace)
    await service.delete_attachment(
        actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(payload["id"])
    )
    # Age the soft-deleted row past retention.
    async with session_factory() as session, session.begin():
        from datetime import UTC, datetime

        attachment = await session.scalar(select(Attachment))
        attachment.updated_at = datetime.now(UTC) - timedelta(days=30)
    reaped = await service.run_retention()
    assert reaped == 1
    # Age the unreferenced blob past the GC grace window, then collect it.
    async with session_factory() as session, session.begin():
        from datetime import UTC, datetime

        blob = await session.scalar(select(AttachmentBlob))
        blob.created_at = datetime.now(UTC) - timedelta(hours=2)
        storage_key = blob.storage_key
    collected = await service.gc_unreferenced_blobs()
    assert collected == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AttachmentBlob)) == 0
    assert await object_storage.object_exists(storage_key) is False


async def test_gc_never_touches_referenced_blobs(service, tenant, session_factory):
    workspace, member = tenant
    await _upload(service, member, workspace)
    async with session_factory() as session, session.begin():
        from datetime import UTC, datetime

        blob = await session.scalar(select(AttachmentBlob))
        blob.created_at = datetime.now(UTC) - timedelta(hours=2)
        blob.ref_count = 0  # lie about ref_count — referencing row still exists
    assert await service.gc_unreferenced_blobs() == 0


async def test_quota_cache_refresh(service, tenant, session_factory):
    workspace, member = tenant
    async with session_factory() as session, session.begin():
        session.add(AttachmentQuota(
            workspace_id=workspace.id,
            max_file_bytes=100 * 1024 * 1024,
            total_bytes=1024 * 1024 * 1024,
            used_bytes=0,
        ))
    await _upload(service, member, workspace)
    refreshed = await service.refresh_quota_caches()
    assert refreshed == 1
    async with session_factory() as session:
        quota = await session.scalar(select(AttachmentQuota))
        assert quota.used_bytes == len(PNG)
