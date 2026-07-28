"""Data-job reaper + compensating sweep (import-export.md §3.8 R3).

One supervised worker loop eliminates the permanent-stuck classes:

1. **lease-expired reclaim** — ``status IN ('running','validating')`` with
   ``lease_expires_at < now()``: clear the owner (status, counters and
   checkpoint stay — they commit atomically with entities; ``lease_seq``
   is NEVER reset, so the next claim's ``+1`` invalidates every token the
   old holder carries) and re-dispatch ``data_job.resume``; the new
   worker continues from ``checkpoint.last_committed_batch``.
2. **unclaimed running/validating** — the API set the transitional state
   but the dispatch event was marked failed after max attempts
   (at-least-once residual): re-dispatch resume.
3. **stuck pending exports** — created but never claimed (same residual):
   re-dispatch enqueue.

Re-dispatch idempotency keys follow the README §6.5 sha256 convention
(``sha256(job_id | 'resume' | checkpoint_batch)``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import Settings
from mesh.data_jobs.runner import ENQUEUE_EVENT_TYPE, RESUME_EVENT_TYPE, _rearm_bucket, resume_idempotency_key
from mesh.db.models.data_job import DataJob
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.data_jobs.reaper")


async def data_job_reaper_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    stop: asyncio.Event | None = None,
    clock: Callable[[], datetime] | None = None,
) -> None:
    """Supervised loop: sweep on the configured interval until ``stop``."""
    clock = clock or (lambda: datetime.now(UTC))
    interval = settings.data_job_reaper_interval
    while stop is None or not stop.is_set():
        try:
            reclaimed = await run_reaper_pass(session_factory, settings=settings, clock=clock)
            if reclaimed:
                logger.info("data-job reaper reclaimed %d job(s)", reclaimed)
        except Exception:  # noqa: BLE001 — the supervisor restarts the loop
            logger.exception("data-job reaper pass failed")
        if stop is not None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(interval)


async def run_reaper_pass(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    clock: Callable[[], datetime] | None = None,
) -> int:
    """One reclaim + compensating sweep; returns reclaimed job count."""
    clock = clock or (lambda: datetime.now(UTC))
    now = clock()
    grace = settings.data_job_stuck_grace
    reclaimed = 0
    reclaimed += await _reclaim_expired_leases(session_factory, settings=settings, now=now)
    reclaimed += await _redispatch_unclaimed(session_factory, settings=settings, now=now, grace=grace)
    reclaimed += await _redispatch_stuck_pending_exports(session_factory, now=now, grace=grace)
    return reclaimed


async def _reclaim_expired_leases(
    session_factory: async_sessionmaker[AsyncSession], *, settings: Settings, now: datetime
) -> int:
    async with session_factory() as session, session.begin():
        jobs = (
            (
                await session.execute(
                    select(DataJob)
                    .where(
                        DataJob.status.in_(("running", "validating")),
                        DataJob.lease_owner.is_not(None),
                        DataJob.lease_expires_at.is_not(None),
                        DataJob.lease_expires_at < now,
                    )
                    .order_by(DataJob.lease_expires_at.asc())
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            # Clear the owner only — status stays (running/validating),
            # counters/checkpoint commit atomically with entities, and
            # lease_seq is preserved so the next claim's +1 fences the
            # resurrected old worker out (R4).
            job.lease_owner = None
            job.updated_at = now
            last_batch = int((job.checkpoint or {}).get("last_committed_batch") or 0)
            await emit_event(
                session,
                workspace_id=job.workspace_id,
                event_type=RESUME_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "action": "resume"},
                idempotency_key=resume_idempotency_key(
                    job.id, last_batch, bucket=_rearm_bucket(now, settings)
                ),
            )
            logger.warning("data job %s lease expired; re-dispatched resume", job.id)
        return len(jobs)


async def _redispatch_unclaimed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    now: datetime,
    grace: timedelta,
) -> int:
    """Running/validating jobs with NO lease older than the grace window —
    the transitional state was set but its dispatch event never landed."""
    async with session_factory() as session, session.begin():
        jobs = (
            (
                await session.execute(
                    select(DataJob)
                    .where(
                        DataJob.status.in_(("running", "validating")),
                        DataJob.lease_owner.is_(None),
                        DataJob.updated_at < now - grace,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            last_batch = int((job.checkpoint or {}).get("last_committed_batch") or 0)
            await emit_event(
                session,
                workspace_id=job.workspace_id,
                event_type=RESUME_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "action": "resume"},
                idempotency_key=resume_idempotency_key(
                    job.id, last_batch, bucket=_rearm_bucket(now, settings)
                ),
            )
        return len(jobs)


async def _redispatch_stuck_pending_exports(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime, grace: timedelta
) -> int:
    """Exports still pending past the grace window — re-enqueue (the claim
    is idempotent via the status guard; the time-bucketed key re-arms per
    window so a job cannot be re-enqueued more than once per grace span)."""
    bucket = int(now.timestamp() // max(grace.total_seconds(), 1))
    async with session_factory() as session, session.begin():
        jobs = (
            (
                await session.execute(
                    select(DataJob)
                    .where(
                        DataJob.kind == "export",
                        DataJob.status == "pending",
                        DataJob.created_at < now - grace,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for job in jobs:
            await emit_event(
                session,
                workspace_id=job.workspace_id,
                event_type=ENQUEUE_EVENT_TYPE,
                payload={"data_job_id": str(job.id), "action": "export"},
                idempotency_key=f"export-retry:{job.id}:{bucket}",
            )
        return len(jobs)
