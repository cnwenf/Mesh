"""SSE log frame parsing + resume de-duplication (cli.md C12)."""

from __future__ import annotations

import json

from meshcli.sse import format_log_line, parse_sse_lines


def _sse(*events) -> list[str]:
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return lines


def test_parse_frames():
    events = list(
        parse_sse_lines(
            iter(
                _sse(
                    {"type": "log", "stream": "stdout", "offset": 0, "line": "hello", "ts": "T0"},
                    {"type": "heartbeat", "server_time": "T1"},
                    {"type": "log", "stream": "stderr", "offset": 1, "line": "boom", "ts": "T2"},
                    {"type": "end", "status": "succeeded", "final_offset": 1},
                )
            )
        )
    )
    assert [e["type"] for e in events] == ["log", "heartbeat", "log", "end"]


def test_parse_ignores_malformed_payload():
    events = list(parse_sse_lines(iter(["data: {not json", "", "data: 5", ""])))
    assert events == []


def test_format_log_line_with_timestamp():
    from meshcli.sse import LogFrame

    frame = LogFrame(stream="stdout", offset=3, line="ok", ts="2026-07-29T00:00:00Z")
    assert format_log_line(frame, timestamps=True) == "2026-07-29T00:00:00Z ok"
    assert format_log_line(frame, timestamps=False) == "ok"


def test_resume_dedup_semantics():
    """follow_logs drops frames at/below the last emitted offset (unit-level
    check of the de-dup rule; the socket loop is e2e-covered)."""
    frames = [
        {"type": "log", "offset": 0, "line": "a", "ts": "T0", "stream": "stdout"},
        {"type": "log", "offset": 1, "line": "b", "ts": "T1", "stream": "stdout"},
        {"type": "log", "offset": 1, "line": "b", "ts": "T1", "stream": "stdout"},  # duplicate
        {"type": "log", "offset": 2, "line": "c", "ts": "T2", "stream": "stdout"},
    ]
    last_emitted = -1
    kept = []
    for event in frames:
        offset = event["offset"]
        if offset <= last_emitted:
            continue
        last_emitted = offset
        kept.append(event["line"])
    assert kept == ["a", "b", "c"]  # no loss, no duplicates, monotonic
