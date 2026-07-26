"""Issue module routes (issue.md §3.1, README §6.14 envelopes).

Workspace-scoped paths use the ``require_workspace`` membership gate;
workspace-less paths (``/issues/{id}``, ``/statuses/{id}``,
``/issue-templates/{id}``, ``/issues/bulk``) resolve the tenant workspace
through the narrow SECURITY DEFINER lookups (migration 0009) and then run the
same membership gate. Resource-level authorization (project visibility,
project write gates) lives in the service layer.
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace, resolve_workspace_context
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.issue.schemas import (
    BulkRequest,
    CreateDependencyRequest,
    CreateIssueRequest,
    CreateIssueTemplateRequest,
    CreateStatusRequest,
    InstantiateIssueTemplateRequest,
    MovePreviewRequest,
    MoveRequest,
    UpdateIssueRequest,
    UpdateIssueTemplateRequest,
    UpdateStatusRequest,
)
from mesh.issue.service import UNSET, IssuePatch, IssueService
from mesh.issue.statuses import StatusPatch

router = APIRouter(prefix="/api/v1", tags=["issue"])

WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_ISSUE_NOT_FOUND = "issue not found"
_STATUS_NOT_FOUND = "issue status not found"
_TEMPLATE_NOT_FOUND = "issue template not found"


def _issues(request: Request) -> IssueService:
    return request.app.state.issue_service


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
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _query_uuid(raw: str | None, *, field: str) -> uuid.UUID | None:
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


def _body_uuid(raw: str | None, *, field: str) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: raw[:64]}) from exc


def _tri(value, present: bool):
    return value if present else UNSET


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
    That message differs from the "resource not found" an unknown id gets —
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


# ----------------------------------------------------------------------
# issues CRUD
# ----------------------------------------------------------------------


@router.post("/workspaces/{workspace_id}/issues", status_code=201)
async def create_issue(
    body: CreateIssueRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    body.project_id = body.project_id  # keep mypy calm about mutation
    created = await _issues(request).create_issue(
        actor=context.member,
        workspace_id=context.workspace.id,
        body=body,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/issues")
async def list_issues(
    request: Request,
    status: str | None = Query(default=None, alias="status"),
    state_category: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    assignee_id: str | None = Query(default=None),
    reporter_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    cycle_id: str | None = Query(default=None),
    milestone_id: str | None = Query(default=None),
    parent_id: str | None = Query(default=None),
    due_before: str | None = Query(default=None),
    due_after: str | None = Query(default=None),
    q: str | None = Query(default=None),
    filters: str | None = Query(default=None),
    sort: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    group_by: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    parsed_filters = None
    if filters is not None:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                "filters must be a JSON object", details={"filters": filters[:64]}
            ) from exc

    def _date(raw: str | None, field: str):
        if raw is None:
            return None
        from datetime import date as _date

        try:
            return _date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(f"invalid {field}", details={field: raw[:32]}) from exc

    result = await _issues(request).list_issues(
        viewer=context.member,
        workspace_id=context.workspace.id,
        status_id=_query_uuid(status, field="status"),
        state_category=state_category,
        priority=priority,
        assignee_id=_query_uuid(assignee_id, field="assignee_id"),
        reporter_id=_query_uuid(reporter_id, field="reporter_id"),
        project_id=_query_uuid(project_id, field="project_id"),
        cycle_id=_query_uuid(cycle_id, field="cycle_id"),
        milestone_id=_query_uuid(milestone_id, field="milestone_id"),
        parent_id=_query_uuid(parent_id, field="parent_id"),
        due_before=_date(due_before, "due_before"),
        due_after=_date(due_after, "due_after"),
        q=q,
        filters=parsed_filters,
        sort=sort,
        order=order,
        group_by=group_by,
        limit=limit,
        cursor=cursor,
    )
    return result


@router.get("/workspaces/{workspace_id}/issues/by-identifier/{identifier}")
async def get_issue_by_identifier(
    identifier: str,
    request: Request,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    data = await _issues(request).get_issue_by_identifier(
        viewer=context.member,
        workspace_id=context.workspace.id,
        identifier=identifier,
    )
    return {"data": data}


@router.get("/issues/{issue_id}")
async def get_issue(
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = _issues(request)
    workspace_id = await service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.get_issue(
        viewer=context.member, workspace_id=workspace_id, issue_id=parsed
    )
    return {"data": data}


def _issue_patch_from(body: UpdateIssueRequest) -> tuple[IssuePatch, int | None]:
    fields = body.model_fields_set
    patch = IssuePatch(
        title=_tri(body.title, "title" in fields),
        description=_tri(body.description, "description" in fields),
        status_id=_tri(
            _body_uuid(body.status_id, field="status_id"), "status_id" in fields
        ),
        priority=_tri(body.priority, "priority" in fields),
        assignee_id=_tri(
            _body_uuid(body.assignee_id, field="assignee_id"),
            "assignee_id" in fields,
        ),
        reporter_id=_tri(
            _body_uuid(body.reporter_id, field="reporter_id"),
            "reporter_id" in fields,
        ),
        estimate=_tri(body.estimate, "estimate" in fields),
        estimate_unit=_tri(body.estimate_unit, "estimate_unit" in fields),
        due_date=_tri(body.due_date, "due_date" in fields),
        start_date=_tri(body.start_date, "start_date" in fields),
        milestone_id=_tri(
            _body_uuid(body.milestone_id, field="milestone_id"),
            "milestone_id" in fields,
        ),
        cycle_id=_tri(_body_uuid(body.cycle_id, field="cycle_id"), "cycle_id" in fields),
        parent_id=_tri(_body_uuid(body.parent_id, field="parent_id"), "parent_id" in fields),
        position=_tri(body.position, "position" in fields),
    )
    return patch, body.version if "version" in fields else None


@router.patch("/issues/{issue_id}")
async def update_issue(
    body: UpdateIssueRequest,
    request: Request,
    response: Response,
    issue_id: str,
    if_match: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = _issues(request)
    workspace_id = await service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    patch, version = _issue_patch_from(body)
    data = await service.update_issue(
        actor=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        patch=patch,
        expected_version=version,
        if_match=if_match,
        **_client_meta(request),
    )
    return {"data": data}


@router.delete("/issues/{issue_id}")
async def delete_issue(
    issue_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = _issues(request)
    workspace_id = await service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.delete_issue(
        actor=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        **_client_meta(request),
    )
    return {"data": data}


@router.get("/issues/{issue_id}/children")
async def list_children(
    issue_id: str,
    request: Request,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = _issues(request)
    workspace_id = await service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    items, next_cursor = await service.list_children(
        viewer=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.get("/issues/{issue_id}/activity")
async def list_activity(
    issue_id: str,
    request: Request,
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = _issues(request)
    workspace_id = await service.resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    items, next_cursor = await service.list_activity(
        viewer=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


# ----------------------------------------------------------------------
# dependencies
# ----------------------------------------------------------------------


@router.get("/issues/{issue_id}/dependencies")
async def list_dependencies(
    issue_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = request.app.state.dependency_service
    workspace_id = await _issues(request).resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    items = await service.list_dependencies(
        viewer=context.member, workspace_id=workspace_id, issue_id=parsed
    )
    return {"data": items}


@router.post("/issues/{issue_id}/dependencies", status_code=201)
async def add_dependency(
    body: CreateDependencyRequest,
    request: Request,
    response: Response,
    issue_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = request.app.state.dependency_service
    workspace_id = await _issues(request).resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    depends_on = _body_uuid(body.depends_on_id, field="depends_on_id")
    data = await service.add_dependency(
        actor=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        depends_on_id=depends_on,
        dep_type=body.type,
    )
    return {"data": data}


@router.delete("/issues/{issue_id}/dependencies/{dependency_id}")
async def remove_dependency(
    issue_id: str,
    dependency_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    dep_parsed = _path_uuid(dependency_id, message="dependency not found")
    service = request.app.state.dependency_service
    workspace_id = await _issues(request).resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.remove_dependency(
        actor=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        dependency_id=dep_parsed,
    )
    return {"data": data}


# ----------------------------------------------------------------------
# cross-project move (§3.8 two-step contract)
# ----------------------------------------------------------------------


@router.post("/issues/{issue_id}/move-preview")
async def move_preview(
    body: MovePreviewRequest,
    request: Request,
    issue_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = request.app.state.move_service
    workspace_id = await _issues(request).resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.preview(
        viewer=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        target_project_id=_body_uuid(body.target_project_id, field="target_project_id"),
    )
    return {"data": data}


@router.post("/issues/{issue_id}/move")
async def move_issue(
    body: MoveRequest,
    request: Request,
    response: Response,
    issue_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-write")
    parsed = _path_uuid(issue_id, message=_ISSUE_NOT_FOUND)
    service = request.app.state.move_service
    workspace_id = await _issues(request).resolve_issue_workspace(parsed)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.move(
        actor=context.member,
        workspace_id=workspace_id,
        issue_id=parsed,
        target_project_id=_body_uuid(body.target_project_id, field="target_project_id"),
        confirm=body.confirm,
        expected_version=body.version,
    )
    return {"data": data}


# ----------------------------------------------------------------------
# bulk (§1.2.5 / §5.5)
# ----------------------------------------------------------------------


@router.post("/issues/bulk")
async def bulk_issues(
    body: BulkRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "issue-bulk")
    service = request.app.state.bulk_service
    first = _body_uuid(body.issue_ids[0], field="issue_ids")
    workspace_id = await _issues(request).resolve_issue_workspace(first)
    if workspace_id is None:
        raise NotFoundError(_ISSUE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_ISSUE_NOT_FOUND
    )
    data = await service.execute(
        actor=context.member, workspace_id=workspace_id, body=body
    )
    return {"data": data}


# ----------------------------------------------------------------------
# statuses (§1.2.3 / §3.1)
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/statuses")
async def list_statuses(
    request: Request,
    project_id: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items = await request.app.state.status_service.list_statuses(
        workspace_id=context.workspace.id,
        project_id=_query_uuid(project_id, field="project_id"),
    )
    return {"data": items}


@router.post("/workspaces/{workspace_id}/statuses", status_code=201)
async def create_status(
    body: CreateStatusRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response, "status-write")
    data = await request.app.state.status_service.create_status(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        category=body.category,
        color=body.color,
        position=body.position,
        is_default=body.is_default,
        project_id=_body_uuid(body.project_id, field="project_id"),
        allowed_transitions=body.allowed_transitions,
    )
    return {"data": data}


@router.patch("/statuses/{status_id}")
async def update_status(
    body: UpdateStatusRequest,
    request: Request,
    response: Response,
    status_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "status-write")
    parsed = _path_uuid(status_id, message=_STATUS_NOT_FOUND)
    service = request.app.state.status_service
    async with request.app.state.session_factory() as lookup:
        from sqlalchemy import text as _text

        workspace_id = await lookup.scalar(
            _text("SELECT mesh_issue_status_workspace_id(:id)"), {"id": parsed}
        )
    if workspace_id is None:
        raise NotFoundError(_STATUS_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_STATUS_NOT_FOUND
    )
    fields = body.model_fields_set
    patch = StatusPatch(
        name=_tri(body.name, "name" in fields),
        color=_tri(body.color, "color" in fields),
        position=_tri(body.position, "position" in fields),
        category=_tri(body.category, "category" in fields),
        is_default=_tri(body.is_default, "is_default" in fields),
        allowed_transitions=_tri(body.allowed_transitions, "allowed_transitions" in fields),
    )
    from mesh.issue.service import _Unset

    data = await service.update_status(
        actor=context.member,
        workspace_id=workspace_id,
        status_id=parsed,
        patch=patch,
        is_unset=lambda value: isinstance(value, _Unset),
    )
    return {"data": data}


@router.delete("/statuses/{status_id}")
async def delete_status(
    status_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "status-write")
    parsed = _path_uuid(status_id, message=_STATUS_NOT_FOUND)
    service = request.app.state.status_service
    async with request.app.state.session_factory() as lookup:
        from sqlalchemy import text as _text

        workspace_id = await lookup.scalar(
            _text("SELECT mesh_issue_status_workspace_id(:id)"), {"id": parsed}
        )
    if workspace_id is None:
        raise NotFoundError(_STATUS_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_STATUS_NOT_FOUND
    )
    data = await service.delete_status(
        actor=context.member, workspace_id=workspace_id, status_id=parsed
    )
    return {"data": data}


# ----------------------------------------------------------------------
# templates (§3.9)
# ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/issue-templates")
async def list_templates(
    request: Request,
    project_id: str | None = Query(default=None),
    limit: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    items, next_cursor = await request.app.state.template_service.list_templates(
        viewer=context.member,
        workspace_id=context.workspace.id,
        project_id=_query_uuid(project_id, field="project_id"),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/issue-templates", status_code=201)
async def create_template(
    body: CreateIssueTemplateRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response, "template-write")
    data = await request.app.state.template_service.create_template(
        actor=context.member, workspace_id=context.workspace.id, body=body
    )
    return {"data": data}


@router.patch("/issue-templates/{template_id}")
async def update_template(
    body: UpdateIssueTemplateRequest,
    request: Request,
    response: Response,
    template_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "template-write")
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = request.app.state.template_service
    async with request.app.state.session_factory() as lookup:
        from sqlalchemy import text as _text

        workspace_id = await lookup.scalar(
            _text("SELECT mesh_issue_template_workspace_id(:id)"), {"id": parsed}
        )
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.update_template(
        actor=context.member, workspace_id=workspace_id, template_id=parsed, body=body
    )
    return {"data": data}


@router.delete("/issue-templates/{template_id}")
async def delete_template(
    template_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "template-write")
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = request.app.state.template_service
    async with request.app.state.session_factory() as lookup:
        from sqlalchemy import text as _text

        workspace_id = await lookup.scalar(
            _text("SELECT mesh_issue_template_workspace_id(:id)"), {"id": parsed}
        )
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.delete_template(
        actor=context.member, workspace_id=workspace_id, template_id=parsed
    )
    return {"data": data}


@router.post("/issue-templates/{template_id}/instantiate", status_code=201)
async def instantiate_template(
    body: InstantiateIssueTemplateRequest,
    request: Request,
    response: Response,
    template_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_write(request, user, response, "template-write")
    parsed = _path_uuid(template_id, message=_TEMPLATE_NOT_FOUND)
    service = request.app.state.template_service
    async with request.app.state.session_factory() as lookup:
        from sqlalchemy import text as _text

        workspace_id = await lookup.scalar(
            _text("SELECT mesh_issue_template_workspace_id(:id)"), {"id": parsed}
        )
    if workspace_id is None:
        raise NotFoundError(_TEMPLATE_NOT_FOUND)
    context = await _context_for(
        session, user, workspace_id, not_found_message=_TEMPLATE_NOT_FOUND
    )
    data = await service.instantiate(
        actor=context.member,
        workspace_id=workspace_id,
        template_id=parsed,
        body=body,
        **_client_meta(request),
    )
    return {"data": data}
