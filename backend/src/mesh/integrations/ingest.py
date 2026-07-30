"""Shared ingestion core (integrations.md §2.10:651-664, MES-87).

The ingestion layering boundary — written in stone:

    [platform auth adapters (swapped per receive mode)]
      HTTP callback adapter: timestamp+sign verification / integration
        location → normalized VerifiedEnvelope
      Stream channel adapter: connection-level authentication (signature
        equivalent) / frame routing → normalized VerifiedEnvelope
    [shared ingestion core — the ONE implementation, this module]
      ingest_verified_event(envelope)

Both receive modes differ ONLY in the auth adapter in front; everything
behind the envelope is this single code path.

Pipeline order (the MES-88 released shape, adopted verbatim — this core
delegates instead of re-implementing):

    disabled gate → dedup (UNIQUE(integration_id, external_event_id))
      → msgtype gate (text-only triggering; non-text audit-only)
      → normalized keys (§2.10 N-1 / E-1, fail-closed segment validation)
      → sender-less messages audit-only (queue items need a resolvable
         triple; authorization would be impossible)
      → command plane (§3.7, ``commands.maybe_handle_command`` — /stop ·
         /btw · /help; commands never queue and never trigger; /btw
         without an in-flight item continues as an ordinary message)
      → binding match (§6.9: unmatched / no agent → audit only)
      → post-signature semantic guards (§2.10, ``inbound_guards`` —
         identity 20/min · conversation 60/min · pending depth pre-check;
         FAIL-CLOSED: a Redis outage raises through and the ingest
         transaction rolls back — the platform re-pushes, dedupe keeps it
         safe; never fail-open)
      → conversation queue (``message_queue.enqueue_message`` — imq_seq
         advisory lock → lock-order ``ack_window_at`` → the AUTHORITATIVE
         pending-depth re-check under the lock → seq=max+1 → dispatch_mode
         snapshot (drain-then-switch) → §3.8 ack leader determination →
         parallel optimistic dispatch with the §6.9 key + queue_item_id →
         ``integration.queue_updated`` invalidation notices)
      → audit ledger + realtime (integration.event_ingested)
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from mesh.db.constraints import violates as _violates_constraint
from mesh.db.models.agent import Agent
from mesh.db.models.integration import (
    Integration,
    IntegrationBinding,
    IntegrationEvent,
)
from mesh.db.tenant import set_tenant_context
from mesh.errors import ValidationError
from mesh.integrations.commands import maybe_handle_command
from mesh.integrations.connectors import NormalizedEvent, VerifiedEnvelope
from mesh.integrations.inbound_guards import (
    InboundGuardRejected,
    check_inbound_guards,
    rate_limit_hint_allowed,
)
from mesh.integrations.matching import binding_matches
from mesh.integrations.message_queue import enqueue_message
from mesh.integrations.queue_events import IM_SEND_EVENT
from mesh.integrations.queue_keys import (
    build_conversation_key,
    conversation_delivery_fields,
)
from mesh.outbox.service import emit_event, emit_realtime

logger = logging.getLogger("mesh.integrations.ingest")

REJECTED_KEY_PREFIX = "rejected:"

# Rejected audit rows carry UNTRUSTED, attacker-inflatable external content —
# persist a size-capped head only (forensic prefix + original byte size).
REJECTED_PAYLOAD_MAX_BYTES = 16 * 1024
REJECTED_PAYLOAD_HEAD_BYTES = 4 * 1024

# IM providers go through the conversation queue; VCS providers keep the
# direct §6.9 path in inbound.py (events, not messages).
IM_PROVIDERS = frozenset({"dingtalk", "feishu", "slack"})

# §2.10 self-rate-limit notice text (the MES-88 released literal — the
# emitter side of the over-limit disposition; kept identical to the
# conversation-queue pipeline's published copy).
_RATE_LIMIT_HINT_TEXT = "Messages are arriving too fast — please slow down a little."

# Fallbacks mirror mesh.config.Settings defaults for callers that do not
# thread settings through (legacy unit tests); production always passes the
# real Settings object (inbound_routes / the Stream manager).
_IM_SETTINGS_DEFAULTS = SimpleNamespace(
    im_ack_coalesce_window_seconds=5.0,
    im_inbound_per_identity_per_min=20,
    im_inbound_per_conversation_per_min=60,
    im_queue_max_pending_per_conversation=50,
    im_inbound_text_max_chars=4000,
    im_dispatch_lease_buffer_seconds=300,
    context_append_max_count=20,
    context_append_max_chars=32000,
)


def _resolve_im_settings(settings: Any) -> Any:
    return settings if settings is not None else _IM_SETTINGS_DEFAULTS


@dataclass(frozen=True)
class IngestResult:
    """Outcome of one envelope through the shared core.

    ``status_code`` / ``body`` render the bare-JSON platform contract on the
    HTTP channel (NOT the §6.14 envelope); the Stream adapter ACKs frames
    off the same outcome and ignores the HTTP shape.
    """

    status_code: int
    body: dict[str, Any]
    process_status: str
    event_id: uuid.UUID | None = None
    queue_item_id: uuid.UUID | None = None
    deduped: bool = False


def enqueue_idempotency_key(
    *, agent_id: uuid.UUID, binding_id: uuid.UUID, external_event_id: str
) -> str:
    """README §6.9 / integrations.md §3.2: sha256(agent|binding|external_event).

    Identical formula to ``message_queue.execution_idempotency_key`` (the
    queue path's copy; a unit test pins the two equal). Kept here for the
    VCS direct §6.9 path in inbound.py, which never touches the queue.
    """
    return hashlib.sha256(
        f"{agent_id}|{binding_id}|{external_event_id}".encode()
    ).hexdigest()


def audit_payload(payload: dict[str, Any], process_status: str) -> dict[str, Any]:
    """Truncate rejected-audit payloads (valid events keep full payloads)."""
    if process_status != "rejected":
        return payload
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) <= REJECTED_PAYLOAD_MAX_BYTES:
        return payload
    return {
        "_truncated": True,
        "original_bytes": len(encoded),
        "head": encoded[:REJECTED_PAYLOAD_HEAD_BYTES].decode("utf-8", "replace"),
    }


def _mark_payload(event_row: IntegrationEvent, key: str, value: Any) -> None:
    """Set one audit marker on the event payload (JSONB mutation-safe)."""
    payload = dict(event_row.payload or {})
    payload[key] = value
    event_row.payload = payload
    flag_modified(event_row, "payload")


def _rejected_event_id(raw_payload: dict[str, Any]) -> str:
    """The ``rejected:<hash>`` namespace (anti pre-occupation, §3.2)."""
    canonical = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True).encode()
    return f"{REJECTED_KEY_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


async def store_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    external_event_id: str,
    event_type: str,
    payload: dict[str, Any],
    signature_status: str,
    process_status: str,
    now: datetime,
) -> IntegrationEvent:
    """Insert one ledger row (dedup key: integration_id + external_event_id)."""
    event = IntegrationEvent(
        workspace_id=workspace_id,
        integration_id=integration_id,
        external_event_id=external_event_id,
        event_type=event_type,
        payload=audit_payload(payload, process_status),
        signature_status=signature_status,
        process_status=process_status,
        received_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(event)
    await session.flush()
    return event


async def match_bindings(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration: Integration,
    external_ref: str,
) -> list[IntegrationBinding]:
    """Active bindings whose external_ref equals the event's external object."""
    rows = (
        await session.execute(
            select(IntegrationBinding).where(
                IntegrationBinding.workspace_id == workspace_id,
                IntegrationBinding.integration_id == integration.id,
                IntegrationBinding.external_ref == external_ref,
                IntegrationBinding.status == "active",
            )
        )
    ).scalars().all()
    return list(rows)


def _is_triggering(envelope: VerifiedEnvelope) -> bool:
    """§3.2 msgtype matrix: DingTalk triggers on text ONLY (non-text is
    audit-only); feishu/slack carry no platform delivery matrix here —
    '' / 'text' message types trigger."""
    if envelope.provider == "dingtalk":
        from mesh.integrations.dingtalk import TRIGGER_MSGTYPES

        return envelope.msgtype in TRIGGER_MSGTYPES
    return envelope.msgtype in ("", "text")


# ---------------------------------------------------------------------------
# Over-limit disposition (§2.10)
# ---------------------------------------------------------------------------


async def _reject_rate_limited(
    session: AsyncSession,
    *,
    redis: Any,
    integration: Integration,
    event_row: IntegrationEvent,
    envelope: VerifiedEnvelope,
    conversation_key: str,
    now: datetime,
) -> IngestResult:
    """Over-limit disposition (§2.10): NOT enqueued/executed/acked — rejected
    audit under the REAL msgId dedupe key; one bot hint per minute per
    conversation (notice-reflection guard); bare 200 (non-2xx would trigger
    platform re-push amplification).

    The row was stored as 'received' (FULL payload, up to the 1MiB route
    cap) — flipping to 'rejected' re-runs the §3.2 16KiB truncation,
    otherwise an over-limit flood sediments ≤1MiB ledger rows (the 16KiB
    defense exists precisely for rejected rows).

    The hint payload is SELF-SPECIFIED: a rejected message is never
    enqueued, so the relay's queue-item derivation has nothing to read —
    the conversation type (and the direct-chat target) travel with the
    payload (MES-122), derived from the FULL pre-truncation raw payload.
    """
    event_row.process_status = "rejected"
    event_row.payload = {
        **audit_payload(event_row.payload or {}, "rejected"),
        "_mesh_reject_reason": "rate_limited",  # survives truncation (structured)
    }
    event_row.updated_at = now
    await session.flush()
    if redis is not None and await rate_limit_hint_allowed(
        redis, conversation_key=conversation_key
    ):
        await emit_event(
            session,
            workspace_id=integration.workspace_id,
            event_type=IM_SEND_EVENT,
            payload={
                "kind": "rate_limit_hint",
                "integration_id": str(integration.id),
                "conversation_key": conversation_key,
                **conversation_delivery_fields(
                    envelope.raw_payload, actor_key=envelope.sender_key
                ),
                "text": _RATE_LIMIT_HINT_TEXT,
            },
            idempotency_key=f"im-hint:{event_row.id}",
        )
    logger.warning(
        "inbound rate-limited: event %s conversation %s", event_row.id, conversation_key
    )
    return IngestResult(
        status_code=200,
        body={
            "received": True,
            "event_id": envelope.external_event_id,
            "process_status": "rejected",
            "reason": "rate_limited",
        },
        process_status="rejected",
        event_id=event_row.id,
    )


async def _reject_invalid_request(
    session: AsyncSession,
    *,
    envelope: VerifiedEnvelope,
    event_row: IntegrationEvent,
    now: datetime,
) -> IngestResult:
    """A key the §2.10 segment rules refuse (e.g. a colon-carrying segment
    from a signed-but-hostile payload) is a REJECTION, not a 500: audit on
    the existing ledger row and answer bare-JSON 200 — the platform must
    never see a §6.14 envelope or a non-2xx retry trigger here."""
    event_row.process_status = "rejected"
    # Re-run the §3.2 16KiB truncation on the flip (stored as 'received'
    # with the full payload — rejected rows keep only the forensic head).
    event_row.payload = {
        **audit_payload(event_row.payload or {}, "rejected"),
        "_mesh_reject_reason": "invalid_request",  # survives truncation
    }
    event_row.updated_at = now
    await session.flush()
    return IngestResult(
        status_code=200,
        body={
            "received": True,
            "event_id": envelope.external_event_id,
            "process_status": "rejected",
            "reason": "invalid_request",
        },
        process_status="rejected",
        event_id=event_row.id,
    )


# ---------------------------------------------------------------------------
# The shared core
# ---------------------------------------------------------------------------


async def ingest_verified_event(
    session: AsyncSession,
    *,
    integration: Integration,
    envelope: VerifiedEnvelope,
    now: datetime,
    redis: Any = None,
    settings: Any = None,
) -> IngestResult:
    """Run one AUTHENTICATED envelope through the shared ingestion core.

    The caller (HTTP auth adapter / Stream channel adapter) has already
    established authenticity — per-request signature or channel-level
    connection auth — and normalized the payload. Runs inside the caller's
    transaction; returns the bare-JSON contract outcome.

    ``redis`` / ``settings`` enable the §2.10 semantic guards (FAIL-CLOSED:
    a Redis outage raises through and rolls the ingest transaction back —
    the platform re-pushes, dedupe keeps it safe). The conversation queue
    (MES-88 ``enqueue_message``) runs regardless — its lock-held pending
    depth re-check is authoritative even without the Redis fast path.
    Production always wires both (inbound_routes / the Stream manager).
    """
    if envelope.provider not in IM_PROVIDERS:
        raise ValueError(
            f"ingest_verified_event is the IM message path; {envelope.provider!r} "
            "events use the VCS direct path (inbound.py)"
        )
    await set_tenant_context(session, integration.workspace_id)
    workspace_id = integration.workspace_id
    settings = _resolve_im_settings(settings)

    # Disabled integration → reject distribution (§5.1).
    if integration.status == "disabled":
        try:
            async with session.begin_nested():
                await store_event(
                    session,
                    workspace_id=workspace_id,
                    integration_id=integration.id,
                    external_event_id=_rejected_event_id(envelope.raw_payload),
                    event_type=envelope.event_type,
                    payload=envelope.raw_payload,
                    signature_status="valid",
                    process_status="rejected",
                    now=now,
                )
        except IntegrityError as exc:
            # repeated event, same body → already audited (dedup key); any
            # other constraint violation is logged, never disguised as dedup.
            if not _violates_constraint(exc, "uq_integration_event_dedup"):
                logger.exception(
                    "disabled-rejection audit insert failed on a non-dedup constraint"
                )
        return IngestResult(
            status_code=401,
            body={
                "error": {
                    "code": "integration_disabled",
                    "message": "integration is disabled; inbound events are rejected",
                    "details": {},
                }
            },
            process_status="rejected",
        )

    # Dedup INSERT — first writer wins; conflict → idempotent 200, never
    # dispatched twice (§6.9). The real msgId occupies the dedup slot (also
    # the over-limit behavior — no retry storm on the same msg).
    try:
        async with session.begin_nested():
            event_row = await store_event(
                session,
                workspace_id=workspace_id,
                integration_id=integration.id,
                external_event_id=envelope.external_event_id,
                event_type=envelope.event_type,
                payload=_channel_payload(envelope),
                signature_status="valid",
                process_status="received",
                now=now,
            )
    except IntegrityError as exc:
        # ONLY the dedup key maps to idempotent 200; any other constraint
        # violation is a real defect and must surface (relay → alert).
        if not _violates_constraint(exc, "uq_integration_event_dedup"):
            raise
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "deduped",
            },
            process_status="deduped",
            deduped=True,
        )

    await _emit_event_ingested(
        session,
        workspace_id=workspace_id,
        integration=integration,
        event_row=event_row,
        envelope=envelope,
        process_status="received",
    )

    # msgtype matrix gate: non-text (or empty text) → audit-only (processed;
    # no trigger, no queue, no ack, no counters — §3.2 matrix / C-1).
    im_text = (envelope.text or "").strip()
    if not _is_triggering(envelope) or not im_text:
        event_row.process_status = "processed"
        _mark_payload(event_row, "_mesh_trigger_skipped", "non_text")
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "processed",
            },
            process_status="processed",
            event_id=event_row.id,
        )

    # Normalized conversation key (§2.10 N-1 — segment validation fails
    # closed; a signed-but-hostile payload with a colon-carrying segment is
    # a rejection, not a 500).
    try:
        conversation_key = build_conversation_key(
            envelope.provider, envelope.provider_tenant_key, envelope.external_ref
        )
    except ValidationError:
        return await _reject_invalid_request(
            session, envelope=envelope, event_row=event_row, now=now
        )

    # Sender-less messages are audit-only: never enqueued — authorization
    # would be impossible and queue items must carry a resolvable triple.
    if not envelope.sender_key:
        event_row.process_status = "matched"
        _mark_payload(event_row, "_mesh_trigger_skipped", "no_sender_identity")
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "matched",
            },
            process_status="matched",
            event_id=event_row.id,
        )

    # Command plane (§3.7, MES-88) — commands never queue and never trigger.
    # Runs BEFORE matching/guards (the released pipeline order); /btw with
    # no in-flight item falls through as an ordinary message with the
    # stripped argument text (§3.7 passthrough).
    effective_text = im_text
    outcome = await maybe_handle_command(
        session,
        settings=settings,
        integration=integration,
        event_row=event_row,
        normalized_text=im_text,
        provider=envelope.provider,
        tenant_key=envelope.provider_tenant_key,
        user_key=envelope.sender_key,
        conversation_key=conversation_key,
    )
    if outcome is not None:
        event_row.process_status = "processed"
        event_row.updated_at = now
        await session.flush()
        if outcome.passthrough_text is None:
            return IngestResult(
                status_code=200,
                body={
                    "received": True,
                    "event_id": envelope.external_event_id,
                    "process_status": "processed",
                },
                process_status="processed",
                event_id=event_row.id,
            )
        effective_text = outcome.passthrough_text

    # Binding match (§6.9: unmatched / no agent → audit only, no trigger).
    bindings = await match_bindings(
        session,
        workspace_id=workspace_id,
        integration=integration,
        external_ref=envelope.external_ref,
    )
    if len(bindings) > 1:
        # Ambiguous routing → audit + alert, trigger NOTHING (§5.4).
        logger.error(
            "multiple bindings matched inbound event %s (integration %s) — "
            "dispatch suppressed",
            event_row.id,
            integration.id,
        )
        event_row.process_status = "matched"
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "matched",
            },
            process_status="matched",
            event_id=event_row.id,
        )

    if not bindings:
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "received",
            },
            process_status="received",
            event_id=event_row.id,
        )

    binding = bindings[0]
    match_config = dict(binding.match_config or {})
    match_event = NormalizedEvent(
        external_event_id=envelope.external_event_id,
        event_type=envelope.event_type,
        external_ref=envelope.external_ref,
        actor_key=envelope.sender_key,
        tenant_key=envelope.provider_tenant_key,
        text=effective_text,
        extra=dict(envelope.extra),
    )
    bound_agent = str(binding.bound_agent_id) if binding.bound_agent_id else None
    if not binding_matches(
        envelope.provider,
        match_config,
        match_event,
        bot_mentioned=envelope.bot_mentioned,
        is_direct_message=envelope.is_direct_message,
        bound_agent_id=bound_agent,
    ) or binding.bound_agent_id is None:
        event_row.process_status = "matched"  # audit only (§6.9)
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "matched",
            },
            process_status="matched",
            event_id=event_row.id,
        )

    agent = await session.scalar(
        select(Agent).where(Agent.id == binding.bound_agent_id)
    )
    if agent is None or agent.lifecycle_status != "active":
        # Agent soft-deleted / paused → audit only (§6.9).
        event_row.process_status = "matched"
        event_row.updated_at = now
        await session.flush()
        return IngestResult(
            status_code=200,
            body={
                "received": True,
                "event_id": envelope.external_event_id,
                "process_status": "matched",
            },
            process_status="matched",
            event_id=event_row.id,
        )

    # §2.10 frequency guards (MES-88 inbound_guards) — FAIL-CLOSED: a Redis
    # outage raises through and rolls back the ingest transaction (the
    # platform re-pushes, dedupe keeps it safe); never fail-open. The
    # pending-DEPTH fast path here is advisory; enqueue_message re-checks
    # it authoritatively under the imq_seq lock.
    if redis is not None:
        try:
            await check_inbound_guards(
                redis,
                session,
                settings=settings,
                provider=envelope.provider,
                tenant_key=envelope.provider_tenant_key,
                user_key=envelope.sender_key,
                conversation_key=conversation_key,
            )
        except InboundGuardRejected:
            return await _reject_rate_limited(
                session,
                redis=redis,
                integration=integration,
                event_row=event_row,
                envelope=envelope,
                conversation_key=conversation_key,
                now=now,
            )

    # Conversation queue (MES-88 message_queue): imq_seq advisory lock →
    # lock-order ack_window_at → AUTHORITATIVE pending-depth re-check →
    # seq=max+1 → dispatch_mode snapshot → §3.8 ack leader → parallel
    # optimistic dispatch (execution.enqueue with queue_item_id) →
    # integration.queue_updated invalidation notice.
    try:
        result = await enqueue_message(
            session,
            settings=settings,
            integration=integration,
            binding=binding,
            event_row=event_row,
            event=match_event,
            provider=envelope.provider,
        )
    except InboundGuardRejected as exc:
        # The authoritative pending-depth gate under the conversation lock
        # (queue_seq_conflict is the practically-unreachable backstop —
        # rejected the same way, never silently dropped).
        logger.warning(
            "inbound enqueue rejected (%s): event %s conversation %s",
            exc.reason,
            event_row.id,
            conversation_key,
        )
        return await _reject_rate_limited(
            session,
            redis=redis,
            integration=integration,
            event_row=event_row,
            envelope=envelope,
            conversation_key=conversation_key,
            now=now,
        )
    except ValidationError:
        return await _reject_invalid_request(
            session, envelope=envelope, event_row=event_row, now=now
        )

    item = result.item
    # 'dispatched' = "in the conversation queue AND dispatch is determined"
    # (serial = awaiting the dispatcher in seq order; parallel = optimistically
    # dispatched already). The vocabulary gains no new value (§3.2 note).
    event_row.process_status = "dispatched"
    event_row.updated_at = now
    await session.flush()

    await _emit_event_ingested(
        session,
        workspace_id=workspace_id,
        integration=integration,
        event_row=event_row,
        envelope=envelope,
        process_status="dispatched",
    )

    return IngestResult(
        status_code=200,
        body={
            "received": True,
            "event_id": envelope.external_event_id,
            "process_status": "dispatched",
            "dispatched": True,
            "queue_item_id": str(item.id),
            "queue_state": item.state,
            "queue_seq": item.seq,
        },
        process_status="dispatched",
        event_id=event_row.id,
        queue_item_id=item.id,
    )


def _channel_payload(envelope: VerifiedEnvelope) -> dict[str, Any]:
    """Ledger payload = raw payload + channel provenance (§3.2: stream
    frames carry ``_mesh_channel='stream'``; truncated text is flagged)."""
    payload = dict(envelope.raw_payload or {})
    payload["_mesh_channel"] = envelope.channel
    if envelope.truncated:
        payload["truncated"] = True
    return payload


async def _emit_event_ingested(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    integration: Integration,
    event_row: IntegrationEvent,
    envelope: VerifiedEnvelope,
    process_status: str,
) -> None:
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"workspace:{workspace_id}:integrations",
        event="integration.event_ingested",
        data={
            "event_id": str(event_row.id),
            "integration_id": str(integration.id),
            "event_type": envelope.event_type,
            "signature_status": "valid",
            "process_status": process_status,
        },
        idempotency_key=f"integration-event:{event_row.id}:{process_status}",
    )
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=f"integration:{integration.id}",
        event="integration.event_ingested",
        data={
            "event_id": str(event_row.id),
            "event_type": envelope.event_type,
            "signature_status": "valid",
            "process_status": process_status,
        },
        idempotency_key=f"integration-event:{event_row.id}:{process_status}:detail",
    )


__all__ = [
    "IM_PROVIDERS",
    "IngestResult",
    "REJECTED_KEY_PREFIX",
    "REJECTED_PAYLOAD_HEAD_BYTES",
    "REJECTED_PAYLOAD_MAX_BYTES",
    "audit_payload",
    "enqueue_idempotency_key",
    "ingest_verified_event",
    "match_bindings",
    "store_event",
]
