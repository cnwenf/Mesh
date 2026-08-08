"""Read + stale notification auto-archive (README §6.13 分组与归档).

Inbox groups fold by ``group_key``; a group that is already read AND whose
activity went stale is automatically archived — ``archived_at`` semantics are
"moved out of the main view, still queryable" (the inbox list hides archived
rows unless asked). The sweep runs workspace-agnostic on the owner role
(same convention as ``workers/retention.py``): it only flips state on rows
that are already read, so it never changes any member's unread count and no
realtime broadcast is required.

Bounded batch per pass + error swallow keep the supervisor-managed loop
alive; cadence/window come from settings.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from mesh.db.models.notification import Notification

logger = logging.getLogger("mesh.workers.notification_archive")

# Upper bound on rows archived per pass — keeps each transaction short
# regardless of backlog size (same batch convention as retention sweeps).
NOTIFICATION_ARCHIVE_BATCH_LIMIT = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def archive_read_expired_notifications(
    session_factory,
    *,
    retention: timedelta,
    now: datetime,
    batch_limit: int = NOTIFICATION_ARCHIVE_BATCH_LIMIT,
) -> int:
    """Archive read notifications whose newest activity is older than
    ``retention``; returns the number of rows archived.

    A row is eligible when it is read, not yet archived, and both its
    creation and last aggregation refresh predate the cutoff — a group that
    received fresh activity within the window stays in the main view even if
    it was read earlier (README §6.13 「已读 + 过期组自动归档」).
    """
    cutoff = now - retention
    async with session_factory() as session:
        async with session.begin():
            expired_ids = (
                (
                    await session.execute(
                        select(Notification.id)
                        .where(
                            Notification.read_at.is_not(None),
                            Notification.archived_at.is_(None),
                            Notification.created_at < cutoff,
                            Notification.updated_at < cutoff,
                        )
                        .order_by(Notification.updated_at.asc())
                        .limit(batch_limit)
                    )
                )
                .scalars()
                .all()
            )
            if not expired_ids:
                return 0
            result = await session.execute(
                update(Notification)
                .where(Notification.id.in_(expired_ids))
                .values(archived_at=now, updated_at=now)
            )
            archived = int(result.rowcount or 0)
    if archived:
        logger.info(
            "auto-archived %d read+expired notification groups (cutoff=%s)",
            archived,
            cutoff.isoformat(),
        )
    return archived


async def notification_archive_loop(
    session_factory,
    *,
    settings,
    stop: asyncio.Event,
    clock=_utcnow,
) -> None:
    """Periodically archive read+expired notifications until ``stop``."""
    retention = settings.notification_archive_retention
    interval = settings.notification_archive_interval
    while not stop.is_set():
        try:
            await archive_read_expired_notifications(
                session_factory, retention=retention, now=clock()
            )
        except Exception:  # noqa: BLE001 — log + keep the loop alive
            logger.exception("notification auto-archive pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


__all__ = [
    "NOTIFICATION_ARCHIVE_BATCH_LIMIT",
    "archive_read_expired_notifications",
    "notification_archive_loop",
]
