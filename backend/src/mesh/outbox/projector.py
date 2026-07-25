"""Realtime projector (README §2.2 / §6.6 / §6.7 — 唯一写入路径).

Consumes ``realtime.publish`` outbox events and is the ONLY writer of
``realtime_events``: de-duplicates by ``outbox_event_id`` and allocates the
per-channel ``seq`` inside the same transaction. Business transactions never
write ``realtime_events`` or allocate seq directly.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.events.vocab import require_realtime_event

logger = logging.getLogger("mesh.outbox.projector")


class ProjectionError(ValueError):
    """The outbox payload cannot be projected (malformed or tenant mismatch)."""


async def project_realtime_event(
    session, event: OutboxEvent
) -> list[tuple[str, dict]] | None:
    """Project one ``realtime.publish`` outbox event into ``realtime_events``.

    Returns ``[(channel, frame)]`` for post-commit Redis fan-out. Raises on
    unrecoverable payload problems so the relay applies its retry/failed policy.
    """
    payload = event.payload or {}
    channel = payload.get("channel")
    event_name = payload.get("event")
    data = payload.get("data")
    if not isinstance(channel, str) or not isinstance(event_name, str) or not isinstance(data, dict):
        raise ProjectionError(
            "realtime.publish payload must contain channel/event/data "
            f"(outbox_event_id={event.id})"
        )

    # Vocabulary gate (defense in depth; emit_realtime already checks at write time).
    require_realtime_event(event_name)

    # Idempotency: an event already projected (duplicate delivery) is a no-op.
    existing = await session.execute(
        select(RealtimeEvent.seq, RealtimeEvent.event, RealtimeEvent.payload).where(
            RealtimeEvent.outbox_event_id == event.id
        )
    )
    existing_row = existing.first()
    if existing_row is not None:
        frame = {
            "op": "event",
            "channel": channel,
            "seq": existing_row.seq,
            "event": existing_row.event,
            "payload": existing_row.payload,
        }
        return [(channel, frame)]

    # Auto-register the channel with the outbox row's tenant key.
    await session.execute(
        pg_insert(RealtimeChannel)
        .values(channel=channel, workspace_id=event.workspace_id)
        .on_conflict_do_nothing(index_elements=["channel"])
    )

    # Allocate the channel seq in this transaction — tenant-guarded so a channel
    # owned by another workspace can never have its watermark bumped.
    new_seq = await session.scalar(
        select(RealtimeChannel.last_seq)
        .where(
            RealtimeChannel.channel == channel,
            RealtimeChannel.workspace_id == event.workspace_id,
        )
        .with_for_update()
    )
    if new_seq is None:
        raise ProjectionError(
            f"channel {channel!r} does not belong to workspace {event.workspace_id}"
        )
    new_seq += 1
    await session.execute(
        RealtimeChannel.__table__.update()
        .where(
            RealtimeChannel.channel == channel,
            RealtimeChannel.workspace_id == event.workspace_id,
        )
        .values(last_seq=new_seq)
    )

    try:
        # Nested savepoint: a UNIQUE violation must not abort the caller's
        # transaction (the relay dispatches inside its own savepoint, but the
        # projector must stay safe in any transaction context).
        async with session.begin_nested():
            await session.execute(
                RealtimeEvent.__table__.insert().values(
                    workspace_id=event.workspace_id,
                    channel=channel,
                    seq=new_seq,
                    event=event_name,
                    payload=data,
                    outbox_event_id=event.id,
                )
            )
    except IntegrityError as exc:  # UNIQUE(outbox_event_id) backstop — treat as done
        logger.warning("duplicate projection for outbox event %s: %s", event.id, exc)
        return None

    frame = {
        "op": "event",
        "channel": channel,
        "seq": new_seq,
        "event": event_name,
        "payload": data,
    }
    return [(channel, frame)]
