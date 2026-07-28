"""SSE stream consumption for chat generations (README §6.8).

Wire format is EventSource-compatible (``id:`` / ``event:`` / ``data:``
frames). Browsers' native EventSource cannot send Authorization headers, so
the web client consumes this endpoint with fetch streaming and implements
its own reconnect + ``Last-Event-ID`` reconciliation (README §6.8 option 4);
the resume contract itself is server-side:

- every frame carries a monotonically increasing numeric ``id:``;
- reconnects send ``Last-Event-ID`` and the server replays buffered frames
  after it;
- if the buffer was evicted, the client degrades to "REST final content +
  resubscribe" — the server supports late subscribers by synthesizing the
  final content as a single delta plus the terminal frame whenever the
  message is already terminal and the buffer is gone (chat-session.md §3.3).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable

TERMINAL_EVENTS = frozenset({"message.done", "message.interrupted", "error"})


def format_sse_frame(seq: int | None, event: str, data: dict) -> str:
    """Render one SSE frame (trailing blank line terminates it)."""
    # H4: heartbeat (seq=None) carries NO id: line, so it cannot pollute the
    # client's Last-Event-ID resume cursor (a ping with id:0 made reconnects
    # replay the whole stream).
    id_line = f"id: {seq}\n" if seq is not None else ""
    return f"{id_line}event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def parse_last_event_id(raw: str | None) -> int:
    """EventSource resume cursor; absent / malformed → replay from start."""
    if not raw:
        return 0
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


async def generation_event_stream(
    redis,
    *,
    generation_id: str,
    last_event_id: int,
    ping_seconds: float,
    max_seconds: float,
    load_message_state: Callable[[], Awaitable[dict | None]],
) -> AsyncIterator[str]:
    """Yield SSE frames for one generation until its terminal frame.

    ``load_message_state`` returns ``{"generation_status", "content"}`` for
    the agent message (or None when absent) — the REST-degradation source of
    truth for late subscribers after buffer eviction.
    """
    from mesh.chat.engine import PUBSUB_KEY_TEMPLATE, STREAM_KEY_TEMPLATE

    stream_key = STREAM_KEY_TEMPLATE.format(generation_id=generation_id)
    pubsub_key = PUBSUB_KEY_TEMPLATE.format(generation_id=generation_id)
    deadline = time.monotonic() + max_seconds
    last_seq = last_event_id

    # 1) Replay whatever the buffer still holds past the resume cursor.
    raw = await redis.xrange(stream_key, min=f"({last_seq}-0", max="+")
    for _entry_id, fields in raw:
        frame = json.loads(fields["frame"])
        last_seq = max(last_seq, int(frame["seq"]))
        yield format_sse_frame(frame["seq"], frame["event"], frame["data"])
        if frame["event"] in TERMINAL_EVENTS:
            return

    # 2) Buffer gone AND message already terminal → synthesize the final
    #    content frame + terminal frame (缓冲淘汰降级, chat-session.md §3.3).
    if not raw:
        state = await load_message_state()
        if state is not None and state["generation_status"] in {
            "done",
            "interrupted",
            "failed",
        }:
            if state["content"]:
                yield format_sse_frame(
                    last_seq + 1,
                    "message.delta",
                    {"message_id": state["message_id"], "delta": state["content"]},
                )
                last_seq += 1
            event = {
                "done": "message.done",
                "interrupted": "message.interrupted",
                "failed": "error",
            }[state["generation_status"]]
            data: dict = {
                "message_id": state["message_id"],
                "generation_status": state["generation_status"],
            }
            if event == "error":
                data = {
                    "message_id": state["message_id"],
                    "code": "generation_failed",
                    "message": state.get("error_message") or "generation failed",
                }
            yield format_sse_frame(last_seq + 1, event, data)
            return

    # 3) Follow live frames via pub/sub; heartbeat between them.
    pubsub = redis.pubsub()
    await pubsub.subscribe(pubsub_key)
    last_ping_at = time.monotonic()
    try:
        while time.monotonic() < deadline:
            try:
                await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=min(ping_seconds, 1.0)
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # transient redis hiccup — retry until deadline
                await asyncio.sleep(0.2)
            new_frames = await redis.xrange(stream_key, min=f"({last_seq}-0", max="+")
            for _entry_id, fields in new_frames:
                frame = json.loads(fields["frame"])
                last_seq = max(last_seq, int(frame["seq"]))
                last_ping_at = time.monotonic()
                yield format_sse_frame(frame["seq"], frame["event"], frame["data"])
                if frame["event"] in TERMINAL_EVENTS:
                    return
            if time.monotonic() - last_ping_at >= ping_seconds:
                last_ping_at = time.monotonic()
                yield format_sse_frame(None, "ping", {"ts": int(time.time())})
    finally:
        await pubsub.unsubscribe(pubsub_key)
        await pubsub.aclose()
