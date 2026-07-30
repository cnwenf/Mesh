"""IM command plane — /stop · /btw · /help (integrations.md §3.7).

Command messages are CONTROL-plane traffic: parsed after dedupe and BEFORE
binding matching, processed immediately, never enqueued, never triggering an
execution (except the documented ``/btw``-with-no-in-flight-item fallthrough,
where the stripped argument continues as an ordinary message). All three IM
platforms share this registry; only text normalization is connector-specific.

Authorization is triple-based, never bare-key: the initiator's
``provider:tenant:user_key`` resolves through ``external_identities`` to a
global ``users.id``; queue items carry the full ``sender_identity_key``
triple which is resolved the same way and compared by ``users.id`` equality.
The same string under a different provider/tenant may map to a different
Mesh user — bare ``external_user_key`` comparison is forbidden (§5.6
cross-provider negative test). Members holding ``integration:manage``
(admin/owner of the binding's workspace) may act on others' items.

/stop is two-phase: ``processing → cancelling`` (atomic state guard, the
runtime cancel intent persisted in the SAME transaction via
``request_execution_cancel_tx`` — no new outbox event type; the daemon
stops via heartbeat downlink) and the item keeps occupying the serial lane
until ``execution.finished(cancelled)`` arrives; the initiator's ``pending``
items cancel immediately. Multi-person semantics: each person's /stop
touches only their own items, never refusing as a whole because someone
else's task cannot be stopped.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from mesh.auth.rbac import role_satisfies
from mesh.db.models.integration import (
    Integration,
    IntegrationEvent,
    IntegrationMessageQueue,
)
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import BusinessRuleError
from mesh.integrations.queue_events import IM_SEND_EVENT, emit_queue_updated
from mesh.integrations.queue_keys import (
    sanitize_excerpt,
    truncate_inbound_text,
    validate_sender_identity_key,
)
from mesh.outbox.service import emit_event
from mesh.runtime.attempts import request_execution_cancel_tx
from mesh.runtime.context_appends import append_context

logger = logging.getLogger(__name__)

# Line-start only; a "/stop" mid-message is ordinary text. Case-insensitive.
COMMAND_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+([\s\S]*))?$", re.IGNORECASE)

_KNOWN_COMMANDS = ("stop", "btw", "help")

HELP_TEXT = (
    "Available commands:\n"
    "/stop [reason] — cancel your in-flight task and queued messages in this conversation\n"
    "/btw <note> — append context to the task currently processing (takes effect next step)\n"
    "/help — show this help"
)

_LINK_PROMPT_TEXT = (
    "Your external account is not linked to a Mesh user yet. "
    "Please link it in the Mesh web app (Settings → External identities) first."
)
_FORBIDDEN_TEXT = "You don't have permission to operate on that task."
_NOTHING_TO_STOP_TEXT = "There is no in-flight or queued task of yours in this conversation."
_CANCELLING_IN_PROGRESS_TEXT = "The task is already stopping."
_TERMINAL_NO_TASK_TEXT = "There is no in-flight task right now."
_BTW_OK_TEXT = "Appended to the processing task (takes effect at its next step)."
_BTW_CANCELLING_TEXT = (
    "The task is stopping and cannot receive context; redispatch after it stops."
)
_BTW_NO_ITEM_HINT = "No task is processing right now; queued as a new message."
_BTW_LIMIT_TEXT = "Context appends reached the limit; please open a new task instead."
_BTW_USAGE_TEXT = "Usage: /btw <note> — append context to the processing task."


@dataclass(frozen=True)
class CommandOutcome:
    """Result of command-plane processing for one inbound message."""

    handled: bool  # True → message consumed (audit 'processed', bare 200)
    passthrough_text: str | None = None  # /btw fallthrough: continue as ordinary text


async def maybe_handle_command(
    session: AsyncSession,
    *,
    settings,
    integration: Integration,
    event_row: IntegrationEvent,
    normalized_text: str,
    provider: str,
    tenant_key: str,
    user_key: str,
    conversation_key: str,
) -> CommandOutcome | None:
    """Parse + dispatch the command plane. None → not a command (continue)."""
    text = (normalized_text or "").strip()
    match = COMMAND_RE.match(text)
    if match is None:
        return None
    name = match.group(1).lower()
    args = (match.group(2) or "").strip()
    if name not in _KNOWN_COMMANDS:
        _audit_command(
            session, event_row, name=name, actor_key=user_key, targets=[],
            result="unknown_command",
        )
        await _feedback(
            session, integration=integration, event_row=event_row,
            conversation_key=conversation_key, name=name, text=HELP_TEXT,
        )
        return CommandOutcome(handled=True)
    if name == "help":
        _audit_command(session, event_row, name=name, actor_key=user_key, targets=[], result="help")
        await _feedback(
            session, integration=integration, event_row=event_row,
            conversation_key=conversation_key, name=name, text=HELP_TEXT,
        )
        return CommandOutcome(handled=True)

    actor_user_id = await resolve_actor_user_id(
        session, provider=provider, tenant_key=tenant_key, user_key=user_key
    )
    if actor_user_id is None:
        _audit_command(
            session, event_row, name=name, actor_key=user_key, targets=[], result="unmapped_identity"
        )
        await _feedback(
            session, integration=integration, event_row=event_row,
            conversation_key=conversation_key, name=name, text=_LINK_PROMPT_TEXT,
        )
        return CommandOutcome(handled=True)

    can_manage = await _requester_can_manage(
        session, workspace_id=integration.workspace_id, user_id=actor_user_id
    )

    if name == "stop":
        return await _handle_stop(
            session,
            settings=settings,
            integration=integration,
            event_row=event_row,
            actor_user_id=actor_user_id,
            actor_key=user_key,
            can_manage=can_manage,
            conversation_key=conversation_key,
        )
    return await _handle_btw(
        session,
        settings=settings,
        integration=integration,
        event_row=event_row,
        actor_user_id=actor_user_id,
        actor_key=user_key,
        can_manage=can_manage,
        conversation_key=conversation_key,
        args=args,
    )


# ---------------------------------------------------------------------------
# /stop
# ---------------------------------------------------------------------------


async def _handle_stop(
    session: AsyncSession,
    *,
    settings,
    integration: Integration,
    event_row: IntegrationEvent,
    actor_user_id: uuid.UUID,
    actor_key: str,
    can_manage: bool,
    conversation_key: str,
) -> CommandOutcome:
    items = await _conversation_items(session, conversation_key=conversation_key)
    own_processing: list[IntegrationMessageQueue] = []
    other_processing: list[IntegrationMessageQueue] = []
    own_pending: list[IntegrationMessageQueue] = []
    own_cancelling: list[IntegrationMessageQueue] = []
    for item in items:
        owner = await _item_owner_user_id(session, item)
        if item.state == "processing":
            if owner == actor_user_id:
                own_processing.append(item)
            else:
                other_processing.append(item)
        elif item.state == "pending" and owner == actor_user_id:
            own_pending.append(item)
        elif item.state == "cancelling" and owner == actor_user_id:
            own_cancelling.append(item)

    stopped_excerpts: list[str] = []
    targets: list[uuid.UUID] = []

    # (a) in-flight: atomic processing→cancelling + same-txn runtime cancel
    # persistence. With manage permission, others' in-flight items too.
    to_cancel = list(own_processing)
    if can_manage:
        to_cancel.extend(other_processing)
    for item in to_cancel:
        result = await session.execute(
            update(IntegrationMessageQueue)
            .where(
                IntegrationMessageQueue.id == item.id,
                IntegrationMessageQueue.state == "processing",
            )
            .values(state="cancelling", updated_at=datetime.now(UTC))
        )
        if result.rowcount != 1:
            continue  # lost the race to terminal/dispatch — state guard
        item.state = "cancelling"
        targets.append(item.id)
        stopped_excerpts.append(item.message_excerpt)
        if item.execution_id is not None:
            # Same-transaction DB persistence of the cancel intent (heartbeat
            # downlink stops the daemon; NO new outbox event type — §3.7 写死).
            try:
                await request_execution_cancel_tx(
                    session,
                    workspace_id=integration.workspace_id,
                    execution_id=item.execution_id,
                    failure_reason="cancelled_by_command",
                )
            except BusinessRuleError:
                logger.info("stop: execution %s already settled", item.execution_id)
        await emit_queue_updated(
            session, item=item, idempotency_key=f"imq-updated:{item.id}:cancelling"
        )

    # (b) queued: immediate batch cancel (pending has no execution).
    cancelled_count = 0
    for item in own_pending:
        result = await session.execute(
            update(IntegrationMessageQueue)
            .where(
                IntegrationMessageQueue.id == item.id,
                IntegrationMessageQueue.state == "pending",
            )
            .values(
                state="cancelled",
                finished_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        if result.rowcount == 1:
            item.state = "cancelled"
            cancelled_count += 1
            targets.append(item.id)
            await emit_queue_updated(
                session, item=item, idempotency_key=f"imq-updated:{item.id}:cancelled"
            )

    # Two-stage feedback — immediate stage (terminal stage is written by the
    # execution.finished consumer when the graceful stop completes, §3.7).
    if stopped_excerpts and cancelled_count:
        immediate = (
            f"⏳ Stopping task 「{stopped_excerpts[0]}」…, "
            f"and cancelled {cancelled_count} queued message(s)"
        )
        outcome = "stopping_and_cancelled"
    elif stopped_excerpts:
        immediate = f"⏳ Stopping task 「{stopped_excerpts[0]}」…"
        outcome = "stopping"
    elif cancelled_count:
        suffix = ""
        if other_processing:
            suffix = "; the in-flight task in this conversation is not yours"
        immediate = f"Cancelled {cancelled_count} queued message(s){suffix}"
        outcome = "cancelled_queued"
    elif own_cancelling:
        immediate = _CANCELLING_IN_PROGRESS_TEXT  # idempotent repeat /stop
        outcome = "already_cancelling"
    elif other_processing and not can_manage:
        # Unauthorized against others' in-flight item, nothing of own to do:
        # refusal text + audit, no task detail leak (§3.7 step 5).
        immediate = _FORBIDDEN_TEXT
        outcome = "forbidden"
    elif not items or all(
        i.state in ("done", "failed", "cancelled") for i in items
    ):
        immediate = _TERMINAL_NO_TASK_TEXT
        outcome = "nothing_in_flight"
    else:
        immediate = _NOTHING_TO_STOP_TEXT
        outcome = "nothing_own"

    _audit_command(
        session, event_row, name="stop", actor_key=actor_key, targets=targets, result=outcome
    )
    await _feedback(
        session, integration=integration, event_row=event_row,
        conversation_key=conversation_key, name="stop", text=immediate,
    )
    return CommandOutcome(handled=True)


# ---------------------------------------------------------------------------
# /btw
# ---------------------------------------------------------------------------


async def _handle_btw(
    session: AsyncSession,
    *,
    settings,
    integration: Integration,
    event_row: IntegrationEvent,
    actor_user_id: uuid.UUID,
    actor_key: str,
    can_manage: bool,
    conversation_key: str,
    args: str,
) -> CommandOutcome:
    if not args:
        _audit_command(session, event_row, name="btw", actor_key=actor_key, targets=[], result="usage")
        await _feedback(
            session, integration=integration, event_row=event_row,
            conversation_key=conversation_key, name="btw", text=_BTW_USAGE_TEXT,
        )
        return CommandOutcome(handled=True)

    items = await _conversation_items(session, conversation_key=conversation_key)
    processing = [i for i in items if i.state == "processing"]
    cancelling = [i for i in items if i.state == "cancelling"]

    target: IntegrationMessageQueue | None = None
    for item in processing:
        owner = await _item_owner_user_id(session, item)
        if owner == actor_user_id or can_manage:
            target = item
            break

    if target is None:
        if cancelling:
            # A cancelling item exists (own or not): refuse append, do not
            # fall through (§3.7 step 3).
            _audit_command(
                session, event_row, name="btw", actor_key=actor_key, targets=[],
                result="cancelling",
            )
            await _feedback(
                session, integration=integration, event_row=event_row,
                conversation_key=conversation_key, name="btw", text=_BTW_CANCELLING_TEXT,
            )
            return CommandOutcome(handled=True)
        if processing:
            # Others' processing item without manage permission (§3.7 step 1:
            # refusal path identical to /stop — no detail leak).
            _audit_command(
                session, event_row, name="btw", actor_key=actor_key, targets=[],
                result="forbidden",
            )
            await _feedback(
                session, integration=integration, event_row=event_row,
                conversation_key=conversation_key, name="btw", text=_FORBIDDEN_TEXT,
            )
            return CommandOutcome(handled=True)
        # No in-flight item: strip the prefix and continue as an ordinary
        # message (§3.7 step 4) after a one-line hint.
        _audit_command(
            session, event_row, name="btw", actor_key=actor_key, targets=[], result="passthrough"
        )
        await _feedback(
            session, integration=integration, event_row=event_row,
            conversation_key=conversation_key, name="btw", text=_BTW_NO_ITEM_HINT,
        )
        return CommandOutcome(handled=True, passthrough_text=args)

    if target.execution_id is None:
        # Defensive: processing without a bound execution is unreachable via
        # the consumer contract; treat as no target.
        return CommandOutcome(handled=True, passthrough_text=args)

    # Untrusted-data isolation (§6.15): the note enters the execution context
    # as DATA (source='im_btw'), never as instructions; caps enforced under
    # the eca: lock inside append_context (M3).
    truncated_text, _ = truncate_inbound_text(args, settings.im_inbound_text_max_chars)
    sender_display = await _display_name(session, user_id=actor_user_id)
    try:
        await append_context(
            session,
            settings=settings,
            workspace_id=integration.workspace_id,
            execution_id=target.execution_id,
            source="im_btw",
            payload={
                "sender_user_id": str(actor_user_id),
                "sender_display": sender_display,
                "text": truncated_text,
                "received_at": datetime.now(UTC).isoformat(),
                "conversation_ref": conversation_key,
            },
        )
    except BusinessRuleError as exc:
        if exc.code == "append_limit_exceeded":
            _audit_command(
                session, event_row, name="btw", actor_key=actor_key,
                targets=[target.id], result="limit_exceeded",
            )
            await _feedback(
                session, integration=integration, event_row=event_row,
                conversation_key=conversation_key, name="btw", text=_BTW_LIMIT_TEXT,
            )
            return CommandOutcome(handled=True)
        if exc.code in ("append_not_acceptable", "append_execution_terminal"):
            _audit_command(
                session, event_row, name="btw", actor_key=actor_key,
                targets=[target.id], result="not_acceptable",
            )
            await _feedback(
                session, integration=integration, event_row=event_row,
                conversation_key=conversation_key, name="btw", text=_BTW_CANCELLING_TEXT,
            )
            return CommandOutcome(handled=True)
        raise

    _audit_command(
        session, event_row, name="btw", actor_key=actor_key, targets=[target.id], result="appended"
    )
    await _feedback(
        session, integration=integration, event_row=event_row,
        conversation_key=conversation_key, name="btw", text=_BTW_OK_TEXT,
    )
    return CommandOutcome(handled=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def resolve_actor_user_id(
    session: AsyncSession, *, provider: str, tenant_key: str, user_key: str
) -> uuid.UUID | None:
    """Full-triple resolution external identity → global users.id (§3.7)."""
    from mesh.db.models.integration import ExternalIdentity

    return await session.scalar(
        select(ExternalIdentity.user_id).where(
            ExternalIdentity.provider == provider,
            ExternalIdentity.provider_tenant_key == tenant_key,
            ExternalIdentity.external_user_key == user_key,
        )
    )


async def _requester_can_manage(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """integration:manage (admin/owner) on the binding's workspace — the
    roster-chain authorization shared with the card-callback path (§3.2)."""
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == user_id,
            Member.status == "active",
        )
    )
    return member is not None and role_satisfies(member.role, "integration:manage")


async def _item_owner_user_id(
    session: AsyncSession, item: IntegrationMessageQueue
) -> uuid.UUID | None:
    """Resolve the item's sender_identity_key TRIPLE to users.id.

    Never compares raw external_user_key strings — the same string under a
    different provider/tenant may belong to a different user (§5.6 negative).
    """
    if not item.sender_identity_key:
        return None
    try:
        provider, tenant, user_key = validate_sender_identity_key(item.sender_identity_key)
    except BusinessRuleError:
        return None
    return await resolve_actor_user_id(
        session, provider=provider, tenant_key=tenant, user_key=user_key
    )


async def _conversation_items(
    session: AsyncSession, *, conversation_key: str
) -> list[IntegrationMessageQueue]:
    rows = (
        await session.execute(
            select(IntegrationMessageQueue)
            .where(IntegrationMessageQueue.conversation_key == conversation_key)
            .order_by(IntegrationMessageQueue.seq)
        )
    ).scalars().all()
    return list(rows)


async def _display_name(session: AsyncSession, *, user_id: uuid.UUID) -> str:
    user = await session.scalar(select(User).where(User.id == user_id))
    return (user.display_name or user.email) if user else str(user_id)


async def _feedback(
    session: AsyncSession,
    *,
    integration: Integration,
    event_row: IntegrationEvent,
    conversation_key: str,
    name: str,
    text: str,
) -> None:
    """Conversational reply via im.send — NOT an ack: never coalesced, never
    routed through notification_delivery (§3.8 ledger note)."""
    await emit_event(
        session,
        workspace_id=integration.workspace_id,
        event_type=IM_SEND_EVENT,
        payload={
            "kind": "command_feedback",
            "stage": "immediate",
            "command": name,
            "integration_id": str(integration.id),
            "conversation_key": conversation_key,
            "text": sanitize_excerpt(text, limit=500),
        },
        idempotency_key=f"im-cmdfb:{event_row.id}:{name}",
    )


def _audit_command(
    session: AsyncSession,
    event_row: IntegrationEvent,
    *,
    name: str,
    actor_key: str,
    targets: list[uuid.UUID],
    result: str,
) -> None:
    """§3.7 full-process audit on the event ledger payload."""
    payload = dict(event_row.payload or {})
    payload["_mesh_command"] = {
        "name": name,
        "actor_identity_key": actor_key,
        "target_item_ids": [str(t) for t in targets],
        "result": result,
    }
    event_row.payload = payload
    flag_modified(event_row, "payload")
