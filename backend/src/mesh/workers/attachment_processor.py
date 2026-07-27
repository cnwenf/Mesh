"""Attachment worker loops (attachment.md §3.3 / README §2.2).

Two supervised loops, decoupled from the relay and each other via SKIP LOCKED
and independent cancel domains:

- ``attachment-scan`` — the quarantine pipeline. Claims
  ``attachment_blobs(scan_status='pending')`` with ``FOR UPDATE SKIP LOCKED``
  and runs MIME sniff / SHA-256 / AV / thumbnails per blob. A crash leaves
  rows pending so the next pass reclaims them. The relay's
  ``attachment.scan_requested`` handler gives low latency on the happy path;
  this loop is the crash-recovery sweep (both share ``process_blob``).
- ``attachment-maintenance`` — orphan reaping (uploads past ``expires_at``,
  NOT gated by ref_count — the object was never admitted to blob truth),
  soft-delete / terminal retention hard-deletes, blob GC (the ONLY object
  deletion condition: ``ref_count = 0`` AND no referencing rows) and the
  quota usage cache refresh.

The per-pass bodies are exposed as ``run_scan_pass`` / ``run_maintenance_pass``
so tests drive a single deterministic pass synchronously instead of racing a
background loop task against the per-test TRUNCATE isolation.
"""

from __future__ import annotations

import asyncio
import logging

from mesh.attachment.processing import claim_pending_blobs, process_blob
from mesh.attachment.scanner import HeuristicScanner, VirusScanner
from mesh.attachment.service import AttachmentService
from mesh.attachment.storage import ObjectStorage
from mesh.config import Settings

logger = logging.getLogger("mesh.workers.attachment_processor")


async def run_scan_pass(
    session_factory,
    *,
    storage: ObjectStorage,
    settings: Settings,
    scanner: VirusScanner | None = None,
) -> int:
    """One quarantine sweep pass; returns the number of blobs processed."""
    scanner = scanner or HeuristicScanner()
    processed = 0
    async with session_factory() as session, session.begin():
        blobs = await claim_pending_blobs(
            session, batch=settings.attachment_scan_batch_size
        )
        for blob in blobs:
            await process_blob(
                session, blob, storage=storage, settings=settings, scanner=scanner
            )
            processed += 1
    return processed


async def run_maintenance_pass(
    service: AttachmentService, *, run_gc: bool
) -> tuple[int, int, int]:
    """One maintenance pass; returns (swept, reaped, collected)."""
    swept = await service.sweep_expired_uploads()
    reaped = await service.run_retention()
    collected = 0
    if run_gc:
        collected = await service.gc_unreferenced_blobs()
        await service.refresh_quota_caches()
    return swept, reaped, collected


async def attachment_scan_loop(
    session_factory,
    *,
    storage: ObjectStorage,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Quarantine sweep: process pending blobs until ``stop`` is set."""
    scanner = HeuristicScanner()
    logger.info(
        "attachment scan loop started (interval=%.1fs, batch=%d)",
        settings.attachment_scan_interval,
        settings.attachment_scan_batch_size,
    )
    while not stop.is_set():
        try:
            processed = await run_scan_pass(
                session_factory, storage=storage, settings=settings, scanner=scanner
            )
            if processed:
                logger.info("attachment scan processed %d blob(s)", processed)
        except Exception:
            logger.exception("attachment scan iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.attachment_scan_interval)
        except TimeoutError:
            pass


async def attachment_maintenance_loop(
    session_factory,
    *,
    storage: ObjectStorage,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    """Orphan reap + retention + blob GC + quota cache refresh."""
    service = AttachmentService(session_factory, settings, storage)
    interval = settings.attachment_orphan_sweep_interval
    gc_every = max(1, int(settings.attachment_gc_interval // interval))
    ticks = 0
    logger.info("attachment maintenance loop started (interval=%.1fs)", interval)
    while not stop.is_set():
        try:
            ticks += 1
            swept, reaped, collected = await run_maintenance_pass(
                service, run_gc=(ticks % gc_every == 0)
            )
            if swept:
                logger.info("attachment orphan sweep expired %d upload(s)", swept)
            if reaped:
                logger.info("attachment retention hard-deleted %d record(s)", reaped)
            if collected:
                logger.info("attachment GC deleted %d blob(s)", collected)
        except Exception:
            logger.exception("attachment maintenance iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass
