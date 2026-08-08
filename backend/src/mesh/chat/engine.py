"""Chat generation SSE frame buffer primitives (chat-session.md §3.3).

Chat generations execute on the REAL runtime chain (README §6.9 chat
trigger): the daemon's provider streams TextDelta chunks, the log mirror
(``mesh.runtime.logs``) appends them here as ``message.delta`` frames, and
the terminal write-back (``mesh.chat.finalize``) appends the terminal frame.
This module owns ONLY the shared buffer protocol:

- key templates (Stream buffer / pub-sub fan-out / INCR seq authority);
- ``append_chat_frame`` — the single atomic appender every writer uses, so
  independent appenders never collide or gap on ``seq`` (the SSE
  ``Last-Event-ID`` resume cursor);
- channel-name and idempotency-key helpers.

Buffers expire after a TTL; late subscribers then degrade to "REST final
content + terminal frame" (chat-session.md §3.3 缓冲淘汰降级).
"""

from __future__ import annotations

import hashlib
import json
import uuid

STREAM_KEY_TEMPLATE = "chat:gen:{generation_id}:events"
PUBSUB_KEY_TEMPLATE = "chat:gen:{generation_id}:pubsub"
SEQ_KEY_TEMPLATE = "chat:gen:{generation_id}:seq"

MAX_BUFFER_FRAMES = 2000
DEFAULT_BUFFER_TTL_SECONDS = 3600
AUTO_TITLE_MAX_CHARS = 40


async def append_chat_frame(
    redis,
    *,
    generation_id: uuid.UUID,
    event: str,
    data: dict,
    buffer_ttl_seconds: int = DEFAULT_BUFFER_TTL_SECONDS,
) -> int:
    """Atomically append one SSE frame to a generation buffer and fan it out.

    The seq is assigned by Redis ``INCR`` (single writer authority), so
    independent appenders — the runtime log mirror and the terminal
    write-back — never collide or gap. Frames carry monotonically increasing
    ``seq`` values (the SSE ``Last-Event-ID`` resume cursor).
    """
    stream_key = STREAM_KEY_TEMPLATE.format(generation_id=generation_id)
    pubsub_key = PUBSUB_KEY_TEMPLATE.format(generation_id=generation_id)
    seq_key = SEQ_KEY_TEMPLATE.format(generation_id=generation_id)
    seq = int(await redis.incr(seq_key))
    frame = json.dumps({"seq": seq, "event": event, "data": data})
    await redis.xadd(stream_key, {"frame": frame}, id=f"{seq}-0", maxlen=MAX_BUFFER_FRAMES)
    await redis.expire(stream_key, buffer_ttl_seconds)
    await redis.expire(seq_key, buffer_ttl_seconds)
    await redis.publish(pubsub_key, "frame")
    return seq


def chat_execution_idempotency_key(
    *, agent_id: uuid.UUID, issue_id: uuid.UUID | None, trigger_event_id: uuid.UUID
) -> str:
    """README §6.5: sha256(agent_id | issue_id | trigger_event_id).

    ``issue_id`` is the session's context issue; sessions without one hash a
    stable ``nil`` placeholder so the same trigger never enqueues twice.
    """
    issue_part = str(issue_id) if issue_id is not None else "nil"
    return hashlib.sha256(
        f"{agent_id}|{issue_part}|{trigger_event_id}".encode()
    ).hexdigest()


def chat_session_channel(session_id: uuid.UUID) -> str:
    return f"chat_session:{session_id}"


def chat_list_channel(owner_member_id: uuid.UUID) -> str:
    """Owner-private session-list channel (H1: never a workspace-wide fan-out).

    Carries only the owner's own list-preview fields; authorized by
    ``chat_list`` checker (principal must own that member row). Replaces the
    earlier workspace-wide channel that leaked every member's private session
    terminal events (incl. interrupted partial content) to the whole workspace.
    """
    return f"chat_list:{owner_member_id}"
