"""queue_keys unit tests — §2.10 key grammar, §3.10 encoding (T39-12/T39-15)."""

from __future__ import annotations

import base64

import pytest

from mesh.errors import ValidationError
from mesh.integrations.queue_keys import (
    EXTERNAL_REF_RE,
    STAFF_ID_RE,
    build_conversation_key,
    build_sender_identity_key,
    encode_external_user_key,
    sanitize_excerpt,
    truncate_inbound_text,
    validate_conversation_key,
    validate_sender_identity_key,
)

pytestmark = pytest.mark.unit

# Official sample values (schema_r2_validation.sql T39-12).
OFFICIAL_CONVERSATION_ID = "cid6EUvB2O8qVF2RYQtHTKEsg=="  # base64-ish, has '='
OFFICIAL_SENDER_ID = "$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9"  # has ':' '$' '+'
OFFICIAL_STAFF_ID = "014728255240768602"


class TestEncodeExternalUserKey:
    def test_staff_id_passthrough(self):
        assert (
            encode_external_user_key(staff_id=OFFICIAL_STAFF_ID, sender_id=None)
            == OFFICIAL_STAFF_ID
        )

    def test_staff_id_wins_over_sender_id(self):
        assert (
            encode_external_user_key(staff_id=OFFICIAL_STAFF_ID, sender_id=OFFICIAL_SENDER_ID)
            == OFFICIAL_STAFF_ID
        )

    def test_external_contact_encoded_without_colon(self):
        encoded = encode_external_user_key(staff_id=None, sender_id=OFFICIAL_SENDER_ID)
        assert encoded.startswith("x=")
        assert encoded[1] == "="  # E-1: 2nd char always '='
        assert ":" not in encoded  # colon stripped by base64url re-encoding
        # round-trip: the payload decodes back to the raw senderId
        raw = encoded[2:]
        padded = raw + "=" * (-len(raw) % 4)
        assert base64.urlsafe_b64decode(padded).decode() == OFFICIAL_SENDER_ID

    def test_distinct_sender_ids_encode_distinctly(self):
        a = encode_external_user_key(staff_id=None, sender_id=OFFICIAL_SENDER_ID)
        other = OFFICIAL_SENDER_ID[:-1] + ("N" if OFFICIAL_SENDER_ID[-1] != "N" else "M")
        b = encode_external_user_key(staff_id=None, sender_id=other)
        assert a != b

    def test_staff_id_charset_guard_rejects_invalid(self):
        with pytest.raises(ValidationError):
            encode_external_user_key(staff_id="bad staff:id", sender_id=None)

    def test_both_missing_rejected(self):
        with pytest.raises(ValidationError):
            encode_external_user_key(staff_id=None, sender_id=None)

    def test_empty_strings_treated_as_missing(self):
        with pytest.raises(ValidationError):
            encode_external_user_key(staff_id="", sender_id="")


class TestKeySpaceDisjointE1:
    """T39-15: staffId-charset keys and x=<base64url> keys cannot collide."""

    def test_encoded_key_not_in_staffid_charset(self):
        encoded = encode_external_user_key(staff_id=None, sender_id=OFFICIAL_SENDER_ID)
        import re

        assert re.fullmatch(STAFF_ID_RE, encoded) is None
        assert re.fullmatch(STAFF_ID_RE, OFFICIAL_STAFF_ID) is not None

    def test_encoded_prefix_as_staff_id_rejected(self):
        # T39-15c: feeding an encoded key where a staffId is expected fails
        # the charset guard (impersonation of an external contact impossible).
        encoded = encode_external_user_key(staff_id=None, sender_id=OFFICIAL_SENDER_ID)
        with pytest.raises(ValidationError):
            encode_external_user_key(staff_id=encoded, sender_id=None)


class TestConversationKey:
    def test_build_and_parse_round_trip_with_official_cid(self):
        key = build_conversation_key("dingtalk", "dingxxxxsample", OFFICIAL_CONVERSATION_ID)
        assert key == f"dingtalk:dingxxxxsample:{OFFICIAL_CONVERSATION_ID}"
        provider, tenant, ref = validate_conversation_key(key)
        assert (provider, tenant, ref) == ("dingtalk", "dingxxxxsample", OFFICIAL_CONVERSATION_ID)

    def test_dingtalk_tenant_pattern(self):
        build_conversation_key("dingtalk", "ding123abc", "cidOK==")
        with pytest.raises(ValidationError):
            build_conversation_key("dingtalk", "acme:corp", "cidOK")

    def test_non_dingtalk_lenient_tenant(self):
        key = build_conversation_key("slack", "T0xxx", "C0yyy")
        assert validate_conversation_key(key) == ("slack", "T0xxx", "C0yyy")

    def test_external_ref_colon_rejected(self):
        with pytest.raises(ValidationError):
            build_conversation_key("dingtalk", "ding123", "bad:ref")

    def test_external_ref_charset(self):
        import re

        assert re.fullmatch(EXTERNAL_REF_RE, OFFICIAL_CONVERSATION_ID) is not None
        assert re.fullmatch(EXTERNAL_REF_RE, "has:colon") is None

    def test_unknown_provider_rejected(self):
        with pytest.raises(ValidationError):
            build_conversation_key("telegram", "t123", "ref")

    def test_validate_rejects_fourth_segment(self):
        # ':' in the ref would split into an ambiguous 4th segment — the tail
        # lands with the colon and fails the charset guard.
        with pytest.raises(ValidationError):
            validate_conversation_key("dingtalk:ding123:cid:x")

    def test_validate_rejects_two_segments(self):
        with pytest.raises(ValidationError):
            validate_conversation_key("dingtalk:ding123")

    def test_validate_rejects_empty_segment(self):
        with pytest.raises(ValidationError):
            validate_conversation_key("dingtalk::cid")

    def test_validate_rejects_control_chars(self):
        with pytest.raises(ValidationError):
            validate_conversation_key("dingtalk:ding123:cid\x01x")


class TestSenderIdentityKey:
    def test_build_and_parse_staff(self):
        key = build_sender_identity_key("dingtalk", "dingxxxxsample", OFFICIAL_STAFF_ID)
        assert validate_sender_identity_key(key) == (
            "dingtalk",
            "dingxxxxsample",
            OFFICIAL_STAFF_ID,
        )

    def test_build_and_parse_encoded_contact(self):
        encoded = encode_external_user_key(staff_id=None, sender_id=OFFICIAL_SENDER_ID)
        key = build_sender_identity_key("dingtalk", "dingxxxxsample", encoded)
        provider, tenant, user_key = validate_sender_identity_key(key)
        assert user_key == encoded
        assert ":" not in user_key

    def test_raw_sender_id_with_colon_rejected(self):
        # N-1 negative: taking senderId verbatim would collapse the triple.
        with pytest.raises(ValidationError):
            build_sender_identity_key("dingtalk", "ding123", OFFICIAL_SENDER_ID)


class TestTextHygiene:
    def test_excerpt_strips_controls_and_zero_width(self):
        text = "hello​ world‍!\nsecond"
        assert sanitize_excerpt(text) == "hello world!second"

    def test_excerpt_truncates_to_limit(self):
        out = sanitize_excerpt("a" * 500)
        assert len(out) == 120

    def test_excerpt_custom_limit(self):
        assert sanitize_excerpt("a" * 300, limit=200) == "a" * 200

    def test_truncate_inbound_text_flag(self):
        assert truncate_inbound_text("short", 4000) == ("short", False)
        text, truncated = truncate_inbound_text("a" * 5000, 4000)
        assert truncated is True
        assert len(text) == 4000
