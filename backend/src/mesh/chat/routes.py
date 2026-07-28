"""Chat routes (chat-session.md §3.1 — REST + GET SSE stream).

Streaming follows README §6.8: POST messages / regenerate only CREATE a
generation and return ``stream_url``; the stream itself is a GET (EventSource
wire format) with ``Last-Event-ID`` resume; interruption is the separate
idempotent ``POST .../stop`` endpoint. Sessions are owner-only (§3.5); the
service layer enforces that with uniform 404s.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.chat.schemas import (
    CreateChatSessionRequest,
    DistillPreviewRequest,
    PatchChatSessionRequest,
    SelectCandidateRequest,
    SendMessageRequest,
)
from mesh.chat.service import (
    DEFAULT_MESSAGE_LIMIT,
    DEFAULT_SESSION_LIMIT,
    MAX_MESSAGE_LIMIT,
    MAX_SESSION_LIMIT,
    UNSET,
    ChatService,
)
from mesh.chat.stream import generation_event_stream, parse_last_event_id
from mesh.db.models.user import User
from mesh.errors import NotFoundError

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _chat(request: Request) -> ChatService:
    return request.app.state.chat_service


def _path_uuid(raw: str, *, message: str = "chat session not found") -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


async def _rate_limit_session_write(
    request: Request, user: User, response: Response, session_id: str, bucket: str
) -> None:
    """Per-user per-session write limit (§3.5 发送限流).

    L5: parse the path id to a UUID first so an unauthenticated-by-format
    random string cannot churn the rate-limit key space (an invalid id 404s
    before any Redis write).
    """
    parsed = _path_uuid(session_id)
    settings = request.app.state.settings
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"{bucket}:{user.id}:{parsed}",
        limit=settings.chat_send_rate_limit,
        window_seconds=settings.chat_send_rate_window_seconds,
    )
    response.headers["X-RateLimit-Limit"] = str(settings.chat_send_rate_limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/chat-sessions", status_code=201)
async def create_chat_session(
    body: CreateChatSessionRequest,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    created = await _chat(request).create_session(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=body.agent_id,
        context_issue_id=body.context_issue_id,
        context_project_id=body.context_project_id,
        title=body.title,
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/chat-sessions")
async def list_chat_sessions(
    request: Request,
    agent_id: str | None = Query(default=None),
    status: str = Query(default="active"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_SESSION_LIMIT, ge=1, le=MAX_SESSION_LIMIT),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    parsed_agent = _path_uuid(agent_id, message="agent not found") if agent_id else None
    page = await _chat(request).list_sessions(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=parsed_agent,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return {"data": page["items"], "next_cursor": page["next_cursor"]}


@router.get("/workspaces/{workspace_id}/chat-sessions/{session_id}")
async def get_chat_session(
    session_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    detail = await _chat(request).get_session(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
    )
    return {"data": detail}


@router.patch("/workspaces/{workspace_id}/chat-sessions/{session_id}")
async def patch_chat_session(
    body: PatchChatSessionRequest,
    session_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    fields = body.model_dump(exclude_unset=True)
    kwargs = {
        name: (value if name in fields else UNSET)
        for name, value in (
            ("title", body.title),
            ("status", body.status),
            ("context_issue_id", body.context_issue_id),
            ("context_project_id", body.context_project_id),
        )
    }
    detail = await _chat(request).patch_session(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        **kwargs,
    )
    return {"data": detail}


@router.delete("/workspaces/{workspace_id}/chat-sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> Response:
    await _chat(request).delete_session(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# messages + generation lifecycle
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/chat-sessions/{session_id}/messages",
             status_code=201)
async def send_chat_message(
    body: SendMessageRequest,
    session_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """POST creates the generation (README §6.8 step 1); consume ``stream_url``."""
    await _rate_limit_session_write(request, user, response, session_id, "chat-send")
    created = await _chat(request).send_message(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        content=body.content,
        attachment_ids=list(body.attachment_ids),
        quote_message_id=body.quote_message_id,
        idempotency_key=idempotency_key,
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/chat-sessions/{session_id}/messages")
async def list_chat_messages(
    session_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_MESSAGE_LIMIT, ge=1, le=MAX_MESSAGE_LIMIT),
    parent_id: str | None = Query(default=None),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    parsed_parent = _path_uuid(parent_id, message="message not found") if parent_id else None
    page = await _chat(request).list_messages(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        cursor=cursor,
        limit=limit,
        parent_id=parsed_parent,
    )
    return {"data": page["items"], "next_cursor": page["next_cursor"]}


@router.get("/workspaces/{workspace_id}/chat-sessions/{session_id}"
            "/generations/{generation_id}/stream")
async def stream_chat_generation(
    session_id: str,
    generation_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> StreamingResponse:
    """GET SSE stream (README §6.8 step 2 — EventSource wire format)."""
    service = _chat(request)
    settings = request.app.state.settings
    resolved_session = _path_uuid(session_id)
    resolved_generation = _path_uuid(generation_id, message="generation not found")
    # Authorization runs BEFORE the stream opens so failures are JSON, not
    # a broken event stream.
    await service.authorize_stream(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=resolved_session,
        generation_id=resolved_generation,
    )

    async def load_state() -> dict | None:
        return await service.load_message_state(
            workspace_id=context.workspace.id,
            session_id=resolved_session,
            generation_id=resolved_generation,
        )

    generator = generation_event_stream(
        request.app.state.redis,
        generation_id=str(resolved_generation),
        last_event_id=parse_last_event_id(last_event_id),
        ping_seconds=settings.chat_stream_ping_seconds,
        max_seconds=settings.chat_stream_max_seconds,
        load_message_state=load_state,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workspaces/{workspace_id}/chat-sessions/{session_id}"
             "/generations/{generation_id}/stop", status_code=202)
async def stop_chat_generation(
    session_id: str,
    generation_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Independent idempotent stop endpoint (README §6.8 — 重复 stop 无副作用).

    The conditional status flip is inherently idempotent, so an
    ``Idempotency-Key`` (accepted per §3.5) needs no extra dedup table.
    """
    _ = idempotency_key
    await _rate_limit_session_write(request, user, response, session_id, "chat-stop")
    result = await _chat(request).stop_generation(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        generation_id=_path_uuid(generation_id, message="generation not found"),
    )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/chat-sessions/{session_id}"
             "/messages/{message_id}/regenerate", status_code=201)
async def regenerate_chat_message(
    session_id: str,
    message_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    await _rate_limit_session_write(request, user, response, session_id, "chat-regenerate")
    created = await _chat(request).regenerate(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        message_id=_path_uuid(message_id, message="message not found"),
        idempotency_key=idempotency_key,
    )
    return {"data": created}


@router.post("/workspaces/{workspace_id}/chat-sessions/{session_id}"
             "/messages/{message_id}/select")
async def select_chat_candidate(
    body: SelectCandidateRequest,
    session_id: str,
    message_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    result = await _chat(request).select_candidate(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        message_id=_path_uuid(message_id, message="message not found"),
        selected_message_id=body.selected_message_id,
    )
    return {"data": result}


# ---------------------------------------------------------------------------
# distillation preview (沉淀为评论 — README §6.9 one-submit closed loop)
# ---------------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/chat-sessions/{session_id}/distill-preview")
async def distill_preview(
    body: DistillPreviewRequest,
    session_id: str,
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("chat:write")),
) -> dict:
    """Side-effect-free preview; submission calls the comment endpoint once."""
    preview = await _chat(request).distill_preview(
        actor=context.member,
        workspace_id=context.workspace.id,
        session_id=_path_uuid(session_id),
        body_markdown=body.body_markdown,
        target_issue_id=body.target_issue_id,
        attachment_ids=list(body.attachment_ids),
    )
    return {"data": preview}
