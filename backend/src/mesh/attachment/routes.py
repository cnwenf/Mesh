"""Attachment routes (attachment.md §3.1).

Three-stage direct upload: ``upload-requests`` signs a short-lived PUT, the
client streams bytes straight to object storage, ``complete`` HEAD-checks and
hands the object to the quarantine pipeline. Workspace-less paths resolve the
tenant through the SECURITY DEFINER lookup (migration 0013) and then run the
membership gate; human JWTs and agent API tokens share the same endpoints
(§5.3). Rate limiting covers ``upload-requests`` and ``download`` (§3.0).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session, get_session_factory
from mesh.attachment.auth import Caller, authenticate, gate_workspace
from mesh.attachment.schemas import (
    CompleteBody,
    MultipartCompleteBody,
    MultipartPartsBody,
    UploadRequestBody,
)
from mesh.attachment.service import AttachmentService
from mesh.db.models.member import Member
from mesh.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["attachments"])

UPLOAD_REQUEST_LIMIT = 60
DOWNLOAD_LIMIT = 120
RATE_WINDOW_SECONDS = 60

_ATTACHMENT_NOT_FOUND = "attachment not found"
_ISSUE_NOT_FOUND = "issue not found"
_COMMENT_NOT_FOUND = "comment not found"


def _service(request: Request) -> AttachmentService:
    return request.app.state.attachment_service


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": request.client.host if request.client is not None else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _path_uuid(raw: str, *, message: str = _ATTACHMENT_NOT_FOUND) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _caller_identity(caller: Caller) -> str:
    if caller.token is not None:
        return f"token:{caller.token.id}"
    assert caller.user is not None
    return f"user:{caller.user.id}"


async def _rate_limit(
    request: Request, caller: Caller, response: Response, bucket: str, limit: int
) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"{bucket}:{_caller_identity(caller)}:{client_ip}",
        limit=limit,
        window_seconds=RATE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _resolve_attachment_context(
    request: Request, session: AsyncSession, attachment_id: uuid.UUID
) -> tuple[Caller, Member, uuid.UUID]:
    """Workspace-less path: resolve tenant → membership gate (labels pattern)."""
    service = _service(request)
    workspace_id = await service.resolve_attachment_workspace(attachment_id)
    if workspace_id is None:
        raise NotFoundError(_ATTACHMENT_NOT_FOUND)
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, workspace_id)
    return caller, member, workspace_id


# ----------------------------------------------------------------------
# upload lifecycle (§3.2 / §3.3)
# ----------------------------------------------------------------------


@router.post("/attachments/upload-requests", status_code=201)
async def create_upload_request(
    body: UploadRequestBody,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    service = _service(request)
    caller = await authenticate(request, get_session_factory(request))

    # Tenant: explicit workspace_id, else derived from the link target (§3.2).
    workspace_id: uuid.UUID | None = None
    if body.workspace_id is not None:
        workspace_id = _path_uuid(body.workspace_id, message="workspace not found")
    elif body.link_to is not None:
        linked_id = _path_uuid(body.link_to.id, message=f"{body.link_to.type} not found")
        workspace_id = await service.resolve_host_workspace(
            session, body.link_to.type, linked_id
        )
        if workspace_id is None:
            raise NotFoundError(f"{body.link_to.type} not found")
    elif caller.token is not None:
        workspace_id = caller.token.workspace_id
    if workspace_id is None:
        raise ValidationError(
            "workspace_id is required when link_to is absent",
            details={"field": "workspace_id"},
        )

    await _rate_limit(request, caller, response, "attachments-upload", UPLOAD_REQUEST_LIMIT)
    member = await gate_workspace(session, caller, workspace_id)

    link_to: dict | None = None
    if body.link_to is not None:
        link_to = {
            "type": body.link_to.type,
            "id": _path_uuid(body.link_to.id, message=f"{body.link_to.type} not found"),
            "display": body.link_to.display,
            "position": body.link_to.position,
        }
    return await service.request_upload(
        actor=member,
        workspace_id=workspace_id,
        file_name=body.file_name,
        file_size=body.file_size,
        mime_type=body.mime_type,
        content_hash=body.content_hash,
        link_to=link_to,
        idempotency_key=idempotency_key,
        **_client_meta(request),
    )


@router.post("/attachments/{attachment_id}/complete")
async def complete_upload(
    body: CompleteBody,
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).complete_upload(
        actor=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        file_size=body.file_size,
        content_hash=body.content_hash,
        **_client_meta(request),
    )


@router.post("/attachments/{attachment_id}/abort")
async def abort_upload(
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).abort_upload(
        actor=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        **_client_meta(request),
    )


# ----------------------------------------------------------------------
# read / delete / download / thumbnail (§3.4)
# ----------------------------------------------------------------------


@router.get("/attachments/{attachment_id}")
async def get_attachment(
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).get_attachment(
        viewer=member, workspace_id=workspace_id, attachment_id=parsed
    )


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).delete_attachment(
        actor=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        **_client_meta(request),
    )


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    request: Request,
    response: Response,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    await _rate_limit(request, caller, response, "attachments-download", DOWNLOAD_LIMIT)
    return await _service(request).download_attachment(
        viewer=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        **_client_meta(request),
    )


@router.get("/attachments/{attachment_id}/thumbnail")
async def get_thumbnail(
    request: Request,
    attachment_id: str,
    size: str = Query(default="md"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).thumbnail_url(
        viewer=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        size=size,
    )


# ----------------------------------------------------------------------
# host listings (§3.1)
# ----------------------------------------------------------------------


async def _list_host_attachments(
    request: Request,
    session: AsyncSession,
    linked_type: str,
    linked_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
) -> dict:
    service = _service(request)
    workspace_id = await service.resolve_host_workspace(session, linked_type, linked_id)
    if workspace_id is None:
        raise NotFoundError(f"{linked_type} not found")
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, workspace_id)
    items, next_cursor = await service.list_for_host(
        viewer=member,
        workspace_id=workspace_id,
        linked_type=linked_type,
        linked_id=linked_id,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.get("/issues/{issue_id}/attachments")
async def list_issue_attachments(
    request: Request,
    issue_id: str,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _list_host_attachments(
        request, session, "issue", _path_uuid(issue_id, message=_ISSUE_NOT_FOUND), limit, cursor
    )


@router.get("/comments/{comment_id}/attachments")
async def list_comment_attachments(
    request: Request,
    comment_id: str,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _list_host_attachments(
        request, session, "comment", _path_uuid(comment_id, message=_COMMENT_NOT_FOUND), limit, cursor
    )


# ----------------------------------------------------------------------
# multipart (§2.5 / §3.1)
# ----------------------------------------------------------------------


@router.post("/multipart/{attachment_id}/parts")
async def request_multipart_parts(
    body: MultipartPartsBody,
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).request_multipart_parts(
        actor=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        part_numbers=body.part_numbers,
    )


@router.post("/multipart/{attachment_id}/complete")
async def complete_multipart(
    body: MultipartCompleteBody,
    request: Request,
    attachment_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(attachment_id)
    caller, member, workspace_id = await _resolve_attachment_context(
        request, session, parsed
    )
    return await _service(request).complete_multipart(
        actor=member,
        workspace_id=workspace_id,
        attachment_id=parsed,
        parts=[part.model_dump() for part in body.parts],
        content_hash=body.content_hash,
        **_client_meta(request),
    )
