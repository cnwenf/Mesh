"""Queue dispatcher + lease repair (integrations.md §3.9).

Deployment shape: a supervised asyncio task inside ``mesh.workers`` (no new
compose service). The loop waits on an explicit ``imq.dispatch_wake`` outbox
event (set by the relay handler) with a 1s tick fallback; lease repair runs on
its own slower cadence. Every per-conversation / per-item step is its own
short transaction, entered with ``set_tenant_context`` so RLS equals the HTTP
path even though workers connect with the owner role.

Dispatch preconditions (by the head item's snapshot ``dispatch_mode``):

* ``serial_conversation`` → the conversation has NO in-flight item at all
  (including parallel ``dispatching/processing/cancelling`` — cross-mode
  serialization so serial never overlaps a residual parallel lane);
* ``parallel`` (dispatcher path only — normal parallel items were direct-
  dispatched at ingest) → same precondition (never overtake ordering).

The database-level backstop is ``uq_imq_conversation_active``: a second
serial dispatch for the conversation fails the unique index and backs off —
"no concurrent conflict" is a hard constraint, not worker cooperation.

Lease repair covers five branches (crash safety — every branch removes the
item from the "in-flight AND expired" set; a queued message is either
executed or reaches a queryable terminal state, never silently lost):

1. execution terminal (terminal event lost) → backfill done/failed/cancelled;
2. execution in-flight (long task, buffer too small) → renew the lease
   aligned to the attempt lease — never fail a running task;
3. execution ``queued`` within ``im_queue_max_stuck_seconds`` → renew (legal
   wait under capacity pressure);
4. execution ``queued`` beyond max-stuck → ``failed(dispatch_stuck)`` + alert,
   NEVER re-dispatch (the fixed idempotency key makes re-enqueue a no-op);
5. execution missing → outbox rearm, four states against the real
   ``outbox_events`` DDL (§6.6: status/delivery_attempts/available_at/
   published_at), derived row key ``K2 = sha256(K|'rearm'|item_id)`` while
   the payload keeps the execution-level key K.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    QUEUE_INFLIGHT_STATES,
    IntegrationBinding,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.outbox import OutboxEvent
from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.integrations.message_queue import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    build_execution_enqueue_payload,
    execution_idempotency_key,
)
from mesh.integrations.queue_events import (
    emit_dispatch_wake,
    emit_queue_updated,
)
from mesh.outbox.service import emit_event, scope_idempotency_key
from mesh.runtime.enqueue import ENQUEUE_EVENT_TYPE

logger = logging.getLogger(__name__)

EXECUTION_TERMINAL_STATES = ("completed", "failed", "timeout", "cancelled")
_FINISHED_STATUS_TO_QUEUE_STATE = {
    "completed": "done",
    "failed": "failed",
    "timeout": "failed",
    "cancelled": "cancelled",
}
_REARM_KEY_SEPARATOR = "|rearm|"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


async def run_dispatcher_pass(session_factory: async_sessionmaker, *, settings: Any) -> int:
    """One dispatcher sweep; returns the number of dispatched items."""
    candidates = await _candidate_conversations(session_factory)
    dispatched = 0
    for workspace_id, conversation_key in candidates:
        try:
            outcome = await _dispatch_conversation_head(
                session_factory,
                settings,
                workspace_id=workspace_id,
                conversation_key=conversation_key,
            )
            dispatched += 1 if outcome else 0
        except IntegrityError:
            # uq_imq_conversation_active: another replica owns the lane.
            logger.debug("dispatch contention on %s — backing off", conversation_key)
        except Exception:  # noqa: BLE001 — one conversation never blocks others
            logger.exception("dispatch pass failed for %s", conversation_key)
    return dispatched


async def _candidate_conversations(
    session_factory: async_sessionmaker,
) -> list[tuple[Any, str]]:
    """Conversations with a pending head item and no in-flight item at all."""
    async with session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT imq.workspace_id, imq.conversation_key
                  FROM integration_message_queue imq
                 WHERE imq.state = 'pending'
                   AND imq.seq = (
                         SELECT min(h.seq) FROM integration_message_queue h
                          WHERE h.conversation_key = imq.conversation_key
                            AND h.state = 'pending')
                   AND NOT EXISTS (
                         SELECT 1 FROM integration_message_queue o
                          WHERE o.conversation_key = imq.conversation_key
                            AND o.state IN ('dispatching','processing','cancelling'))
                """
            )
        )
        return [(r[0], r[1]) for r in rows.all()]


async def _dispatch_conversation_head(
    session_factory: async_sessionmaker,
    settings: Any,
    *,
    workspace_id: Any,
    conversation_key: str,
) -> bool:
    """Dispatch the FIFO head of one conversation (own transaction)."""
    async with session_factory() as session, session.begin():
        await set_tenant_context(session, workspace_id)
        # WAIT for the FIFO head — never SKIP LOCKED: skipping a
        # transiently-locked head dispatches a LATER item out of order
        # (observed: seq2 dispatched while seq1, briefly row-locked by a
        # concurrent short transaction, stayed pending forever — a §3.9
        # seq-order violation). Waiting keeps dispatch strictly at the
        # head; the holder is always a short transaction (the row lock is
        # released in milliseconds). Multi-replica contention still
        # resolves safely: a genuine double-dispatch dies on
        # uq_imq_conversation_active (IntegrityError → backoff, below in
        # run_dispatcher_pass).
        item = (
            (
                await session.execute(
                    select(IntegrationMessageQueue)
                    .where(
                        IntegrationMessageQueue.conversation_key == conversation_key,
                        IntegrationMessageQueue.state == "pending",
                    )
                    .order_by(IntegrationMessageQueue.seq.asc())
                    .limit(1)
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if item is None:
            return False  # no pending item left (head taken by another replica)

        # Snapshot target validation (§2.10: no silent repoint at a retargeted
        # binding; a vanished/disabled snapshot target fails the item).
        agent = (
            await session.scalar(select(Agent).where(Agent.id == item.target_agent_id))
            if item.target_agent_id
            else None
        )
        if agent is None or agent.lifecycle_status != "active":
            item.state = "failed"
            item.finished_at = func.now()
            await session.flush()
            logger.warning(
                "queue item %s failed: target_unavailable (agent snapshot)", item.id
            )
            await emit_queue_updated(
                session, item=item, idempotency_key=f"imq-updated:{item.id}:failed:target_unavailable"
            )
            await emit_dispatch_wake(
                session,
                workspace_id=workspace_id,
                conversation_key=conversation_key,
                item_id=item.id,
                reason="failed:target_unavailable",
            )
            return True

        item.state = "dispatching"
        item.lease_expires_at = datetime.now(UTC) + timedelta(
            seconds=DEFAULT_EXECUTION_TIMEOUT_SECONDS
            + int(settings.im_dispatch_lease_buffer_seconds)
        )
        await session.flush()

        binding = await session.scalar(
            select(IntegrationBinding).where(IntegrationBinding.id == item.binding_id)
        )
        event_row = await session.scalar(
            select(IntegrationEvent).where(IntegrationEvent.id == item.integration_event_id)
        )
        if binding is None or event_row is None:
            # Orphaning mid-flight is CHECK-blocked for non-terminal items, so
            # this is defensive only.
            item.state = "failed"
            item.finished_at = func.now()
            logger.warning("queue item %s failed: parent rows missing", item.id)
            return True

        provider = item.conversation_key.split(":", 1)[0]
        external_event_id = event_row.external_event_id or str(event_row.id)
        key = execution_idempotency_key(
            agent_id=agent.id, binding_id=binding.id, external_event_id=external_event_id
        )
        await emit_event(
            session,
            workspace_id=workspace_id,
            event_type=ENQUEUE_EVENT_TYPE,
            payload=build_execution_enqueue_payload(
                agent=agent,
                binding=binding,
                event_row=event_row,
                provider=provider,
                external_event_id=external_event_id,
                event_type=event_row.event_type,
                idempotency_key=key,
                queue_item_id=item.id,
            ),
            idempotency_key=key,
        )
        await emit_queue_updated(
            session, item=item, idempotency_key=f"imq-updated:{item.id}:dispatching"
        )
        return True


# ---------------------------------------------------------------------------
# Lease repair
# ---------------------------------------------------------------------------


async def run_lease_repair_pass(session_factory: async_sessionmaker, *, settings: Any) -> int:
    """Repair expired in-flight items; returns the number handled."""
    expired_ids = await _expired_item_ids(session_factory)
    handled = 0
    for workspace_id, item_id in expired_ids:
        try:
            async with session_factory() as session, session.begin():
                await set_tenant_context(session, workspace_id)
                item = await session.get(
                    IntegrationMessageQueue, item_id, with_for_update=True
                )
                if item is None or item.state not in QUEUE_INFLIGHT_STATES:
                    continue
                if item.lease_expires_at is None or item.lease_expires_at >= datetime.now(
                    UTC
                ):
                    continue
                await _repair_one(session, settings, item)
                handled += 1
        except Exception:  # noqa: BLE001 — poison rows must not stall the scan
            logger.exception("lease repair failed for item %s", item_id)
    return handled


async def _expired_item_ids(
    session_factory: async_sessionmaker,
) -> list[tuple[Any, Any]]:
    async with session_factory() as session:
        rows = await session.execute(
            select(IntegrationMessageQueue.workspace_id, IntegrationMessageQueue.id).where(
                IntegrationMessageQueue.state.in_(QUEUE_INFLIGHT_STATES),
                IntegrationMessageQueue.lease_expires_at < func.now(),
            )
        )
        return [(r[0], r[1]) for r in rows.all()]


async def _repair_one(session: AsyncSession, settings: Any, item: IntegrationMessageQueue) -> None:
    execution = (
        await session.scalar(
            select(TaskExecution).where(TaskExecution.id == item.execution_id)
        )
        if item.execution_id
        else None
    )

    # Branch 1: execution reached a terminal state (terminal event lost).
    if execution is not None and execution.status in EXECUTION_TERMINAL_STATES:
        await _backfill_terminal(session, item, execution.status)
        return

    # Branch 2: execution still in-flight — renew, never fail a running task.
    if execution is not None and execution.status in ("claimed", "running", "cancelling"):
        await _renew_lease_aligned(session, item)
        return

    # Branches 3/4: execution queued — capacity wait vs stuck.
    if execution is not None and execution.status in ("queued", "awaiting_approval"):
        stuck_seconds = _seconds_since(execution.queued_at)
        if stuck_seconds < settings.im_queue_max_stuck_seconds:
            await _renew_lease_aligned(session, item)  # branch 3: legal wait
            return
        # Branch 4: stuck — fail, alert, never re-dispatch.
        item.state = "failed"
        item.finished_at = func.now()
        logger.error(
            "ALERT queue item %s failed: dispatch_stuck (execution %s queued %.0fs)",
            item.id,
            execution.id,
            stuck_seconds,
        )
        await session.flush()
        await emit_queue_updated(
            session, item=item, idempotency_key=f"imq-updated:{item.id}:failed:dispatch_stuck"
        )
        await emit_dispatch_wake(
            session,
            workspace_id=item.workspace_id,
            conversation_key=item.conversation_key,
            item_id=item.id,
            reason="failed:dispatch_stuck",
        )
        return

    # Branch 5: execution missing — outbox rearm (four states).
    await _rearm_enqueue_event(session, settings, item)


async def _backfill_terminal(
    session: AsyncSession, item: IntegrationMessageQueue, execution_status: str
) -> None:
    new_state = _FINISHED_STATUS_TO_QUEUE_STATE[execution_status]
    was_cancelling = item.state == "cancelling"
    item.state = new_state
    item.finished_at = func.now()
    await session.flush()
    await emit_queue_updated(
        session, item=item, idempotency_key=f"imq-updated:{item.id}:finished:{execution_status}"
    )
    await emit_dispatch_wake(
        session,
        workspace_id=item.workspace_id,
        conversation_key=item.conversation_key,
        item_id=item.id,
        reason=f"finished:{execution_status}",
    )
    if was_cancelling and new_state == "cancelled":
        from mesh.integrations.queue_events import IM_SEND_EVENT, stopped_feedback_text

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


async def _renew_lease_aligned(session: AsyncSession, item: IntegrationMessageQueue) -> None:
    """Lease = max(now()+buffer, the execution's current attempt lease)."""
    attempt_lease = await session.scalar(
        select(func.max(ExecutionAttempt.lease_expires_at)).where(
            ExecutionAttempt.execution_id == item.execution_id,
            ExecutionAttempt.status.in_(("claimed", "running", "cancelling")),
        )
    )
    candidates = [
        datetime.now(UTC) + timedelta(seconds=DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS)
    ]
    if attempt_lease is not None:
        candidates.append(attempt_lease)
    item.lease_expires_at = max(candidates)
    await session.flush()


DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS = 300


async def _rearm_enqueue_event(
    session: AsyncSession, settings: Any, item: IntegrationMessageQueue
) -> None:
    """Branch 5: the execution row is missing — repair via the outbox event."""
    key = await _original_enqueue_key(session, item)
    if key is None:
        # Parent rows gone mid-flight (defensive); nothing to rearm with.
        item.state = "failed"
        item.finished_at = func.now()
        logger.warning("queue item %s failed: enqueue key unrecoverable", item.id)
        await session.flush()
        return
    scoped_key = scope_idempotency_key(item.workspace_id, key)
    original = await session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.workspace_id == item.workspace_id,
            OutboxEvent.idempotency_key == scoped_key,
        )
    )

    if original is not None and original.status == "pending":
        sla_age = _seconds_since(original.created_at)
        if sla_age <= settings.outbox_consume_sla_seconds:
            # 5a (within SLA): a relay will consume it — renew and wait.
            item.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS
            )
            await session.flush()
            return
        # 5a escalation: pending past the consume SLA with no execution row —
        # treat as lost; fall through to the derived-key rearm (d).
    elif original is not None and original.status == "failed":
        # 5b: conditional rearm — 0 rows means another repairer won the race.
        result = await session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == original.id, OutboxEvent.status == "failed")
            .values(status="pending", delivery_attempts=0, published_at=None)
        )
        if result.rowcount == 0:
            item.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS
            )
            await session.flush()
            return
        # The relay re-consumes under the original key; keep the item leased.
        item.lease_expires_at = datetime.now(UTC) + timedelta(
            seconds=DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS
        )
        await session.flush()
        return
    elif original is not None and original.status == "published":
        # 5c: anomalous (handler claimed success but the row is missing).
        # The original row is KEPT — outbox terminal rows are audit-retained
        # (§6.6); the derived key never collides, so no placeholder release is
        # needed. Fall through to d.
        logger.error(
            "ALERT queue item %s: enqueue event %s published but execution missing",
            item.id,
            original.id,
        )
    # 5d (missing row or 5c): write a derived event. Row-level key K2 (outbox
    # dedupe only); payload keeps execution-level key K + queue_item_id so the
    # consumer resolves the SAME task_executions row (R5-2).
    await _write_derived_enqueue(session, settings, item, key)


async def _write_derived_enqueue(
    session: AsyncSession, settings: Any, item: IntegrationMessageQueue, key: str
) -> None:
    binding = await session.scalar(
        select(IntegrationBinding).where(IntegrationBinding.id == item.binding_id)
    )
    event_row = await session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.id == item.integration_event_id)
    )
    agent = (
        await session.scalar(select(Agent).where(Agent.id == item.target_agent_id))
        if item.target_agent_id
        else None
    )
    if binding is None or event_row is None or agent is None:
        item.state = "failed"
        item.finished_at = func.now()
        logger.warning("queue item %s failed: rearm inputs missing", item.id)
        await session.flush()
        return
    provider = item.conversation_key.split(":", 1)[0]
    external_event_id = event_row.external_event_id or str(event_row.id)
    k2 = rearm_row_key(original_key=key, item_id=item.id)
    await emit_event(
        session,
        workspace_id=item.workspace_id,
        event_type=ENQUEUE_EVENT_TYPE,
        payload=build_execution_enqueue_payload(
            agent=agent,
            binding=binding,
            event_row=event_row,
            provider=provider,
            external_event_id=external_event_id,
            event_type=event_row.event_type,
            idempotency_key=key,
            queue_item_id=item.id,
        ),
        idempotency_key=k2,
    )
    item.lease_expires_at = datetime.now(UTC) + timedelta(
        seconds=DEFAULT_QUEUE_REPAIR_BUFFER_SECONDS
    )
    await session.flush()


async def _original_enqueue_key(
    session: AsyncSession, item: IntegrationMessageQueue
) -> str | None:
    """Recompute K = sha256(agent|binding|external_event_id) for the item."""
    if item.target_agent_id is None or item.binding_id is None:
        return None
    event_row = await session.scalar(
        select(IntegrationEvent).where(IntegrationEvent.id == item.integration_event_id)
    )
    if event_row is None:
        return None
    return execution_idempotency_key(
        agent_id=item.target_agent_id,
        binding_id=item.binding_id,
        external_event_id=event_row.external_event_id or str(event_row.id),
    )


def rearm_row_key(*, original_key: str, item_id: Any) -> str:
    """K2 = sha256(K | 'rearm' | item_id) hex — outbox ROW-level key only.

    Never collides with the original key's unique constraint; the payload
    still carries the execution-level key K (T39-9/T39-14/T39-17).
    """
    return hashlib.sha256(
        f"{original_key}{_REARM_KEY_SEPARATOR}{item_id}".encode()
    ).hexdigest()


def _seconds_since(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - value).total_seconds())


# ---------------------------------------------------------------------------
# Supervised loop
# ---------------------------------------------------------------------------


async def dispatcher_loop(
    session_factory: async_sessionmaker,
    *,
    settings: Any,
    wake: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    """Queue dispatcher task: wake-event driven with a 1s tick fallback.

    ``wake`` is set by the relay handler registered for ``imq.dispatch_wake``
    (workers/main.py). Lease repair interleaves on its own interval.
    """
    last_repair = datetime.now(UTC)
    while not stop.is_set():
        try:
            await asyncio.wait_for(wake.wait(), timeout=settings.im_dispatch_tick_seconds)
        except TimeoutError:
            pass  # tick fallback
        wake.clear()
        if stop.is_set():
            break
        try:
            await run_dispatcher_pass(session_factory, settings=settings)
        except Exception:  # noqa: BLE001 — supervised task must keep running
            logger.exception("dispatcher pass crashed")
        now = datetime.now(UTC)
        if (now - last_repair).total_seconds() >= settings.im_lease_repair_interval_seconds:
            last_repair = now
            try:
                await run_lease_repair_pass(session_factory, settings=settings)
            except Exception:  # noqa: BLE001
                logger.exception("lease repair pass crashed")


def make_dispatch_wake_handler(wake: asyncio.Event):
    """Relay handler factory: any ``imq.dispatch_wake`` event wakes the loop."""

    async def _handler(session: AsyncSession, event: OutboxEvent) -> None:
        wake.set()

    return _handler


__all__ = [
    "make_dispatch_wake_handler",
    "dispatcher_loop",
    "rearm_row_key",
    "run_dispatcher_pass",
    "run_lease_repair_pass",
]
