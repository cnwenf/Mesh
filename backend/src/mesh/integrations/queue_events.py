"""Queue invalidation notifications + terminal write-back (integrations.md §3.9).

``integration.queue_updated`` is an *invalidation* notice (§3.9 写死): it
carries no mutable detail and maintains no revision counter — ordering and
idempotent invalidation follow the realtime envelope channel ``seq`` (README
§6.7). Clients refetch their authorized slice; they never patch local state.

Two payload shapes (project-level metadata isolation):

* workspace-scoped binding item → ``{integration_id, conversation_key,
  subject}`` on ``workspace:{ws}:integrations``;
* project-scoped binding item → ``{integration_id, subject, scope:'project'}``
  — **never** carries ``conversation_key`` (leaks nothing to members without
  project visibility); detail arrives via the authorized refetch.

The terminal write-back handler is chained into the ``execution.finished``
internal outbox event (runtime.md: the single terminal fan-out source). It is
the *only* driver of queue-item terminal states: ``completed → done``,
``failed/timeout → failed``, ``cancelled → cancelled`` (accepts both
``processing`` and ``cancelling``; terminal→terminal is a no-op). The same
transaction writes ``imq.dispatch_wake`` so the dispatcher picks up the next
pending item without waiting for the 1s tick, emits the invalidation notice,
and — for an item that was ``cancelling`` and finished ``cancelled`` — writes
the terminal-stage /stop feedback (§3.7 two-stage copy).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.integration import IntegrationMessageQueue
from mesh.db.models.outbox import OutboxEvent
from mesh.outbox.service import emit_event, emit_realtime

logger = logging.getLogger(__name__)

QUEUE_UPDATED_EVENT = "integration.queue_updated"
DISPATCH_WAKE_EVENT = "imq.dispatch_wake"
IM_SEND_EVENT = "im.send"

_QUEUE_UPDATED_SUBJECT = "queue_updated"

# execution.finished payload.status → queue item state (§2.10 state machine).
_FINISHED_STATUS_TO_QUEUE_STATE = {
    "completed": "done",
    "failed": "failed",
    "timeout": "failed",
    "cancelled": "cancelled",
}


def integrations_channel(workspace_id: object) -> str:
    """Realtime channel carrying workspace-level integration invalidations."""
    return f"workspace:{workspace_id}:integrations"


def stopped_feedback_text(message_excerpt: str) -> str:
    """Terminal-stage /stop feedback copy (§3.7 「🛑 已停止任务…」)."""
    return f"🛑 已停止任务「{message_excerpt}」"


async def emit_queue_updated(
    session: AsyncSession,
    *,
    item: IntegrationMessageQueue,
    idempotency_key: str | None = None,
) -> None:
    """Write the invalidation notice for a queue-item state change.

    Orphan rows (``integration_id IS NULL`` after parent deletion) carry no
    integration to invalidate against and are excluded from the normal queue
    endpoints entirely (§3.9 audit endpoint is their only read path) — no
    notice is emitted for them.
    """
    if item.integration_id is None:
        return
    channel = integrations_channel(item.workspace_id)
    if item.project_id_snapshot is not None:
        data: dict[str, Any] = {
            "integration_id": str(item.integration_id),
            "subject": _QUEUE_UPDATED_SUBJECT,
            "scope": "project",
        }
    else:
        data = {
            "integration_id": str(item.integration_id),
            "conversation_key": item.conversation_key,
            "subject": _QUEUE_UPDATED_SUBJECT,
        }
    key = idempotency_key or f"imq-updated:{item.id}:{item.state}:{item.seq}"
    await emit_realtime(
        session,
        workspace_id=item.workspace_id,
        channel=channel,
        event=QUEUE_UPDATED_EVENT,
        data=data,
        idempotency_key=key,
    )


async def emit_dispatch_wake(
    session: AsyncSession,
    *,
    workspace_id: Any,
    conversation_key: str,
    item_id: Any,
    reason: str,
) -> None:
    """Explicit dispatcher wakeup (the 1s tick is only a fallback, §3.9)."""
    await emit_event(
        session,
        workspace_id=workspace_id,
        event_type=DISPATCH_WAKE_EVENT,
        payload={"conversation_key": conversation_key, "reason": reason},
        idempotency_key=f"imq-wake:{item_id}:{reason}",
    )


async def queue_execution_finished_handler(session: AsyncSession, event: OutboxEvent) -> None:
    """Write back the queue-item terminal state from ``execution.finished``.

    Registered in the relay's composed ``execution.finished`` chain
    (workers/main.py). Idempotent: the conditional UPDATE accepts only
    ``processing``/``cancelling``; a repeated delivery or a race where the
    item already reached a terminal state updates 0 rows and returns.
    """
    payload = event.payload or {}
    execution_id = payload.get("execution_id")
    status = payload.get("status")
    if execution_id is None or status not in _FINISHED_STATUS_TO_QUEUE_STATE:
        return
    new_state = _FINISHED_STATUS_TO_QUEUE_STATE[status]

    rows = (
        (
            await session.execute(
                select(IntegrationMessageQueue)
                .where(IntegrationMessageQueue.execution_id == _as_uuid(execution_id))
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for item in rows:
        if item.state not in ("processing", "cancelling"):
            # Terminal→terminal (or still pending/dispatching — unreachable for
            # a bound execution) is a guarded no-op (§2.10 state machine).
            continue
        was_cancelling = item.state == "cancelling"
        item.state = new_state
        item.finished_at = func.now()
        await session.flush()
        await emit_queue_updated(
            session, item=item, idempotency_key=f"imq-updated:{item.id}:finished:{status}"
        )
        await emit_dispatch_wake(
            session,
            workspace_id=item.workspace_id,
            conversation_key=item.conversation_key,
            item_id=item.id,
            reason=f"finished:{status}",
        )
        if was_cancelling and new_state == "cancelled":
            # Terminal-stage /stop feedback (§3.7 两段式第二段): conversational
            # reply via im.send — not an ack, never coalesced, not routed
            # through notification_delivery (§3.8 台账注记).
            await emit_event(
                session,
                workspace_id=item.workspace_id,
                event_type=IM_SEND_EVENT,
                payload={
                    "kind": "command_feedback",
                    "stage": "stopped",
                    "integration_id": str(item.integration_id) if item.integration_id else None,
                    "conversation_key": item.conversation_key,
                    "queue_item_id": str(item.id),
                    "text": stopped_feedback_text(item.message_excerpt),
                },
                idempotency_key=f"im-cmdfb:{item.id}:stopped",
            )


def _as_uuid(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
