"""IM outbound semantic layer tests (integrations.md §3.3 / §3.10)."""

from __future__ import annotations

import base64
import hashlib
import random
import string
import uuid

import pytest

from mesh.integrations.im_outbound import (
    DEFAULT_CHUNK_MAX_BYTES,
    chunk_idempotency_key,
    encode_external_contact_key,
    is_card_notification,
    is_external_contact_key,
    is_valid_staff_id_key,
    normalize_dingtalk_user_key,
    should_push_notification,
    split_markdown_chunks,
    validate_identity_segment,
)

# Official sample values from DingTalk documentation (real IDs, §5.6 N-1).
OFFICIAL_CONVERSATION_ID = "cid6EUvB2O8qVF2RYQtHTKEsg=="
OFFICIAL_SENDER_ID = "$:LWCP_v1:$6GYsn+zrv5WZ77xc2v4zsyXfBv1MhAv9"
OFFICIAL_STAFF_ID = "014728255240768602"


# ---------------------------------------------------------------------------
# External user-key encoding (§3.10 / §5.6 N-1 / E-1)
# ---------------------------------------------------------------------------


def test_staff_id_passthrough():
    assert (
        normalize_dingtalk_user_key(sender_staff_id=OFFICIAL_STAFF_ID, sender_id=OFFICIAL_SENDER_ID)
        == OFFICIAL_STAFF_ID
    )


def test_external_contact_encoding_official_sample():
    key = encode_external_contact_key(OFFICIAL_SENDER_ID)
    assert key.startswith("x=")
    # no separators survive the encoding
    assert ":" not in key
    assert "$" not in key
    assert "+" not in key
    # round-trip: the base64url payload decodes to the raw senderId
    payload = key[2:]
    padding = "=" * (-len(payload) % 4)
    assert base64.urlsafe_b64decode(payload + padding).decode() == OFFICIAL_SENDER_ID


def test_distinct_sender_ids_yield_distinct_keys():
    # Same encoded prefix substring, different tails → keys must differ.
    a = encode_external_contact_key("$:LWCP_v1:$AAAA")
    b = encode_external_contact_key("$:LWCP_v1:$AAAB")
    assert a != b


def test_key_spaces_structurally_disjoint():
    """E-1 — charset algebra, not documentation assumptions."""
    encoded = encode_external_contact_key(OFFICIAL_SENDER_ID)
    assert encoded[1] == "="  # 2nd char is always '='
    staff_charset = set(string.ascii_letters + string.digits + "._-")
    assert "=" not in staff_charset
    # 1000 random widest-caliber staffIds can never equal an encoded key
    rng = random.Random(42)
    alphabet = string.ascii_letters + string.digits + "._-"
    for _ in range(1000):
        candidate = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        assert not is_external_contact_key(candidate)
        assert is_valid_staff_id_key(candidate)


def test_encoded_key_is_not_a_valid_staff_id():
    """§5.6 attack-chain negative — an x= key presented as staffId is
    refused by the charset guard (link flow)."""
    encoded = encode_external_contact_key(OFFICIAL_SENDER_ID)
    assert not is_valid_staff_id_key(encoded)


def test_normalize_without_anything_is_empty():
    assert normalize_dingtalk_user_key(sender_staff_id=None, sender_id=None) == ""
    assert normalize_dingtalk_user_key(sender_staff_id="  ", sender_id=None) == ""


def test_encode_requires_sender_id():
    with pytest.raises(ValueError):
        encode_external_contact_key("")


def test_identity_segment_validation():
    validate_identity_segment(OFFICIAL_STAFF_ID)
    validate_identity_segment(OFFICIAL_CONVERSATION_ID)  # '=' is legal, ':' is not
    with pytest.raises(ValueError):
        validate_identity_segment(OFFICIAL_SENDER_ID)  # raw senderId has ':'
    with pytest.raises(ValueError):
        validate_identity_segment("has\ncontrol")
    with pytest.raises(ValueError):
        validate_identity_segment("")


# ---------------------------------------------------------------------------
# Markdown chunking (§3.10 — ≤15000 bytes per message)
# ---------------------------------------------------------------------------


def test_empty_text_no_chunks():
    assert split_markdown_chunks("") == []


def test_under_limit_single_chunk():
    text = "short result"
    assert split_markdown_chunks(text) == [text]


def test_exactly_at_limit_single_chunk():
    text = "a" * DEFAULT_CHUNK_MAX_BYTES
    assert split_markdown_chunks(text) == [text]


def test_every_chunk_under_max_bytes():
    rng = random.Random(7)
    # ~80KB of mixed ASCII + CJK + code fences
    parts = []
    for i in range(400):
        parts.append(f"## 段落 {i}\n")
        parts.append("中文内容" * rng.randint(5, 30))
        parts.append("\n\n```python\nprint('x' * 200)\n```\n\n")
        parts.append("ascii words " * rng.randint(10, 60))
        parts.append("\n\n")
    text = "".join(parts)
    assert len(text.encode()) > 80_000
    chunks = split_markdown_chunks(text)
    assert len(chunks) > 5
    for chunk in chunks:
        assert len(chunk.encode("utf-8")) <= DEFAULT_CHUNK_MAX_BYTES
    # content preserved (modulo stripped blank-line runs at cut points)
    rejoined = "".join(chunks)
    assert rejoined.replace("\n", "") == text.replace("\n", "")


def test_prefers_paragraph_boundary():
    para = "x" * 8000
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = split_markdown_chunks(text, max_bytes=16100)
    assert len(chunks) == 2
    # the cut lands right after a paragraph separator, never mid-paragraph
    assert chunks[0] == f"{para}\n\n{para}\n\n"
    assert chunks[1] == para


def test_falls_back_to_line_boundary():
    lines = ["line-%04d" % i for i in range(3000)]  # no blank lines
    text = "\n".join(lines)
    chunks = split_markdown_chunks(text, max_bytes=10000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.encode()) <= 10000
    # line-boundary cuts never split a line mid-content
    assert "".join(chunks) == text
    for chunk in chunks[:-1]:
        assert chunk.endswith("\n")


def test_utf8_safe_hard_cut_without_boundaries():
    text = "中" * 10000  # 3 bytes/char, no whitespace at all
    chunks = split_markdown_chunks(text, max_bytes=9000)
    assert len(chunks) > 1
    for chunk in chunks:
        encoded = chunk.encode("utf-8")
        assert len(encoded) <= 9000
        encoded.decode("utf-8")  # no half-character truncation
    assert "".join(chunks) == text


def test_small_max_bytes_still_terminates():
    text = "abcd" * 100
    chunks = split_markdown_chunks(text, max_bytes=10)
    assert all(len(c.encode()) <= 10 for c in chunks)
    assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# Chunk idempotency keys + verbosity (§6.5 / §3.3)
# ---------------------------------------------------------------------------


def test_chunk_idempotency_key_exact_value():
    notification_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    expected = hashlib.sha256(f"{notification_id}|chunk|2".encode()).hexdigest()
    assert chunk_idempotency_key(notification_id, 2) == expected


def test_chunk_idempotency_keys_unique_per_index():
    nid = uuid.uuid4()
    keys = {chunk_idempotency_key(nid, i) for i in range(5)}
    assert len(keys) == 5


def test_verbosity_final_only_pushes_finals_not_progress():
    assert should_push_notification(notification_type="execution_finished", verbosity="final_only")
    assert should_push_notification(notification_type="review_requested", verbosity="final_only")
    assert should_push_notification(notification_type="comment_created", verbosity="final_only")
    assert not should_push_notification(notification_type="assigned", verbosity="final_only")
    assert not should_push_notification(notification_type="status_changed", verbosity="final_only")


def test_verbosity_progress_pushes_everything():
    assert should_push_notification(notification_type="assigned", verbosity="progress")
    assert should_push_notification(notification_type="execution_finished", verbosity="progress")


def test_card_rendering_only_for_approval_requests():
    assert is_card_notification("review_requested")
    assert not is_card_notification("execution_finished")
    assert not is_card_notification("comment_created")
