"""Issue-association routes (label-property.md §3.1, MES-32 remainder).

Workspace-less paths: the tenant is resolved through the same narrow
SECURITY DEFINER lookups the issue / label modules use (migrations 0008 /
0009), then the standard membership gate runs. Resource-level authorization
(issue read/write gates, label scope management) lives in the services.

Endpoints: issue label list / add / remove / whole-set replace, label merge,
and the per-issue custom-field-value read / whole-form write (§3.2).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, resolve_workspace_context
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.labels.schemas import (
    CustomFieldValueInput,
    MergeLabelRequest,
    ReplaceIssueLabelsRequest,
    SetCustomFieldValuesRequest,
)

router = APIRouter(prefix="/api/v1", tags=["labels"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_ISSUE_NOT_FOUND = "issue not found"
_LABEL_NOT_FOUND = "label not found"


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": request.client.host if request.client is not None else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def _rate_limit_write(request: Request, user: User, response: Response, bucket: str) -> None:
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"{bucket}:{user.id}:{client_ip}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(raw: str, *, message: str) -> uuid.UUID:
    """Path ids that are not UUIDs are 404s (never leak id shape, §5.3)."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _body_uuid(raw: str, *, field: str, index: int | None = None) -> uuid.UUID:
    details: dict = {field: raw[:64]}
    if index is not None:
        details["index"] = index
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details=details) from exc


async def _context_for(
    session: AsyncSession,
    user: User,
    workspace_id: uuid.UUID,
    *,
    not_found_message: str,
) -> WorkspaceContext:
    """Run the membership gate for a workspace-less path (workspace.md §5.3).

    The resolver already proved the resource exists SOMEWHERE; if the caller
    is not a member of that workspace the gate raises "workspace not found".
    That message differs from the "<resource> not found" an unknown id gets —
    a two-message existence oracle for arbitrary UUIDs. Rewriting the gate
    404 to the resource message makes the two cases indistinguishable; no
    content leaks either way (the service-layer read gate still runs).
    """
    try:
        return await resolve_workspace_context(
            session, user=user, workspace_id=workspace_id, permission=None
        )
    except NotFoundError as exc:
        raise NotFoundError(not_found_message) from exc


async def _issue_context(
    request: Request, user: User, session: AsyncSession, issue_id: str
) -> tuple[WorkspaceContext, uuid.UUID]:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    workspace_id = await request.app.state.issue_service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND)
    return context, parsed


def _value_entry(entry: CustomFieldValueInput) -> dict:
    """Keep only the keys the client actually sent (presence is semantics)."""
    return {key: getattr(entry, key) for key in entry.model_fields_set}


# ----------------------------------------------------------------------
# issue ↔ labels
# ----------------------------------------------------------------------


@router.get("/issues/{issue_id}/labels")
async def list_issue_labels(
    request: Request,
    issue_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context, parsed = await _issue_context(request, user, session, issue_id)
    items = await request.app.state.issue_label_service.list_issue_labels(
        viewer=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
    )
    return {"data": items}


@router.put("/issues/{issue_id}/labels")
async def replace_issue_labels(
    body: ReplaceIssueLabelsRequest,
    request: Request,
    response: Response,
    issue_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-labels-write")
    context, parsed = await _issue_context(request, user, session, issue_id)
    label_ids = [
        _body_uuid(raw, field="label_ids", index=index)
        for index, raw in enumerate(body.label_ids)
    ]
    data = await request.app.state.issue_label_service.replace_labels(
        actor=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
        label_ids=label_ids,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.post("/issues/{issue_id}/labels/{label_id}")
async def add_issue_label(
    request: Request,
    response: Response,
    issue_id: str,
    label_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-labels-write")
    context, parsed = await _issue_context(request, user, session, issue_id)
    parsed_label = _path_uuid(label_id, message=_LABEL_NOT_FOUND)
    data = await request.app.state.issue_label_service.add_label(
        actor=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
        label_id=parsed_label,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/issues/{issue_id}/labels/{label_id}")
async def remove_issue_label(
    request: Request,
    response: Response,
    issue_id: str,
    label_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-labels-write")
    context, parsed = await _issue_context(request, user, session, issue_id)
    parsed_label = _path_uuid(label_id, message=_LABEL_NOT_FOUND)
    data = await request.app.state.issue_label_service.remove_label(
        actor=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
        label_id=parsed_label,
        **_client_meta(request),
    )
    return {"data": data}


@router.post("/labels/{label_id}/merge")
async def merge_label(
    body: MergeLabelRequest,
    request: Request,
    response: Response,
    label_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "labels-write")
    parsed = _path_uuid(label_id, message=_LABEL_NOT_FOUND)
    service = request.app.state.label_service
    workspace_id = await service.resolve_label_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_LABEL_NOT_FOUND)
    context = await _context_for(session, user, workspace_id, not_found_message=_LABEL_NOT_FOUND)
    data = await service.merge_label(
        actor=context.member,
        workspace_id=workspace_id,
        source_label_id=parsed,
        target_label_id=_body_uuid(body.target_label_id, field="target_label_id"),
        **_client_meta(request),
    )
    return {"data": data}


# ----------------------------------------------------------------------
# issue custom-field values
# ----------------------------------------------------------------------


@router.get("/issues/{issue_id}/custom-field-values")
async def list_custom_field_values(
    request: Request,
    issue_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    context, parsed = await _issue_context(request, user, session, issue_id)
    items = await request.app.state.field_value_service.list_values(
        viewer=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
    )
    return {"data": items}


@router.put("/issues/{issue_id}/custom-field-values")
async def set_custom_field_values(
    body: SetCustomFieldValuesRequest,
    request: Request,
    response: Response,
    issue_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-fields-write")
    context, parsed = await _issue_context(request, user, session, issue_id)
    data = await request.app.state.field_value_service.set_values(
        actor=context.member,
        workspace_id=context.workspace.id,
        issue_id=parsed,
        values=[_value_entry(entry) for entry in body.values],
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}
