"""DingTalk connector adapter tests (integrations.md §3.2 / §3.7 / §5.6, MES-87).

Pure-function layer: HTTP callback signature verification (timestamp+sign,
±3600s official tolerance — never narrower), payload normalization (msgtype
matrix / @-prefix trim / text truncation), and the normalized key algebra
(conversation_key segments + staffId/x=<base64url(senderId)> identity keys,
N-1 official-sample injection + E-1 structural disjointness).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest

from mesh.errors import ValidationError
from mesh.integrations.connectors import SIG_INVALID, SIG_MISSING, SIG_VALID
from mesh.integrations.dingtalk import (
    DEFAULT_GATEWAY_BASE,
    DINGTALK_SIGNATURE_TOLERANCE,
    EXTERNAL_REF_CHARSET,
    STAFF_ID_CHARSET,
    STREAM_MESSAGE_TOPIC,
    build_conversation_key,
    build_sender_identity_key,
    encode_external_user_key,
    is_trigger_message,
    normalize_message_payload,
    resolve_gateway_base,
    verify_callback_signature,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
APP_SECRET = "test-app-secret-0000000000000000000"

# Official documentation sample values (real platform ID shapes).
OFFICIAL_CORP_ID = "dingxxxxsample"
OFFICIAL_CONVERSATION_ID = "cid6EUvB2O8qVF2RYQtHTKEsg=="  # base64-like, carries '='
OFFICIAL_SENDER_ID = "$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9"  # carries ':' '$' '+'
OFFICIAL_STAFF_ID = "014728255240768602"
OFFICIAL_MSG_ID = "msg1BRjQvFhG2RYQtHTKEsg=="


def sign(timestamp_ms: str | int, secret: str = APP_SECRET) -> str:
    """Recompute the DingTalk callback signature (spec §3.2 M1 formula)."""
    material = f"{timestamp_ms}\n{secret}".encode()
    return base64.b64encode(hmac.new(secret.encode(), material, hashlib.sha256).digest()).decode()


def headers_at(offset_seconds: float = 0.0, *, secret: str = APP_SECRET, ts: str | None = None):
    ts_ms = ts if ts is not None else str(int((NOW.timestamp() + offset_seconds) * 1000))
    return {"timestamp": ts_ms, "sign": sign(ts_ms, secret)}


# ---------------------------------------------------------------------------
# Signature verification (M1 executable assertions)
# ---------------------------------------------------------------------------


def test_valid_signature_accepts():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers=headers_at(), now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_VALID
    )


def test_wrong_secret_is_invalid():
    assert (
        verify_callback_signature(
            app_secret="another-secret", headers=headers_at(), now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


def test_missing_sign_header_is_missing():
    ts = str(int(NOW.timestamp() * 1000))
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers={"timestamp": ts}, now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_MISSING
    )


def test_no_headers_at_all_is_missing():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers={}, now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_MISSING
    )


def test_missing_timestamp_with_sign_is_invalid():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers={"sign": sign(123)}, now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


def test_unparseable_timestamp_is_invalid():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers={"timestamp": "not-a-number", "sign": sign(1)},
            now=NOW, tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


def test_timestamp_3599s_ago_is_within_window():
    """Boundary INSIDE the official ±3600s tolerance must pass."""
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers=headers_at(-3599), now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_VALID
    )


def test_timestamp_3601s_ago_is_replay_rejected():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers=headers_at(-3601), now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


def test_timestamp_3601s_in_future_is_replay_rejected():
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers=headers_at(3601), now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


def test_official_tolerance_constant_is_never_narrowed():
    """The official ±3600s window is the floor — the adapter must not shrink it."""
    assert DINGTALK_SIGNATURE_TOLERANCE == timedelta(seconds=3600)


def test_signature_covers_only_timestamp_and_secret_not_body():
    """The signed string is exactly ``timestamp + "\\n" + app_secret``; body
    integrity is HTTPS/TLS-guaranteed — verification never reads the body."""
    headers = headers_at()
    # The verifier signature takes no raw_body parameter at all.
    import inspect

    params = inspect.signature(verify_callback_signature).parameters
    assert "raw_body" not in params
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET, headers=headers, now=NOW,
            tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_VALID
    )


def test_constant_time_comparison_primitive_used():
    """Implementation assertion (M1): hmac.compare_digest (code review + test
    that a near-miss signature — one character off — is rejected)."""
    good = headers_at()["sign"]
    flipped = ("A" if good[0] != "A" else "B") + good[1:]
    assert (
        verify_callback_signature(
            app_secret=APP_SECRET,
            headers={"timestamp": headers_at()["timestamp"], "sign": flipped},
            now=NOW, tolerance=DINGTALK_SIGNATURE_TOLERANCE,
        )
        == SIG_INVALID
    )


# ---------------------------------------------------------------------------
# External user key encoding (N-1 / E-1)
# ---------------------------------------------------------------------------


def test_staff_id_passes_through_verbatim():
    assert encode_external_user_key(OFFICIAL_STAFF_ID, OFFICIAL_SENDER_ID) == OFFICIAL_STAFF_ID


def test_external_contact_encoded_as_base64url_without_colon():
    key = encode_external_user_key(None, OFFICIAL_SENDER_ID)
    assert key.startswith("x=")
    assert ":" not in key
    # Round-trips to the raw senderId bytes (mapping held opaquely server-side).
    encoded = key[2:]
    padded = encoded + "=" * (-len(encoded) % 4)
    assert base64.urlsafe_b64decode(padded).decode() == OFFICIAL_SENDER_ID


def test_encoded_key_second_char_is_always_equals():
    """E-1 structural disjointness: the encoded key's 2nd char is '='…"""
    assert encode_external_user_key(None, OFFICIAL_SENDER_ID)[1] == "="


def test_equals_is_outside_staff_id_charset():
    """…and '=' is outside the widest official staffId charset — no legal
    staffId can ever equal an encoded key (charset algebra, T39-15)."""
    assert STAFF_ID_CHARSET.match("x=JEx3Q1BfdjE6") is None
    assert STAFF_ID_CHARSET.match(OFFICIAL_STAFF_ID) is not None
    assert "=" not in STAFF_ID_CHARSET.pattern


def test_distinct_sender_ids_encode_to_distinct_keys():
    other = "$:LWCP_v1:$AAAA+zrv5WZ77xc2v4zsyXfBv1MhAv9"
    assert encode_external_user_key(None, OFFICIAL_SENDER_ID) != encode_external_user_key(
        None, other
    )


def test_staff_id_with_colon_is_rejected():
    """A raw senderId-shaped value presented as staffId would collapse the
    triple separator — the service layer refuses it (invalid_request)."""
    with pytest.raises(ValidationError):
        encode_external_user_key(OFFICIAL_SENDER_ID, None)


def test_no_sender_identity_at_all_yields_empty_key():
    assert encode_external_user_key(None, None) == ""
    assert encode_external_user_key("", "") == ""


# ---------------------------------------------------------------------------
# conversation_key / sender_identity_key construction + segment validation
# ---------------------------------------------------------------------------


def test_official_sample_conversation_key_stores_with_equals():
    key = build_conversation_key("dingtalk", OFFICIAL_CORP_ID, OFFICIAL_CONVERSATION_ID)
    assert key == f"dingtalk:{OFFICIAL_CORP_ID}:{OFFICIAL_CONVERSATION_ID}"
    assert key.split(":")[2] == OFFICIAL_CONVERSATION_ID  # third segment intact


def test_external_ref_with_colon_is_rejected():
    with pytest.raises(ValidationError):
        build_conversation_key("dingtalk", OFFICIAL_CORP_ID, "cid:injection")


def test_external_ref_outside_charset_is_rejected():
    with pytest.raises(ValidationError):
        build_conversation_key("dingtalk", OFFICIAL_CORP_ID, "cid$bad")


def test_external_ref_charset_admits_official_shapes():
    assert EXTERNAL_REF_CHARSET.match(OFFICIAL_CONVERSATION_ID)
    assert EXTERNAL_REF_CHARSET.match("cidABCdef012+/==")


def test_dingtalk_tenant_key_must_look_like_corp_id():
    with pytest.raises(ValidationError):
        build_conversation_key("dingtalk", "not-a-corp", OFFICIAL_CONVERSATION_ID)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValidationError):
        build_conversation_key("telegram", OFFICIAL_CORP_ID, "x")


def test_control_characters_rejected_in_segments():
    with pytest.raises(ValidationError):
        build_conversation_key("dingtalk", OFFICIAL_CORP_ID, "cid\x00abc")


def test_sender_identity_key_third_segment_never_has_colon():
    encoded = encode_external_user_key(None, OFFICIAL_SENDER_ID)
    key = build_sender_identity_key("dingtalk", OFFICIAL_CORP_ID, encoded)
    assert key == f"dingtalk:{OFFICIAL_CORP_ID}:{encoded}"
    assert key.count(":") == 2  # exactly the two separators


def test_sender_identity_key_rejects_raw_sender_id():
    with pytest.raises(ValidationError):
        build_sender_identity_key("dingtalk", OFFICIAL_CORP_ID, OFFICIAL_SENDER_ID)


def test_non_dingtalk_providers_use_generic_segment_rules():
    # Slack/feishu tenant/ref shapes pass the generic no-colon/no-control rule.
    assert build_conversation_key("slack", "T_TEST", "C_ONCALL") == "slack:T_TEST:C_ONCALL"
    assert (
        build_conversation_key("feishu", "tenant_key_x", "oc_chat")
        == "feishu:tenant_key_x:oc_chat"
    )


# ---------------------------------------------------------------------------
# Payload normalization + msgtype matrix
# ---------------------------------------------------------------------------


def _group_text_payload(**overrides):
    payload = {
        "msgId": OFFICIAL_MSG_ID,
        "conversationId": OFFICIAL_CONVERSATION_ID,
        "conversationType": "2",
        "chatbotCorpId": OFFICIAL_CORP_ID,
        "robotCode": "dingrobotxxxx",
        "msgtype": "text",
        "senderStaffId": OFFICIAL_STAFF_ID,
        "senderId": OFFICIAL_SENDER_ID,
        "senderNick": "值班人",
        "isInAtList": True,
        "text": {"content": " 帮我查下昨晚的报警"},
        "sessionWebhookExpiredTime": 1753890000000,
    }
    payload.update(overrides)
    return payload


def test_group_text_message_normalizes_and_trims_at_prefix_space():
    envelope = normalize_message_payload(_group_text_payload(), max_chars=4000, channel="http")
    assert envelope.provider == "dingtalk"
    assert envelope.provider_tenant_key == OFFICIAL_CORP_ID
    assert envelope.external_event_id == OFFICIAL_MSG_ID
    assert envelope.external_ref == OFFICIAL_CONVERSATION_ID
    assert envelope.conversation_type == "2"
    assert envelope.sender_key == OFFICIAL_STAFF_ID
    assert envelope.text == "帮我查下昨晚的报警"  # leading @-bot space trimmed
    assert envelope.msgtype == "text"
    assert envelope.truncated is False
    assert envelope.channel == "http"
    assert envelope.bot_mentioned is True
    assert envelope.is_direct_message is False
    assert is_trigger_message(envelope) is True
    # Raw payload preserved for the audit ledger.
    assert envelope.raw_payload["msgId"] == OFFICIAL_MSG_ID


def test_direct_message_flag():
    envelope = normalize_message_payload(
        _group_text_payload(conversationType="1", isInAtList=False), max_chars=4000,
        channel="http",
    )
    assert envelope.is_direct_message is True
    assert envelope.conversation_type == "1"


def test_external_contact_sender_key_in_envelope():
    envelope = normalize_message_payload(
        _group_text_payload(senderStaffId=None), max_chars=4000, channel="stream"
    )
    assert envelope.sender_key.startswith("x=")
    assert ":" not in envelope.sender_key
    assert envelope.channel == "stream"


def test_text_truncation_with_audit_flag():
    long_text = "报" * 4001
    envelope = normalize_message_payload(
        _group_text_payload(text={"content": long_text}), max_chars=4000, channel="http"
    )
    assert len(envelope.text) == 4000
    assert envelope.truncated is True


def test_text_at_limit_is_not_truncated():
    envelope = normalize_message_payload(
        _group_text_payload(text={"content": "x" * 4000}), max_chars=4000, channel="http"
    )
    assert envelope.truncated is False


def test_non_text_msgtype_is_not_a_trigger():
    """msgtype matrix (C-1): only text triggers; richText/picture audit-only."""
    for msgtype in ("richText", "picture", "audio", "video", "file"):
        envelope = normalize_message_payload(
            _group_text_payload(msgtype=msgtype, text={}), max_chars=4000, channel="http"
        )
        assert envelope.msgtype == msgtype
        assert is_trigger_message(envelope) is False


def test_group_at_bot_matrix_trigger_types():
    """Group @-bot: platform delivers only text/richText/picture; of those,
    only text is a trigger."""
    assert is_trigger_message(
        normalize_message_payload(_group_text_payload(), max_chars=4000, channel="http")
    )
    for delivered in ("richText", "picture"):
        assert not is_trigger_message(
            normalize_message_payload(
                _group_text_payload(msgtype=delivered, text={}), max_chars=4000, channel="http"
            )
        )


def test_missing_msg_id_is_rejected():
    with pytest.raises(ValidationError):
        normalize_message_payload(_group_text_payload(msgId=None), max_chars=4000, channel="http")


def test_missing_conversation_id_is_rejected():
    with pytest.raises(ValidationError):
        normalize_message_payload(
            _group_text_payload(conversationId=None), max_chars=4000, channel="http"
        )


def test_is_in_at_list_string_true_is_accepted():
    envelope = normalize_message_payload(
        _group_text_payload(isInAtList="true"), max_chars=4000, channel="http"
    )
    assert envelope.bot_mentioned is True


# ---------------------------------------------------------------------------
# Gateway base resolution (M2 test-injection door)
# ---------------------------------------------------------------------------


def test_default_gateway_base_is_official():
    base, is_non_default = resolve_gateway_base(None)
    assert base == DEFAULT_GATEWAY_BASE
    assert is_non_default is False


def test_explicit_default_value_is_not_non_default():
    _, is_non_default = resolve_gateway_base("https://api.dingtalk.com/")
    assert is_non_default is False


def test_non_default_gateway_base_flagged():
    base, is_non_default = resolve_gateway_base("https://127.0.0.1:9443")
    assert base == "https://127.0.0.1:9443"
    assert is_non_default is True


def test_stream_message_topic_constant():
    assert STREAM_MESSAGE_TOPIC == "/v1.0/im/bot/messages/get"
