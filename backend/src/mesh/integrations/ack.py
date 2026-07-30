"""Emoji-ack confirmation (integrations.md §3.8).

The ack is a CONVERSATIONAL reply (not a notification — it never touches
``notification_delivery``): immediately after a task message is enqueued,
the bot confirms receipt (``✅ 已接收，处理中`` by default) so the user
knows the task was caught, then the run proceeds asynchronously.

**at-most-once, written in stone**: the outbox is at-least-once, but
"platform accepted, crashed before the ledger write" cannot be deduped by
an idempotency key — so the ack deliberately prefers an occasional MISS
over a duplicate confirmation. The relay persists the ``ack_attempted_at``
gate and marks the event ``published`` in ONE short transaction (T1)
BEFORE the outbound call; a crash after T1 loses the ack (audit-visible:
attempted ∧ ¬sent), a crash before T1 reclaims cleanly.

**leading-edge coalescing (anti-reflection guard)**: the window leader is
chosen INSIDE the enqueue transaction under the conversation sequence
advisory lock (the caller holds ``imq_seq:<conversation_key>`` and has
just taken ``ack_window_at = clock_timestamp()``) — never by relay arrival
order. Followers within ``[leader.ack_window_at, +window)`` get NO outbox
event (no external side effect); their "represented" state is structural
(``ack_leader_id`` points at the leader) and backfilled by the leader's T2
after a successful send. Integrations with ``ack_template == ''`` skip ack
processing entirely — no event, no window occupancy.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.integration import IntegrationMessageQueue
from mesh.outbox.service import emit_event

logger = logging.getLogger("mesh.integrations.ack")

# ``im.send`` — the internal outbox event type carrying ALL conversational
# outbound (acks, command feedback, notification chunks, approval cards).
# Internal event type, NOT a README §6.7 realtime name (no vocab entry).
IM_SEND_EVENT_TYPE = "im.send"

# Payload ``kind`` values (consumer: IMSendRelay). The queue-plane kinds
# (command_feedback / rate_limit_hint) are emitted by the MES-88 queue /
# command / guard modules; the values here are the single source of truth
# the relay matches against.
IM_SEND_KIND_ACK = "ack"
IM_SEND_KIND_FEEDBACK = "feedback"
IM_SEND_KIND_COMMAND_FEEDBACK = "command_feedback"
IM_SEND_KIND_RATE_LIMIT_HINT = "rate_limit_hint"
IM_SEND_KIND_NOTIFICATION = "notification"
IM_SEND_KIND_CARD = "card"

DEFAULT_ACK_TEMPLATE = "✅ 已接收，处理中"

# Wording for the leader confirmation when earlier items are still pending
# (hedged — the serial lane may move fast).
POSITION_HINT_TEMPLATE = "{template}（第 {position} 位，可能很快轮到）"


def ack_idempotency_key(queue_item_id: uuid.UUID) -> str:
    """README §6.5 registered key for ack sends: ``sha256(queue_item_id |
    'ack')``."""
    return hashlib.sha256(f"{queue_item_id}|ack".encode()).hexdigest()


async def position_hint(session: AsyncSession, *, item: IntegrationMessageQueue) -> int:
    """Best-effort queue position: count of smaller-seq PENDING items in
    the conversation + 1 (recomputed at send time by the relay)."""
    count = await session.scalar(
        select(func.count())
        .select_from(IntegrationMessageQueue)
        .where(
            IntegrationMessageQueue.conversation_key == item.conversation_key,
            IntegrationMessageQueue.seq < item.seq,
            IntegrationMessageQueue.state == "pending",
        )
    )
    return int(count or 0) + 1


def compose_ack_text(template: str, position: int) -> str:
    """Leader confirmation copy: plain template at position 1, hedged
    position hint behind it."""
    if position > 1:
        return POSITION_HINT_TEMPLATE.format(template=template, position=position)
    return template


async def elect_ack_leader(
    session: AsyncSession,
    *,
    item: IntegrationMessageQueue,
    ack_template: str,
    coalesce_window: timedelta,
    conversation_type: str,
    target_user_key: str = "",
) -> bool:
    """Decide the ack window role of a just-enqueued item and, for a
    leader, write the ``im.send`` outbox event — ALL inside the enqueue
    transaction.

    CONTRACT (cross-slice, MES-88 enqueue flow): the caller has already
    1. taken ``pg_advisory_xact_lock(hashtext('imq_seq:'||conversation_key))``,
    2. taken ``item.ack_window_at = clock_timestamp()`` AFTER the lock,
    3. INSERTed + flushed ``item`` (its ``id`` and ``seq`` exist).

    Returns True when ``item`` is the window leader AND its ``im.send``
    event has been written. ``ack_template == ''`` returns False without
    touching any ack state (the integration switched confirmations off —
    zero window side effects).

    Leader election is by LOCK-ORDERED ``ack_window_at`` (never
    ``enqueued_at`` / relay arrival): a covering leader L satisfies
    ``L.ack_leader_id = L.id`` (self-reference) and
    ``item.ack_window_at ∈ [L.ack_window_at, L.ack_window_at + window)``.
    """
    if not ack_template:
        return False
    covering_leader = await session.scalar(
        select(IntegrationMessageQueue)
        .where(
            IntegrationMessageQueue.conversation_key == item.conversation_key,
            IntegrationMessageQueue.id != item.id,
            # self-referencing rows only — leaders own their window
            IntegrationMessageQueue.ack_leader_id == IntegrationMessageQueue.id,
            IntegrationMessageQueue.ack_window_at <= item.ack_window_at,
            IntegrationMessageQueue.ack_window_at > item.ack_window_at - coalesce_window,
        )
        .order_by(IntegrationMessageQueue.ack_window_at.desc())
        .limit(1)
    )
    if covering_leader is not None:
        # Follower: structurally represented by the leader; NO outbox event
        # (no external side effect). ack_represented_at / ack_merged_into
        # are backfilled by the leader's relay T2 after a successful send.
        item.ack_leader_id = covering_leader.id
        await session.flush()
        return False
    # Leader: self-reference + the sole im.send event of this window.
    item.ack_leader_id = item.id
    await session.flush()
    position = await position_hint(session, item=item)
    await emit_event(
        session,
        workspace_id=item.workspace_id,
        event_type=IM_SEND_EVENT_TYPE,
        payload={
            "kind": IM_SEND_KIND_ACK,
            "workspace_id": str(item.workspace_id),
            "integration_id": str(item.integration_id) if item.integration_id else None,
            "conversation_key": item.conversation_key,
            "conversation_type": conversation_type,
            "target_user_key": target_user_key,
            "template": ack_template,
            "queue_item_id": str(item.id),
            "position_snapshot": position,
        },
        idempotency_key=ack_idempotency_key(item.id),
    )
    return True


__all__ = [
    "DEFAULT_ACK_TEMPLATE",
    "IM_SEND_EVENT_TYPE",
    "IM_SEND_KIND_ACK",
    "IM_SEND_KIND_CARD",
    "IM_SEND_KIND_COMMAND_FEEDBACK",
    "IM_SEND_KIND_FEEDBACK",
    "IM_SEND_KIND_NOTIFICATION",
    "IM_SEND_KIND_RATE_LIMIT_HINT",
    "POSITION_HINT_TEMPLATE",
    "ack_idempotency_key",
    "compose_ack_text",
    "elect_ack_leader",
    "position_hint",
]
