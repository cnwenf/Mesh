"""Chat generation terminal write-back (chat-session.md §4.4).

Chat generations ride the real runtime chain; their terminal state lands via
the SAME transaction that settles the execution:

- daemon PATCH terminal → ``transition_attempt`` calls
  ``finalize_chat_generation`` in-transaction, then appends the SSE terminal
  frame post-commit;
- every other terminal path (reaper reclaim, supersede cancel of a queued
  run, freeze…) is covered by the §3.6 single fan-out: the outbox relay's
  ``execution.finished`` handler calls ``finalize_chat_from_finished_event``.

Both paths share one conditional UPDATE (``generation_status='streaming'``):
the first writer wins, redelivery / double-observation de-duplicates. The SSE
terminal frame is guarded by a Redis SETNX so exactly one frame reaches the
buffer regardless of which path fires.

Content source of truth: the lossless concatenation of the mirrored stdout
deltas (the daemon uploads each provider TextDelta chunk as one line); when
nothing was streamed (a completion without any delta) the daemon's result
summary is the fallback.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update

from mesh.chat.engine import (
    AUTO_TITLE_MAX_CHARS,
    DEFAULT_BUFFER_TTL_SECONDS,
    STREAM_KEY_TEMPLATE,
    append_chat_frame,
)
from mesh.db.models.chat import ChatMessage, ChatSession
from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime

logger = logging.getLogger(__name__)

# Execution terminal status → chat generation status.
_EXECUTION_TO_GENERATION_STATUS = {
    "completed": "done",
    "cancelled": "interrupted",
    "failed": "failed",
    "timeout": "failed",
}

_GENERATION_TO_TERMINAL_EVENT = {
    "done": "message.done",
    "interrupted": "message.interrupted",
    "failed": "error",
}


@dataclass(frozen=True)
class ChatFinalization:
    """Outcome of the conditional terminal message write."""

    wrote: bool
    generation_id: uuid.UUID
    message_id: uuid.UUID
    session_id: uuid.UUID | None
    generation_status: str
    content: str
    completion_tokens: int
    error_message: str | None
    terminal_event: str


def _parse_uuid(raw: object) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


async def buffered_content_from_redis(redis, generation_id: uuid.UUID) -> str:
    """Lossless reply text currently in the generation's SSE buffer."""
    stream_key = STREAM_KEY_TEMPLATE.format(generation_id=generation_id)
    raw = await redis.xrange(stream_key, min="0", max="+")
    parts: list[str] = []
    for _entry_id, fields in raw:
        try:
            frame = json.loads(fields["frame"])
        except (KeyError, ValueError):
            continue
        if frame.get("event") == "message.delta":
            parts.append(frame.get("data", {}).get("delta", ""))
    return "".join(parts)


def _result_summary(result: object) -> str:
    """Result schema v1 ``outcome.summary`` (server-side ≤4096)."""
    if not isinstance(result, dict):
        return ""
    outcome = result.get("outcome")
    if not isinstance(outcome, dict):
        return ""
    summary = outcome.get("summary")
    return summary if isinstance(summary, str) else ""


def terminal_frame_data(finalization: ChatFinalization) -> dict:
    """SSE terminal frame payload per terminal event kind."""
    if finalization.terminal_event == "message.done":
        return {
            "message_id": str(finalization.message_id),
            "generation_status": "done",
            "completion_tokens": finalization.completion_tokens,
        }
    if finalization.terminal_event == "message.interrupted":
        return {
            "message_id": str(finalization.message_id),
            "partial_content": finalization.content,
            "generation_status": "interrupted",
        }
    return {
        "message_id": str(finalization.message_id),
        "code": "generation_failed",
        "message": finalization.error_message or "generation failed",
    }


async def append_terminal_frame(
    redis, finalization: ChatFinalization, *, buffer_ttl_seconds: int = DEFAULT_BUFFER_TTL_SECONDS
) -> None:
    """Best-effort SSE terminal frame; exactly one per generation (SETNX).

    Live stream followers terminate only on a terminal BUFFER frame, so every
    terminal path calls this after its commit. The SETNX absorbs the
    PATCH/safety-net double-observation; a Redis failure degrades to the
    REST/late-subscriber path, never corrupts the stored reply.
    """
    if redis is None or not finalization.wrote:
        return
    terminal_key = f"chat:gen:{finalization.generation_id}:terminal"
    try:
        first = await redis.set(terminal_key, "1", nx=True, ex=buffer_ttl_seconds)
        if not first:
            return
        await append_chat_frame(
            redis,
            generation_id=finalization.generation_id,
            event=finalization.terminal_event,
            data=terminal_frame_data(finalization),
            buffer_ttl_seconds=buffer_ttl_seconds,
        )
    except Exception:  # noqa: BLE001 — terminal frame is best-effort
        logger.warning(
            "chat SSE terminal frame failed for generation %s",
            finalization.generation_id,
            exc_info=True,
        )


async def _maybe_auto_title(session, workspace_id: uuid.UUID, session_id: uuid.UUID) -> None:
    """First completed round renames '新对话' after the user's question."""
    chat_session = await session.scalar(
        select(ChatSession).where(
            ChatSession.workspace_id == workspace_id, ChatSession.id == session_id
        )
    )
    if chat_session is None or not chat_session.title_is_auto:
        return
    first_user = await session.scalar(
        select(ChatMessage.content)
        .where(
            ChatMessage.workspace_id == workspace_id,
            ChatMessage.session_id == session_id,
            ChatMessage.role == "user",
        )
        .order_by(ChatMessage.created_at.asc())
    )
    if not first_user:
        return
    title = first_user.strip().replace("\n", " ")
    if len(title) > AUTO_TITLE_MAX_CHARS:
        title = title[:AUTO_TITLE_MAX_CHARS] + "…"
    chat_session.title = title or chat_session.title


async def finalize_chat_generation(
    session,
    *,
    workspace_id: uuid.UUID,
    execution,  # TaskExecution — loose typing avoids an import cycle
    redis=None,
) -> ChatFinalization | None:
    """Terminal write-back for one chat execution, inside the CALLER's txn.

    Returns None when the execution is not a chat generation (every other
    trigger keeps its existing terminal behavior untouched).
    """
    if execution.trigger != "chat":
        return None
    spec = execution.task_spec or {}
    generation_id = _parse_uuid(spec.get("generation_id"))
    message_id = _parse_uuid(spec.get("message_id"))
    session_id = _parse_uuid(spec.get("session_id"))
    if generation_id is None or message_id is None:
        return None
    generation_status = _EXECUTION_TO_GENERATION_STATUS.get(execution.status)
    if generation_status is None:
        return None

    # Content: lossless mirror concat; result-summary fallback on completion.
    content = ""
    if redis is not None:
        try:
            content = await buffered_content_from_redis(redis, generation_id)
        except Exception:  # noqa: BLE001 — degrade to the summary fallback
            logger.warning("chat buffer read failed for %s", generation_id, exc_info=True)
            content = ""
    if not content and generation_status == "done":
        content = _result_summary(execution.result)
    error_message = (
        execution.failure_reason or "generation failed"
        if generation_status == "failed"
        else None
    )
    completion_tokens = max(1, len(content) // 3)
    now = datetime.now(UTC)

    result = await session.execute(
        update(ChatMessage)
        .where(
            ChatMessage.id == message_id,
            ChatMessage.generation_status == "streaming",
        )
        .values(
            content=content,
            generation_status=generation_status,
            completion_tokens=completion_tokens,
            error_message=error_message,
            finished_at=now,
            updated_at=now,
        )
    )
    wrote = result.rowcount > 0
    finalization = ChatFinalization(
        wrote=wrote,
        generation_id=generation_id,
        message_id=message_id,
        session_id=session_id,
        generation_status=generation_status,
        content=content,
        completion_tokens=completion_tokens,
        error_message=error_message,
        terminal_event=_GENERATION_TO_TERMINAL_EVENT[generation_status],
    )
    if wrote and session_id is not None:
        await session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(
                last_message_at=now,
                last_message_preview=content[:120],
                updated_at=now,
            )
        )
        if generation_status == "done":
            await _maybe_auto_title(session, workspace_id, session_id)
        await _emit_terminal_frames(
            session,
            workspace_id=workspace_id,
            session_id=session_id,
            finalization=finalization,
        )
    return finalization


async def _emit_terminal_frames(
    session,
    *,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    finalization: ChatFinalization,
) -> None:
    """H1 owner-only terminal realtime frames (README §6.6 outbox).

    The per-session frame (``chat_session:{id}``, owner-only checker) carries
    the full data (incl. ``partial_content`` for interrupts — the owner's own
    content). The list-refresh frame goes to the OWNER's private
    ``chat_list:{owner}`` channel with a SAFE payload — never the content,
    never a workspace-wide broadcast.
    """
    from mesh.chat.engine import chat_list_channel, chat_session_channel

    event = finalization.terminal_event
    data = terminal_frame_data(finalization)
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=chat_session_channel(session_id),
        event=event,
        data=data,
        idempotency_key=f"chat:{finalization.generation_id}:{event}:session",
    )
    chat_session = await session.scalar(
        select(ChatSession).where(
            ChatSession.workspace_id == workspace_id, ChatSession.id == session_id
        )
    )
    if chat_session is None:
        return
    list_payload = {
        "session_id": str(session_id),
        "generation_status": finalization.generation_status,
        "last_message_preview": chat_session.last_message_preview,
        "last_message_at": (
            chat_session.last_message_at.isoformat()
            if chat_session.last_message_at is not None
            else None
        ),
        "message_count": chat_session.message_count,
    }
    await emit_realtime(
        session,
        workspace_id=workspace_id,
        channel=chat_list_channel(chat_session.owner_id),
        event=event,
        data=list_payload,
        idempotency_key=f"chat:{finalization.generation_id}:{event}:list",
    )


async def finalize_chat_from_finished_event(
    session, event, redis=None, *, buffer_ttl_seconds: int = DEFAULT_BUFFER_TTL_SECONDS
) -> None:
    """§3.6 safety net: finalize the generation when the execution reaches a
    terminal state WITHOUT a daemon PATCH (reaper reclaim, supersede cancel
    of a queued run, freeze…). Idempotent under redelivery.
    """
    payload = event.payload or {}
    execution_id = _parse_uuid(payload.get("execution_id"))
    workspace_id = _parse_uuid(payload.get("workspace_id")) or event.workspace_id
    if execution_id is None:
        return
    from mesh.db.models.runtime import TaskExecution

    await set_tenant_context(session, workspace_id)
    execution = (
        await session.execute(
            select(TaskExecution).where(
                TaskExecution.id == execution_id,
                TaskExecution.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if execution is None or execution.trigger != "chat":
        return
    finalization = await finalize_chat_generation(
        session, workspace_id=workspace_id, execution=execution, redis=redis
    )
    if finalization is not None:
        # Best-effort inside the relay transaction: rollback + redelivery
        # re-finalizes idempotently (SETNX absorbs the duplicate frame).
        await append_terminal_frame(redis, finalization, buffer_ttl_seconds=buffer_ttl_seconds)
