"""Comment & inbox routes (comment-inbox.md §3.1 / §3.2, §6.14 envelopes).

Workspace resolution follows the issue-module pattern: issue-scoped paths
resolve the tenant through the narrow SECURITY DEFINER lookup
(``mesh_issue_workspace_id`` / ``mesh_comment_workspace_id``), then run the
membership gate; inbox / preference paths carry a required ``workspace_id``
query parameter (the inbox is per-workspace). Resource-level authorization
(authorship, manager, guest project visibility) lives in the service layer.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session, get_settings
from mesh.auth.deps import AuthenticatedPrincipal, get_current_principal
from mesh.auth.rbac import (
    WorkspaceContext,
    resolve_workspace_context,
    role_satisfies,
)
from mesh.comment_inbox.inbox import INBOX_FILTERS, InboxService
from mesh.comment_inbox.notifications import (
    notification_deep_link,
    verify_email_open_token,
    workspace_slug_for,
)
from mesh.comment_inbox.schemas import (
    AddReactionRequest,
    CreateCommentRequest,
    PutPreferencesRequest,
    ReadAllRequest,
    UpdateCommentRequest,
)
from mesh.comment_inbox.service import CommentService
from mesh.config import Settings
from mesh.db.models.member import Member
from mesh.errors import BusinessRuleError, NotFoundError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["comment-inbox"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_ISSUE_NOT_FOUND = "issue not found"
_COMMENT_NOT_FOUND = "comment not found"
_WORKSPACE_NOT_FOUND = "workspace not found"


def _comments(request: Request) -> CommentService:
    return request.app.state.comment_service


def _inbox(request: Request) -> InboxService:
    return request.app.state.inbox_service


async def _rate_limit_write(
    request: Request,
    principal: AuthenticatedPrincipal,
    response: Response,
    bucket: str,
) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"{bucket}:{principal.user_id or principal.member_id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(raw: str, *, message: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _query_workspace_id(raw: str | None) -> uuid.UUID:
    if not raw:
        raise ValidationError("workspace_id is required", code="validation_error")
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(
            "invalid workspace_id", details={"workspace_id": raw[:64]}
        ) from exc


async def _workspace_id_via(
    session: AsyncSession, resource_id: uuid.UUID, *, function: str, not_found_message: str
) -> uuid.UUID:
    workspace_id = (
        await session.execute(text(f"SELECT {function}(:id)"), {"id": resource_id})
    ).scalar()
    if workspace_id is None:
        raise NotFoundError(not_found_message)
    return workspace_id


async def _context_for(
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    workspace_id: uuid.UUID,
    *,
    permission: str | None,
    not_found_message: str,
) -> WorkspaceContext:
    try:
        return await resolve_workspace_context(
            session, principal=principal, workspace_id=workspace_id, permission=permission
        )
    except NotFoundError as exc:
        raise NotFoundError(not_found_message) from exc


def _is_manager(context: WorkspaceContext) -> bool:
    return role_satisfies(context.member.role, "workspace:settings")


# ---------------------------------------------------------------------------
# comments on issues
# ---------------------------------------------------------------------------


@router.get("/issues/{issue_id}/comments")
async def list_comments(
    issue_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    include: str = Query(default="replies", pattern="^(replies|none)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_issue_workspace_id", not_found_message=_ISSUE_NOT_FOUND
    )
    context = await _context_for(
        session, principal, workspace_id, permission="issue:read", not_found_message=_ISSUE_NOT_FOUND
    )
    items, next_cursor = await _comments(request).list_comments(
        workspace_id=workspace_id,
        issue_id=parsed,
        viewer_member_id=context.member.id,
        member=context.member,
        limit=limit,
        cursor=cursor,
        include=include,
        descending=order == "desc",
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/issues/{issue_id}/comments", status_code=201)
async def create_comment(
    issue_id: str,
    body: CreateCommentRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "comment-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_issue_workspace_id", not_found_message=_ISSUE_NOT_FOUND
    )
    context = await _context_for(
        session, principal, workspace_id,
        permission="comment:write", not_found_message=_ISSUE_NOT_FOUND,
    )
    if body.attachment_ids:
        raise BusinessRuleError(
            "comment attachments land with the attachment module",
            code="attachments_not_available",
        )
    created = await _comments(request).create_comment(
        workspace_id=workspace_id,
        issue_id=parsed,
        author_member=context.member,
        body_markdown=body.body_markdown,
        parent_id=body.parent_id,
        suppress_triggers=body.suppress_triggers,
        idempotency_key=idempotency_key,
        can_trigger_agents=role_satisfies(context.member.role, "agent:trigger"),
    )
    return {"data": created}


# ---------------------------------------------------------------------------
# single-comment operations
# ---------------------------------------------------------------------------


@router.get("/comments/{comment_id}")
async def get_comment(
    comment_id: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="issue:read",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    data = await _comments(request).get_comment(
        workspace_id=workspace_id, comment_id=parsed, viewer_member_id=context.member.id,
        member=context.member,
    )
    return {"data": data}


@router.patch("/comments/{comment_id}")
async def update_comment(
    comment_id: str,
    body: UpdateCommentRequest,
    request: Request,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "comment-write")
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="comment:write",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    updated = await _comments(request).update_comment(
        workspace_id=workspace_id,
        comment_id=parsed,
        editor_member=context.member,
        is_manager=_is_manager(context),
        body_markdown=body.body_markdown,
        expected_updated_at=if_match,
        suppress_triggers=body.suppress_triggers,
        can_trigger_agents=role_satisfies(context.member.role, "agent:trigger"),
    )
    return {"data": updated}


@router.delete("/comments/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _rate_limit_write(request, principal, response, "comment-write")
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="comment:write",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    await _comments(request).delete_comment(
        workspace_id=workspace_id,
        comment_id=parsed,
        actor_member=context.member,
        is_manager=_is_manager(context),
    )


@router.get("/comments/{comment_id}/replies")
async def list_replies(
    comment_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="issue:read",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    items, next_cursor = await _comments(request).list_replies(
        workspace_id=workspace_id,
        comment_id=parsed,
        viewer_member_id=context.member.id,
        member=context.member,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/comments/{comment_id}/resolve")
async def resolve_thread(
    comment_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "comment-write")
    return {
        "data": await _resolve_via(request, session, principal, comment_id, resolved=True)
    }


@router.post("/comments/{comment_id}/reopen")
async def reopen_thread(
    comment_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "comment-write")
    return {
        "data": await _resolve_via(request, session, principal, comment_id, resolved=False)
    }


async def _resolve_via(
    request: Request, session: AsyncSession, principal: AuthenticatedPrincipal, comment_id: str,
    *, resolved: bool
) -> dict:
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="comment:write",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    return await _comments(request).set_thread_resolved(
        workspace_id=workspace_id,
        comment_id=parsed,
        actor_member=context.member,
        resolved=resolved,
    )


# ---------------------------------------------------------------------------
# reactions
# ---------------------------------------------------------------------------


@router.get("/comments/{comment_id}/reactions")
async def list_reactions(
    comment_id: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="issue:read",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    data = await _comments(request).list_reactions(
        workspace_id=workspace_id, comment_id=parsed, viewer_member_id=context.member.id,
        member=context.member,
    )
    return {"data": data}


@router.post("/comments/{comment_id}/reactions")
async def add_reaction(
    comment_id: str,
    body: AddReactionRequest,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "comment-write")
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="comment:write",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    data = await _comments(request).add_reaction(
        workspace_id=workspace_id,
        comment_id=parsed,
        actor_member=context.member,
        emoji=body.emoji,
    )
    return {"data": data}


@router.delete("/comments/{comment_id}/reactions/{emoji}", status_code=204)
async def remove_reaction(
    comment_id: str,
    emoji: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _rate_limit_write(request, principal, response, "comment-write")
    parsed = _path_uuid(comment_id, message=_COMMENT_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_comment_workspace_id",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    context = await _context_for(
        session, principal, workspace_id, permission="comment:write",
        not_found_message=_COMMENT_NOT_FOUND,
    )
    await _comments(request).remove_reaction(
        workspace_id=workspace_id,
        comment_id=parsed,
        actor_member=context.member,
        emoji=emoji,
    )


# ---------------------------------------------------------------------------
# inbox / notifications
# ---------------------------------------------------------------------------


async def _inbox_context(
    workspace_id: str | None, principal: AuthenticatedPrincipal, session: AsyncSession
) -> WorkspaceContext:
    parsed = _query_workspace_id(workspace_id)
    return await _context_for(
        session, principal, parsed, permission=None, not_found_message=_WORKSPACE_NOT_FOUND
    )


@router.get("/inbox")
async def list_inbox(
    request: Request,
    workspace_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    cursor: str | None = Query(default=None),
    filter: str = Query(default="all"),
    type: str | None = Query(default=None),
    grouped: bool = Query(default=False),
    archived: bool = Query(default=False),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context = await _inbox_context(workspace_id, principal, session)
    return await _inbox(request).list_notifications(
        workspace_id=context.workspace.id,
        member=context.member,
        limit=limit,
        cursor=cursor,
        inbox_filter=filter,
        notification_type=type,
        grouped=grouped,
        archived_only=archived,
    )


@router.get("/inbox/unread-count")
async def inbox_unread_count(
    request: Request,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context = await _inbox_context(workspace_id, principal, session)
    count = await _inbox(request).unread_count(
        workspace_id=context.workspace.id, member=context.member
    )
    return {"data": {"count": count}}


@router.post("/inbox/read-all")
async def inbox_read_all(
    request: Request,
    body: ReadAllRequest | None = None,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context = await _inbox_context(workspace_id, principal, session)
    inbox_filter = body.filter if body is not None else None
    notification_type = body.type if body is not None else None
    if inbox_filter is not None and inbox_filter not in INBOX_FILTERS:
        raise ValidationError("invalid filter", details={"filter": inbox_filter[:32]})
    updated = await _inbox(request).read_all(
        workspace_id=context.workspace.id,
        member=context.member,
        inbox_filter=inbox_filter,
        notification_type=notification_type,
    )
    return {"data": {"updated": updated}}


@router.post("/inbox/archive-read")
async def inbox_archive_read(
    request: Request,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context = await _inbox_context(workspace_id, principal, session)
    archived = await _inbox(request).archive_read(
        workspace_id=context.workspace.id, member=context.member
    )
    return {"data": {"archived": archived}}


async def _inbox_item_op(
    request: Request,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    notification_id: str,
    workspace_id: str | None,
    *,
    op: str,
) -> dict:
    parsed = _path_uuid(notification_id, message="notification not found")
    context = await _inbox_context(workspace_id, principal, session)
    service = _inbox(request)
    if op == "read":
        return await service.mark_read(
            workspace_id=context.workspace.id, member=context.member,
            notification_id=parsed, read=True,
        )
    if op == "unread":
        return await service.mark_read(
            workspace_id=context.workspace.id, member=context.member,
            notification_id=parsed, read=False,
        )
    return await service.set_archived(
        workspace_id=context.workspace.id, member=context.member, notification_id=parsed
    )


@router.get("/inbox/{notification_id}/open")
async def inbox_open_from_email(
    notification_id: str,
    request: Request,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Email deep-link entry (comment-inbox.md §4.4 点邮件链接回站内并标已读).

    Unauthenticated by design — the signed one-time token IS the credential
    (recipient + workspace bound, expiry enforced). Any verification failure
    returns the same 404 as a missing notification (anti-oracle). On success
    the notification is read-marked (idempotent) and the client is 302'd to
    the in-site inbox anchor; without a configured app base URL the frame is
    returned as JSON instead.
    """
    parsed = _path_uuid(notification_id, message="notification not found")
    resolved = verify_email_open_token(settings, token, notification_id=parsed)
    if resolved is None:
        raise NotFoundError("notification not found")
    workspace_id, member_id = resolved
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.id == member_id,
        )
    )
    if member is None or member.user_id is None:
        raise NotFoundError("notification not found")
    frame = await _inbox(request).mark_read(
        workspace_id=workspace_id, member=member, notification_id=parsed, read=True
    )
    slug = await workspace_slug_for(session, workspace_id=workspace_id)
    link = notification_deep_link(settings.app_base_url, slug, parsed)
    if link is None:
        return {"data": frame}
    return RedirectResponse(url=link, status_code=302)


@router.post("/inbox/{notification_id}/read")
async def inbox_mark_read(
    notification_id: str,
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    return {
        "data": await _inbox_item_op(
            request, session, principal, notification_id, workspace_id, op="read"
        )
    }


@router.post("/inbox/{notification_id}/unread")
async def inbox_mark_unread(
    notification_id: str,
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    return {
        "data": await _inbox_item_op(
            request, session, principal, notification_id, workspace_id, op="unread"
        )
    }


@router.post("/inbox/{notification_id}/archive")
async def inbox_archive(
    notification_id: str,
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    return {
        "data": await _inbox_item_op(
            request, session, principal, notification_id, workspace_id, op="archive"
        )
    }


# ---------------------------------------------------------------------------
# per-issue mute
# ---------------------------------------------------------------------------


async def _mute_via(
    request: Request,
    session: AsyncSession,
    principal: AuthenticatedPrincipal,
    issue_id: str,
    *,
    muted: bool,
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    workspace_id = await _workspace_id_via(
        session, parsed, function="mesh_issue_workspace_id", not_found_message=_ISSUE_NOT_FOUND
    )
    context = await _context_for(
        session, principal, workspace_id, permission=None, not_found_message=_ISSUE_NOT_FOUND
    )
    return await _inbox(request).set_issue_muted(
        workspace_id=workspace_id, issue_id=parsed, member=context.member, muted=muted
    )


@router.post("/issues/{issue_id}/mute")
async def mute_issue(
    issue_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    return {"data": await _mute_via(request, session, principal, issue_id, muted=True)}


@router.post("/issues/{issue_id}/unmute")
async def unmute_issue(
    issue_id: str,
    request: Request,
    response: Response,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    return {"data": await _mute_via(request, session, principal, issue_id, muted=False)}


# ---------------------------------------------------------------------------
# notification preferences
# ---------------------------------------------------------------------------


@router.get("/notification-preferences")
async def get_notification_preferences(
    request: Request,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context = await _inbox_context(workspace_id, principal, session)
    data = await _inbox(request).get_preferences(
        workspace_id=context.workspace.id, member=context.member
    )
    return {"data": data}


@router.put("/notification-preferences")
async def put_notification_preferences(
    body: PutPreferencesRequest,
    request: Request,
    response: Response,
    workspace_id: str | None = Query(default=None),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, principal, response, "inbox-write")
    context = await _inbox_context(workspace_id, principal, session)
    data = await _inbox(request).put_preferences(
        workspace_id=context.workspace.id,
        member=context.member,
        entries=[entry.model_dump() for entry in body.preferences],
    )
    return {"data": data}
