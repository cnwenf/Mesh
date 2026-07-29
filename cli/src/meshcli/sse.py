"""SSE log streaming client (cli.md C12, runtime.md §3.3 fallback channel).

``mesh execution logs --follow`` subscribes to
``GET /workspaces/{ws}/executions/{id}/logs/stream?offset=N`` and renders
``log`` frames. Contract guarantees:

- offset-based resume with de-duplication: frames at/below the last emitted
  offset are dropped, so a reconnect neither loses nor duplicates lines;
- RFC3339 timestamps from each frame's ``ts`` prefix every line by default
  (``--timestamps=false`` for raw piping);
- ``end`` frame terminates the follow; Ctrl-C exits 130 with no dangling
  connection (the streaming response is closed in a finally block).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class LogFrame:
    stream: str  # "stdout" | "stderr"
    offset: int
    line: str
    ts: str | None  # RFC3339 server receive time


def parse_sse_lines(raw_lines: Iterator[str]) -> Iterator[dict]:
    """Parse an SSE byte/line stream into event data dicts (``data:`` payloads)."""
    buffer: list[str] = []
    for raw in raw_lines:
        line = raw.rstrip("\n")
        if line.startswith("data:"):
            buffer.append(line[len("data:"):].lstrip())
        elif line == "" and buffer:
            payload = "\n".join(buffer)
            buffer = []
            try:
                event = json.loads(payload)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event
        # ignore comments (':') and other fields


def follow_logs(
    client,
    *,
    workspace_id: str,
    execution_id: str,
    start_offset: int = 0,
    timestamps: bool = True,
    on_frame=None,
) -> int:
    """Follow an execution's log stream; returns the final offset.

    ``on_frame(rendered_line)`` receives display-ready lines; the default
    prints to stdout. Reconnects from the next offset on transport errors
    until the server sends ``end``.
    """
    next_offset = start_offset
    last_emitted = start_offset - 1
    while True:
        path = f"/api/v1/workspaces/{workspace_id}/executions/{execution_id}/logs/stream"
        response = client.stream_request(
            "GET", path, params={"offset": next_offset}
        )
        try:
            for event in parse_sse_lines(response.iter_lines()):
                event_type = event.get("type")
                if event_type == "log":
                    offset = int(event.get("offset", last_emitted + 1))
                    if offset <= last_emitted:
                        continue  # resume de-duplication (不丢/不重/单调)
                    last_emitted = offset
                    next_offset = offset + 1
                    frame = LogFrame(
                        stream=event.get("stream", "stdout"),
                        offset=offset,
                        line=event.get("line", ""),
                        ts=event.get("ts"),
                    )
                    rendered = format_log_line(frame, timestamps=timestamps)
                    if on_frame is not None:
                        on_frame(rendered)
                    else:
                        print(rendered, flush=True)
                elif event_type == "status":
                    continue  # status frames carry no log content
                elif event_type == "heartbeat":
                    continue
                elif event_type == "end":
                    final = event.get("final_offset")
                    return int(final) if final is not None else last_emitted
        finally:
            response.close()
        # Stream closed without an ``end`` frame — reconnect from next_offset.


def format_log_line(frame: LogFrame, *, timestamps: bool) -> str:
    if timestamps and frame.ts:
        return f"{frame.ts} {frame.line}"
    return frame.line


def fetch_history(
    client,
    *,
    workspace_id: str,
    execution_id: str,
    offset: int = 0,
    stream: str | None = None,
    timestamps: bool = True,
) -> list[str]:
    """One-shot REST history fetch (non-follow mode)."""
    params: dict = {"offset": offset}
    if stream:
        params["stream"] = stream
    envelope = client.request(
        "GET",
        f"/api/v1/workspaces/{workspace_id}/executions/{execution_id}/logs",
        params=params,
    ).json()
    data = envelope.get("data", {})
    lines_out: list[str] = []
    entries = data if isinstance(data, list) else data.get("lines", data.get("entries", []))
    for entry in entries:
        if not isinstance(entry, dict):
            lines_out.append(str(entry))
            continue
        frame = LogFrame(
            stream=entry.get("stream", "stdout"),
            offset=int(entry.get("offset", 0)),
            line=entry.get("line", entry.get("text", "")),
            ts=entry.get("ts"),
        )
        lines_out.append(format_log_line(frame, timestamps=timestamps))
    return lines_out
