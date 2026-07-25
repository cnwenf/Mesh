"""Outbox write services (README §6.6 — 唯一权威).

Business code calls :func:`emit_event` / :func:`emit_realtime` inside its own
transaction; the row commits atomically with the business rows. Creating
executions/notifications/realtime events outside the outbox is forbidden —
the relay is the only dispatcher.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.outbox import OutboxEvent
from mesh.errors import BusinessRuleError
from mesh.events.vocab import REALTIME_PUBLISH, require_realtime_event
from mesh.realtime.channels import is_valid_channel


async def emit_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> OutboxEvent:
    """Insert an outbox row in the caller's transaction.

    Duplicate ``idempotency_key`` returns the existing row instead of raising
    (at-least-once producers may retry).
    """
    if idempotency_key is not None:
        existing = await session.scalar(
            select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
    event = OutboxEvent(
        workspace_id=workspace_id,
        event_type=event_type,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    session.add(event)
    await session.flush()
    return event


async def emit_realtime(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    channel: str,
    event: str,
    data: dict[str, Any],
    idempotency_key: str | None = None,
) -> OutboxEvent:
    """Queue a realtime event through the unique write path (§6.6/§6.7).

    Validates the channel syntax and the §6.7 event vocabulary at write time;
    the projector re-validates at projection time (defense in depth).
    """
    require_realtime_event(event)
    if not is_valid_channel(channel):
        raise BusinessRuleError("invalid channel name", code="invalid_channel")
    payload = {"channel": channel, "event": event, "data": data}
    return await emit_event(
        session,
        workspace_id=workspace_id,
        event_type=REALTIME_PUBLISH,
        payload=payload,
        idempotency_key=idempotency_key,
    )
