"""Attachment service: three-stage direct upload, quarantine gate, dedup (§3/§4).

Orchestration rules this module enforces (attachment.md, 必须采纳):

- **Three-stage direct upload** — upload-request signs a short-lived PUT, the
  client streams bytes straight to object storage, ``complete`` HEAD-checks
  and hands the object to the quarantine pipeline. Bytes never transit here.
- **Two orthogonal state machines** — ``upload_status`` (session level) and
  blob ``scan_status`` (content level); ``completed`` ≠ usable.
- **Visibility gate (CRITICAL)** — download/preview/thumbnail only when the
  referenced blob is ``clean``/``skipped``; ``pending``/``error`` → 403
  ``scan_pending``; ``infected`` → 403 ``scan_infected`` + critical audit.
- **Blob-truth dedup (T24)** — instant upload requires POSSESSION (the caller
  can already read the blob); without it, a client-supplied hash never
  short-circuits (content probing / unauthorized reuse, RED LINE). Dedup only
  SHARES blobs — attachments rows/links are always independent, ``ref_count``
  moves atomically in the same transaction as the referencing row.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete, func, or_, select, text, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, decode_cursor, encode_cursor
from mesh.attachment.mime import IMAGE_MIMES
from mesh.attachment.policy import UploadLimits, validate_upload_request
from mesh.attachment.storage import (
    STORAGE_PROVIDER,
    ObjectStorage,
    generate_storage_key,
)
from mesh.auth.audit import write_audit
from mesh.config import Settings
from mesh.db.base import Base
from mesh.db.constraints import violates
from mesh.db.models.attachment import (
    Attachment,
    AttachmentBlob,
    AttachmentLink,
    AttachmentQuota,
    UploadSession,
)
from mesh.db.models.issue import Issue
from mesh.db.models.member import Member, MemberProjectAccess
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import (
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    LockedError,
    NotFoundError,
    UnsupportedMediaTypeError,  # noqa: F401 — re-exported for routes
    ValidationError,
)
from mesh.events.vocab import OUTBOX_INTERNAL_EVENT_TYPES
from mesh.outbox.service import emit_event, emit_realtime

logger = logging.getLogger("mesh.attachment.service")

SCAN_GATE_OPEN = frozenset({"clean", "skipped"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STAGING_HASH_PREFIX = "staging:"

ATTACHMENT_PROCESSED = "attachment.processed"
ATTACHMENT_DELETED = "attachment.deleted"
SCAN_REQUESTED_EVENT_TYPE = "attachment.scan_requested"
assert SCAN_REQUESTED_EVENT_TYPE in OUTBOX_INTERNAL_EVENT_TYPES

_ATTACHMENT_NOT_FOUND = "attachment not found"
_ISSUE_NOT_FOUND = "issue not found"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def issue_channel(issue_id: uuid.UUID) -> str:
    return f"issue:{issue_id}"


def _content_disposition(mime: str | None, file_name: str, *, is_image: bool) -> str:
    """Inline for safe images; forced attachment for everything else (§3.4)."""
    encoded = quote(file_name, safe="")
    if is_image and mime in IMAGE_MIMES and mime != "image/svg+xml":
        return f"inline; filename*=UTF-8''{encoded}"
    return f"attachment; filename*=UTF-8''{encoded}"


class AttachmentService:
    """Stateless orchestrator; transactions are owned per public method."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        storage: ObjectStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings
        self._storage = storage
        self._clock = clock or _utcnow

    # ------------------------------------------------------------------
    # tenant resolution (workspace-less paths, SECURITY DEFINER, migration 0013)
    # ------------------------------------------------------------------

    async def resolve_attachment_workspace(self, attachment_id: uuid.UUID) -> uuid.UUID | None:
        async with self._factory() as session:
            return await session.scalar(
                text("SELECT mesh_attachment_workspace_id(:id)"), {"id": attachment_id}
            )

    # ------------------------------------------------------------------
    # policy / quota helpers
    # ------------------------------------------------------------------

    async def _limits_for(self, session: AsyncSession, workspace_id: uuid.UUID) -> UploadLimits:
        quota = await session.scalar(
            select(AttachmentQuota).where(AttachmentQuota.workspace_id == workspace_id)
        )
        if quota is None:
            return UploadLimits(
                max_file_bytes=self._settings.attachment_max_file_bytes,
                max_image_bytes=self._settings.attachment_max_image_bytes,
                total_bytes=self._settings.attachment_total_bytes,
                allowed_mimes=frozenset(_default_allowed_mimes()),
            )
        allowed = (
            frozenset(quota.allowed_mimes)
            if quota.allowed_mimes
            else frozenset(_default_allowed_mimes())
        )
        return UploadLimits(
            max_file_bytes=quota.max_file_bytes,
            max_image_bytes=self._settings.attachment_max_image_bytes,
            total_bytes=quota.total_bytes,
            allowed_mimes=allowed,
        )

    async def _used_bytes(self, session: AsyncSession, workspace_id: uuid.UUID) -> int:
        """Real storage usage: distinct blobs with live references (dedup-aware)."""
        used = await session.scalar(
            select(func.coalesce(func.sum(AttachmentBlob.file_size), 0)).where(
                AttachmentBlob.workspace_id == workspace_id,
                AttachmentBlob.ref_count > 0,
            )
        )
        return int(used or 0)

    # ------------------------------------------------------------------
    # host authorization (polymorphic logical FK targets, §2.4/§2.7)
    # ------------------------------------------------------------------

    async def resolve_host_workspace(
        self, session: AsyncSession, linked_type: str, linked_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Workspace of a link target (pre-tenant-context, SECURITY DEFINER)."""
        if linked_type == "issue":
            return await session.scalar(
                text("SELECT mesh_issue_workspace_id(:id)"), {"id": linked_id}
            )
        # comment / chat_message tables land with their own modules; existence
        # is validated per linked_type at link time (service layer, §2.7).
        if linked_type in {"comment", "chat_message"}:
            table = "comments" if linked_type == "comment" else "chat_messages"
            if table not in Base.metadata.tables:
                return None
            result = await session.execute(
                text(f"SELECT workspace_id FROM {table} WHERE id = :id"), {"id": linked_id}
            )
            return result.scalar()
        return None

    async def _assert_host_write(
        self,
        session: AsyncSession,
        member: Member,
        workspace_id: uuid.UUID,
        link_to: dict[str, Any],
    ) -> None:
        linked_type = link_to["type"]
        linked_id = link_to["id"]
        if linked_type == "issue":
            from mesh.auth.rbac import assert_guest_project_visible, role_satisfies

            issue = await session.scalar(
                select(Issue).where(
                    Issue.id == linked_id,
                    Issue.workspace_id == workspace_id,
                    Issue.deleted_at.is_(None),
                )
            )
            if issue is None:
                raise NotFoundError(_ISSUE_NOT_FOUND)
            if not role_satisfies(member.role, "issue:write"):
                raise ForbiddenError("insufficient role to attach files to this issue")
            if issue.project_id is not None:
                await assert_guest_project_visible(
                    session, member=member, project_id=issue.project_id
                )
            return
        if linked_type in {"comment", "chat_message"}:
            table = "comments" if linked_type == "comment" else "chat_messages"
            if table not in Base.metadata.tables:
                # The owning module has not landed in this deployment yet.
                raise NotFoundError(f"{linked_type} not found")
            exists = await session.scalar(
                text(f"SELECT 1 FROM {table} WHERE id = :id AND workspace_id = :ws"),
                {"id": linked_id, "ws": workspace_id},
            )
            if exists is None:
                raise NotFoundError(f"{linked_type} not found")
            return
        raise ValidationError(
            "invalid link_to.type", details={"linked_type": str(linked_type)[:32]}
        )

    async def _can_read_host(
        self,
        session: AsyncSession,
        member: Member,
        workspace_id: uuid.UUID,
        linked_type: str,
        linked_id: uuid.UUID,
    ) -> bool:
        if linked_type == "issue":
            from mesh.auth.rbac import role_satisfies

            issue = await session.scalar(
                select(Issue).where(
                    Issue.id == linked_id,
                    Issue.workspace_id == workspace_id,
                    Issue.deleted_at.is_(None),
                )
            )
            if issue is None:
                return False
            if not role_satisfies(member.role, "issue:read"):
                return False
            if member.role == "guest" and issue.project_id is not None:
                granted = await session.scalar(
                    select(MemberProjectAccess.id).where(
                        MemberProjectAccess.workspace_id == workspace_id,
                        MemberProjectAccess.member_id == member.id,
                        MemberProjectAccess.project_id == issue.project_id,
                    )
                )
                return granted is not None
            return True
        if linked_type in {"comment", "chat_message"}:
            table = "comments" if linked_type == "comment" else "chat_messages"
            if table not in Base.metadata.tables:
                return False
            exists = await session.scalar(
                text(f"SELECT 1 FROM {table} WHERE id = :id AND workspace_id = :ws"),
                {"id": linked_id, "ws": workspace_id},
            )
            return exists is not None
        return False

    async def _assert_attachment_read(
        self,
        session: AsyncSession,
        member: Member,
        workspace_id: uuid.UUID,
        attachment: Attachment,
    ) -> list[AttachmentLink]:
        """Read gate: uploader OR host-read on any link (§3.0 / §4.6 possession)."""
        links = (
            await session.scalars(
                select(AttachmentLink).where(
                    AttachmentLink.workspace_id == workspace_id,
                    AttachmentLink.attachment_id == attachment.id,
                )
            )
        ).all()
        if attachment.uploader_id == member.id:
            return list(links)
        for link in links:
            if await self._can_read_host(
                session, member, workspace_id, link.linked_type, link.linked_id
            ):
                return list(links)
        # Uniform 404 — invisible and missing are indistinguishable (§5.3).
        raise NotFoundError(_ATTACHMENT_NOT_FOUND)

    async def _caller_can_read_blob(
        self, session: AsyncSession, member: Member, blob_id: uuid.UUID
    ) -> bool:
        """Possession predicate for instant upload (RED LINE, §3.2/§4.6)."""
        attachments = (
            await session.scalars(
                select(Attachment).where(
                    Attachment.blob_id == blob_id,
                    Attachment.deleted_at.is_(None),
                    Attachment.upload_status == "completed",
                )
            )
        ).all()
        for attachment in attachments:
            if attachment.uploader_id == member.id:
                return True
            links = await session.scalars(
                select(AttachmentLink).where(
                    AttachmentLink.attachment_id == attachment.id,
                    AttachmentLink.workspace_id == attachment.workspace_id,
                )
            )
            for link in links:
                if await self._can_read_host(
                    session, member, attachment.workspace_id, link.linked_type, link.linked_id
                ):
                    return True
        return False

    # ------------------------------------------------------------------
    # rendering
    # ------------------------------------------------------------------

    async def _render_attachment(
        self,
        session: AsyncSession,
        attachment: Attachment,
        blob: AttachmentBlob,
        links: list[AttachmentLink],
        member: Member | None = None,
    ) -> dict[str, Any]:
        uploader = {"id": str(attachment.uploader_id), "member_type": None, "display_name": None}
        if member is None:
            member = await session.get(Member, attachment.uploader_id)
        if member is not None:
            uploader["member_type"] = member.member_type
            if member.user_id is not None:
                user = await session.get(User, member.user_id)
                if user is not None:
                    uploader["display_name"] = user.display_name
            elif member.agent_id is not None and "agents" in Base.metadata.tables:
                # agent 产出物来源名(§4.4「来自 <agent> 运行」):agent 模块
                # 合入后按 (workspace_id, agent_id) 取名;表未就位时留空。
                agent_name = await session.scalar(
                    text("SELECT name FROM agents WHERE workspace_id = :ws AND id = :id"),
                    {"ws": attachment.workspace_id, "id": member.agent_id},
                )
                if agent_name is not None:
                    uploader["display_name"] = agent_name
        released = blob.scan_status in SCAN_GATE_OPEN
        return {
            "id": str(attachment.id),
            "blob_id": str(blob.id),
            "file_name": attachment.file_name,
            "file_size": attachment.file_size,
            # Snapshot fields — truth lives on attachment_blobs (§2.3).
            "mime_type": blob.mime_type,
            "extension": blob.extension,
            "is_image": blob.is_image,
            "image_width": blob.image_width,
            "image_height": blob.image_height,
            "scan_status": blob.scan_status,
            "upload_status": attachment.upload_status,
            "uploader": uploader,
            "links": [
                {
                    "type": link.linked_type,
                    "id": str(link.linked_id),
                    "display": link.display,
                    "position": link.position,
                }
                for link in links
            ],
            "thumbnail_url": (
                f"/api/v1/attachments/{attachment.id}/thumbnail?size=md"
                if blob.is_image and released
                else None
            ),
            "download_url": f"/api/v1/attachments/{attachment.id}/download",
            "created_at": _iso(attachment.created_at),
            "updated_at": _iso(attachment.updated_at),
        }

    # ------------------------------------------------------------------
    # upload-request (§3.2)
    # ------------------------------------------------------------------

    async def request_upload(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        file_name: str,
        file_size: int,
        mime_type: str,
        content_hash: str | None = None,
        link_to: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        declared = self._normalize_hash(content_hash)
        now = self._now()
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)

            # Idempotent replay (README §6.5/§6.14): duplicate key → first record.
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(Attachment).where(
                        Attachment.workspace_id == workspace_id,
                        Attachment.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    return await self._render_upload_response(session, existing, workspace_id)

            limits = await self._limits_for(session, workspace_id)
            validate_upload_request(
                file_name=file_name, file_size=file_size, mime_type=mime_type, limits=limits
            )

            # Quota pre-check BEFORE any bytes move (§3.6 — fail early).
            await session.scalar(
                select(AttachmentQuota)
                .where(AttachmentQuota.workspace_id == workspace_id)
                .with_for_update()
            )
            used = await self._used_bytes(session, workspace_id)
            if used + file_size > limits.total_bytes:
                raise LockedError(
                    "workspace attachment quota exceeded",
                    code="quota_exceeded",
                    details={"used_bytes": used, "total_bytes": limits.total_bytes},
                )

            if link_to is not None:
                await self._assert_host_write(session, actor, workspace_id, link_to)

            declared_mime = mime_type.split(";")[0].strip().lower()
            expires_at = now + self._settings.attachment_upload_ttl
            multipart = file_size >= self._settings.attachment_multipart_threshold

            # ---- instant upload (秒传) — possession required (RED LINE, T24) ----
            shared_blob: AttachmentBlob | None = None
            if declared is not None:
                candidate = await session.scalar(
                    select(AttachmentBlob).where(
                        AttachmentBlob.workspace_id == workspace_id,
                        AttachmentBlob.content_hash == declared,
                    )
                )
                if candidate is not None and await self._caller_can_read_blob(
                    session, actor, candidate.id
                ):
                    shared_blob = candidate
            if shared_blob is not None:
                attachment = Attachment(
                    workspace_id=workspace_id,
                    uploader_id=actor.id,
                    blob_id=shared_blob.id,
                    file_name=file_name,
                    file_size=file_size,
                    upload_status="completed",
                    idempotency_key=idempotency_key,
                )
                session.add(attachment)
                await session.flush()
                await self._ref_count(session, shared_blob.id, +1)
                if link_to is not None:
                    await self._create_link(session, workspace_id, attachment, link_to)
                if shared_blob.scan_status in SCAN_GATE_OPEN:
                    await self._emit_processed(session, workspace_id, attachment, shared_blob)
                await self._audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="attachment.instant_upload",
                    resource_id=attachment.id,
                    metadata={"blob_id": str(shared_blob.id)},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                return await self._render_upload_response(session, attachment, workspace_id)

            # ---- new upload: provision blob truth row + storage key ----
            blob = await self._provision_blob(
                session,
                workspace_id=workspace_id,
                declared_hash=declared,
                declared_mime=declared_mime,
                file_size=file_size,
            )
            attachment = Attachment(
                workspace_id=workspace_id,
                uploader_id=actor.id,
                blob_id=blob.id,
                file_name=file_name,
                file_size=file_size,
                upload_status="pending",
                expires_at=expires_at,
                idempotency_key=idempotency_key,
            )
            session.add(attachment)
            await session.flush()
            await self._ref_count(session, blob.id, +1)
            if link_to is not None:
                await self._create_link(session, workspace_id, attachment, link_to)
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="attachment.upload_requested",
                resource_id=attachment.id,
                metadata={"multipart": multipart, "file_size": file_size},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            response = await self._render_upload_response(session, attachment, workspace_id)
            return response

    async def _provision_blob(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        declared_hash: str | None,
        declared_mime: str | None,
        file_size: int,
    ) -> AttachmentBlob:
        """Create the blob truth row; hash collisions fall back to a staging key.

        The content_hash starts as the CLIENT-DECLARED hash (or an opaque
        staging marker) — the quarantine worker recomputes the authoritative
        SHA-256 and post-dedupes (§3.2/§3.3). The declared MIME is stashed in
        ``scan_detail`` so the signed PUT can bind it as Content-Type; the
        worker's magic-byte sniff remains the source of truth.
        """
        hash_for_blob = declared_hash or f"{_STAGING_HASH_PREFIX}{uuid.uuid4().hex}"
        detail: dict[str, Any] = {}
        if declared_hash is not None:
            detail["declared_hash"] = declared_hash
        if declared_mime is not None:
            detail["declared_mime"] = declared_mime
        scan_detail: dict[str, Any] | None = detail or None
        try:
            async with session.begin_nested():
                blob = AttachmentBlob(
                    workspace_id=workspace_id,
                    content_hash=hash_for_blob,
                    storage_provider=STORAGE_PROVIDER,
                    storage_bucket=self._storage.bucket,
                    storage_key=generate_storage_key(workspace_id, hash_for_blob),
                    file_size=file_size,
                    scan_detail=scan_detail,
                )
                session.add(blob)
                await session.flush()
                return blob
        except IntegrityError as exc:
            if not violates(exc, "uq_attachment_blobs_ws_hash"):
                raise
            # Another upload claimed this declared hash; we cannot read it
            # (possession failed above) — stage under an opaque key and let
            # the worker's post-dedup converge on the truth row.
            staging = f"{_STAGING_HASH_PREFIX}{uuid.uuid4().hex}"
            blob = AttachmentBlob(
                workspace_id=workspace_id,
                content_hash=staging,
                storage_provider=STORAGE_PROVIDER,
                storage_bucket=self._storage.bucket,
                storage_key=generate_storage_key(workspace_id, staging),
                file_size=file_size,
                scan_detail=scan_detail,
            )
            session.add(blob)
            await session.flush()
            return blob

    async def _create_link(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        attachment: Attachment,
        link_to: dict[str, Any],
    ) -> AttachmentLink:
        display = link_to.get("display") or (
            "inline"  # screenshots default to inline preview (§4.4)
        )
        link = AttachmentLink(
            workspace_id=workspace_id,
            attachment_id=attachment.id,
            linked_type=link_to["type"],
            linked_id=link_to["id"],
            display=display if display in {"inline", "card"} else "card",
            position=int(link_to.get("position") or 0),
        )
        try:
            async with session.begin_nested():
                session.add(link)
                await session.flush()
                return link
        except IntegrityError as exc:
            if not violates(exc, "uq_attachment_link"):
                raise
            existing = await session.scalar(
                select(AttachmentLink).where(
                    AttachmentLink.attachment_id == attachment.id,
                    AttachmentLink.linked_type == link_to["type"],
                    AttachmentLink.linked_id == link_to["id"],
                )
            )
            if existing is not None:
                return existing
            raise

    async def _ref_count(self, session: AsyncSession, blob_id: uuid.UUID, delta: int) -> None:
        """Atomic ref_count maintenance, same transaction as the referencing row."""
        await session.execute(
            update(AttachmentBlob)
            .where(AttachmentBlob.id == blob_id)
            .values(
                ref_count=AttachmentBlob.ref_count + delta,
                updated_at=self._now(),
            )
        )

    async def _render_upload_response(
        self, session: AsyncSession, attachment: Attachment, workspace_id: uuid.UUID
    ) -> dict[str, Any]:
        blob = await session.get(
            AttachmentBlob, attachment.blob_id
        )
        assert blob is not None
        links = (
            await session.scalars(
                select(AttachmentLink).where(
                    AttachmentLink.attachment_id == attachment.id,
                    AttachmentLink.workspace_id == workspace_id,
                )
            )
        ).all()
        rendered = await self._render_attachment(session, attachment, blob, list(links))
        upload_payload: dict[str, Any] | None = None
        if attachment.upload_status in {"pending", "uploading"}:
            upload_payload = await self._sign_upload(session, attachment, blob)
        rendered["upload"] = upload_payload
        rendered["limits"] = {
            "max_file_bytes": self._settings.attachment_max_file_bytes
        }
        return {"data": rendered}

    async def _sign_upload(
        self, session: AsyncSession, attachment: Attachment, blob: AttachmentBlob
    ) -> dict[str, Any]:
        ttl = int(self._settings.attachment_upload_ttl.total_seconds())
        session_row = await session.scalar(
            select(UploadSession).where(UploadSession.attachment_id == attachment.id)
        )
        declared_mime = (
            (blob.scan_detail or {}).get("declared_mime")
            if blob.scan_detail
            else None
        ) or "application/octet-stream"
        if session_row is None and attachment.file_size >= self._settings.attachment_multipart_threshold:
            upload_id = await self._storage.create_multipart_upload(
                blob.storage_key, content_type=declared_mime
            )
            session_row = UploadSession(
                workspace_id=attachment.workspace_id,
                attachment_id=attachment.id,
                upload_id=upload_id,
                part_size=self._settings.attachment_multipart_part_bytes,
                parts=[],
            )
            session.add(session_row)
            await session.flush()
        if session_row is not None:
            part_count = max(
                1, -(-attachment.file_size // session_row.part_size)
            )  # ceil division
            batch = self._settings.attachment_multipart_part_batch
            part_urls = []
            for part_number in range(1, min(batch, part_count) + 1):
                url = await self._storage.presign_upload_part(
                    blob.storage_key,
                    upload_id=session_row.upload_id,
                    part_number=part_number,
                    expires_in=ttl,
                )
                part_urls.append({"part_number": part_number, "url": url})
            return {
                "upload_id": session_row.upload_id,
                "part_urls": part_urls,
                "part_size": session_row.part_size,
                "part_count": part_count,
                "expires_at": _iso(attachment.expires_at),
            }
        url = await self._storage.presign_put(
            blob.storage_key, content_type=declared_mime, expires_in=ttl
        )
        return {
            "method": "PUT",
            "url": url,
            "headers": {"Content-Type": declared_mime},
            "expires_at": _iso(attachment.expires_at),
        }

    # ------------------------------------------------------------------
    # complete / abort (§3.3)
    # ------------------------------------------------------------------

    async def complete_upload(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        file_size: int | None = None,
        content_hash: str | None = None,  # noqa: ARG002 — worker verifies async
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        failure: str | None = None
        details: dict[str, Any] | None = None
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment, blob = await self._load_owned(session, workspace_id, attachment_id, actor)
            if attachment.upload_status == "completed":
                raise ConflictError("upload already completed")
            if attachment.upload_status not in {"pending", "uploading"}:
                raise ConflictError(
                    "upload cannot be completed",
                    details={"upload_status": attachment.upload_status},
                )

            # Multipart uploads must merge parts first (§3.1).
            multipart_session = await session.scalar(
                select(UploadSession).where(UploadSession.attachment_id == attachment.id)
            )
            if multipart_session is not None:
                raise ConflictError(
                    "multipart upload must be completed via /multipart/{id}/complete"
                )

            # HEAD existence/size check ONLY — MIME sniffing and SHA-256 are the
            # quarantine worker's job (§3.3 CRITICAL: complete ≠ usable).
            size = await self._storage.head_size(blob.storage_key)
            if size is None:
                attachment.upload_status = "failed"
                attachment.updated_at = self._now()
                failure = "uploaded object not found in storage"
            elif size != attachment.file_size:
                attachment.upload_status = "failed"
                attachment.updated_at = self._now()
                failure = "uploaded object size does not match the declared size"
                details = {"expected": attachment.file_size, "actual": size}
            else:
                attachment.upload_status = "completed"
                attachment.expires_at = None
                attachment.updated_at = self._now()
                # Quarantine hand-off: the processing worker claims the blob
                # with SKIP LOCKED (README §2.2 / §6.6 outbox).
                await emit_event(
                    session,
                    workspace_id=workspace_id,
                    event_type=SCAN_REQUESTED_EVENT_TYPE,
                    payload={
                        "attachment_id": str(attachment.id),
                        "blob_id": str(blob.id),
                        "workspace_id": str(workspace_id),
                    },
                    idempotency_key=f"scan-requested:{attachment.id}",
                )
                await self._audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="attachment.completed",
                    resource_id=attachment.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        if failure is not None:
            raise BusinessRuleError(failure, code="hash_mismatch", details=details)
        return await self._render_complete_response(workspace_id, attachment_id)

    async def complete_multipart(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        parts: list[dict[str, Any]],
        content_hash: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        failure: str | None = None
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment, blob = await self._load_owned(session, workspace_id, attachment_id, actor)
            if attachment.upload_status == "completed":
                raise ConflictError("upload already completed")
            if attachment.upload_status not in {"pending", "uploading"}:
                raise ConflictError("upload cannot be completed")
            upload_session = await session.scalar(
                select(UploadSession).where(UploadSession.attachment_id == attachment.id)
            )
            if upload_session is None:
                raise NotFoundError("multipart session not found")
            await self._storage.complete_multipart_upload(
                blob.storage_key, upload_id=upload_session.upload_id, parts=parts
            )
            await session.execute(
                delete(UploadSession).where(UploadSession.id == upload_session.id)
            )
            size = await self._storage.head_size(blob.storage_key)
            if size is None or size != attachment.file_size:
                attachment.upload_status = "failed"
                attachment.updated_at = self._now()
                failure = "merged object failed the size check"
            else:
                attachment.upload_status = "completed"
                attachment.expires_at = None
                attachment.updated_at = self._now()
                await emit_event(
                    session,
                    workspace_id=workspace_id,
                    event_type=SCAN_REQUESTED_EVENT_TYPE,
                    payload={
                        "attachment_id": str(attachment.id),
                        "blob_id": str(blob.id),
                        "workspace_id": str(workspace_id),
                    },
                    idempotency_key=f"scan-requested:{attachment.id}",
                )
                await self._audit(
                    session,
                    workspace_id=workspace_id,
                    actor=actor,
                    action="attachment.completed",
                    resource_id=attachment.id,
                    metadata={"multipart": True},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
        if failure is not None:
            raise BusinessRuleError(failure, code="hash_mismatch")
        return await self._render_complete_response(workspace_id, attachment_id)

    async def request_multipart_parts(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        part_numbers: list[int],
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment, blob = await self._load_owned(session, workspace_id, attachment_id, actor)
            if attachment.upload_status not in {"pending", "uploading"}:
                raise ConflictError(
                    "parts can only be requested for an active upload",
                    details={"upload_status": attachment.upload_status},
                )
            upload_session = await session.scalar(
                select(UploadSession).where(UploadSession.attachment_id == attachment.id)
            )
            if upload_session is None:
                raise NotFoundError("multipart session not found")
            part_count = max(1, -(-attachment.file_size // upload_session.part_size))
            part_urls = []
            known: list[dict[str, Any]] = list(upload_session.parts or [])
            seen = {int(p["part_number"]) for p in known}
            ttl = int(self._settings.attachment_upload_ttl.total_seconds())
            for part_number in part_numbers:
                if part_number < 1 or part_number > part_count:
                    raise ValidationError(
                        "part_number out of range",
                        details={"part_number": part_number, "part_count": part_count},
                    )
                url = await self._storage.presign_upload_part(
                    blob.storage_key,
                    upload_id=upload_session.upload_id,
                    part_number=part_number,
                    expires_in=ttl,
                )
                part_urls.append({"part_number": part_number, "url": url})
                if part_number not in seen:
                    known.append({"part_number": part_number, "uploaded": False})
                    seen.add(part_number)
            upload_session.parts = known
            upload_session.updated_at = self._now()
            attachment.upload_status = "uploading"
            attachment.updated_at = self._now()
        return {
            "data": {
                "part_urls": part_urls,
                "part_size": upload_session.part_size,
                "part_count": part_count,
                "expires_at": _iso(attachment.expires_at),
            }
        }

    async def abort_upload(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment, blob = await self._load_owned(session, workspace_id, attachment_id, actor)
            if attachment.upload_status not in {"pending", "uploading"}:
                raise ConflictError(
                    "upload is not active",
                    details={"upload_status": attachment.upload_status},
                )
            attachment.upload_status = "failed"
            attachment.expires_at = None
            attachment.updated_at = self._now()
            # The failed record no longer references live content: release the
            # blob so GC can reclaim the object.
            await self._ref_count(session, blob.id, -1)
            upload_session = await session.scalar(
                select(UploadSession).where(UploadSession.attachment_id == attachment.id)
            )
            if upload_session is not None:
                await self._storage.abort_multipart_upload(
                    blob.storage_key, upload_id=upload_session.upload_id
                )
                await session.execute(
                    delete(UploadSession).where(UploadSession.id == upload_session.id)
                )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="attachment.aborted",
                resource_id=attachment.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        await self._storage.delete_object(blob.storage_key)
        return {"data": {"id": str(attachment_id), "upload_status": "failed"}}

    # ------------------------------------------------------------------
    # read / delete / download / thumbnail (§3.4 / §4.6)
    # ------------------------------------------------------------------

    async def get_attachment(
        self, *, viewer: Member, workspace_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment = await self._load_visible(session, workspace_id, attachment_id)
            links = await self._assert_attachment_read(session, viewer, workspace_id, attachment)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            assert blob is not None
            return {"data": await self._render_attachment(session, attachment, blob, links)}

    async def delete_attachment(
        self,
        *,
        actor: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment = await session.scalar(
                select(Attachment).where(
                    Attachment.id == attachment_id,
                    Attachment.workspace_id == workspace_id,
                )
            )
            if attachment is None:
                raise NotFoundError(_ATTACHMENT_NOT_FOUND)
            is_manager = actor.role in {"admin", "owner"}
            if attachment.uploader_id != actor.id and not is_manager:
                raise ForbiddenError("only the uploader or an admin can delete this attachment")
            if attachment.deleted_at is not None:
                return {"data": {"id": str(attachment.id), "deleted": True}}
            attachment.deleted_at = self._now()
            attachment.updated_at = attachment.deleted_at
            await self._ref_count(session, attachment.blob_id, -1)
            links = (
                await session.scalars(
                    select(AttachmentLink).where(
                        AttachmentLink.attachment_id == attachment.id,
                        AttachmentLink.workspace_id == workspace_id,
                    )
                )
            ).all()
            for link in links:
                if link.linked_type == "issue":
                    await emit_realtime(
                        session,
                        workspace_id=workspace_id,
                        channel=issue_channel(link.linked_id),
                        event=ATTACHMENT_DELETED,
                        data={
                            "id": str(attachment.id),
                            "linked_type": "issue",
                            "linked_id": str(link.linked_id),
                            "visibility": "restricted",
                        },
                    )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=actor,
                action="attachment.deleted",
                resource_id=attachment.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return {"data": {"id": str(attachment.id), "deleted": True}}

    async def download_attachment(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment = await self._load_visible(session, workspace_id, attachment_id)
            await self._assert_attachment_read(session, viewer, workspace_id, attachment)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            assert blob is not None
            self._assert_scan_gate(session, blob, actor=viewer, attachment=attachment)
            ttl = int(self._settings.attachment_download_url_ttl.total_seconds())
            url = await self._storage.presign_get(
                blob.storage_key,
                expires_in=ttl,
                content_disposition=_content_disposition(
                    blob.mime_type, attachment.file_name, is_image=blob.is_image
                ),
                content_type=blob.mime_type,
            )
            await self._audit(
                session,
                workspace_id=workspace_id,
                actor=viewer,
                action="attachment.downloaded",
                resource_id=attachment.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return {
            "data": {
                "url": url,
                "file_name": attachment.file_name,
                "expires_at": _iso(self._now() + self._settings.attachment_download_url_ttl),
            }
        }

    async def thumbnail_url(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        size: str = "md",
    ) -> dict[str, Any]:
        if size not in {"sm", "md", "lg"}:
            raise ValidationError("invalid thumbnail size", details={"size": size[:8]})
        async with self._factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            attachment = await self._load_visible(session, workspace_id, attachment_id)
            await self._assert_attachment_read(session, viewer, workspace_id, attachment)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            assert blob is not None
            self._assert_scan_gate(session, blob, actor=viewer, attachment=attachment)
            if not blob.is_image:
                raise NotFoundError("attachment is not an image")
            keys = blob.thumbnail_keys or {}
            key = keys.get(size)
            if key is None:
                raise NotFoundError(
                    "thumbnail not ready", details={"scan_status": blob.scan_status}
                )
            ttl = int(self._settings.attachment_download_url_ttl.total_seconds())
            url = await self._storage.presign_get(key, expires_in=ttl)
        expires_at = _iso(self._now() + self._settings.attachment_download_url_ttl)
        return {"data": {"url": url, "size": size, "expires_at": expires_at}}

    def _assert_scan_gate(
        self,
        session: AsyncSession,  # noqa: ARG002 — reserved for notification fan-out
        blob: AttachmentBlob,
        *,
        actor: Member,
        attachment: Attachment,
    ) -> None:
        """Visibility gate (CRITICAL, §3.4 / README §9 T14)."""
        if blob.scan_status in SCAN_GATE_OPEN:
            return
        if blob.scan_status == "infected":
            # Security event: permanent refusal + uploader/admin notification
            # via the audit trail (README §6.13 critical tier).
            logger.warning(
                "infected attachment download refused attachment=%s blob=%s workspace=%s",
                attachment.id,
                blob.id,
                blob.workspace_id,
            )
            raise ForbiddenError(
                "attachment blocked by security scan",
                code="scan_infected",
                details={"severity": "critical"},
            )
        # pending / error → quarantine not cleared yet.
        raise ForbiddenError(
            "attachment is still being scanned",
            code="scan_pending",
            details={"scan_status": blob.scan_status},
        )

    # ------------------------------------------------------------------
    # host listings (§3.1)
    # ------------------------------------------------------------------

    async def list_for_host(
        self,
        *,
        viewer: Member,
        workspace_id: uuid.UUID,
        linked_type: str,
        linked_id: uuid.UUID,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        page_limit = min(max(limit or DEFAULT_PAGE_LIMIT, 1), MAX_PAGE_LIMIT)
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            if not await self._can_read_host(
                session, viewer, workspace_id, linked_type, linked_id
            ):
                raise NotFoundError(f"{linked_type} not found")
            stmt = (
                select(Attachment, AttachmentLink)
                .join(
                    AttachmentLink,
                    AttachmentLink.attachment_id == Attachment.id,
                )
                .where(
                    Attachment.workspace_id == workspace_id,
                    AttachmentLink.linked_type == linked_type,
                    AttachmentLink.linked_id == linked_id,
                    Attachment.upload_status == "completed",
                    Attachment.deleted_at.is_(None),
                )
                .order_by(AttachmentLink.position, Attachment.id)
            )
            if cursor is not None:
                position = decode_cursor(cursor)
                stmt = stmt.where(
                    tuple_(AttachmentLink.position, Attachment.id)
                    > (position.sort_value, position.id)
                )
            rows = (await session.execute(stmt.limit(page_limit + 1))).all()
            next_cursor = None
            if len(rows) > page_limit:
                last = rows[page_limit - 1]
                next_cursor = encode_cursor(last[1].position, last[0].id)
                rows = rows[:page_limit]
            items = []
            for attachment, link in rows:
                blob = await session.get(AttachmentBlob, attachment.blob_id)
                assert blob is not None
                items.append(
                    await self._render_attachment(session, attachment, blob, [link])
                )
        return items, next_cursor

    # ------------------------------------------------------------------
    # maintenance (worker-facing, owner role)
    # ------------------------------------------------------------------

    async def sweep_expired_uploads(self, *, batch: int = 100) -> int:
        """Reap incomplete uploads past expires_at (§4.6 孤儿对象清理)."""
        now = self._now()
        expired_keys: list[str] = []
        async with self._factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Attachment)
                    .where(
                        Attachment.upload_status.in_(("pending", "uploading")),
                        Attachment.expires_at.is_not(None),
                        Attachment.expires_at < now,
                    )
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for attachment in rows:
                attachment.upload_status = "expired"
                attachment.updated_at = now
                await self._ref_count(session, attachment.blob_id, -1)
                blob = await session.get(AttachmentBlob, attachment.blob_id)
                if blob is not None:
                    expired_keys.append(blob.storage_key)
                upload_session = await session.scalar(
                    select(UploadSession).where(UploadSession.attachment_id == attachment.id)
                )
                if upload_session is not None:
                    await session.execute(
                        delete(UploadSession).where(UploadSession.id == upload_session.id)
                    )
        for key in expired_keys:  # best-effort, outside the transaction
            await self._storage.delete_object(key)
        return len(expired_keys)

    async def run_retention(self, *, batch: int = 500) -> int:
        """Hard-delete soft-deleted / terminal uploads past the retention window."""
        cutoff = self._now() - self._settings.attachment_soft_delete_retention
        async with self._factory() as session, session.begin():
            ids = (
                await session.scalars(
                    select(Attachment.id)
                    .where(Attachment.updated_at < cutoff)
                    .where(
                        or_(
                            Attachment.deleted_at.is_not(None),
                            Attachment.upload_status.in_(("failed", "expired")),
                        )
                    )
                    .limit(batch)
                )
            ).all()
            if not ids:
                return 0
            # Links / sessions cascade; blob ref_count was adjusted at the
            # state transition, so the rows can simply go.
            await session.execute(delete(Attachment).where(Attachment.id.in_(ids)))
        return len(ids)

    async def gc_unreferenced_blobs(self, *, batch: int = 100) -> int:
        """Physically delete objects + blob rows with ref_count = 0 (§4.6 GC).

        The ONLY condition for object deletion: ``ref_count = 0`` AND no
        in-flight pending upload (no attachment row references the blob).
        Deleting one attachment never touches a shared blob's other records.
        """
        grace_cutoff = self._now() - self._settings.attachment_download_url_ttl
        collected: list[tuple[str, list[str]]] = []
        async with self._factory() as session, session.begin():
            blobs = (
                await session.scalars(
                    select(AttachmentBlob)
                    .where(
                        AttachmentBlob.ref_count == 0,
                        AttachmentBlob.created_at < grace_cutoff,
                    )
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for blob in blobs:
                referencing = await session.scalar(
                    select(Attachment.id).where(Attachment.blob_id == blob.id).limit(1)
                )
                if referencing is not None:
                    continue  # terminal-but-unreaped record; retention will clear it
                keys = [blob.storage_key]
                keys.extend((blob.thumbnail_keys or {}).values())
                collected.append((str(blob.id), keys))
                await session.execute(
                    delete(AttachmentBlob).where(AttachmentBlob.id == blob.id)
                )
        for _, keys in collected:
            for key in keys:
                await self._storage.delete_object(key)
        return len(collected)

    async def refresh_quota_caches(self) -> int:
        """Recompute ``attachment_quotas.used_bytes`` from blob truth (§2.6)."""
        async with self._factory() as session, session.begin():
            rows = await session.scalars(select(AttachmentQuota.workspace_id))
            refreshed = 0
            for workspace_id in rows.all():
                used = await self._used_bytes(session, workspace_id)
                await session.execute(
                    update(AttachmentQuota)
                    .where(AttachmentQuota.workspace_id == workspace_id)
                    .values(used_bytes=used, updated_at=self._now())
                )
                refreshed += 1
        return refreshed

    # ------------------------------------------------------------------
    # shared internals
    # ------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    @staticmethod
    def _normalize_hash(content_hash: str | None) -> str | None:
        if content_hash is None:
            return None
        normalized = content_hash.strip().lower()
        if not _SHA256_RE.match(normalized):
            raise ValidationError(
                "content_hash must be a lowercase hex SHA-256 digest",
                details={"content_hash": content_hash[:16]},
            )
        return normalized

    async def _load_visible(
        self, session: AsyncSession, workspace_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> Attachment:
        attachment = await session.scalar(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.workspace_id == workspace_id,
                Attachment.deleted_at.is_(None),
            )
        )
        if attachment is None:
            raise NotFoundError(_ATTACHMENT_NOT_FOUND)
        return attachment

    async def _load_owned(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        actor: Member,
    ) -> tuple[Attachment, AttachmentBlob]:
        attachment = await session.scalar(
            select(Attachment)
            .where(
                Attachment.id == attachment_id,
                Attachment.workspace_id == workspace_id,
                Attachment.deleted_at.is_(None),
            )
            .with_for_update()
        )
        if attachment is None:
            raise NotFoundError(_ATTACHMENT_NOT_FOUND)
        # Owner-only operations (§5.4 属主校验): uploader == principal.
        if attachment.uploader_id != actor.id:
            raise ForbiddenError("only the upload requester can operate on this upload")
        blob = await session.get(
            AttachmentBlob, attachment.blob_id, with_for_update=True
        )
        assert blob is not None
        return attachment, blob

    async def _render_complete_response(
        self, workspace_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> dict[str, Any]:
        async with self._factory() as session:
            await set_tenant_context(session, workspace_id)
            attachment = await self._load_visible(session, workspace_id, attachment_id)
            blob = await session.get(AttachmentBlob, attachment.blob_id)
            assert blob is not None
            member = await session.get(Member, attachment.uploader_id)
            links = (
                await session.scalars(
                    select(AttachmentLink).where(
                        AttachmentLink.attachment_id == attachment.id
                    )
                )
            ).all()
            rendered = await self._render_attachment(session, attachment, blob, list(links), member)
        note = (
            "scanning — downloads open once the scan completes"
            if blob.scan_status not in SCAN_GATE_OPEN
            else None
        )
        rendered["note"] = note
        return {"data": rendered}

    async def _emit_processed(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        attachment: Attachment,
        blob: AttachmentBlob,
    ) -> None:
        """attachment.processed to every issue channel this attachment links to.

        Comment/chat channels are owned by their modules; until they land the
        client fetches on demand (spec §3.7 note — 前端打开灯箱时按需拉取).
        """
        links = await session.scalars(
            select(AttachmentLink).where(
                AttachmentLink.attachment_id == attachment.id,
                AttachmentLink.linked_type == "issue",
            )
        )
        released = blob.scan_status in SCAN_GATE_OPEN
        payload = {
            "id": str(attachment.id),
            "blob_id": str(blob.id),
            "scan_status": blob.scan_status,
            "mime_type": blob.mime_type,
            "is_image": blob.is_image,
            "file_name": attachment.file_name,
            "thumbnail_url": (
                f"/api/v1/attachments/{attachment.id}/thumbnail?size=md"
                if blob.is_image and released
                else None
            ),
            "visibility": "restricted",
        }
        for link in links:
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=issue_channel(link.linked_id),
                event=ATTACHMENT_PROCESSED,
                data=payload,
            )

    async def _audit(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        actor: Member | None,
        action: str,
        resource_id: uuid.UUID,
        metadata: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        await write_audit(
            session,
            workspace_id=workspace_id,
            actor_member_id=actor.id if actor is not None else None,
            actor_kind="member" if actor is not None else "system",
            action=action,
            resource_type="attachment",
            resource_id=resource_id,
            metadata=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def _default_allowed_mimes() -> frozenset[str]:
    from mesh.attachment.policy import DEFAULT_ALLOWED_MIMES

    return DEFAULT_ALLOWED_MIMES
