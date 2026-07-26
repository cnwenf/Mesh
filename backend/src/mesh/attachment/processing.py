"""Quarantine pipeline: the attachment processing worker's brain (§3.3/§2.2).

Claim model — ``attachment_blobs(scan_status='pending')`` with
``FOR UPDATE SKIP LOCKED`` (README §2.2): multiple worker replicas never
double-process; a crash mid-scan leaves the row ``pending`` so the next pass
reclaims it. ``error`` rows carry an attempt counter in ``scan_detail``; the
retry limit turns poison objects terminal instead of looping forever.

Processing one blob, in order (all server-side, client claims ignored):

1. read the object bytes (bounded);
2. full SHA-256 → post-dedup against an existing truth blob (repoint the
   referencing attachments, delete the duplicate object);
3. compare against the client-declared hash → ``HASH_MISMATCH`` error state;
4. magic-byte MIME sniff → write back ``mime_type``/``extension``/``is_image``;
5. plain-text whitelist → ``skipped`` (sniff + hash still done);
6. AV pass → ``infected`` (+critical audit) or continue;
7. image renditions (sm/md/lg) → ``thumbnail_keys``;
8. ``clean`` — and ``attachment.processed`` for every live attachment that
   references the blob, on its issue channel.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.attachment.mime import extension_for_mime, sniff_mime
from mesh.attachment.policy import TEXT_SCAN_SKIP_MIMES
from mesh.attachment.scanner import HeuristicScanner, VirusScanner
from mesh.attachment.storage import ObjectStorage, generate_thumbnail_key
from mesh.attachment.thumbnails import ThumbnailError, is_thumbnailable, make_thumbnails
from mesh.config import Settings
from mesh.db.models.attachment import Attachment, AttachmentBlob, AttachmentLink
from mesh.errors import StorageError
from mesh.outbox.service import emit_realtime

logger = logging.getLogger("mesh.attachment.processing")

SCAN_RETRY_LIMIT = 3
_STAGING_HASH_PREFIX = "staging:"
_SCAN_GATE_OPEN = frozenset({"clean", "skipped"})


def issue_channel(issue_id: Any) -> str:
    return f"issue:{issue_id}"


async def claim_pending_blobs(session: AsyncSession, *, batch: int) -> list[AttachmentBlob]:
    """SKIP LOCKED claim of quarantine rows (README §2.2 decoupling)."""
    rows = await session.scalars(
        select(AttachmentBlob)
        .where(AttachmentBlob.scan_status == "pending")
        .order_by(AttachmentBlob.created_at)
        .limit(batch)
        .with_for_update(skip_locked=True)
    )
    return list(rows.all())


async def process_blob(
    session: AsyncSession,
    blob: AttachmentBlob,
    *,
    storage: ObjectStorage,
    settings: Settings,
    scanner: VirusScanner | None = None,
) -> None:
    """Run the full quarantine pass for ONE claimed blob (caller owns the tx).

    Every terminal transition emits ``attachment.processed`` for each live
    attachment referencing the blob (through the outbox — the projector is the
    only realtime writer, README §6.6).
    """
    scanner = scanner or HeuristicScanner()
    detail = dict(blob.scan_detail or {})
    attempts = int(detail.get("attempts", 0))

    # -- 0. object existence + bounded read ------------------------------------
    exists = await storage.object_exists(blob.storage_key)
    if not exists:
        await _mark_error(
            session, blob, detail, error_code="OBJECT_MISSING", emit=True
        )
        return
    try:
        data = await storage.get_bytes(
            blob.storage_key, max_bytes=settings.attachment_max_file_bytes
        )
    except StorageError:
        attempts += 1
        if attempts >= SCAN_RETRY_LIMIT:
            await _mark_error(
                session, blob, detail, error_code="STORAGE_READ_FAILED", emit=True
            )
        else:
            blob.scan_detail = {**detail, "attempts": attempts}
        return

    # -- 1. authoritative SHA-256 + post-dedup (§3.2 服务端后置去重) --------------
    computed = hashlib.sha256(data).hexdigest()
    if await _dedup_into_existing(session, blob, computed_hash=computed, storage=storage):
        return

    declared = detail.get("declared_hash") or (
        blob.content_hash if not blob.content_hash.startswith(_STAGING_HASH_PREFIX) else None
    )
    hash_matches = declared is None or declared == computed

    # Adopt the computed hash as the content address. A race with another
    # upload computing the same hash is resolved by the unique constraint →
    # converge onto the winner via post-dedup.
    if blob.content_hash != computed:
        try:
            async with session.begin_nested():
                blob.content_hash = computed
                await session.flush()
        except IntegrityError:
            # The savepoint auto-rolled back; the outer transaction is intact.
            if await _dedup_into_existing(
                session, blob, computed_hash=computed, storage=storage
            ):
                return
            await _mark_error(
                session, blob, detail, error_code="HASH_COLLISION", emit=True
            )
            return

    if not hash_matches:
        await _mark_error(
            session,
            blob,
            {**detail, "sha256": computed, "hash_matches": False},
            error_code="HASH_MISMATCH",
            emit=True,
        )
        return

    # -- 2. magic-byte MIME sniff (never trust the client, §3.3) ----------------
    sniffed = sniff_mime(data)
    blob.mime_type = sniffed
    blob.extension = extension_for_mime(sniffed)
    blob.is_image = sniffed.startswith("image/")

    scan_detail: dict[str, Any] = {
        "sniffed_mime": sniffed,
        "sha256": computed,
        "hash_matches": True,
    }

    # -- 3. plain-text scan-skip whitelist (§3.6 — the ONLY skipped source) -----
    if settings.attachment_scan_skip_text and sniffed in TEXT_SCAN_SKIP_MIMES:
        scan_detail["av_engine"] = None
        scan_detail["av_result"] = "skipped-text-whitelist"
        blob.scan_status = "skipped"
        blob.scan_detail = scan_detail
        blob.updated_at = datetime.now(UTC)
        await session.flush()
        await _emit_processed_for_blob(session, blob)
        return

    # -- 4. AV pass --------------------------------------------------------------
    verdict = scanner.scan(data, sniffed_mime=sniffed)
    scan_detail["av_engine"] = verdict.engine
    scan_detail["av_result"] = verdict.result
    if verdict.infected:
        blob.scan_status = "infected"
        blob.scan_detail = scan_detail
        blob.updated_at = datetime.now(UTC)
        await session.flush()
        # Security event (README §6.13 critical): audit trail notifies
        # uploader + workspace admins.
        from mesh.auth.audit import write_audit

        await write_audit(
            session,
            workspace_id=blob.workspace_id,
            actor_member_id=None,
            actor_kind="system",
            action="attachment.scan_infected",
            resource_type="attachment_blob",
            resource_id=blob.id,
            metadata={
                "severity": "critical",
                "av_engine": verdict.engine,
                "av_result": verdict.result,
                "sha256": computed,
            },
        )
        await _emit_processed_for_blob(session, blob)
        logger.warning("blob %s marked infected (%s)", blob.id, verdict.result)
        return

    # -- 5. thumbnails (after the gate opens, §3.3) -------------------------------
    if is_thumbnailable(sniffed):
        try:
            info = await make_thumbnails(data, source_mime=sniffed)
        except ThumbnailError as exc:
            await _mark_error(
                session,
                blob,
                {**scan_detail, "thumbnail_error": str(exc)},
                error_code="THUMBNAIL_FAILED",
                emit=True,
            )
            return
        blob.image_width = info.width
        blob.image_height = info.height
        thumbnail_keys: dict[str, str] = {}
        for name, (payload, content_type) in info.renditions.items():
            key = generate_thumbnail_key(blob.workspace_id, name)
            await storage.put_bytes(key, payload, content_type=content_type)
            thumbnail_keys[name] = key
        blob.thumbnail_keys = thumbnail_keys

    # -- 6. clean ------------------------------------------------------------------
    blob.scan_status = "clean"
    blob.scan_detail = scan_detail
    blob.updated_at = datetime.now(UTC)
    await session.flush()
    await _emit_processed_for_blob(session, blob)


async def _dedup_into_existing(
    session: AsyncSession,
    blob: AttachmentBlob,
    *,
    computed_hash: str,
    storage: ObjectStorage,
) -> bool:
    """Repoint referencing attachments onto the canonical blob, if one exists.

    Returns True when the blob was merged away (caller must stop processing).
    De-dup only SHARES the truth row — attachment records stay independent
    (independent uploader/links/lifecycle, §4.6).
    """
    existing = await session.scalar(
        select(AttachmentBlob)
        .where(
            AttachmentBlob.workspace_id == blob.workspace_id,
            AttachmentBlob.content_hash == computed_hash,
            AttachmentBlob.id != blob.id,
        )
        .with_for_update()
    )
    if existing is None:
        return False
    await session.execute(
        update(Attachment)
        .where(Attachment.blob_id == blob.id)
        .values(blob_id=existing.id)
    )
    existing.ref_count = existing.ref_count + blob.ref_count
    blob.ref_count = 0
    await session.flush()  # repoint first so the RESTRICT FK clears
    await session.delete(blob)
    await session.flush()
    await storage.delete_object(blob.storage_key)
    await _emit_processed_for_blob(session, existing)
    logger.info(
        "post-dedup: blob %s merged into %s (workspace %s)",
        blob.id,
        existing.id,
        blob.workspace_id,
    )
    return True


async def _mark_error(
    session: AsyncSession,
    blob: AttachmentBlob,
    detail: dict[str, Any],
    *,
    error_code: str,
    emit: bool,
) -> None:
    blob.scan_status = "error"
    blob.scan_detail = {**detail, "error_code": error_code}
    await session.flush()
    if emit:
        await _emit_processed_for_blob(session, blob)
    logger.error("blob %s scan error: %s", blob.id, error_code)


async def _emit_processed_for_blob(session: AsyncSession, blob: AttachmentBlob) -> None:
    """attachment.processed for every live attachment referencing this blob."""
    attachments = (
        await session.scalars(
            select(Attachment).where(
                Attachment.blob_id == blob.id,
                Attachment.deleted_at.is_(None),
                Attachment.upload_status == "completed",
            )
        )
    ).all()
    released = blob.scan_status in _SCAN_GATE_OPEN
    for attachment in attachments:
        links = (
            await session.scalars(
                select(AttachmentLink).where(
                    AttachmentLink.attachment_id == attachment.id,
                    AttachmentLink.linked_type == "issue",
                )
            )
        ).all()
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
                workspace_id=blob.workspace_id,
                channel=issue_channel(link.linked_id),
                event="attachment.processed",
                data=payload,
            )
