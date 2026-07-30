"""IM outbound semantic layer (integrations.md §3.3 / §3.8 / §3.10).

Between the platform-neutral notification / ack / command machinery and the
DingTalk OpenAPI transport (:mod:`mesh.integrations.dingtalk_api`):

- external user-key normalization (enterprise staffId passthrough vs.
  external-contact ``x=<base64url(senderId)>`` encoding — the two key
  spaces are structurally disjoint, §3.10 E-1),
- long-result markdown chunking (paragraph / line / UTF-8-safe hard cuts,
  each chunk under the 15000-byte platform cap),
- verbosity gating (``final_only`` default: the IM conversation only sees
  the ack, approval cards and FINAL results; intermediate progress stays
  in the in-app execution detail — README §6.13),
- the per-chunk idempotency key registered at README §6.5
  (``sha256(notification_id | 'chunk' | i)``),
- :class:`DingTalkIMAdapter` (conversation send + notification push),
- :class:`IMSendRelay` (the ``im.send`` outbox consumer — ack T1/T2
  at-most-once protocol, notification chunk ledger writeback, plain
  command feedback),
- :func:`derive_im_deliveries_from_fanout` (chains onto
  ``notification.fanout`` to materialize the ``notification_delivery``
  ledger rows + ``im.send`` events for integration-triggered executions).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.integration import Integration, IntegrationMessageQueue
from mesh.db.models.notification import NotificationDelivery
from mesh.db.models.outbox import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_PUBLISHED,
    OutboxEvent,
)
from mesh.db.tenant import set_tenant_context
from mesh.integrations.ack import (
    DEFAULT_ACK_TEMPLATE,
    IM_SEND_EVENT_TYPE,
    IM_SEND_KIND_ACK,
    IM_SEND_KIND_CARD,
    IM_SEND_KIND_FEEDBACK,
    IM_SEND_KIND_NOTIFICATION,
    compose_ack_text,
    position_hint,
)
from mesh.integrations.dingtalk_api import (
    GROUP_MSG_PARAM_MAX_BYTES,
    MSG_KEY_MARKDOWN,
    MSG_KEY_TEXT,
    DingTalkClient,
    DingTalkError,
    DingTalkRateLimited,
    DingTalkTokenManager,
    InvalidCredentials,
    TokenRefreshBusy,
)
from mesh.runtime.credentials import decrypt_credential_value

if TYPE_CHECKING:
    pass

logger = logging.getLogger("mesh.integrations.im_outbound")

# ---------------------------------------------------------------------------
# External user-key encoding (§3.10 — unambiguous, structurally disjoint)
# ---------------------------------------------------------------------------

EXTERNAL_CONTACT_PREFIX = "x="

# Enterprise-member staffId charset — the WIDEST official DingTalk caliber
# (the docs vary between "alphanumeric" and "alphanumeric plus -_"; we take
# the widest union and admit '.' too). Single source of truth shared with
# the conversation-key segment validation (§2.10).
STAFF_ID_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def encode_external_contact_key(sender_id: str) -> str:
    """External contact (no staffId) → ``x=<base64url(senderId bytes)>``.

    DingTalk ``senderId`` values are encrypted strings containing ``:``/``$``
    /``+`` (e.g. ``$:LWCP_v1:$6GYsn+…``); using the raw value would collapse
    the ``:``-separated identity triple. base64url (alphabet ``A-Za-z0-9_-``,
    NO padding) eliminates every separator. The encoded key's 2nd character
    is always ``=``, which no staffId can contain → the two key spaces are
    disjoint by charset algebra, not by documentation version (§3.10 E-1).
    """
    if not sender_id:
        raise ValueError("sender_id is required for external-contact encoding")
    encoded = base64.urlsafe_b64encode(sender_id.encode("utf-8")).rstrip(b"=").decode("ascii")
    return f"{EXTERNAL_CONTACT_PREFIX}{encoded}"


def normalize_dingtalk_user_key(
    *, sender_staff_id: str | None, sender_id: str | None
) -> str:
    """The normalized external user key for identities / outbound userIds.

    Enterprise members pass their staffId through unchanged (single source
    of truth); external contacts (no staffId) are base64url-encoded under
    the ``x=`` prefix. Empty when neither is present.
    """
    staff_id = (sender_staff_id or "").strip()
    if staff_id:
        return staff_id
    raw_sender_id = (sender_id or "").strip()
    if raw_sender_id:
        return encode_external_contact_key(raw_sender_id)
    return ""


def is_external_contact_key(key: str) -> bool:
    return key.startswith(EXTERNAL_CONTACT_PREFIX)


def is_valid_staff_id_key(key: str) -> bool:
    """True when ``key`` could be an enterprise staffId (widest caliber).

    Encoded external-contact keys NEVER match (their 2nd char is ``=``,
    outside the charset) — the link flow uses this guard to refuse
    ``x=…`` strings presented as staffIds (§5.6 attack-chain negative).
    """
    return bool(STAFF_ID_KEY_PATTERN.match(key))


def validate_identity_segment(segment: str) -> None:
    """Identity-triple segment guard (§2.10): no ``:`` (the triple
    separator) and no control characters. Raw ``senderId`` values fail
    here — they MUST be encoded first."""
    if not segment:
        raise ValueError("identity segment is empty")
    if ":" in segment:
        raise ValueError("identity segment must not contain ':' (encode external contacts)")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in segment):
        raise ValueError("identity segment must not contain control characters")


# ---------------------------------------------------------------------------
# Long-result markdown chunking (§3.10 — ≤15000 bytes per message)
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_MAX_BYTES = 15000


def split_markdown_chunks(text: str, max_bytes: int = DEFAULT_CHUNK_MAX_BYTES) -> list[str]:
    """Split ``text`` into chunks each ≤ ``max_bytes`` (UTF-8).

    Cut preference: paragraph boundary (``\\n\\n``) → line boundary (``\\n``)
    → UTF-8-safe hard cut. Boundaries are only honored in the SECOND HALF
    of the available span (a boundary near the start would produce a
    sliver chunk and a hot loop). Empty input yields no chunks.
    """
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining.encode("utf-8")) <= max_bytes:
            chunks.append(remaining)
            break
        char_limit = _prefix_within_bytes(remaining, max_bytes)
        cut = _boundary_cut(remaining, char_limit)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _prefix_within_bytes(text: str, max_bytes: int) -> int:
    """Largest char index ``i`` with ``len(text[:i].encode()) <= max_bytes``.

    Binary search over char counts (UTF-8 is ≥1 byte/char, so ``max_bytes``
    is an upper bound on the char count); slicing at a char index can never
    split a code point.
    """
    low, high = 0, min(len(text), max_bytes)
    while low < high:
        mid = (low + high + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            low = mid
        else:
            high = mid - 1
    return max(low, 1)


def _boundary_cut(text: str, char_limit: int) -> int:
    half = char_limit // 2
    paragraph = text.rfind("\n\n", 0, char_limit)
    if paragraph > half:
        return paragraph + 2
    line = text.rfind("\n", 0, char_limit)
    if line > half:
        return line + 1
    return char_limit


# ---------------------------------------------------------------------------
# Chunk idempotency + verbosity (§3.3 / §3.10 / README §6.5)
# ---------------------------------------------------------------------------


def chunk_idempotency_key(notification_id: uuid.UUID | str, index: int) -> str:
    """README §6.5 registered key — at-least-once dequeue never re-sends a
    chunk: ``sha256(notification_id | 'chunk' | i)``."""
    material = f"{notification_id}|chunk|{index}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# Notification types that count as FINAL results in the IM conversation
# (always pushed under the default ``final_only`` verbosity). Everything
# else is intermediate progress — gated behind ``verbosity='progress'``.
FINAL_NOTIFICATION_TYPES: frozenset[str] = frozenset(
    {
        "execution_finished",  # terminal result of the triggered run
        "review_requested",  # approval card (§6.10 — rendered as a card)
        "comment_created",  # agent reply comment
        "mentioned",  # direct mention of a human in the run
    }
)

VERBOSITY_FINAL_ONLY = "final_only"
VERBOSITY_PROGRESS = "progress"


def should_push_notification(*, notification_type: str, verbosity: str) -> bool:
    """§3.3/§3.10 — final_only (default) pushes confirmations / cards /
    final results only; progress adds intermediate notifications (the
    in-app execution detail is ALWAYS complete — README §6.13)."""
    if notification_type in FINAL_NOTIFICATION_TYPES:
        return True
    return verbosity == VERBOSITY_PROGRESS


def is_card_notification(notification_type: str) -> bool:
    """Approval requests render as interactive cards (§6.10 / §4.4),
    everything else as markdown text."""
    return notification_type == "review_requested"


# ---------------------------------------------------------------------------
# Conversation send + notification push (§3.10)
# ---------------------------------------------------------------------------

CONVERSATION_GROUP = "group"
CONVERSATION_DIRECT = "direct"

# The robot message APIs do NOT support @ mentions — copy must call people
# out by display name instead (§3.10 UX constraint). Outbound text is
# scrubbed of @mention tokens before sending.
_MENTION_TOKEN_PATTERN = re.compile(r"@[^\s@]+")

# §3.10 deep-link line appended to the final chunk of a truncated result.
TRUNCATION_LINK_TEMPLATE = "\n\n---\n完整结果见 Mesh:{url}"

SEND_STATUS_SENT = "sent"
SEND_STATUS_FAILED = "failed"

REASON_NO_STAFF_ID = "no_staff_id"
REASON_RATE_LIMITED = "rate_limited"
REASON_TOKEN_BUSY = "token_refresh_busy"
REASON_INVALID_CREDENTIALS = "invalid_credentials"
REASON_UPSTREAM_ERROR = "upstream_error"


def sanitize_no_mentions(text: str) -> str:
    """Strip ``@token`` mention sequences (the platform cannot deliver
    mentions; attention is expressed by naming the person in copy)."""
    return _MENTION_TOKEN_PATTERN.sub("", text)


def truncate_to_bytes(text: str, max_bytes: int) -> str:
    """UTF-8-safe prefix truncation to ``max_bytes`` (ellipsis marker)."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    marker = "…"
    limit = _prefix_within_bytes(text, max(max_bytes - len(marker.encode("utf-8")), 1))
    return text[:limit] + marker


def plan_result_chunks(
    markdown: str, *, max_chunks: int, detail_url: str | None = None
) -> list[str]:
    """Split a long result into send-ready markdown chunks (§3.10).

    Beyond ``max_chunks`` the remainder is dropped and the final chunk
    carries the in-app execution-detail deep link (room reserved so the
    link is never cut in half). Shared by the adapter's direct send path
    and the notification-fanout derivation (one chunk per ``im.send``
    event there).
    """
    chunks = split_markdown_chunks(markdown)
    if not chunks:
        return []
    if len(chunks) <= max_chunks:
        return chunks
    chunks = chunks[: max(1, max_chunks)]
    if detail_url:
        link = TRUNCATION_LINK_TEMPLATE.format(url=detail_url)
        head = truncate_to_bytes(
            chunks[-1], GROUP_MSG_PARAM_MAX_BYTES - len(link.encode("utf-8")) - 60
        )
        chunks[-1] = head + link
    return chunks


@dataclass(frozen=True)
class ConversationTarget:
    """Where an outbound IM message goes (derived from the queue item /
    binding at send time)."""

    workspace_id: uuid.UUID
    integration_id: uuid.UUID
    provider_tenant_key: str  # corp_id
    external_ref: str  # conversationId (group or direct conversation)
    conversation_type: str  # CONVERSATION_GROUP / CONVERSATION_DIRECT
    sender_key: str = ""  # direct chats: recipient staffId ('' → undeliverable)
    binding_id: uuid.UUID | None = None
    robot_code: str = ""


@dataclass(frozen=True)
class SendOutcome:
    status: str  # SEND_STATUS_SENT / SEND_STATUS_FAILED
    reason: str = ""
    rate_limit_code: str = ""
    flow_controlled_staff_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sent(self) -> bool:
        return self.status == SEND_STATUS_SENT


def _fit_msg_param(param: dict[str, object], cap: int = GROUP_MSG_PARAM_MAX_BYTES) -> dict[str, object]:
    """Shrink the largest string field until the encoded msgParam fits the
    platform cap (adversarial single-message path; chunked results never
    reach this)."""
    encoded = json.dumps(param, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= cap:
        return param
    largest_key = max(
        (key for key, value in param.items() if isinstance(value, str)),
        key=lambda key: len(str(param[key])),
        default=None,
    )
    if largest_key is None:
        return param
    overhead = len(encoded.encode("utf-8")) - len(str(param[largest_key]).encode("utf-8"))
    fitted = truncate_to_bytes(str(param[largest_key]), cap - overhead)
    return {**param, largest_key: fitted}


class DingTalkIMAdapter:
    """Semantic outbound adapter for one DingTalk integration instance.

    - conversation channel selection (group ``groupMessages/send`` vs.
      direct ``oToMessages/batchSend`` per ``conversation_type``),
    - external-contact direct chats fail ``no_staff_id`` (§3.10 written
      degradation — the run itself is unaffected),
    - long results split into markdown chunks with truncation + in-app
      deep link beyond ``max_chunks`` (§3.10),
    - rate-limit outcomes carry the flow-controlled staff list so the
      relay can back off per recipient instead of failing wholesale.
    """

    def __init__(
        self,
        client: DingTalkClient,
        *,
        max_chunks: int = 5,
    ) -> None:
        self._client = client
        self._max_chunks = max(1, max_chunks)

    @property
    def client(self) -> DingTalkClient:
        """The underlying OpenAPI client (card push path)."""
        return self._client

    async def send_text(self, target: ConversationTarget, text: str) -> SendOutcome:
        return await self._send(target, MSG_KEY_TEXT, {"content": sanitize_no_mentions(text)})

    async def send_markdown(
        self, target: ConversationTarget, title: str, markdown: str
    ) -> SendOutcome:
        return await self._send(
            target,
            MSG_KEY_MARKDOWN,
            {"title": sanitize_no_mentions(title), "text": sanitize_no_mentions(markdown)},
        )

    async def _send(
        self, target: ConversationTarget, msg_key: str, msg_param: dict[str, object]
    ) -> SendOutcome:
        param = _fit_msg_param(msg_param)
        try:
            if target.conversation_type == CONVERSATION_GROUP:
                await self._client.send_group(target.external_ref, msg_key, param)
                return SendOutcome(SEND_STATUS_SENT)
            # direct — oToMessages needs an enterprise staffId
            if not target.sender_key or is_external_contact_key(target.sender_key):
                logger.warning(
                    "dingtalk direct outbound undeliverable (no staffId) integration=%s conversation=%s",
                    target.integration_id,
                    target.external_ref,
                )
                return SendOutcome(SEND_STATUS_FAILED, reason=REASON_NO_STAFF_ID)
            await self._client.send_direct([target.sender_key], msg_key, param)
            return SendOutcome(SEND_STATUS_SENT)
        except DingTalkRateLimited as exc:
            return SendOutcome(
                SEND_STATUS_FAILED,
                reason=REASON_RATE_LIMITED,
                rate_limit_code=exc.code,
                flow_controlled_staff_ids=exc.flow_controlled_staff_ids,
            )
        except InvalidCredentials:
            return SendOutcome(SEND_STATUS_FAILED, reason=REASON_INVALID_CREDENTIALS)
        except TokenRefreshBusy:
            # §3.10 retryable NON-failure: another replica holds the refresh
            # lease and the follower wait exhausted. MUST be classified before
            # the DingTalkError catch-all (TokenRefreshBusy subclasses it) —
            # the handlers defer available_at without consuming the budget.
            return SendOutcome(SEND_STATUS_FAILED, reason=REASON_TOKEN_BUSY)
        except DingTalkError:
            return SendOutcome(SEND_STATUS_FAILED, reason=REASON_UPSTREAM_ERROR)

    async def send_result(
        self,
        target: ConversationTarget,
        *,
        notification_id: uuid.UUID,
        markdown: str,
        title: str = "Mesh 执行结果",
        detail_url: str | None = None,
    ) -> list[SendOutcome]:
        """Push a (possibly long) result as markdown chunks (§3.10).

        Beyond ``max_chunks`` the remainder is truncated and the last sent
        chunk carries the in-app execution-detail deep link. Each chunk's
        idempotency key is :func:`chunk_idempotency_key` — the relay
        registers it with the outbox event so at-least-once dequeue never
        re-sends a chunk. Terminal failures (invalid credentials /
        undeliverable direct chat) abort the remaining chunks.
        """
        chunks = plan_result_chunks(markdown, max_chunks=self._max_chunks, detail_url=detail_url)
        if not chunks:
            return []
        total = len(chunks)
        outcomes: list[SendOutcome] = []
        for index, chunk in enumerate(chunks):
            chunk_title = f"{title} ({index + 1}/{total})" if total > 1 else title
            outcome = await self.send_markdown(target, chunk_title, chunk)
            outcomes.append(outcome)
            if not outcome.sent and outcome.reason in (
                REASON_INVALID_CREDENTIALS,
                REASON_NO_STAFF_ID,
            ):
                break  # terminal — remaining chunks cannot succeed either
        return outcomes


# ---------------------------------------------------------------------------
# im.send fast relay (§3.8 at-most-once ack + §3.10 notification delivery)
# ---------------------------------------------------------------------------

REASON_TIMEOUT = "timeout"
REASON_INTEGRATION_UNAVAILABLE = "integration_unavailable"

JITTER_MIN = 0.5
JITTER_MAX = 1.0


def _parse_conversation_key(conversation_key: str) -> tuple[str, str]:
    """(provider_tenant_key, external_ref) from ``provider:tenant:ref`` —
    the ref segment never contains ':' (§2.10 segment validation)."""
    _provider, sep, rest = conversation_key.partition(":")
    tenant, sep2, ref = rest.partition(":")
    if not sep or not sep2:
        raise ValueError(f"malformed conversation_key {conversation_key!r}")
    return tenant, ref


def target_from_payload(
    payload: dict[str, Any],
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID | None,
) -> ConversationTarget:
    """Rebuild the outbound target from an ``im.send`` payload (shared by
    the relay and the card pusher)."""
    conversation_key = str(payload.get("conversation_key") or "")
    tenant_key, external_ref = _parse_conversation_key(conversation_key)
    return ConversationTarget(
        workspace_id=workspace_id,
        integration_id=integration_id or uuid.UUID(int=0),
        provider_tenant_key=tenant_key,
        external_ref=external_ref,
        conversation_type=str(payload.get("conversation_type") or CONVERSATION_GROUP),
        sender_key=str(payload.get("target_user_key") or ""),
        binding_id=_uuid_or_none(payload.get("binding_id")),
    )


class IMSendRelay:
    """Sole consumer of ``im.send`` outbox events — a high-priority
    supervised task in the ``mesh.workers`` process set (§3.8/§3.9),
    separate from the general outbox relay (which only claims event types
    it has handlers for — it never touches ``im.send``).

    Two claim paths each pass:

    1. **ack fast path** — claim ``kind='ack'`` events; T1 persists the
       ``ack_attempted_at`` gate AND marks the event published in one
       short transaction (committed before any outbound call); the send
       happens OUTSIDE the transaction; T2 records the outcome
       (``ack_sent_at`` + window-follower backfill on success, audit-only
       on failure — never a retry). at-most-once.
    2. **general path** — ``feedback`` / ``notification`` / ``card``
       events: send inside the claim transaction (row lock held,
       WebhookDeliveryWorker-style), publish + ledger write in the same
       commit. at-least-once. Rate-limit outcomes move ``available_at``
       forward with exponential backoff WITHOUT consuming the failure
       budget (retryable non-failure, README §6.6 R4-4).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        redis: Any,
        signing_secret: str,
        api_base: str = "https://api.dingtalk.com",
        poll_interval: float = 0.2,
        batch_size: int = 20,
        max_chunks: int = 5,
        ack_send_timeout: float = 3.0,
        max_attempts: int = 5,
        token_refresh_timeout: float = 10.0,
        token_lock_ttl: int = 30,
        token_follower_wait: float = 12.0,
        request_timeout: float = 10.0,
        rate_limit_base_seconds: float = 2.0,
        rate_limit_max_seconds: float = 60.0,
        token_busy_backoff_seconds: float = 2.0,
        http_client: httpx.AsyncClient | None = None,
        card_pusher: Callable[..., Awaitable[SendOutcome]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._signing_secret = signing_secret
        self._api_base = api_base
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._max_chunks = max_chunks
        self._ack_send_timeout = ack_send_timeout
        self._max_attempts = max_attempts
        self._token_refresh_timeout = token_refresh_timeout
        self._token_lock_ttl = token_lock_ttl
        self._token_follower_wait = token_follower_wait
        self._request_timeout = request_timeout
        self._rate_limit_base_seconds = rate_limit_base_seconds
        self._rate_limit_max_seconds = rate_limit_max_seconds
        self._token_busy_backoff_seconds = token_busy_backoff_seconds
        self._http_client = http_client
        self._card_pusher = card_pusher
        self._clock = clock or (lambda: datetime.now(UTC))
        # (integration_id, secret_ref) → adapter; secret_ref in the key
        # drops the cache entry automatically on credential rotation.
        self._adapters: dict[tuple[uuid.UUID, str], DingTalkIMAdapter] = {}

    # -- lifecycle ---------------------------------------------------------

    async def run_once(self) -> int:
        ack_processed = await self._run_ack_pass()
        general_processed = await self._run_general_pass()
        return ack_processed + general_processed

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        while stop is None or not stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:  # noqa: BLE001 — the relay must survive bad events
                logger.exception("im.send relay pass failed")
                processed = 0
            if processed == 0:
                if stop is not None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                    except TimeoutError:
                        pass
                else:
                    await asyncio.sleep(self._poll_interval)

    # -- ack fast path (at-most-once, §3.8) ----------------------------------

    async def _run_ack_pass(self) -> int:
        pending_sends: list[dict[str, Any]] = []
        async with self._session_factory() as session:
            async with session.begin():
                events = await self._claim_events(session, ack=True)
                now = self._clock()
                for event in events:
                    # Tenant-safe UPDATEs (defense in depth with the
                    # explicit workspace_id predicate below).
                    await set_tenant_context(session, event.workspace_id)
                    payload = event.payload or {}
                    raw_item_id = str(payload.get("queue_item_id") or "")
                    try:
                        item_id = uuid.UUID(raw_item_id)
                    except ValueError:
                        event.status = OUTBOX_STATUS_PUBLISHED  # malformed: drop
                        event.published_at = now
                        continue
                    # T1: the gate + event finalization in ONE transaction.
                    gated = await session.execute(
                        update(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.id == item_id,
                            IntegrationMessageQueue.workspace_id == event.workspace_id,
                            IntegrationMessageQueue.ack_attempted_at.is_(None),
                        )
                        .values(ack_attempted_at=now, updated_at=now)
                    )
                    event.status = OUTBOX_STATUS_PUBLISHED
                    event.published_at = now
                    pending_sends.append(
                        {"payload": payload, "gated": (gated.rowcount or 0) > 0}
                    )
        # OUTSIDE the transaction: the outbound call, then T2.
        for entry in pending_sends:
            if not entry["gated"]:
                continue  # another replica already committed to this ack
            await self._ack_send_and_record(entry["payload"])
        return len(pending_sends)

    async def _ack_send_and_record(self, payload: dict[str, Any]) -> None:
        try:
            outcome = await asyncio.wait_for(
                self._send_ack_message(payload), timeout=self._ack_send_timeout
            )
        except TimeoutError:
            outcome = SendOutcome(SEND_STATUS_FAILED, reason=REASON_TIMEOUT)
        except Exception:  # noqa: BLE001 — record the loss, never crash the relay
            logger.exception("ack send crashed")
            outcome = SendOutcome(SEND_STATUS_FAILED, reason=REASON_UPSTREAM_ERROR)

        leader_id = uuid.UUID(str(payload["queue_item_id"]))
        workspace_id = uuid.UUID(str(payload["workspace_id"]))
        async with self._session_factory() as session:
            async with session.begin():
                await set_tenant_context(session, workspace_id)
                now = self._clock()
                if outcome.sent:
                    await session.execute(
                        update(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.id == leader_id,
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.ack_sent_at.is_(None),
                        )
                        .values(ack_sent_at=now, updated_at=now)
                    )
                    # Window-follower backfill (§3.8 five-field semantics).
                    await session.execute(
                        update(IntegrationMessageQueue)
                        .where(
                            IntegrationMessageQueue.ack_leader_id == leader_id,
                            IntegrationMessageQueue.workspace_id == workspace_id,
                            IntegrationMessageQueue.id != leader_id,
                            IntegrationMessageQueue.ack_represented_at.is_(None),
                        )
                        .values(
                            ack_represented_at=now, ack_merged_into=leader_id, updated_at=now
                        )
                    )
                else:
                    # at-most-once: NO retry. The loss is audit-visible —
                    # attempted ∧ ¬sent on the queue item plus the explicit
                    # ``_mesh_ack_failed`` entry on the source inbound event
                    # written below. Confirmation is UX sugar, not the
                    # task's source of truth; dispatch is unaffected.
                    logger.warning(
                        "ack send failed (not retried) item=%s reason=%s",
                        leader_id,
                        outcome.reason,
                    )
                # §3.8 ledger artifact: the ack send outcome (sent OR lost)
                # is recorded on the source inbound event's payload — both
                # paths (acceptance R4/R8).
                await self._record_ack_result_in_event_ledger(
                    session,
                    leader_id=leader_id,
                    workspace_id=workspace_id,
                    outcome=outcome,
                    now=now,
                )

    async def _record_ack_result_in_event_ledger(
        self,
        session: AsyncSession,
        *,
        leader_id: uuid.UUID,
        workspace_id: uuid.UUID,
        outcome: SendOutcome,
        now: datetime,
    ) -> None:
        """Append the ack send result to the source inbound event's payload
        (§3.8 ledger: ``integration_events.payload`` carries the send
        result): ``_mesh_ack`` on success, ``_mesh_ack_failed`` on
        failure/timeout. Savepoint-isolated — a ledger write failure must
        NOT roll back the T2 queue-item writes (the at-most-once shape is
        fixed); the queue item's four ack fields remain the primary truth.
        Items without a linked inbound event skip the write gracefully."""
        from mesh.db.models.integration import IntegrationEvent

        try:
            async with session.begin_nested():
                item = await session.scalar(
                    select(IntegrationMessageQueue).where(
                        IntegrationMessageQueue.id == leader_id,
                        IntegrationMessageQueue.workspace_id == workspace_id,
                    )
                )
                if item is None or item.integration_event_id is None:
                    return
                event = await session.get(IntegrationEvent, item.integration_event_id)
                if event is None:
                    return
                if outcome.sent:
                    audit_key = "_mesh_ack"
                    audit_value = {"status": "sent", "sent_at": now.isoformat()}
                else:
                    audit_key = "_mesh_ack_failed"
                    audit_value = {
                        "status": "failed",
                        "reason": outcome.reason,
                        "at": now.isoformat(),
                    }
                event.payload = {**(event.payload or {}), audit_key: audit_value}
                event.updated_at = now
                await session.flush()
        except Exception:  # noqa: BLE001 — ledger is best-effort (§3.8 audit)
            logger.exception("ack result ledger write failed item=%s", leader_id)

    async def _send_ack_message(self, payload: dict[str, Any]) -> SendOutcome:
        workspace_id = uuid.UUID(str(payload["workspace_id"]))
        template = str(payload.get("template") or DEFAULT_ACK_TEMPLATE)
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            integration_id = _uuid_or_none(payload.get("integration_id"))
            adapter = (
                await self._adapter_for(session, integration_id=integration_id)
                if integration_id
                else None
            )
            if adapter is None:
                return SendOutcome(SEND_STATUS_FAILED, reason=REASON_INTEGRATION_UNAVAILABLE)
            target = self._target_from_payload(payload, workspace_id, integration_id)
            item = await session.get(IntegrationMessageQueue, uuid.UUID(str(payload["queue_item_id"])))
            position = await position_hint(session, item=item) if item is not None else 1
        text = compose_ack_text(template, position)
        return await adapter.send_text(target, text)

    # -- general path (at-least-once) ------------------------------------------

    async def _run_general_pass(self) -> int:
        async with self._session_factory() as session:
            async with session.begin():
                events = await self._claim_events(session, ack=False)
                for event in events:
                    await self._handle_general_event(session, event)
        return len(events)

    async def _handle_general_event(self, session: AsyncSession, event: OutboxEvent) -> None:
        payload = event.payload or {}
        kind = str(payload.get("kind") or "")
        await set_tenant_context(session, event.workspace_id)
        if kind == IM_SEND_KIND_FEEDBACK:
            outcome = await self._send_feedback(workspace_id=event.workspace_id, payload=payload)
            if not outcome.sent:
                logger.warning("command feedback send failed reason=%s", outcome.reason)
            # Conversational reply: publish regardless (no retry, no ledger).
            self._mark_published(event)
            return
        if kind == IM_SEND_KIND_NOTIFICATION:
            await self._handle_notification_chunk(session, event, payload)
            return
        if kind == IM_SEND_KIND_CARD:
            await self._handle_card(session, event, payload)
            return
        # Unknown kind — publish to avoid a poison-event claim loop.
        logger.error("im.send event %s has unknown kind %r", event.id, kind)
        self._mark_published(event)

    async def _send_feedback(self, *, workspace_id: uuid.UUID, payload: dict[str, Any]) -> SendOutcome:
        integration_id = _uuid_or_none(payload.get("integration_id"))
        async with self._session_factory() as session:
            await set_tenant_context(session, workspace_id)
            adapter = (
                await self._adapter_for(session, integration_id=integration_id)
                if integration_id
                else None
            )
        if adapter is None:
            return SendOutcome(SEND_STATUS_FAILED, reason=REASON_INTEGRATION_UNAVAILABLE)
        target = self._target_from_payload(payload, workspace_id, integration_id)
        text = str(payload.get("text") or payload.get("template") or "")
        return await adapter.send_text(target, text)

    async def _handle_notification_chunk(
        self, session: AsyncSession, event: OutboxEvent, payload: dict[str, Any]
    ) -> None:
        workspace_id = event.workspace_id
        integration_id = _uuid_or_none(payload.get("integration_id"))
        adapter = (
            await self._adapter_for(session, integration_id=integration_id)
            if integration_id
            else None
        )
        if adapter is None:
            await self._fail_delivery(session, payload, reason=REASON_INTEGRATION_UNAVAILABLE)
            self._mark_published(event)
            return
        target = self._target_from_payload(payload, workspace_id, integration_id)
        text = str(payload.get("text") or "")
        chunk_index = int(payload.get("chunk_index") or 0)
        chunks_total = int(payload.get("chunks_total") or 1)
        title = "Mesh 执行结果"
        if chunks_total > 1:
            title = f"{title} ({chunk_index + 1}/{chunks_total})"
        outcome = await adapter.send_markdown(target, title, text)
        if outcome.sent:
            await self._record_chunk_progress(session, payload)
            self._mark_published(event)
            return
        if outcome.reason == REASON_RATE_LIMITED:
            self._defer_rate_limited(event, payload)
            return
        if outcome.reason == REASON_TOKEN_BUSY:
            await self._defer_token_busy(session, event, payload)
            return
        if outcome.reason in (REASON_INVALID_CREDENTIALS, REASON_NO_STAFF_ID):
            await self._fail_delivery(session, payload, reason=outcome.reason)
            self._mark_published(event)
            return
        # Upstream failure: consume the failure budget; terminal at max.
        event.delivery_attempts += 1
        if event.delivery_attempts >= self._max_attempts:
            await self._fail_delivery(session, payload, reason=REASON_UPSTREAM_ERROR)
            event.status = OUTBOX_STATUS_FAILED
            event.published_at = self._clock()
        else:
            event.available_at = self._clock() + timedelta(
                seconds=self._rate_limit_base_seconds * random.uniform(JITTER_MIN, JITTER_MAX)
            )

    async def _handle_card(
        self, session: AsyncSession, event: OutboxEvent, payload: dict[str, Any]
    ) -> None:
        if self._card_pusher is None:
            logger.error("card push requested but no card_pusher is wired")
            self._mark_published(event)
            return
        integration_id = _uuid_or_none(payload.get("integration_id"))
        adapter = (
            await self._adapter_for(session, integration_id=integration_id)
            if integration_id
            else None
        )
        if adapter is None:
            await self._fail_delivery(session, payload, reason=REASON_INTEGRATION_UNAVAILABLE)
            self._mark_published(event)
            return
        outcome = await self._card_pusher(session, adapter, payload)
        if outcome.sent:
            await self._mark_delivery_sent(session, payload)
            self._mark_published(event)
            return
        if outcome.reason == REASON_RATE_LIMITED:
            self._defer_rate_limited(event, payload)
            return
        if outcome.reason == REASON_TOKEN_BUSY:
            await self._defer_token_busy(session, event, payload)
            return
        if outcome.reason in (
            REASON_INVALID_CREDENTIALS,
            REASON_NO_STAFF_ID,
            "not_found",
            "invalid_request",
        ):
            # Terminal — the card can never be delivered (missing approval /
            # undeliverable target / malformed event).
            await self._fail_delivery(session, payload, reason=outcome.reason)
            self._mark_published(event)
            return
        event.delivery_attempts += 1
        if event.delivery_attempts >= self._max_attempts:
            await self._fail_delivery(session, payload, reason=REASON_UPSTREAM_ERROR)
            event.status = OUTBOX_STATUS_FAILED
            event.published_at = self._clock()
        else:
            event.available_at = self._clock() + timedelta(
                seconds=self._rate_limit_base_seconds * random.uniform(JITTER_MIN, JITTER_MAX)
            )

    # -- shared helpers ------------------------------------------------------

    async def _claim_events(self, session: AsyncSession, *, ack: bool) -> list[OutboxEvent]:
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == IM_SEND_EVENT_TYPE,
                OutboxEvent.status == OUTBOX_STATUS_PENDING,
                OutboxEvent.available_at <= self._clock(),
            )
            .order_by(OutboxEvent.available_at.asc(), OutboxEvent.created_at.asc())
            .limit(self._batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list((await session.execute(stmt)).scalars().all())
        if ack:
            return [e for e in events if str((e.payload or {}).get("kind")) == IM_SEND_KIND_ACK]
        return [e for e in events if str((e.payload or {}).get("kind")) != IM_SEND_KIND_ACK]

    def _mark_published(self, event: OutboxEvent) -> None:
        event.status = OUTBOX_STATUS_PUBLISHED
        event.published_at = self._clock()

    def _defer_rate_limited(self, event: OutboxEvent, payload: dict[str, Any]) -> None:
        """Retryable non-failure (README §6.6 R4-4): exponential backoff by
        recorded rate-limit hits, NO failure-budget consumption."""
        hits = int(payload.get("_mesh_rate_limit_hits") or 0) + 1
        # Reassign (JSONB in-place mutation is not change-tracked).
        event.payload = {**(event.payload or {}), "_mesh_rate_limit_hits": hits}
        delay = min(
            self._rate_limit_base_seconds * (2 ** (hits - 1)),
            self._rate_limit_max_seconds,
        ) * random.uniform(JITTER_MIN, JITTER_MAX)
        event.available_at = self._clock() + timedelta(seconds=delay)
        # event stays pending; delivery_attempts untouched

    async def _defer_token_busy(
        self, session: AsyncSession, event: OutboxEvent, payload: dict[str, Any]
    ) -> None:
        """§3.10 ``token_refresh_busy`` — retryable NON-failure (written in
        stone): only move ``available_at`` forward by a short fixed backoff
        (anti-hot-loop), NEVER consume the failure budget, NEVER reach a
        terminal state. The busy attempt is recorded on the delivery
        ledger; the event stays pending until the backoff expires."""
        event.available_at = self._clock() + timedelta(
            seconds=self._token_busy_backoff_seconds
        )
        await self._record_busy_in_delivery(session, payload)
        logger.info(
            "im.send deferred: token refresh busy (budget untouched) event=%s",
            event.id,
        )

    async def _record_busy_in_delivery(
        self, session: AsyncSession, payload: dict[str, Any]
    ) -> None:
        """Ledger trace of a busy attempt (台账记一次 busy 尝试, §3.10)."""
        delivery_id = _uuid_or_none(payload.get("delivery_id"))
        if delivery_id is None:
            return
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is not None and delivery.state == "pending":
            delivery.error = REASON_TOKEN_BUSY
            await session.flush()

    async def _record_chunk_progress(self, session: AsyncSession, payload: dict[str, Any]) -> None:
        delivery_id = _uuid_or_none(payload.get("delivery_id"))
        if delivery_id is None:
            return
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is None or delivery.state != "pending":
            return
        try:
            meta = json.loads(delivery.external_target or "{}")
        except json.JSONDecodeError:
            meta = {}
        sent_chunks = int(meta.get("sent_chunks") or 0) + 1
        meta["sent_chunks"] = sent_chunks
        chunks_total = int(meta.get("chunks_total") or payload.get("chunks_total") or 1)
        delivery.external_target = json.dumps(meta)
        if sent_chunks >= chunks_total:
            delivery.state = "sent"
            delivery.sent_at = self._clock()
            delivery.error = None  # clear any transient busy trace
        await session.flush()

    async def _mark_delivery_sent(self, session: AsyncSession, payload: dict[str, Any]) -> None:
        delivery_id = _uuid_or_none(payload.get("delivery_id"))
        if delivery_id is None:
            return
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is not None and delivery.state == "pending":
            delivery.state = "sent"
            delivery.sent_at = self._clock()
            delivery.error = None  # clear any transient busy trace
            await session.flush()

    async def _fail_delivery(
        self, session: AsyncSession, payload: dict[str, Any], *, reason: str
    ) -> None:
        delivery_id = _uuid_or_none(payload.get("delivery_id"))
        if delivery_id is None:
            return
        delivery = await session.get(NotificationDelivery, delivery_id)
        if delivery is not None and delivery.state == "pending":
            delivery.state = "failed"
            delivery.error = reason
            await session.flush()

    def _target_from_payload(
        self,
        payload: dict[str, Any],
        workspace_id: uuid.UUID,
        integration_id: uuid.UUID | None,
    ) -> ConversationTarget:
        return target_from_payload(payload, workspace_id, integration_id)

    async def _adapter_for(
        self, session: AsyncSession, *, integration_id: uuid.UUID
    ) -> DingTalkIMAdapter | None:
        integration = await session.get(Integration, integration_id)
        if integration is None or integration.status != "active" or not integration.secret_ref:
            return None
        cache_key = (integration.id, integration.secret_ref)
        cached = self._adapters.get(cache_key)
        if cached is not None:
            return cached
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=self._request_timeout)
        adapter = await make_adapter(
            redis=self._redis,
            integration=integration,
            signing_secret=self._signing_secret,
            api_base=self._api_base,
            http_client=self._http_client,
            max_chunks=self._max_chunks,
            refresh_timeout=self._token_refresh_timeout,
            lock_ttl=self._token_lock_ttl,
            follower_wait=self._token_follower_wait,
            request_timeout=self._request_timeout,
        )
        if adapter is not None:
            self._adapters[cache_key] = adapter
        return adapter


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# notification.fanout → IM delivery derivation (§3.3 / §3.10 主动推送)
# ---------------------------------------------------------------------------


def _im_result_markdown(notification_type: str, payload: dict[str, Any]) -> str:
    """Conversational IM copy for a notification (site inbox stays the
    source of truth; this is the outbound enhancement, README §6.13)."""
    if notification_type == "execution_finished":
        status = str(payload.get("status") or "")
        if status in ("failed", "timeout"):
            reason = str(payload.get("failure_reason") or "")
            return f"❌ 执行失败：{reason}" if reason else "❌ 执行失败"
        if status == "cancelled":
            return "⏹ 执行已取消"
        summary = str(payload.get("result_summary") or payload.get("summary") or "")
        return f"✅ 执行完成\n\n{summary}" if summary else "✅ 执行完成"
    if notification_type == "comment_created":
        body = str(payload.get("body") or payload.get("excerpt") or "")
        return body or "💬 新评论"
    # Progress / other types (only pushed under verbosity='progress').
    return str(payload.get("body") or payload.get("text") or "")


async def derive_im_deliveries_from_fanout(session: AsyncSession, event: OutboxEvent) -> None:
    """Chain AFTER the base ``notification.fanout`` handler: for
    integration-triggered executions, materialize the
    ``notification_delivery(channel='im')`` ledger row + the ``im.send``
    outbox events (approval card, or chunked result text) that the
    IMSendRelay delivers to the source conversation.

    Verbosity (§3.3): ``final_only`` (default) derives only final-result /
    approval notifications; progress types require ``verbosity='progress'``.
    The in-app inbox is ALWAYS complete — IM is the enhancement.
    """
    from mesh.db.models.integration import IntegrationEvent
    from mesh.db.models.notification import Notification
    from mesh.db.models.runtime import TaskExecution

    payload = event.payload or {}
    notification_type = str(payload.get("type") or payload.get("kind") or "")
    execution_id = _uuid_or_none(payload.get("execution_id"))
    if not notification_type or execution_id is None:
        return
    await set_tenant_context(session, event.workspace_id)
    execution = await session.get(TaskExecution, execution_id)
    if execution is None or execution.trigger != "integration":
        return
    item = await session.scalar(
        select(IntegrationMessageQueue)
        .where(IntegrationMessageQueue.execution_id == execution_id)
        .limit(1)
    )
    if item is None or item.integration_id is None:
        return
    integration = await session.get(Integration, item.integration_id)
    if (
        integration is None
        or integration.kind != "im_dingtalk"
        or integration.status != "active"
    ):
        return
    config = integration.config or {}
    verbosity = str(config.get("verbosity") or VERBOSITY_FINAL_ONLY)
    if not should_push_notification(notification_type=notification_type, verbosity=verbosity):
        return
    # In-app is the source of truth: mirror an existing notification only.
    notification = await session.scalar(
        select(Notification)
        .where(
            Notification.workspace_id == event.workspace_id,
            Notification.execution_id == execution_id,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
    )
    if notification is None:
        return
    _tenant, external_ref = _parse_conversation_key(item.conversation_key)
    # conversation type: authoritative from the ingested event payload
    conversation_type = CONVERSATION_GROUP
    if item.integration_event_id is not None:
        ingested = await session.get(IntegrationEvent, item.integration_event_id)
        raw_type = str((ingested.payload or {}).get("conversationType") or "") if ingested else ""
        if raw_type == "1":
            conversation_type = CONVERSATION_DIRECT
    sender_key = ""
    if conversation_type == CONVERSATION_DIRECT:
        sender_key = item.sender_identity_key.rpartition(":")[2]
    destination_key = f"dingtalk:{item.binding_id}:{external_ref}"

    card = is_card_notification(notification_type)
    approval_id = _uuid_or_none(payload.get("approval_id"))
    if card and approval_id is None:
        return
    chunks_total = 1
    text_chunks: list[str] = []
    if not card:
        markdown = _im_result_markdown(notification_type, payload)
        if not markdown:
            return
        text_chunks = plan_result_chunks(markdown, max_chunks=5)
        if not text_chunks:
            return
        chunks_total = len(text_chunks)

    delivery = NotificationDelivery(
        workspace_id=event.workspace_id,
        notification_id=notification.id,
        channel="im",
        provider="dingtalk",
        destination_key=destination_key,
        integration_id=integration.id,
        binding_id=item.binding_id,
        external_target=json.dumps(
            {
                "chunks_total": chunks_total,
                "sent_chunks": 0,
                "conversation_type": conversation_type,
                "conversation_key": item.conversation_key,
                "sender_key": sender_key,
                "card": card,
            }
        ),
        state="pending",
    )
    session.add(delivery)
    try:
        async with session.begin_nested():
            await session.flush()
    except Exception:  # noqa: BLE001 — UNIQUE(notification_id, channel, dest)
        return  # duplicate derivation (at-least-once fanout) — ledger exists
    base_payload = {
        "workspace_id": str(event.workspace_id),
        "integration_id": str(integration.id),
        "binding_id": str(item.binding_id) if item.binding_id else None,
        "conversation_key": item.conversation_key,
        "conversation_type": conversation_type,
        "target_user_key": sender_key,
        "delivery_id": str(delivery.id),
    }
    from mesh.outbox.service import emit_event as _emit

    if card:
        await _emit(
            session,
            workspace_id=event.workspace_id,
            event_type=IM_SEND_EVENT_TYPE,
            payload={**base_payload, "kind": IM_SEND_KIND_CARD, "approval_id": str(approval_id)},
            idempotency_key=hashlib.sha256(f"{approval_id}|card".encode()).hexdigest(),
        )
        return
    for index, chunk_text in enumerate(text_chunks):
        await _emit(
            session,
            workspace_id=event.workspace_id,
            event_type=IM_SEND_EVENT_TYPE,
            payload={
                **base_payload,
                "kind": IM_SEND_KIND_NOTIFICATION,
                "chunk_index": index,
                "chunks_total": chunks_total,
                "text": chunk_text,
            },
            idempotency_key=chunk_idempotency_key(notification.id, index),
        )


async def make_adapter(
    *,
    redis: Any,
    integration: Integration,
    signing_secret: str,
    api_base: str = "https://api.dingtalk.com",
    http_client: httpx.AsyncClient | None = None,
    max_chunks: int = 5,
    refresh_timeout: float | None = None,
    lock_ttl: int | None = None,
    follower_wait: float | None = None,
    request_timeout: float | None = None,
) -> DingTalkIMAdapter | None:
    """Build the outbound adapter for an integration instance (None when
    the integration is inactive / has no credential). The decrypted
    app_secret exists ONLY inside the token manager's memory (§6.16)."""
    if integration is None or integration.status != "active" or not integration.secret_ref:
        return None
    config = integration.config or {}
    app_secret = decrypt_credential_value(integration.secret_ref, signing_secret)
    app_key = str(config.get("app_key") or "")
    robot_code = str(config.get("robot_code") or app_key)
    client = http_client or httpx.AsyncClient()
    token_kwargs: dict[str, Any] = {}
    if refresh_timeout is not None:
        token_kwargs["refresh_timeout"] = refresh_timeout
    if lock_ttl is not None:
        token_kwargs["lock_ttl"] = lock_ttl
    if follower_wait is not None:
        token_kwargs["follower_wait"] = follower_wait
    token_manager = DingTalkTokenManager(
        redis,
        http_client=client,
        integration_id=integration.id,
        app_key=app_key,
        app_secret=app_secret,
        api_base=api_base,
        **token_kwargs,
    )
    client_kwargs: dict[str, Any] = {}
    if request_timeout is not None:
        client_kwargs["request_timeout"] = request_timeout
    api_client = DingTalkClient(
        token_manager, http_client=client, api_base=api_base, robot_code=robot_code,
        **client_kwargs,
    )
    return DingTalkIMAdapter(api_client, max_chunks=max_chunks)


__all__ = [
    "CONVERSATION_DIRECT",
    "CONVERSATION_GROUP",
    "ConversationTarget",
    "DEFAULT_CHUNK_MAX_BYTES",
    "DingTalkIMAdapter",
    "EXTERNAL_CONTACT_PREFIX",
    "FINAL_NOTIFICATION_TYPES",
    "REASON_INVALID_CREDENTIALS",
    "REASON_NO_STAFF_ID",
    "REASON_RATE_LIMITED",
    "REASON_TOKEN_BUSY",
    "REASON_UPSTREAM_ERROR",
    "SEND_STATUS_FAILED",
    "SEND_STATUS_SENT",
    "STAFF_ID_KEY_PATTERN",
    "SendOutcome",
    "TRUNCATION_LINK_TEMPLATE",
    "VERBOSITY_FINAL_ONLY",
    "VERBOSITY_PROGRESS",
    "chunk_idempotency_key",
    "derive_im_deliveries_from_fanout",
    "encode_external_contact_key",
    "is_card_notification",
    "is_external_contact_key",
    "is_valid_staff_id_key",
    "make_adapter",
    "normalize_dingtalk_user_key",
    "sanitize_no_mentions",
    "should_push_notification",
    "split_markdown_chunks",
    "target_from_payload",
    "truncate_to_bytes",
    "validate_identity_segment",
]
