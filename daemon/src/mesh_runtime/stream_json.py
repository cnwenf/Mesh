"""Strict ``stream-json`` record parser (runtime-executor.md §3.9).

The provider supervisor parses the vendor stream ONE record per line and only
accepts the fixed schemas below — system/init, assistant (text/tool_use/
usage), user tool_result and the terminal result record. Everything else is
dropped with a diagnostic reason (unknown / malformed / oversize); the raw
provider stream is never persisted, and ``thinking`` blocks never become
events (§3.7 — thinking never enters logs, results or resume context).

The parser is TOTAL: it never raises on vendor input. Untrusted bytes in,
validated unified events (``providers.base``) or a drop reason out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from mesh_runtime.providers.base import (
    ExecutorEvent,
    FinalResult,
    SessionStarted,
    TextDelta,
    ToolCompleted,
    ToolRequested,
    UsageObserved,
)

#: A single record line larger than this is dropped WITHOUT parsing (§3.9).
MAX_LINE_BYTES = 1024 * 1024
#: Caps on record internals — a well-formed record that exceeds them counts as
#: oversize and is dropped (parser DoS guard; real records are far smaller).
MAX_CONTENT_BLOCKS = 4096
MAX_BLOCK_TEXT_CHARS = 256 * 1024
#: Terminal summary carried into result schema v1 (attempt.py re-caps at 4096).
SUMMARY_MAX_CHARS = 16 * 1024

_COST_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class ParsedRecord:
    """One parsed stream line: unified events, or exactly why it was dropped."""

    events: tuple[ExecutorEvent, ...] = ()
    dropped: str | None = None  # None | "unknown" | "malformed" | "oversize"
    raw_type: str = ""  # vendor record type, for diagnostics only
    thinking_skipped: int = 0  # §3.7 suppression counter


def _drop(reason: str, raw_type: str = "") -> ParsedRecord:
    return ParsedRecord(events=(), dropped=reason, raw_type=raw_type)


def parse_stream_record(line: object) -> ParsedRecord:
    """Parse one stdout line into unified events. TOTAL — never raises."""
    if not isinstance(line, str):
        return _drop("malformed")
    if len(line.encode("utf-8", errors="replace")) > MAX_LINE_BYTES:
        return _drop("oversize")
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return _drop("malformed")
    if not isinstance(record, dict):
        return _drop("malformed")
    record_type = record.get("type")
    if not isinstance(record_type, str) or not record_type:
        return _drop("malformed")

    if record_type == "system":
        return _parse_system(record)
    if record_type == "assistant":
        return _parse_assistant(record)
    if record_type == "user":
        return _parse_user(record)
    if record_type == "result":
        return _parse_result(record)
    return _drop("unknown", record_type)


def _parse_system(record: dict) -> ParsedRecord:
    subtype = record.get("subtype")
    if subtype != "init":
        # thinking_tokens and any other system chatter: known-but-relayed
        # nowhere — drop as unknown so diagnostics count it (§3.9).
        return _drop("unknown", "system")
    session_id = record.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return _drop("malformed", "system")
    model = record.get("model")
    return ParsedRecord(
        events=(
            SessionStarted(
                session_id=session_id,
                model=model if isinstance(model, str) and model else "unknown",
            ),
        )
    )


def _parse_assistant(record: dict) -> ParsedRecord:
    message = record.get("message")
    if not isinstance(message, dict):
        return _drop("malformed", "assistant")
    content = message.get("content")
    if not isinstance(content, list):
        return _drop("malformed", "assistant")
    if len(content) > MAX_CONTENT_BLOCKS:
        return _drop("oversize", "assistant")

    events: list[ExecutorEvent] = []
    thinking_skipped = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                if len(text) > MAX_BLOCK_TEXT_CHARS:
                    return _drop("oversize", "assistant")
                events.append(TextDelta(text=text))
        elif block_type == "tool_use":
            call_id = block.get("id")
            name = block.get("name")
            if isinstance(call_id, str) and isinstance(name, str):
                events.append(ToolRequested(name=name, call_id=call_id))
        elif block_type == "thinking":
            thinking_skipped += 1  # §3.7: NEVER an event
        # unknown block types are skipped silently inside a valid record

    usage_event = _usage_event(message.get("usage"))
    if usage_event is _MALFORMED:
        return _drop("malformed", "assistant")
    if usage_event is not None:
        events.append(usage_event)
    return ParsedRecord(events=tuple(events), thinking_skipped=thinking_skipped)


def _parse_user(record: dict) -> ParsedRecord:
    message = record.get("message")
    if not isinstance(message, dict):
        return _drop("malformed", "user")
    content = message.get("content")
    if not isinstance(content, list):
        return _drop("unknown", "user")  # plain-text user records (stdin echo)
    if len(content) > MAX_CONTENT_BLOCKS:
        return _drop("oversize", "user")
    events: list[ExecutorEvent] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        call_id = block.get("tool_use_id")
        if isinstance(call_id, str):
            outcome = "error" if block.get("is_error") else "ok"
            events.append(ToolCompleted(call_id=call_id, outcome=outcome))
    if not events:
        return _drop("unknown", "user")
    return ParsedRecord(events=tuple(events))


def _parse_result(record: dict) -> ParsedRecord:
    subtype = record.get("subtype")
    if not isinstance(subtype, str) or not subtype:
        return _drop("malformed", "result")
    result_text = record.get("result")
    if not isinstance(result_text, str):
        return _drop("malformed", "result")
    num_turns = record.get("num_turns", 0)
    if not isinstance(num_turns, int) or isinstance(num_turns, bool) or num_turns < 0:
        return _drop("malformed", "result")
    cost = format_cost_usd(record.get("total_cost_usd", 0))
    if cost is None:
        return _drop("malformed", "result")

    usage_event = _usage_event(record.get("usage"))
    if usage_event is _MALFORMED:
        return _drop("malformed", "result")
    success = subtype == "success"
    final_usage = UsageObserved(
        input_tokens=usage_event.input_tokens if usage_event else 0,
        output_tokens=usage_event.output_tokens if usage_event else 0,
        cache_read_tokens=usage_event.cache_read_tokens if usage_event else 0,
        cache_creation_tokens=usage_event.cache_creation_tokens if usage_event else 0,
        cost_usd=cost,
        turns=num_turns,
    )
    final = FinalResult(
        summary=result_text[:SUMMARY_MAX_CHARS],
        exit_code=0 if success else 1,
        termination="completed" if success else "failed",
    )
    return ParsedRecord(events=(final_usage, final), raw_type="result")


class _MalformedSentinel:
    pass


_MALFORMED = _MalformedSentinel()


def _usage_event(raw: object) -> UsageObserved | _MalformedSentinel | None:
    """Map a vendor ``usage`` object to cumulative UsageObserved. Returns the
    sentinel on schema violation, None when absent."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return _MALFORMED
    values: dict[str, int] = {}
    for target, source in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_read_tokens", "cache_read_input_tokens"),
        ("cache_creation_tokens", "cache_creation_input_tokens"),
    ):
        value = raw.get(source, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return _MALFORMED
        values[target] = value
    return UsageObserved(
        input_tokens=values["input_tokens"],
        output_tokens=values["output_tokens"],
        cache_read_tokens=values["cache_read_tokens"],
        cache_creation_tokens=values["cache_creation_tokens"],
    )


def format_cost_usd(value: object) -> str | None:
    """Render a vendor cost as the result-schema decimal string (6 places).
    Returns None for negative or non-numeric values (fail-closed)."""
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not decimal.is_finite() or decimal < 0:
        return None
    return str(decimal.quantize(_COST_QUANTUM))
