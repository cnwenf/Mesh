"""Queue-orphan audit retention (integrations.md §2.10 / §3.9 delete protection).

After a protected parent deletion (a binding/integration ``?force=cancel`` or a
project's cascaded binding delete), surviving queue items become self-describing
ORPHAN audit rows — ``binding_id IS NULL`` and necessarily terminal (enforced by
``ck_imq_orphan_terminal``). They stay queryable through the workspace-level
audit endpoint for ``MESH_IM_QUEUE_AUDIT_RETENTION`` (default 30 days); this
loop then purges them in bounded batches so the table — including its unique
indexes — does not grow without bound.

Only ``binding_id IS NULL`` TERMINAL rows past the window are eligible. A row
whose parent is intact (``binding_id IS NOT NULL``) is a live queue item and is
NEVER touched; a non-terminal row can never be an orphan (the CHECK forbids it),
so the terminal predicate is defense-in-depth rather than the primary guard.
Conventions mirror ``workers/retention.py`` exactly (bounded batch, per-pass
logging, error swallow that keeps the supervisor-managed loop alive).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from mesh.db.models.integration import QUEUE_TERMINAL_STATES, IntegrationMessageQueue

logger = logging.getLogger("mesh.workers.queue_retention")

# Upper bound on rows removed per purge pass — keeps each purge transaction
# short regardless of backlog size; the loop reclaims any remainder on
# subsequent ticks (same batch convention as workers/retention.py).
QUEUE_AUDIT_PURGE_BATCH_LIMIT = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def purge_queue_audit_orphans(
    session_factory,
    *,
    retention: timedelta,
    now: datetime,
    batch_limit: int = QUEUE_AUDIT_PURGE_BATCH_LIMIT,
) -> int:
    """Delete terminal orphan audit rows older than ``retention``; returns count.

    Eligible rows: ``binding_id IS NULL`` AND state terminal AND ``updated_at``
    older than the window. Live items (parent intact) and non-terminal rows are
    never eligible. Deletion is capped at ``batch_limit`` rows per call so a
    large backlog is reclaimed incrementally without long transactions.
    """
    cutoff = now - retention
    expired_ids = (
        select(IntegrationMessageQueue.id)
        .where(
            IntegrationMessageQueue.binding_id.is_(None),
            IntegrationMessageQueue.state.in_(QUEUE_TERMINAL_STATES),
            IntegrationMessageQueue.updated_at < cutoff,
        )
        .order_by(IntegrationMessageQueue.updated_at.asc())
        .limit(batch_limit)
    )
    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(
                delete(IntegrationMessageQueue).where(IntegrationMessageQueue.id.in_(expired_ids))
            )
            deleted = result.rowcount or 0
    if deleted:
        logger.info(
            "purged %d integration queue audit orphans (cutoff=%s)",
            deleted,
            cutoff.isoformat(),
        )
    return deleted


async def integration_queue_audit_retention_loop(
    session_factory,
    *,
    settings,
    stop: asyncio.Event,
    clock=_utcnow,
) -> None:
    """Periodically purge terminal queue-orphan audit rows until ``stop``.

    The retention window and cadence come from settings
    (``im_queue_audit_retention`` / ``im_queue_audit_retention_interval``).
    Each pass swallows and logs its own errors so a transient failure never
    terminates the supervisor-managed loop.
    """
    retention = settings.im_queue_audit_retention
    interval = settings.im_queue_audit_retention_interval
    while not stop.is_set():
        try:
            await purge_queue_audit_orphans(session_factory, retention=retention, now=clock())
        except Exception:  # noqa: BLE001 — log + keep the loop alive
            logger.exception("integration queue audit retention pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


__all__ = [
    "QUEUE_AUDIT_PURGE_BATCH_LIMIT",
    "integration_queue_audit_retention_loop",
    "purge_queue_audit_orphans",
]
