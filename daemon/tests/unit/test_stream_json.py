"""Strict stream-json parser tests (runtime-executor.md §3.9).

Fixed-schema records in, unified events out. Unknown/malformed/oversize
records are dropped with a diagnostic reason — never raised, never relayed.
Thinking blocks never become events (§3.7).
"""

import json

import pytest

from mesh_runtime.providers.base import (
    FinalResult,
    SessionStarted,
    TextDelta,
    ToolCompleted,
    ToolRequested,
    UsageObserved,
)
from mesh_runtime.stream_json import (
    MAX_LINE_BYTES,
    ParsedRecord,
    format_cost_usd,
    parse_stream_record,
)


def _rec(line: str) -> ParsedRecord:
    return parse_stream_record(line)


class TestSessionInit:
    def test_init_record_yields_session_started(self):
        line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "sess-123",
                "model": "pinned-model",
                "tools": ["Read"],
            }
        )
        rec = _rec(line)
        assert rec.dropped is None
        assert rec.events == (SessionStarted(session_id="sess-123", model="pinned-model"),)

    def test_init_without_session_id_is_malformed(self):
        rec = _rec(json.dumps({"type": "system", "subtype": "init", "model": "m"}))
        assert rec.dropped == "malformed"
        assert rec.events == ()

    def test_non_init_system_records_are_dropped_unknown(self):
        # thinking_tokens / anything else under type=system never relays (§3.7).
        rec = _rec(
            json.dumps({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 7})
        )
        assert rec.dropped == "unknown"
        assert rec.events == ()


class TestAssistantRecords:
    def test_text_block_yields_text_delta(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "session_id": "s",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hello world"}],
                        "usage": {"input_tokens": 10, "output_tokens": 3},
                    },
                }
            )
        )
        assert rec.dropped is None
        kinds = [type(e) for e in rec.events]
        assert TextDelta in kinds
        assert UsageObserved in kinds
        text = next(e for e in rec.events if isinstance(e, TextDelta))
        assert text.text == "hello world"
        usage = next(e for e in rec.events if isinstance(e, UsageObserved))
        assert usage.input_tokens == 10
        assert usage.output_tokens == 3
        assert usage.total_tokens == 13

    def test_thinking_block_never_emits(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "secret reasoning"},
                            {"type": "text", "text": "visible"},
                        ]
                    },
                }
            )
        )
        assert rec.dropped is None
        texts = [e.text for e in rec.events if isinstance(e, TextDelta)]
        assert texts == ["visible"]
        assert all("secret reasoning" not in repr(e) for e in rec.events)
        assert rec.thinking_skipped == 1

    def test_tool_use_yields_tool_requested(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "tool_use", "id": "call-1", "name": "Read", "input": {}}
                        ]
                    },
                }
            )
        )
        assert rec.events == (ToolRequested(name="Read", call_id="call-1"),)

    def test_usage_with_cache_fields(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [],
                        "usage": {
                            "input_tokens": 5,
                            "output_tokens": 2,
                            "cache_read_input_tokens": 100,
                            "cache_creation_input_tokens": 50,
                        },
                    },
                }
            )
        )
        usage = next(e for e in rec.events if isinstance(e, UsageObserved))
        assert usage.cache_read_tokens == 100
        assert usage.cache_creation_tokens == 50

    def test_negative_usage_is_malformed(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [], "usage": {"input_tokens": -1, "output_tokens": 0}},
                }
            )
        )
        assert rec.dropped == "malformed"

    def test_content_not_a_list_is_malformed(self):
        rec = _rec(json.dumps({"type": "assistant", "message": {"content": "plain string"}}))
        assert rec.dropped == "malformed"

    def test_unknown_block_type_skipped_not_fatal(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "server_tool", "name": "web_search"},
                            {"type": "text", "text": "ok"},
                        ]
                    },
                }
            )
        )
        assert rec.dropped is None
        assert [type(e) for e in rec.events] == [TextDelta]

    def test_too_many_content_blocks_is_oversize(self):
        blocks = [{"type": "text", "text": "x"} for _ in range(10_000)]
        rec = _rec(json.dumps({"type": "assistant", "message": {"content": blocks}}))
        assert rec.dropped == "oversize"


class TestUserToolResult:
    def test_tool_result_yields_tool_completed(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "tool_result", "tool_use_id": "call-1", "content": "file body"}
                        ]
                    },
                }
            )
        )
        assert rec.events == (ToolCompleted(call_id="call-1", outcome="ok"),)

    def test_tool_result_error_flag(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "call-2",
                                "is_error": True,
                                "content": "boom",
                            }
                        ]
                    },
                }
            )
        )
        assert rec.events == (ToolCompleted(call_id="call-2", outcome="error"),)

    def test_plain_user_text_record_dropped(self):
        rec = _rec(json.dumps({"type": "user", "message": {"content": "echo replay"}}))
        assert rec.dropped == "unknown"
        assert rec.events == ()


class TestResultRecord:
    def test_success_result_yields_usage_and_final(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 3,
                    "total_cost_usd": 0.24756,
                    "session_id": "sess-123",
                    "result": "the answer",
                    "usage": {"input_tokens": 100, "output_tokens": 23},
                }
            )
        )
        assert rec.dropped is None
        assert len(rec.events) == 2
        usage, final = rec.events
        assert isinstance(usage, UsageObserved)
        assert usage.turns == 3
        assert usage.cost_usd == "0.247560"
        assert isinstance(final, FinalResult)
        assert final.exit_code == 0
        assert final.termination == "completed"
        assert final.summary == "the answer"

    def test_success_result_uses_model_usage_when_aggregate_fields_are_zero(self):
        """Claude Code 2.1 emits authoritative per-model camelCase totals.

        Its legacy top-level ``usage`` and ``total_cost_usd`` fields can remain
        zero, so the pinned-provider parser must aggregate ``modelUsage``
        without exposing model-key metadata to the unified event stream.
        """
        rec = _rec(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 1,
                    "total_cost_usd": 0,
                    "result": "the answer",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "modelUsage": {
                        "model-a": {
                            "inputTokens": 10,
                            "outputTokens": 2,
                            "cacheReadInputTokens": 3,
                            "cacheCreationInputTokens": 4,
                            "costUSD": 0.012345,
                        },
                        "model-b": {
                            "inputTokens": 5,
                            "outputTokens": 1,
                            "cacheReadInputTokens": 2,
                            "cacheCreationInputTokens": 0,
                            "costUSD": 0.003,
                        },
                    },
                }
            )
        )
        assert rec.dropped is None
        usage = rec.events[0]
        assert usage == UsageObserved(
            input_tokens=15,
            output_tokens=3,
            cache_read_tokens=5,
            cache_creation_tokens=4,
            cost_usd="0.015345",
            turns=1,
            terminal=True,
        )

    @pytest.mark.parametrize(
        "bad_usage",
        [
            [],
            {"model": {"inputTokens": -1}},
            {"model": {"inputTokens": "1"}},
            {"model": {"costUSD": "not-a-decimal"}},
        ],
    )
    def test_zero_aggregate_with_malformed_model_usage_is_rejected(self, bad_usage):
        rec = _rec(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 1,
                    "total_cost_usd": 0,
                    "result": "x",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "modelUsage": bad_usage,
                }
            )
        )
        assert rec.dropped == "malformed"

    def test_error_result_subtype_maps_to_failed(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "error_max_turns",
                    "num_turns": 9,
                    "total_cost_usd": 0.1,
                    "result": "",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )
        )
        final = rec.events[-1]
        assert isinstance(final, FinalResult)
        assert final.exit_code == 1
        assert final.termination == "failed"

    def test_missing_result_field_is_malformed(self):
        rec = _rec(
            json.dumps(
                {"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.0}
            )
        )
        assert rec.dropped == "malformed"

    def test_negative_cost_is_malformed(self):
        rec = _rec(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 1,
                    "total_cost_usd": -0.5,
                    "result": "x",
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                }
            )
        )
        assert rec.dropped == "malformed"


class TestDropSemantics:
    def test_unknown_top_level_type_dropped(self):
        rec = _rec(json.dumps({"type": "stream_event", "event": {}}))
        assert rec.dropped == "unknown"
        assert rec.raw_type == "stream_event"

    def test_missing_type_dropped_malformed(self):
        rec = _rec(json.dumps({"no_type": True}))
        assert rec.dropped == "malformed"

    def test_non_json_dropped_malformed(self):
        rec = _rec("this is not json {")
        assert rec.dropped == "malformed"
        assert rec.events == ()

    def test_oversize_line_dropped_without_parsing(self):
        line = "x" * (MAX_LINE_BYTES + 1)
        rec = _rec(line)
        assert rec.dropped == "oversize"
        assert rec.events == ()

    def test_non_string_input_dropped_malformed(self):
        rec = parse_stream_record(None)  # type: ignore[arg-type]
        assert rec.dropped == "malformed"


class TestCostFormatting:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, "0.000000"),
            (0.24756, "0.247560"),
            (1.5, "1.500000"),
            ("2.25", "2.250000"),
            (0.0000004, "0.000000"),
        ],
    )
    def test_decimal_string_six_places(self, value, expected):
        assert format_cost_usd(value) == expected

    def test_negative_or_invalid_returns_none(self):
        assert format_cost_usd(-1) is None
        assert format_cost_usd("abc") is None
        assert format_cost_usd(None) is None


class TestEdgeBranches:
    def test_assistant_message_not_dict(self):
        assert _rec(json.dumps({"type": "assistant", "message": "x"})).dropped == "malformed"

    def test_assistant_non_dict_block_skipped(self):
        rec = _rec(
            json.dumps(
                {"type": "assistant", "message": {"content": ["junk", {"type": "text", "text": "ok"}]}}
            )
        )
        assert [e.text for e in rec.events if isinstance(e, TextDelta)] == ["ok"]

    def test_assistant_oversize_text_block(self):
        big = "y" * 300_000
        rec = _rec(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": big}]}})
        )
        assert rec.dropped == "oversize"

    def test_assistant_text_non_string_ignored(self):
        rec = _rec(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": 5}]}})
        )
        assert rec.events == ()

    def test_tool_use_missing_id_ignored(self):
        rec = _rec(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}})
        )
        assert rec.events == ()

    def test_user_message_not_dict(self):
        assert _rec(json.dumps({"type": "user", "message": 7})).dropped == "malformed"

    def test_user_oversize_content_list(self):
        blocks = [{"type": "tool_result", "tool_use_id": "c"} for _ in range(5000)]
        assert _rec(json.dumps({"type": "user", "message": {"content": blocks}})).dropped == "oversize"

    def test_result_bad_num_turns(self):
        for turns in (True, -1, "2"):
            rec = _rec(
                json.dumps(
                    {"type": "result", "subtype": "success", "num_turns": turns,
                     "total_cost_usd": 0.1, "result": "x", "usage": {}}
                )
            )
            assert rec.dropped == "malformed"

    def test_result_missing_subtype(self):
        rec = _rec(json.dumps({"type": "result", "result": "x", "total_cost_usd": 0.1}))
        assert rec.dropped == "malformed"

    def test_result_usage_non_dict_malformed(self):
        rec = _rec(
            json.dumps(
                {"type": "result", "subtype": "success", "num_turns": 1,
                 "total_cost_usd": 0.1, "result": "x", "usage": [1, 2]}
            )
        )
        assert rec.dropped == "malformed"

    def test_assistant_usage_bool_malformed(self):
        rec = _rec(
            json.dumps(
                {"type": "assistant", "message": {"content": [],
                 "usage": {"input_tokens": True, "output_tokens": 0}}}
            )
        )
        assert rec.dropped == "malformed"

    def test_usage_non_dict_malformed(self):
        rec = _rec(json.dumps({"type": "assistant", "message": {"content": [], "usage": "nope"}}))
        assert rec.dropped == "malformed"

    def test_format_cost_infinity_rejected(self):
        assert format_cost_usd(float("inf")) is None
        assert format_cost_usd(float("nan")) is None
