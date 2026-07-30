"""AttachmentService unit tests — real PG + real MinIO (attachment.md §3/§4/§5)."""

from __future__ import annotations

import asyncio
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


async def test_oversized_put_rejected_by_signature_binding_then_complete_fails(
    service, tenant, object_storage
):
    """F4 (§5.4): the signed PUT binds the declared Content-Length, so object
    storage rejects a wrong-size upload (SignatureDoesNotMatch) — the bytes
    never land; complete then fails hash_mismatch on the missing object."""
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    url = response["data"]["upload"]["url"]
    async with httpx.AsyncClient() as client:
        oversized = await client.put(url, content=PNG + b"extra",
                                     headers={"Content-Type": "image/png"})
    assert oversized.status_code == 403  # storage-side size binding rejects
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


async def test_resolve_host_workspace_per_linked_type(service, tenant, session_factory):
    """link_to tenant resolution (§2.7): issue/comment go through the narrow
    SECURITY DEFINER lookups (app-role RLS is fail-closed without the GUC);
    chat_message resolves to None until its module ships; unknown types are
    None so link-time validation rejects them."""
    workspace, _member = tenant
    issue = await seed_issue(session_factory, workspace)
    async with session_factory() as session:
        assert await service.resolve_host_workspace(session, "issue", issue.id) == workspace.id
        assert await service.resolve_host_workspace(session, "issue", uuid.uuid4()) is None
        assert await service.resolve_host_workspace(session, "chat_message", uuid.uuid4()) is None
        assert await service.resolve_host_workspace(session, "bogus", uuid.uuid4()) is None


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


async def test_chat_message_read_requires_session_ownership(service, tenant, member_factory, session_factory):
    """读侧 L2 属主校验(MES-111 批次③ HIGH-1 回归):链接到 chat_message 的附件,
    仅其私聊会话属主可读;同空间非属主成员持 UUID 亦 404(镜像写侧 _assert_host_write)。"""
    from mesh.db.models.agent import Agent
    from mesh.db.models.chat import ChatMessage, ChatSession
    from mesh.db.models.user import User
    from mesh.db.tenant import set_tenant_context

    workspace, owner = tenant
    stranger = await member_factory(workspace)

    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        agent_owner = User(email=f"ao-{uuid.uuid4().hex[:8]}@x.io", display_name="AO")
        session.add(agent_owner)
        await session.flush()
        agent = Agent(workspace_id=workspace.id, name="bot", owner_user_id=agent_owner.id)
        session.add(agent)
        await session.flush()
        chat_session = ChatSession(
            workspace_id=workspace.id, owner_id=owner.id, agent_id=agent.id
        )
        session.add(chat_session)
        await session.flush()
        message = ChatMessage(
            workspace_id=workspace.id, session_id=chat_session.id, role="user", content="hi"
        )
        session.add(message)
        await session.flush()
        message_id = message.id

    # owner 上传(未预关联),随后链接到其私聊会话的消息。
    payload = await _upload(service, owner, workspace)
    attachment_id = uuid.UUID(payload["id"])
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace.id)
        session.add(AttachmentLink(
            workspace_id=workspace.id, attachment_id=attachment_id,
            linked_type="chat_message", linked_id=message_id,
        ))

    # 非属主成员越权读取 → 404。
    with pytest.raises(NotFoundError):
        await service.get_attachment(
            viewer=stranger, workspace_id=workspace.id, attachment_id=attachment_id,
        )
    # 属主本人可读。
    visible = await service.get_attachment(
        viewer=owner, workspace_id=workspace.id, attachment_id=attachment_id,
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


# ----------------------------------------------------------------------
# F1 — sweep pre-race guard: in-flight uploads are invisible to the sweep
# ----------------------------------------------------------------------


async def test_sweep_skips_blobs_without_completed_references(
    session_factory, object_storage, service, workspace_factory, member_factory
):
    """request→(sweep tick)→PUT→complete must still yield a downloadable file.

    The old claim set grabbed every pending blob — a sweep tick between
    upload-request and complete terminally errored the blob (OBJECT_MISSING)
    and permanently bricked the upload. The claim set now requires a live
    completed attachment referencing the blob.
    """
    from mesh.attachment.processing import claim_pending_blobs, process_blob

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    settings = service._settings

    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="slow.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    storage_key = None
    async with session_factory() as session:
        from mesh.db.models.attachment import Attachment

        row = await session.get(Attachment, attachment_id)
        from mesh.db.models.attachment import AttachmentBlob

        blob = await session.get(AttachmentBlob, row.blob_id)
        storage_key = blob.storage_key
        assert blob.scan_status == "pending"

    # Sweep tick BEFORE the bytes arrive: nothing claimable.
    async with session_factory() as session, session.begin():
        claimed = await claim_pending_blobs(session, batch=10)
        assert claimed == []

    # Client finishes normally afterwards.
    async with httpx.AsyncClient() as client:
        put = await client.put(response["data"]["upload"]["url"], content=PNG,
                               headers={"Content-Type": "image/png"})
        assert put.status_code == 200
    await service.complete_upload(actor=member, workspace_id=workspace.id, attachment_id=attachment_id)

    # Now the blob is claimable and processes to clean; download opens.
    async with session_factory() as session, session.begin():
        claimed = await claim_pending_blobs(session, batch=10)
        assert len(claimed) == 1
        await process_blob(session, claimed[0], storage=object_storage, settings=settings)
    download = await service.download_attachment(
        viewer=member, workspace_id=workspace.id, attachment_id=attachment_id
    )
    async with httpx.AsyncClient() as client:
        got = await client.get(download["data"]["url"])
        assert got.status_code == 200
        assert got.content == PNG
    assert storage_key is not None


async def test_complete_resets_errored_blob_for_rescan(
    session_factory, object_storage, service, workspace_factory, member_factory
):
    """§3.3 step 3: complete explicitly (re-)places the blob in quarantine.

    Self-heal for any blob left terminally errored (e.g. by a sweep before
    the F1 guard, or an earlier failed scan pass): complete resets
    scan_status to pending and re-emits scan_requested, so a perfect upload
    is never permanently 403.
    """
    from mesh.attachment.processing import claim_pending_blobs, process_blob

    workspace = await workspace_factory()
    member = await member_factory(workspace)

    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="heal.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    # Simulate a stale terminal error state on the blob.
    async with session_factory() as session, session.begin():
        from mesh.db.models.attachment import Attachment, AttachmentBlob

        row = await session.get(Attachment, attachment_id)
        blob = await session.get(AttachmentBlob, row.blob_id)
        blob.scan_status = "error"
        blob.scan_detail = {"error_code": "OBJECT_MISSING"}
        blob_id = blob.id

    async with httpx.AsyncClient() as client:
        put = await client.put(response["data"]["upload"]["url"], content=PNG,
                               headers={"Content-Type": "image/png"})
        assert put.status_code == 200
    done = await service.complete_upload(
        actor=member, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert done["data"]["scan_status"] == "pending"  # reset, not the stale error
    async with session_factory() as session:
        from mesh.db.models.attachment import AttachmentBlob

        blob = await session.get(AttachmentBlob, blob_id)
        assert blob.scan_status == "pending"
        assert (blob.scan_detail or {}).get("error_code") is None  # stale state cleared
    # Reclaim + process → clean → download opens.
    async with session_factory() as session, session.begin():
        claimed = await claim_pending_blobs(session, batch=10)
        assert len(claimed) == 1
        await process_blob(session, claimed[0], storage=object_storage, settings=service._settings)
    download = await service.download_attachment(
        viewer=member, workspace_id=workspace.id, attachment_id=attachment_id
    )
    assert download["data"]["url"].startswith("http")


# ----------------------------------------------------------------------
# F2 — complete failure releases ref_count and the residual object
# ----------------------------------------------------------------------


async def test_complete_size_mismatch_releases_ref_count_and_object(
    session_factory, object_storage, service, workspace_factory, member_factory
):
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="bad.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    async with session_factory() as session:
        from mesh.db.models.attachment import Attachment, AttachmentBlob

        row = await session.get(Attachment, attachment_id)
        blob = await session.get(AttachmentBlob, row.blob_id)
        storage_key = blob.storage_key
        assert blob.ref_count == 1

    # PUT a DIFFERENT size than declared → complete HEAD check fails.
    async with httpx.AsyncClient() as client:
        await client.put(
            response["data"]["upload"]["url"],
            content=PNG + b"extra-bytes",
            headers={"Content-Type": "image/png"},
        )
    # The size-bound signature rejects the oversized PUT at the storage side
    # (§5.4 / F4) — either way the object must not persist as referenced.
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=attachment_id
        )
    assert excinfo.value.code == "hash_mismatch"
    async with session_factory() as session:
        from mesh.db.models.attachment import Attachment, AttachmentBlob

        row = await session.get(Attachment, attachment_id)
        blob = await session.get(AttachmentBlob, row.blob_id)
        assert row.upload_status == "failed"
        assert blob.ref_count == 0  # F2: released, no permanent leak
    assert await object_storage.object_exists(storage_key) is False  # residual object deleted


async def test_failed_upload_retention_then_gc_reclaims_blob_row(
    session_factory, object_storage, service, workspace_factory, member_factory
):
    """Full leak chain regression: failed complete → retention hard-deletes
    the row → GC reclaims the blob row (ref_count=0 path)."""
    from datetime import UTC, datetime, timedelta

    workspace = await workspace_factory()
    member = await member_factory(workspace)
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="leak.png",
        file_size=len(PNG), mime_type="image/png", content_hash=sha256_hex(PNG),
    )
    attachment_id = uuid.UUID(response["data"]["id"])
    # Fail it: complete with the object missing.
    with pytest.raises(BusinessRuleError):
        await service.complete_upload(
            actor=member, workspace_id=workspace.id, attachment_id=attachment_id
        )
    # Age the failed row past retention.
    async with session_factory() as session, session.begin():
        from mesh.db.models.attachment import Attachment

        row = await session.get(Attachment, attachment_id)
        row.updated_at = datetime.now(UTC) - timedelta(days=30)
    reaped = await service.run_retention()
    assert reaped == 1
    async with session_factory() as session:
        from mesh.db.models.attachment import Attachment, AttachmentBlob

        assert await session.get(Attachment, attachment_id) is None
        blob_count = (await session.execute(
            select(func.count()).select_from(AttachmentBlob)
        )).scalar()
        assert blob_count == 1  # blob row still present until GC
    # Age the blob past the GC grace window → collected.
    async with session_factory() as session, session.begin():
        from mesh.db.models.attachment import AttachmentBlob

        blob = await session.scalar(select(AttachmentBlob))
        blob.created_at = datetime.now(UTC) - timedelta(hours=2)
    collected = await service.gc_unreferenced_blobs()
    assert collected == 1
    async with session_factory() as session:
        from mesh.db.models.attachment import AttachmentBlob

        blob_count = (await session.execute(
            select(func.count()).select_from(AttachmentBlob)
        )).scalar()
        assert blob_count == 0


# ----------------------------------------------------------------------
# F6 — Idempotency-Key scoped per uploader + concurrent replay
# ----------------------------------------------------------------------


async def test_idempotency_key_is_scoped_per_uploader(
    session_factory, service, workspace_factory, member_factory
):
    """Member B replaying member A's client key gets B's own record, never
    A's (info-leak fix); A replaying its own key replays the first record."""
    workspace = await workspace_factory()
    alice = await member_factory(workspace)
    bob = await member_factory(workspace)

    first = await service.request_upload(
        actor=alice, workspace_id=workspace.id, file_name="a.png",
        file_size=len(PNG), mime_type="image/png", idempotency_key="shared-key-1",
    )
    # Alice replay → same record.
    replay = await service.request_upload(
        actor=alice, workspace_id=workspace.id, file_name="a.png",
        file_size=len(PNG), mime_type="image/png", idempotency_key="shared-key-1",
    )
    assert replay["data"]["id"] == first["data"]["id"]
    # Bob with the SAME key → a distinct record of his own (no leak of Alice's).
    bob_record = await service.request_upload(
        actor=bob, workspace_id=workspace.id, file_name="b.png",
        file_size=len(PNG), mime_type="image/png", idempotency_key="shared-key-1",
    )
    assert bob_record["data"]["id"] != first["data"]["id"]
    assert bob_record["data"]["uploader"]["id"] == str(bob.id)


# ----------------------------------------------------------------------
# F7 — quota pre-check serialized without a quota row (advisory lock)
# ----------------------------------------------------------------------


async def test_quota_default_path_serializes_concurrent_requests(
    session_factory, object_storage, attachment_settings_kwargs,
    workspace_factory, member_factory
):
    """Two concurrent oversized requests against the deployment default quota
    (no quota row) must not both pass: the advisory lock serializes the
    pre-check so exactly one gets quota_exceeded."""

    tight_kwargs = {**attachment_settings_kwargs, "attachment_total_bytes": 1000}
    tight_service = build_service(session_factory, object_storage, tight_kwargs)
    workspace = await workspace_factory()
    member = await member_factory(workspace)

    async def _request(name: str):
        try:
            await tight_service.request_upload(
                actor=member, workspace_id=workspace.id, file_name=name,
                file_size=600, mime_type="image/png",
            )
            return "ok"
        except LockedError as exc:
            assert exc.code == "quota_exceeded"
            return "quota"

    results = await asyncio.gather(_request("x1.png"), _request("x2.png"))
    assert sorted(results) == ["ok", "quota"]  # exactly one exceeds the total


# ----------------------------------------------------------------------
# complete HEAD-check failure branches + multipart lifecycle coverage
# ----------------------------------------------------------------------


async def test_complete_head_size_mismatch_releases_ref_count(
    session_factory, object_storage, service, tenant, monkeypatch
):
    """complete HEAD returns a different size → failed + ref_count released
    (F2 failure branch, forced via a lying HEAD since §5.4 signature binding
    rejects mismatched PUTs before they land)."""
    workspace, member = tenant
    response = await service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="shot.png",
        file_size=len(PNG), mime_type="image/png",
    )
    await _put_signed(response["data"]["upload"]["url"], PNG, "image/png")

    real_head = object_storage.head_size

    async def _lying_head(key):
        size = await real_head(key)
        return None if size is None else size + 99

    monkeypatch.setattr(object_storage, "head_size", _lying_head)
    with pytest.raises(BusinessRuleError) as excinfo:
        await service.complete_upload(
            actor=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(response["data"]["id"]),
        )
    assert excinfo.value.code == "hash_mismatch"
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0  # F2 released


async def _multipart_request(session_factory, object_storage, attachment_settings_kwargs, member, workspace):
    """Request a multipart upload (threshold lowered under the PNG size)."""
    mp_service = build_service(session_factory, object_storage,
                               {**attachment_settings_kwargs, "attachment_multipart_threshold": 100})
    response = await mp_service.request_upload(
        actor=member, workspace_id=workspace.id, file_name="big.png",
        file_size=len(PNG), mime_type="image/png",
    )
    return mp_service, response["data"]


async def test_multipart_complete_size_failure_releases_ref_count(
    session_factory, object_storage, attachment_settings_kwargs,
    workspace_factory, member_factory, monkeypatch
):
    """Merged object fails the HEAD size check → failed + ref_count released."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    mp_service, data = await _multipart_request(
        session_factory, object_storage, attachment_settings_kwargs, member, workspace
    )
    assert "upload_id" in data["upload"]
    async with httpx.AsyncClient() as client:
        part = await client.put(data["upload"]["part_urls"][0]["url"], content=PNG)
        assert part.status_code == 200
        etag = part.headers["ETag"]

    real_head = mp_service._storage.head_size

    async def _lying_head(key):
        size = await real_head(key)
        return None if size is None else size + 1

    monkeypatch.setattr(mp_service._storage, "head_size", _lying_head)
    with pytest.raises(BusinessRuleError) as excinfo:
        await mp_service.complete_multipart(
            actor=member, workspace_id=workspace.id,
            attachment_id=uuid.UUID(data["id"]),
            parts=[{"part_number": 1, "etag": etag}],
        )
    assert excinfo.value.code == "hash_mismatch"
    async with session_factory() as session:
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0


async def test_multipart_abort_cleans_session_and_object(
    session_factory, object_storage, attachment_settings_kwargs, workspace_factory, member_factory
):
    """Abort on a multipart upload deletes the session row and releases ref."""
    workspace = await workspace_factory()
    member = await member_factory(workspace)
    mp_service, data = await _multipart_request(
        session_factory, object_storage, attachment_settings_kwargs, member, workspace
    )
    assert "upload_id" in data["upload"]
    result = await mp_service.abort_upload(
        actor=member, workspace_id=workspace.id, attachment_id=uuid.UUID(data["id"])
    )
    assert result["data"]["upload_status"] == "failed"
    async with session_factory() as session:
        from mesh.db.models.attachment import UploadSession

        assert await session.scalar(select(func.count()).select_from(UploadSession)) == 0
        blob = await session.scalar(select(AttachmentBlob))
        assert blob.ref_count == 0


async def test_comment_link_paths_with_table_present(monkeypatch):
    """Host-authorization branches for comment/chat_message links (the tables
    land with MES-58/chat; cover the code paths with a stubbed registry)."""
    from mesh.attachment import service as service_module

    class _FakeMeta:
        tables = {"comments": object(), "chat_messages": object()}

    class _FakeBase:
        metadata = _FakeMeta()

    monkeypatch.setattr(service_module, "Base", _FakeBase)

    ws_id = uuid.uuid4()
    linked_id = uuid.uuid4()
    svc = service_module.AttachmentService.__new__(service_module.AttachmentService)

    class _StubSession:
        def __init__(self, exists: bool):
            self._exists = exists

        async def scalar(self, *args, **kwargs):
            return linked_id if self._exists else None

        async def execute(self, *args, **kwargs):
            class _Result:
                def __init__(self, value):
                    self._value = value

                def scalar(self):
                    return self._value

            return _Result(linked_id if self._exists else None)

    # resolve_host_workspace: table present → existence query result.
    assert await svc.resolve_host_workspace(_StubSession(True), "comment", linked_id) == linked_id
    assert await svc.resolve_host_workspace(_StubSession(False), "chat_message", linked_id) is None

    import types

    member = types.SimpleNamespace(role="member", id=uuid.uuid4())

    # _can_read_host: comment = 空间级可见(exists → True / missing → False);
    # chat_message = 读侧 L2 属主校验(MES-111 批次③ HIGH-1)——存在且会话属主匹配 → True,
    # 属主不匹配(含缺失) → False,镜像写侧 _assert_host_write。
    class _OwnerSession(_StubSession):
        """existence 查询返回 linked_id;属主查询返回指定 owner_id。"""

        def __init__(self, exists: bool, owner_id):
            super().__init__(exists)
            self._owner_id = owner_id

        async def scalar(self, *args, **kwargs):
            sql = str(args[0]) if args else ""
            if "owner_id" in sql:
                return self._owner_id if self._exists else None
            return linked_id if self._exists else None

    assert await svc._can_read_host(_StubSession(True), member, ws_id, "comment", linked_id) is True
    assert await svc._can_read_host(_StubSession(False), member, ws_id, "comment", linked_id) is False
    assert (
        await svc._can_read_host(_OwnerSession(True, member.id), member, ws_id, "chat_message", linked_id)
        is True
    )
    # 同空间非属主 → 读门拒绝(越权下载他人私聊附件被堵)。
    assert (
        await svc._can_read_host(_OwnerSession(True, uuid.uuid4()), member, ws_id, "chat_message", linked_id)
        is False
    )
    assert (
        await svc._can_read_host(_OwnerSession(False, member.id), member, ws_id, "chat_message", linked_id)
        is False
    )

    # _assert_host_write: exists passes; missing → NotFoundError.
    await svc._assert_host_write(_StubSession(True), member, ws_id,
                                 {"type": "comment", "id": linked_id})
    with pytest.raises(NotFoundError):
        await svc._assert_host_write(_StubSession(False), member, ws_id,
                                     {"type": "chat_message", "id": linked_id})
