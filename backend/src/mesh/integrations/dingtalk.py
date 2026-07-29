"""DingTalk (DingDing) enterprise-internal-app robot connector adapter.

integrations.md §3.2 / §3.7 / §5.6 (MES-87). The DUAL receive modes share
this adaptation layer:

* **HTTP callback mode** (``config.receive_mode='http'``): per-request
  ``timestamp`` + ``sign`` header verification, where
  ``sign = Base64(HMAC_SHA256(app_secret, timestamp + "\\n" + app_secret))``.
  Constant-time comparison; the official ±3600s replay window is NEVER
  narrowed (a stricter window would reject legitimate platform callbacks).
  The signed string covers ONLY ``timestamp + "\\n" + app_secret`` — body
  integrity is guaranteed by the HTTPS/TLS transport; verification must not
  read the body (a "tampered body, valid sign" combination is unreachable
  on the real channel and rejecting it would drop legitimate callbacks).
* **Stream mode** (``config.receive_mode='stream'``): Mesh dials the
  platform gateway itself (``dingtalk_stream.py``); frame authenticity is
  established once at ``connections/open`` by the same app_key/app_secret
  credential pair — the channel-level equivalent of per-frame signatures.

Payload normalization (same for both channels): ``chatbotCorpId`` →
``provider_tenant_key``; ``conversationId`` → ``external_ref``;
``msgId`` → ``external_event_id`` (dedup key); sender identity = staffId
verbatim (widest official charset ``[A-Za-z0-9._-]``) or, for external
contacts without a staffId, ``x=<base64url(senderId)>`` — the raw senderId
(``$:LWCP_v1:$…``) carries colons and would collapse the ``provider:tenant:
ref`` triple separator, so it is NEVER used verbatim (N-1 / E-1).

Message-type matrix (platform-side delivery facts, declared as-is): group
@-bot delivery exists only for ``text`` / ``richText`` / ``picture``;
direct messages deliver all types. **Triggering is text-only** — non-text
types are audit-only (``processed``; no trigger, no queue, no ack). The
@-bot prefix space the platform injects is trimmed; text is truncated to
``MESH_IM_INBOUND_TEXT_MAX_CHARS`` (default 4000) with a ``truncated``
audit flag (§2.10).

Secrets reach this module ONLY as decrypted plaintext arguments (the
pipeline decrypts ``app_secret_ref`` ciphertext via the
``runtime_credentials`` contract, README §6.16); values are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import timedelta
from typing import Any

from mesh.errors import ValidationError
from mesh.integrations.connectors import (
    SIG_INVALID,
    SIG_MISSING,
    SIG_VALID,
    VerifiedEnvelope,
)

PROVIDER = "dingtalk"
KIND = "im_dingtalk"

# §3.2 M1: the DingTalk-OFFICIAL timestamp tolerance. The adapter must not
# narrow it — doing so rejects legitimate platform callbacks (§5.6).
DINGTALK_SIGNATURE_TOLERANCE = timedelta(seconds=3600)

# §3.2: signature covers the timestamp (milliseconds) + "\n" + app_secret.
_SIGNATURE_HEADERS = ("sign",)
_TIMESTAMP_HEADER = "timestamp"

# --- Normalized key algebra (§2.10 N-1 / E-1) -----------------------------
# Widest OFFICIAL staffId charset (the docs carry two versions — digits-only
# and "letters, digits and -_"; this is their widest union, '.' admitted).
STAFF_ID_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")
# external_ref segment charset: a COLON-FREE superset admitting the official
# base64-like conversationId/msgId alphabet (A-Za-z0-9+/=, e.g. 'cid…==').
EXTERNAL_REF_CHARSET = re.compile(r"^[A-Za-z0-9_.@+/=-]+$")
# DingTalk corpId shape (provider_tenant_key dimension).
_CORP_ID_CHARSET = re.compile(r"^ding[A-Za-z0-9]+$")
# Generic (non-DingTalk) segment rule: no ':' (the key separator) and no
# control characters — keeps feishu/slack tenant/ref shapes working.
_GENERIC_SEGMENT_FORBIDDEN = re.compile(r"[:\x00-\x1f\x7f]")

# Encoded external-contact identity key prefix (E-1: the 2nd char '=' is
# outside STAFF_ID_CHARSET → the two key spaces are structurally disjoint).
_ENCODED_KEY_PREFIX = "x="

# §3.2 message-type matrix: platform delivers these for group @-bot.
GROUP_DELIVERED_MSGTYPES = frozenset({"text", "richText", "picture"})
# Triggering is text-only (command plane + task messages).
TRIGGER_MSGTYPES = frozenset({"text"})

# §2.10 inbound text ceiling (MESH_IM_INBOUND_TEXT_MAX_CHARS default).
DEFAULT_INBOUND_TEXT_MAX_CHARS = 4000

# Stream frame protocol topics (§3.2).
STREAM_MESSAGE_TOPIC = "/v1.0/im/bot/messages/get"
STREAM_CARD_TOPIC = "/v1.0/card/instances/callback"
STREAM_SPEC_VERSION = "1.0"

# §2.7 config: MESH_DINGTALK_GATEWAY_BASE default (official domain). The
# gateway base is a DEPLOY-TIME environment variable ONLY (M2): it never
# enters integrations.config nor any admin API (an admin-editable gateway
# base would be a Stream-MITM privilege-escalation path exfiltrating the
# in-memory app_secret). A non-default value in production triggers a
# startup warning + audit entry (dingtalk_stream.py).
DEFAULT_GATEWAY_BASE = "https://api.dingtalk.com"
GATEWAY_OPEN_PATH = "/v1.0/gateway/connections/open"


# ---------------------------------------------------------------------------
# HTTP callback signature verification (§3.2 M1)
# ---------------------------------------------------------------------------


def compute_callback_sign(timestamp_ms: str | int, app_secret: str) -> str:
    """Recompute ``Base64(HMAC_SHA256(app_secret, timestamp + "\\n" + secret))``."""
    material = f"{timestamp_ms}\n{app_secret}".encode()
    digest = hmac.new(app_secret.encode(), material, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_callback_signature(
    *,
    app_secret: str,
    headers: dict[str, str],
    now,
    tolerance: timedelta = DINGTALK_SIGNATURE_TOLERANCE,
) -> str:
    """Verify the ``timestamp`` + ``sign`` callback headers.

    Returns ``SIG_VALID`` / ``SIG_INVALID`` / ``SIG_MISSING``. Constant-time
    comparison; the signed material is exactly ``timestamp + "\\n" +
    app_secret`` (body untouched — TLS guarantees body integrity). The
    official ±3600s ``tolerance`` is the floor; callers must not pass a
    narrower window.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    presented = next((lowered[h] for h in _SIGNATURE_HEADERS if lowered.get(h)), None)
    if not presented:
        return SIG_MISSING
    raw_ts = lowered.get(_TIMESTAMP_HEADER)
    try:
        ts_ms = float(str(raw_ts or "").strip())
    except (TypeError, ValueError):
        return SIG_INVALID
    # Replay protection over the official window (milliseconds on the wire).
    if abs(now.timestamp() * 1000.0 - ts_ms) > tolerance.total_seconds() * 1000.0:
        return SIG_INVALID
    # Sign over the timestamp STRING AS PRESENTED (not the parsed float) —
    # the platform signs the literal header value.
    expected = compute_callback_sign(str(raw_ts).strip(), app_secret)
    if not hmac.compare_digest(expected.encode(), presented.encode()):
        return SIG_INVALID
    return SIG_VALID


# ---------------------------------------------------------------------------
# Sender identity encoding (N-1 official samples / E-1 disjointness)
# ---------------------------------------------------------------------------


def _base64url_nopad(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def encode_external_user_key(staff_id: str | None, sender_id: str | None) -> str:
    """Normalize the sender identity segment (§3.10 encoding, E-1 closed).

    * Enterprise member (``senderStaffId`` present): the staffId VERBATIM —
      it must match the widest official charset ``[A-Za-z0-9._-]+``; a value
      carrying ':' (e.g. a raw senderId presented as staffId) is REFUSED
      (would collapse the triple separator).
    * External contact (no staffId): ``x=<base64url(senderId)>`` — colon-free
      by construction; the 2nd char is always '=' which is outside the
      staffId charset → the two key spaces can never collide.
    * Neither present: '' (audit-only, no identity).
    """
    staff = str(staff_id or "").strip()
    if staff:
        if not STAFF_ID_CHARSET.match(staff):
            raise ValidationError(
                "senderStaffId outside the official charset",
                code="invalid_request",
                details={"field": "senderStaffId"},
            )
        return staff
    sender = str(sender_id or "").strip()
    if sender:
        return f"{_ENCODED_KEY_PREFIX}{_base64url_nopad(sender)}"
    return ""


def _validate_key_segment(value: str, *, field: str) -> None:
    """A key segment may never contain ':' (the triple separator) or control
    characters — the separator-collapse defense (N-1)."""
    if not value or _GENERIC_SEGMENT_FORBIDDEN.search(value):
        raise ValidationError(
            f"{field} contains a reserved separator or control character",
            code="invalid_request",
            details={"field": field},
        )


def _validate_user_key_segment(user_key: str) -> None:
    """Third segment of sender_identity_key: a staffId-charset value or an
    encoded ``x=<base64url>`` key — never a raw colon-carrying senderId."""
    _validate_key_segment(user_key, field="external_user_key")
    if user_key.startswith(_ENCODED_KEY_PREFIX):
        return  # encoded external-contact key (colon-free by construction)
    if not STAFF_ID_CHARSET.match(user_key):
        raise ValidationError(
            "external_user_key must be a staffId-charset value or an x=<base64url> key",
            code="invalid_request",
            details={"field": "external_user_key"},
        )


def build_conversation_key(provider: str, provider_tenant_key: str, external_ref: str) -> str:
    """``provider:provider_tenant_key:external_ref`` with per-segment checks.

    DingTalk: tenant must be a corpId (``ding[A-Za-z0-9]+``), external_ref
    must match the colon-free superset ``[A-Za-z0-9_.@+/=-]+`` (official
    base64-like conversationIds — 'cid…==' — pass; ':' injection is
    refused). Other providers: generic no-separator/no-control rule.
    """
    from mesh.db.models.integration import BINDING_PROVIDER_VALUES

    if provider not in BINDING_PROVIDER_VALUES:
        raise ValidationError(
            "unknown provider", code="invalid_request", details={"provider": provider}
        )
    # Tenant may be '' (bindings created without a platform tenant; the
    # bindings table defaults provider_tenant_key to '') — when present it
    # must be separator/control-free; DingTalk requires the corpId shape.
    if provider_tenant_key:
        _validate_key_segment(provider_tenant_key, field="provider_tenant_key")
    _validate_key_segment(external_ref, field="external_ref")
    if provider == PROVIDER:
        if not _CORP_ID_CHARSET.match(provider_tenant_key):
            raise ValidationError(
                "dingtalk provider_tenant_key must be a corpId (ding…)",
                code="invalid_request",
                details={"field": "provider_tenant_key"},
            )
        if not EXTERNAL_REF_CHARSET.match(external_ref):
            raise ValidationError(
                "dingtalk external_ref outside the official ID charset",
                code="invalid_request",
                details={"field": "external_ref"},
            )
    return f"{provider}:{provider_tenant_key}:{external_ref}"


def build_sender_identity_key(provider: str, provider_tenant_key: str, external_user_key: str) -> str:
    """``provider:provider_tenant_key:external_user_key`` (full triple).

    The third segment is validated as a staffId-charset value or an encoded
    ``x=<base64url>`` key — a raw senderId (with colons) is refused.
    """
    from mesh.db.models.integration import IDENTITY_PROVIDER_VALUES

    if provider not in IDENTITY_PROVIDER_VALUES:
        raise ValidationError(
            "unknown provider", code="invalid_request", details={"provider": provider}
        )
    if provider_tenant_key:
        _validate_key_segment(provider_tenant_key, field="provider_tenant_key")
    if provider == PROVIDER:
        if not _CORP_ID_CHARSET.match(provider_tenant_key):
            raise ValidationError(
                "dingtalk provider_tenant_key must be a corpId (ding…)",
                code="invalid_request",
                details={"field": "provider_tenant_key"},
            )
        # E-1 charset algebra: staffId-charset or x=<base64url> — a raw
        # colon-carrying senderId is refused (separator-collapse defense).
        _validate_user_key_segment(external_user_key)
    else:
        _validate_key_segment(external_user_key, field="external_user_key")
    return f"{provider}:{provider_tenant_key}:{external_user_key}"


# ---------------------------------------------------------------------------
# Payload normalization → VerifiedEnvelope
# ---------------------------------------------------------------------------


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _extract_text(payload: dict[str, Any]) -> str:
    """text.content with the platform-injected @-bot leading space trimmed."""
    text_field = payload.get("text")
    if isinstance(text_field, dict):
        raw = text_field.get("content")
    else:
        raw = text_field
    return str(raw or "").strip()


def normalize_message_payload(
    payload: dict[str, Any],
    *,
    max_chars: int = DEFAULT_INBOUND_TEXT_MAX_CHARS,
    channel: str,
) -> VerifiedEnvelope:
    """Map one DingTalk bot-message payload onto the shared envelope.

    Applies the §3.7 trim (@-bot prefix space), the §2.10 truncation (with
    ``truncated`` audit flag), and the N-1/E-1 sender key encoding. The raw
    payload is carried whole for the audit ledger (untrusted data, §6.15).
    """
    msg_id = str(payload.get("msgId") or "").strip()
    if not msg_id:
        raise ValidationError("missing msgId", code="invalid_request", details={"field": "msgId"})
    conversation_id = str(payload.get("conversationId") or "").strip()
    if not conversation_id:
        raise ValidationError(
            "missing conversationId", code="invalid_request", details={"field": "conversationId"}
        )
    corp_id = str(payload.get("chatbotCorpId") or "").strip()
    conversation_type = str(payload.get("conversationType") or "").strip() or None
    msgtype = str(payload.get("msgtype") or "").strip()
    sender_key = encode_external_user_key(
        payload.get("senderStaffId"), payload.get("senderId")
    )

    text = _extract_text(payload) if msgtype in TRIGGER_MSGTYPES else ""
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    is_dm = conversation_type == "1"
    is_in_at_list = _as_bool(payload.get("isInAtList"))
    return VerifiedEnvelope(
        provider=PROVIDER,
        provider_tenant_key=corp_id,
        external_event_id=msg_id,
        event_type="im.bot.message",
        external_ref=conversation_id,
        conversation_type=conversation_type,
        sender_key=sender_key,
        text=text,
        truncated=truncated,
        msgtype=msgtype,
        raw_payload=dict(payload),
        channel=channel,
        is_direct_message=is_dm,
        bot_mentioned=is_dm or is_in_at_list,
        extra={
            "msgtype": msgtype,
            "conversation_type": conversation_type,
            "is_in_at_list": is_in_at_list,
            "robot_code": str(payload.get("robotCode") or ""),
            "sender_nick": str(payload.get("senderNick") or ""),
            "sender_platform": str(payload.get("senderPlatform") or ""),
            "at_users": payload.get("atUsers") or [],
            "session_webhook_expired_time": payload.get("sessionWebhookExpiredTime"),
        },
    )


def is_trigger_message(envelope: VerifiedEnvelope) -> bool:
    """§3.2 msgtype matrix: triggering is TEXT-only; non-text types are
    audit-only (processed; no trigger, no queue, no ack)."""
    return envelope.msgtype in TRIGGER_MSGTYPES


# ---------------------------------------------------------------------------
# Stream gateway base resolution (M2 deploy-time test-injection door)
# ---------------------------------------------------------------------------


def resolve_gateway_base(env_value: str | None) -> tuple[str, bool]:
    """Resolve the gateway base; return ``(base, is_non_default)``.

    ``is_non_default`` drives the production startup warning + audit (M2:
    a non-official gateway base means Stream credentials travel to an
    untrusted peer — operators must see this at boot).
    """
    raw = str(env_value or "").strip()
    base = (raw or DEFAULT_GATEWAY_BASE).rstrip("/")
    is_non_default = base != DEFAULT_GATEWAY_BASE
    return base, is_non_default


def stream_user_agent(version: str | None = None) -> str:
    """The ``ua`` string presented at ``connections/open``."""
    if version:
        return f"mesh-integration/{version}"
    try:
        from importlib.metadata import version as _pkg_version

        return f"mesh-integration/{_pkg_version('mesh-backend')}"
    except Exception:  # noqa: BLE001 — metadata absent in exotic layouts
        return "mesh-integration/dev"


__all__ = [
    "DEFAULT_GATEWAY_BASE",
    "DEFAULT_INBOUND_TEXT_MAX_CHARS",
    "DINGTALK_SIGNATURE_TOLERANCE",
    "EXTERNAL_REF_CHARSET",
    "GATEWAY_OPEN_PATH",
    "GROUP_DELIVERED_MSGTYPES",
    "KIND",
    "PROVIDER",
    "STAFF_ID_CHARSET",
    "STREAM_CARD_TOPIC",
    "STREAM_MESSAGE_TOPIC",
    "STREAM_SPEC_VERSION",
    "TRIGGER_MSGTYPES",
    "build_conversation_key",
    "build_sender_identity_key",
    "compute_callback_sign",
    "encode_external_user_key",
    "is_trigger_message",
    "normalize_message_payload",
    "resolve_gateway_base",
    "stream_user_agent",
    "verify_callback_signature",
]
