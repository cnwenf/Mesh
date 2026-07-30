"""Normalized key encoding + segment validation for the IM message queue.

integrations.md §2.10 (``conversation_key`` / ``sender_identity_key`` encoding
+ segment validation, N-1 correction) and §3.10 (external user key encoding,
E-1 closure).

Three normalized keys share one grammar — ``<provider>:<tenant>:<segment>`` —
with ``:`` reserved as the structural separator. The service layer validates
every segment before insertion so a crafted value can never collapse the
triple (``("a","b:c","d")`` vs ``("a","b","c:d")``) into an ambiguous key:

* ``conversation_key``   → ``provider:provider_tenant_key:external_ref``
* ``sender_identity_key`` → ``provider:provider_tenant_key:external_user_key``

External user key encoding (§3.10): an in-corp member is its ``senderStaffId``
verbatim (widest official charset ``[A-Za-z0-9._-]``); an external contact
(no staffId) is ``x=<base64url(senderId)>`` — ``senderId`` is an opaque
``$:LWCP_v1:$…`` ciphertext carrying ``:``, so it is re-encoded with the
URL-safe base64 alphabet (``A-Za-z0-9_-``, no padding) to strip the colon.
The two key spaces are structurally disjoint: an encoded key's 2nd character
is always ``=`` while ``=`` is outside the staffId charset, so no legal
staffId can ever equal an encoded key (E-1 — guaranteed by charset algebra,
independent of any platform documentation revision).

These helpers are pure: they never mutate their inputs and raise
:class:`~mesh.errors.ValidationError` (400 ``invalid_request``) on any
violation. Executable anchors: schema_r2_validation.sql T39-12 / T39-15.
"""

from __future__ import annotations

import base64
import re

from mesh.db.models.integration import BINDING_PROVIDER_VALUES
from mesh.errors import ValidationError

# §2.10 / §3.10 single source of truth — widest official ``senderStaffId``
# charset (the union of the two published official variants, ``.`` admitted).
STAFF_ID_RE = r"^[A-Za-z0-9._-]+$"
# Superset for the ``external_ref`` / user-key segment: everything base64-ish
# (dingtalk ``conversationId`` carries ``+`` ``/`` ``=``) but never ``:``.
EXTERNAL_REF_RE = r"^[A-Za-z0-9_.@+/=-]+$"

# §3.10 prefix for encoded external-contact keys — the 2nd char of the whole
# key is therefore always ``=``, which ``STAFF_ID_RE`` can never contain.
_ENCODED_USER_KEY_PREFIX = "x="

# §2.10 per-platform tenant grammar. dingtalk ``corpId`` is ``ding<alnum>+``;
# every other provider stays lenient and admits the bindings default ``''``.
_DINGTALK_PROVIDER = "dingtalk"
_DINGTALK_TENANT_RE = r"^ding[A-Za-z0-9]+$"
_GENERIC_TENANT_RE = r"^[A-Za-z0-9._-]*$"

# C0/C1 control characters — never permitted inside a stored key segment.
_CONTROL_CHAR_RE = re.compile("[\x00-\x1f\x7f-\x9f]")

# Controls + newlines + zero-width / bidi formatting characters, stripped when
# building a human-facing excerpt (Python ``re`` has no ``\p{}``; explicit
# ranges). Covers soft hyphen, ZW-space/joiner, LRM/RLM, line/paragraph
# separators, bidi controls/isolates, word joiner and the BOM.
_EXCERPT_STRIP_RE = re.compile(
    "[\x00-\x1f\x7f-\x9f­᠎​-‏ - "
    "⁠-⁤⁦-⁯﻿]"
)
_WHITESPACE_RUN_RE = re.compile(r"\s+")

EXCERPT_DEFAULT_LIMIT = 120


def _invalid(message: str) -> ValidationError:
    return ValidationError(message, code="invalid_request")


def _validate_provider(provider: str) -> None:
    """``provider`` must be a registered enum value (§2.10 segment rule ①)."""
    if provider not in BINDING_PROVIDER_VALUES:
        raise _invalid("unknown provider segment")


def _validate_tenant(provider: str, tenant_key: str) -> None:
    """``tenant_key`` must match the per-platform pattern (§2.10 rule ②)."""
    pattern = _DINGTALK_TENANT_RE if provider == _DINGTALK_PROVIDER else _GENERIC_TENANT_RE
    if re.fullmatch(pattern, tenant_key) is None:
        raise _invalid("tenant segment contains invalid characters")


def _validate_external_ref(external_ref: str) -> None:
    """``external_ref`` must match the colon-free superset (§2.10 rule ③)."""
    if re.fullmatch(EXTERNAL_REF_RE, external_ref) is None:
        raise _invalid("external_ref segment contains invalid characters")


# ---------------------------------------------------------------------------
# External user key encoding (§3.10, E-1)
# ---------------------------------------------------------------------------


def encode_external_user_key(*, staff_id: str | None, sender_id: str | None) -> str:
    """Normalize the external-platform sender onto an ``external_user_key``.

    staffId wins when present (verbatim passthrough after a charset guard);
    otherwise ``sender_id`` is required and encoded as
    ``x=<base64url-nopad(sender_id)>`` so the colon-bearing ciphertext can
    never reach a ``:``-delimited key. Both missing → ``invalid_request``.
    """
    if staff_id:
        if re.fullmatch(STAFF_ID_RE, staff_id) is None:
            raise _invalid("staff_id contains invalid characters")
        return staff_id
    if sender_id:
        encoded = base64.urlsafe_b64encode(sender_id.encode("utf-8")).decode("ascii")
        return f"{_ENCODED_USER_KEY_PREFIX}{encoded.rstrip('=')}"
    raise _invalid("staff_id or sender_id is required")


# ---------------------------------------------------------------------------
# conversation_key / sender_identity_key build + validate (§2.10)
# ---------------------------------------------------------------------------


def build_conversation_key(provider: str, tenant_key: str, external_ref: str) -> str:
    """Assemble ``provider:tenant:external_ref`` after per-segment validation."""
    _validate_provider(provider)
    _validate_tenant(provider, tenant_key)
    _validate_external_ref(external_ref)
    return f"{provider}:{tenant_key}:{external_ref}"


def build_sender_identity_key(provider: str, tenant_key: str, user_key: str) -> str:
    """Assemble ``provider:tenant:user_key``; the user_key never carries ``:``."""
    _validate_provider(provider)
    _validate_tenant(provider, tenant_key)
    _validate_user_key(user_key)
    return f"{provider}:{tenant_key}:{user_key}"


def _validate_user_key(user_key: str) -> None:
    """A user_key is a staffId or an ``x=<base64url>`` key — both colon-free.

    Enforced explicitly (not merely by construction) so a raw ``senderId``
    carrying ``:`` is rejected with ``invalid_request`` rather than collapsing
    the triple (N-1 negative case).
    """
    if ":" in user_key:
        raise _invalid("user_key segment must not contain ':'")
    if re.fullmatch(EXTERNAL_REF_RE, user_key) is None:
        raise _invalid("user_key segment contains invalid characters")


def _split_key(key: str) -> tuple[str, str, str]:
    """Split a normalized key into exactly three validated segments."""
    if not key:
        raise _invalid("key is required")
    segments = key.split(":", 2)
    if len(segments) != 3:
        raise _invalid("key must have exactly three ':'-separated segments")
    provider, tenant, tail = segments
    for segment in segments:
        if not segment:
            raise _invalid("key segment must not be empty")
        if _CONTROL_CHAR_RE.search(segment):
            raise _invalid("key segment must not contain control characters")
    _validate_provider(provider)
    return provider, tenant, tail


def validate_conversation_key(key: str) -> tuple[str, str, str]:
    """Parse ``provider:tenant:external_ref`` or raise ``invalid_request``.

    Exactly three segments (``maxsplit=2``), each non-empty, no control
    characters anywhere, provider a registered enum, and the ``external_ref``
    segment matching :data:`EXTERNAL_REF_RE` (a 4th ``:``-bearing segment is
    caught here — it lands in the tail and fails the charset guard).
    """
    provider, tenant, external_ref = _split_key(key)
    _validate_external_ref(external_ref)
    return provider, tenant, external_ref


def validate_sender_identity_key(key: str) -> tuple[str, str, str]:
    """Parse ``provider:tenant:user_key`` or raise ``invalid_request``.

    Same grammar as :func:`validate_conversation_key`; the user_key segment is
    additionally asserted colon-free (structurally guaranteed by the staffId
    charset and ``x=<base64url>`` encoding, enforced defensively here).
    """
    provider, tenant, user_key = _split_key(key)
    _validate_user_key(user_key)
    return provider, tenant, user_key


# ---------------------------------------------------------------------------
# Text hygiene (§2.10 inbound length cap / §3.9 message_excerpt)
# ---------------------------------------------------------------------------


def sanitize_excerpt(text: str, limit: int = EXCERPT_DEFAULT_LIMIT) -> str:
    """Render a single-line, control-free excerpt of at most ``limit`` chars.

    Strips controls/newlines/zero-width formatting, collapses whitespace runs
    to a single space, then char-truncates. Used for the queue ``message_excerpt``
    (≤120 chars, §3.9) — never the full message body.
    """
    stripped = _EXCERPT_STRIP_RE.sub("", text)
    collapsed = _WHITESPACE_RUN_RE.sub(" ", stripped).strip()
    return collapsed[:limit]


def truncate_inbound_text(text: str, limit: int) -> tuple[str, bool]:
    """Char-truncate inbound text to ``limit``, returning ``(text, truncated)``.

    Message bodies and ``/btw`` arguments are bounded by
    ``MESH_IM_INBOUND_TEXT_MAX_CHARS`` (§2.10); the boolean drives the
    ``payload.truncated=true`` audit marker.
    """
    if len(text) <= limit:
        return text, False
    return text[:limit], True


__all__ = [
    "EXCERPT_DEFAULT_LIMIT",
    "EXTERNAL_REF_RE",
    "STAFF_ID_RE",
    "build_conversation_key",
    "build_sender_identity_key",
    "encode_external_user_key",
    "sanitize_excerpt",
    "truncate_inbound_text",
    "validate_conversation_key",
    "validate_sender_identity_key",
]
