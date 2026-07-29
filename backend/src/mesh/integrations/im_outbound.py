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

import base64
import hashlib
import re
import uuid

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


__all__ = [
    "DEFAULT_CHUNK_MAX_BYTES",
    "EXTERNAL_CONTACT_PREFIX",
    "FINAL_NOTIFICATION_TYPES",
    "STAFF_ID_KEY_PATTERN",
    "VERBOSITY_FINAL_ONLY",
    "VERBOSITY_PROGRESS",
    "chunk_idempotency_key",
    "encode_external_contact_key",
    "is_card_notification",
    "is_external_contact_key",
    "is_valid_staff_id_key",
    "normalize_dingtalk_user_key",
    "should_push_notification",
    "split_markdown_chunks",
    "validate_identity_segment",
]
