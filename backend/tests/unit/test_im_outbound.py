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
    lines = [f"line-{i:04d}" for i in range(3000)]  # no blank lines
    text = "\n".join(lines)
    chunks = split_markdown_chunks(text, max_bytes=10000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.encode()) <= 10000
    # line-boundary cuts never split a line mid-content
    assert "".join(chunks) == text
    for chunk in chunks[:-1]:
        assert chunk.endswith("\n")


def test_prefers_line_boundary_outside_fenced_code_block():
    prefix = "p" * 55 + "\n"
    text = (
        prefix
        + "```python\n"
        + "first = 1\n\n"
        + "second = 2\n"
        + "third = 3\n"
        + "```\n\n"
        + "tail"
    )

    chunks = split_markdown_chunks(text, max_bytes=90)

    # The later paragraph boundary is inside the code fence. The earlier
    # plain line boundary must win so the first chunk does not cut code.
    assert chunks[0] == prefix
    assert chunks[1].startswith("```python\n")


def test_uses_fenced_line_boundary_when_no_outside_boundary_fits():
    first_code_line = "x" * 40 + "\n"
    text = "```python\n" + first_code_line + "y" * 40 + "\n```\ntail"

    chunks = split_markdown_chunks(text, max_bytes=70)

    assert chunks[0] == "```python\n" + first_code_line
    assert "".join(chunks) == text


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


# ---------------------------------------------------------------------------
# DingTalkIMAdapter — channel selection / degradation / truncation
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402

from mesh.integrations.dingtalk_api import DingTalkClient, DingTalkTokenManager  # noqa: E402
from mesh.integrations.im_outbound import (  # noqa: E402
    CONVERSATION_DIRECT,
    CONVERSATION_GROUP,
    REASON_INVALID_CREDENTIALS,
    REASON_NO_STAFF_ID,
    REASON_RATE_LIMITED,
    REASON_UPSTREAM_ERROR,
    ConversationTarget,
    DingTalkIMAdapter,
    sanitize_no_mentions,
    truncate_to_bytes,
)
from tests.unit.integrations_dingtalk_support import (  # noqa: E402
    ScriptedDingTalkTransport,
    make_client,
)


def _group_target(**overrides) -> ConversationTarget:
    base = {
        "workspace_id": uuid.uuid4(),
        "integration_id": uuid.uuid4(),
        "provider_tenant_key": "dingcorp",
        "external_ref": OFFICIAL_CONVERSATION_ID,
        "conversation_type": CONVERSATION_GROUP,
        "sender_key": "",
        "binding_id": uuid.uuid4(),
    }
    return ConversationTarget(**{**base, **overrides})


def _direct_target(sender_key: str = OFFICIAL_STAFF_ID, **overrides) -> ConversationTarget:
    return _group_target(
        conversation_type=CONVERSATION_DIRECT, sender_key=sender_key, **overrides
    )


async def _adapter(redis_client, transport: ScriptedDingTalkTransport, **kwargs) -> DingTalkIMAdapter:
    manager = DingTalkTokenManager(
        redis_client,
        http_client=make_client(transport),
        integration_id=uuid.uuid4(),
        app_key="k",
        app_secret="s",
        jitter=lambda: 0,
    )
    client = DingTalkClient(
        manager, http_client=make_client(transport), robot_code="robot-1"
    )
    return DingTalkIMAdapter(client, **kwargs)


async def test_group_text_uses_group_messages_send(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_group_target(), "✅ 已接收,处理中")
    assert outcome.sent
    (sent,) = transport.group_sends()
    assert sent.body["openConversationId"] == OFFICIAL_CONVERSATION_ID
    assert sent.body["msgKey"] == "sampleText"
    assert _json.loads(sent.body["msgParam"]) == {"content": "✅ 已接收,处理中"}
    assert transport.direct_sends() == []


async def test_direct_text_uses_oto_batch_send(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_direct_target(), "result")
    assert outcome.sent
    (sent,) = transport.direct_sends()
    assert sent.body["userIds"] == [OFFICIAL_STAFF_ID]
    assert transport.group_sends() == []


async def test_direct_external_contact_fails_no_staff_id_without_request(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    external_key = encode_external_contact_key(OFFICIAL_SENDER_ID)
    outcome = await adapter.send_text(_direct_target(sender_key=external_key), "hi")
    assert not outcome.sent
    assert outcome.reason == REASON_NO_STAFF_ID
    assert transport.direct_sends() == []  # no request went out


async def test_direct_missing_sender_key_fails_no_staff_id(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_direct_target(sender_key=""), "hi")
    assert outcome.reason == REASON_NO_STAFF_ID


async def test_rate_limited_outcome_carries_classification(redis_client):
    transport = ScriptedDingTalkTransport(
        send_status=400,
        send_body={"code": "send.too.fast", "flowControlledStaffIdList": ["s1"]},
    )
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_group_target(), "hi")
    assert not outcome.sent
    assert outcome.reason == REASON_RATE_LIMITED
    assert outcome.rate_limit_code == "send.too.fast"
    assert outcome.flow_controlled_staff_ids == ("s1",)


async def test_invalid_credentials_outcome(redis_client):
    """App secret rejected at the REFRESH endpoint → terminal
    invalid_credentials; the business endpoint is never reached."""
    transport = ScriptedDingTalkTransport(
        token_status=400, token_body={"code": "invalidAuthentication"}
    )
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_group_target(), "hi")
    assert outcome.reason == REASON_INVALID_CREDENTIALS
    assert transport.group_sends() == []


async def test_token_invalid_then_refresh_still_failing_is_upstream(redis_client):
    """40014 on the business endpoint → invalidate + refresh once + retry
    once; still failing → upstream_error (NOT invalid_credentials)."""
    transport = ScriptedDingTalkTransport(
        send_queue=[(400, {"code": "40014"}), (400, {"code": "40014"})]
    )
    adapter = await _adapter(redis_client, transport)
    outcome = await adapter.send_text(_group_target(), "hi")
    assert outcome.reason == REASON_UPSTREAM_ERROR
    assert transport.token_calls == 2


def test_sanitize_no_mentions_strips_at_tokens():
    assert sanitize_no_mentions("@张三 结果来了") == " 结果来了"
    assert sanitize_no_mentions("请 @值班 agent 处理") == "请  agent 处理"
    assert sanitize_no_mentions("no mentions here") == "no mentions here"


async def test_outbound_text_never_carries_mentions(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    await adapter.send_text(_group_target(), "@李四 任务完成了")
    (sent,) = transport.group_sends()
    content = _json.loads(sent.body["msgParam"])["content"]
    assert "@" not in content


def test_truncate_to_bytes_utf8_safe():
    text = "中" * 1000
    out = truncate_to_bytes(text, 300)
    assert len(out.encode("utf-8")) <= 300
    assert out.endswith("…")
    assert truncate_to_bytes("short", 300) == "short"


async def test_oversized_single_text_fitted_to_platform_cap(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    await adapter.send_text(_group_target(), "a" * 20000)
    (sent,) = transport.group_sends()
    assert len(sent.body["msgParam"].encode("utf-8")) <= 15000


async def test_send_result_chunks_long_markdown(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport, max_chunks=5)
    markdown = ("## section\n" + "内容" * 3000 + "\n\n") * 16  # ~96KB → more than 5 chunks
    outcomes = await adapter.send_result(
        _group_target(), notification_id=uuid.uuid4(), markdown=markdown
    )
    assert len(outcomes) == 5  # truncated at max_chunks
    assert all(o.sent for o in outcomes)
    sends = transport.group_sends()
    assert len(sends) == 5
    for sent in sends:
        assert len(sent.body["msgParam"].encode()) <= 15000
        assert sent.body["msgKey"] == "sampleMarkdown"


async def test_send_result_truncation_appends_deep_link_to_last_chunk(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport, max_chunks=2)
    markdown = ("para " * 2000 + "\n\n") * 10
    detail_url = "https://mesh.example.com/workspaces/ws/executions/ex1"
    outcomes = await adapter.send_result(
        _group_target(), notification_id=uuid.uuid4(), markdown=markdown, detail_url=detail_url
    )
    assert len(outcomes) == 2
    sends = transport.group_sends()
    last_text = _json.loads(sends[-1].body["msgParam"])["text"]
    first_text = _json.loads(sends[0].body["msgParam"])["text"]
    assert f"完整结果见 Mesh:{detail_url}" in last_text
    assert "完整结果见 Mesh" not in first_text
    # the link survives whole (never cut mid-URL)
    assert last_text.endswith(detail_url)


async def test_send_result_within_budget_no_link_no_suffix(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport, max_chunks=5)
    outcomes = await adapter.send_result(
        _group_target(),
        notification_id=uuid.uuid4(),
        markdown="single short result",
        detail_url="https://x",
    )
    assert len(outcomes) == 1
    (sent,) = transport.group_sends()
    param = _json.loads(sent.body["msgParam"])
    assert param["title"] == "Mesh 执行结果"  # no (i/n) suffix for a single chunk
    assert "完整结果见 Mesh" not in param["text"]


async def test_send_result_terminal_failure_aborts_remaining_chunks(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport, max_chunks=5)
    markdown = ("para " * 2000 + "\n\n") * 16  # 5+ chunks
    # direct chat to an external contact → terminal no_staff_id
    outcomes = await adapter.send_result(
        _direct_target(sender_key=encode_external_contact_key(OFFICIAL_SENDER_ID)),
        notification_id=uuid.uuid4(),
        markdown=markdown,
    )
    assert len(outcomes) == 1  # aborted after the first terminal outcome
    assert outcomes[0].reason == REASON_NO_STAFF_ID
    assert transport.direct_sends() == []


async def test_send_result_empty_markdown_no_sends(redis_client):
    transport = ScriptedDingTalkTransport()
    adapter = await _adapter(redis_client, transport)
    assert await adapter.send_result(_group_target(), notification_id=uuid.uuid4(), markdown="") == []
    assert transport.group_sends() == []
