"""Squad HTTP routes (squad.md §3.1–§3.5).

Middleware chain per README §6.14 (Bearer → membership → RBAC → rate limit).
Workspace-level RBAC gates the broad action class; squad-level authorization
(membership / observer / admin) is enforced in the helpers below. Leader
decompose / dispatch endpoints carry a dedicated rate bucket to stop a leader
high-frequency spamming (§3.4 / §5.3).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.member import Member
from mesh.db.models.squad import SquadMember
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, NotFoundError
from mesh.runtime.approvals import decide_approval
from mesh.squad import schemas
from mesh.squad.service import SquadService
from mesh.squad.sse import task_stream_response
from mesh.squad.tasks import SquadTaskService

router = APIRouter(prefix="/api/v1", tags=["squad"])

# auth.md §3.6 general write class + a tighter leader-orchestration bucket.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60
ORCH_LIMIT = 60
ORCH_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _squad_service(request: Request) -> SquadService:
    return request.app.state.squad_service


def _task_service(request: Request) -> SquadTaskService:
    return request.app.state.squad_task_service


def _path_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError("resource not found") from exc


def _body_uuid(value: str, field: str) -> uuid.UUID:
    from mesh.errors import ValidationError

    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValidationError("invalid id", details={field: value[:64]}) from exc


async def _rate_limit(
    request: Request, user: User, response: Response, bucket: str, limit: int, window: int
) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"squad-{bucket}:{user.id}:{_client_ip(request)}", limit=limit, window_seconds=window
    )
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _assert_squad_read(
    session_factory, *, workspace_id: uuid.UUID, squad_id: uuid.UUID, member: Member
) -> None:
    """Squad detail reads: workspace admin/owner, or an active squad member."""
    if member.role in ("admin", "owner"):
        return
    async with session_factory() as session:
        from mesh.db.tenant import set_tenant_context

        await set_tenant_context(session, workspace_id)
        on_squad = await session.scalar(
            select(SquadMember.id).where(
                SquadMember.squad_id == squad_id,
                SquadMember.member_id == member.id,
                SquadMember.left_at.is_(None),
            )
        )
    if on_squad is None:
        raise ForbiddenError("not a member of this squad")


def _sf(request: Request):
    return request.app.state.session_factory


# -- squad CRUD ---------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/squads")
async def list_squads(
    request: Request,
    status: str | None = None,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 30,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    result = await _squad_service(request).list_squads(
        workspace_id=context.workspace.id, status=status, kind=kind, q=q, limit=limit, cursor=cursor
    )
    return result


@router.post("/workspaces/{workspace_id}/squads", status_code=201)
async def create_squad(
    request: Request,
    response: Response,
    body: schemas.CreateSquadRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    data = await _squad_service(request).create_squad(
        actor=context.member, workspace_id=context.workspace.id, body=body
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/squads/assignments/by-issue/{issue_id}")
async def get_issue_assignment(
    request: Request,
    issue_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    """The active squad assignment carrying an issue (§2.5 / §4.3-2) — powers
    the issue header's single-responsibility badge. Declared before the
    ``{squad_id}`` routes so the static path wins."""
    data = await _squad_service(request).get_issue_assignment(
        workspace_id=context.workspace.id, issue_id=_path_uuid(issue_id)
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/squads/{squad_id}")
async def get_squad(
    request: Request,
    squad_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return {"data": await _squad_service(request).get_squad(workspace_id=context.workspace.id, squad_id=sid)}


@router.patch("/workspaces/{workspace_id}/squads/{squad_id}")
async def update_squad(
    request: Request,
    response: Response,
    squad_id: str,
    body: schemas.UpdateSquadRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    data = await _squad_service(request).update_squad(
        actor=context.member,
        workspace_id=context.workspace.id,
        squad_id=_path_uuid(squad_id),
        body=body,
    )
    return {"data": data}


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/archive")
async def archive_squad(
    request: Request,
    response: Response,
    squad_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).archive_squad(
            actor=context.member, workspace_id=context.workspace.id, squad_id=_path_uuid(squad_id)
        )
    }


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/restore")
async def restore_squad(
    request: Request,
    response: Response,
    squad_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).restore_squad(
            actor=context.member, workspace_id=context.workspace.id, squad_id=_path_uuid(squad_id)
        )
    }


# -- membership ---------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/members")
async def list_members(
    request: Request,
    squad_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return await _squad_service(request).list_members(workspace_id=context.workspace.id, squad_id=sid)


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/members")
async def add_members(
    request: Request,
    response: Response,
    squad_id: str,
    body: schemas.AddMembersRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).add_members(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            body=body,
        )
    }


@router.patch("/workspaces/{workspace_id}/squads/{squad_id}/members/{member_id}")
async def change_role(
    request: Request,
    response: Response,
    squad_id: str,
    member_id: str,
    body: schemas.ChangeRoleRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).change_role(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            member_id=_path_uuid(member_id),
            role=body.role,
        )
    }


@router.delete("/workspaces/{workspace_id}/squads/{squad_id}/members/{member_id}")
async def remove_member(
    request: Request,
    response: Response,
    squad_id: str,
    member_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("workspace:manage_members")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).remove_member(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            member_id=_path_uuid(member_id),
        )
    }


# -- tasks / orchestration ----------------------------------------------------


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks", status_code=202)
async def assign_task(
    request: Request,
    response: Response,
    squad_id: str,
    body: schemas.AssignTaskRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    data = await _task_service(request).assign_issue_to_squad(
        actor=context.member,
        workspace_id=context.workspace.id,
        squad_id=_path_uuid(squad_id),
        body=body,
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/tasks")
async def list_tasks(
    request: Request,
    squad_id: str,
    status: str | None = None,
    limit: int = 50,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    from sqlalchemy import select as _select

    from mesh.db.models.squad import SquadTask
    from mesh.db.tenant import set_tenant_context

    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    async with _sf(request)() as session:
        await set_tenant_context(session, context.workspace.id)
        stmt = (
            _select(SquadTask)
            .where(SquadTask.workspace_id == context.workspace.id, SquadTask.squad_id == sid)
            .order_by(SquadTask.created_at.desc())
        )
        if status:
            stmt = stmt.where(SquadTask.status == status)
        rows = list((await session.execute(stmt.limit(limit))).scalars())
        svc = _task_service(request)
        data = [await svc._render_task(session, r) for r in rows]
    return {"data": data, "next_cursor": None}


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}")
async def get_task(
    request: Request,
    squad_id: str,
    task_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return {
        "data": await _task_service(request).get_task(
            workspace_id=context.workspace.id, squad_id=sid, task_id=_path_uuid(task_id)
        )
    }


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/tree")
async def get_tree(
    request: Request,
    squad_id: str,
    task_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return {
        "data": await _task_service(request).get_tree(
            workspace_id=context.workspace.id, squad_id=sid, task_id=_path_uuid(task_id)
        )
    }


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/status")
async def get_task_status(
    request: Request,
    squad_id: str,
    task_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return {
        "data": await _task_service(request).get_status(
            workspace_id=context.workspace.id, squad_id=sid, task_id=_path_uuid(task_id)
        )
    }


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/stream")
async def stream_task(
    request: Request,
    squad_id: str,
    task_id: str,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):

    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    last_event_id = 0
    raw = request.headers.get("last-event-id")
    if raw and raw.isdigit():
        last_event_id = int(raw)
    return task_stream_response(
        _sf(request),
        workspace_id=context.workspace.id,
        squad_id=sid,
        task_id=_path_uuid(task_id),
        last_event_id=last_event_id,
    )


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/subtasks", status_code=201)
async def create_subtasks(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    body: schemas.CreateSubtasksRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("agent:trigger")),
):
    await _rate_limit(request, user, response, "orch", ORCH_LIMIT, ORCH_WINDOW_SECONDS)
    return {
        "data": await _task_service(request).create_subtasks(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            task_id=_path_uuid(task_id),
            body=body,
        )
    }


async def _plan_decide(
    request: Request, context: WorkspaceContext, squad_id: str, task_id: str, approve: bool, body
) -> dict:
    """Thin wrapper over the unified approvals entry (squad.md §6.10)."""
    from mesh.db.models.runtime import Approval
    from mesh.db.tenant import set_tenant_context

    tid = _path_uuid(task_id)
    async with _sf(request)() as session:
        await set_tenant_context(session, context.workspace.id)
        approval = await session.scalar(
            select(Approval).where(
                Approval.workspace_id == context.workspace.id,
                Approval.subject_type == "squad_plan",
                Approval.subject_task_id == tid,
                Approval.status == "pending",
            )
        )
    if approval is None:
        from mesh.errors import BusinessRuleError

        raise BusinessRuleError("no pending plan approval for this task", code="approval_expired")
    result = await decide_approval(
        _sf(request),
        approval_id=approval.id,
        workspace_id=context.workspace.id,
        member=context.member,
        approve=approve,
        comment=body.comment if body else None,
    )
    return {"data": result}


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/plan/approve")
async def approve_plan(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    body: schemas.PlanDecisionRequest | None = None,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return await _plan_decide(request, context, squad_id, task_id, True, body)


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/plan/reject")
async def reject_plan(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    body: schemas.PlanDecisionRequest | None = None,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return await _plan_decide(request, context, squad_id, task_id, False, body)


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/dispatch")
async def dispatch_task(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("agent:trigger")),
):
    await _rate_limit(request, user, response, "orch", ORCH_LIMIT, ORCH_WINDOW_SECONDS)
    return {
        "data": await _task_service(request).dispatch_task(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            task_id=_path_uuid(task_id),
        )
    }


@router.patch("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/status")
async def move_task_status(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    body: schemas.TaskStatusUpdateRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    """Manual kanban move (§4.2): humans change a subtask's status; the
    server validates the transition (§4.4 → 409 on illegal)."""
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _task_service(request).move_task_status(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            task_id=_path_uuid(task_id),
            status=body.status,
            result_summary=body.result_summary,
        )
    }


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/tasks/{task_id}/cancel")
async def cancel_task(
    request: Request,
    response: Response,
    squad_id: str,
    task_id: str,
    body: schemas.CancelRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _task_service(request).cancel_task(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            task_id=_path_uuid(task_id),
            reason=body.reason,
        )
    }


# -- messages / activity ------------------------------------------------------


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/messages")
async def list_messages(
    request: Request,
    squad_id: str,
    task_id: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return await _squad_service(request).list_messages(
        workspace_id=context.workspace.id,
        squad_id=sid,
        task_id=_path_uuid(task_id) if task_id else None,
        kind=kind,
        limit=limit,
        cursor=cursor,
    )


@router.post("/workspaces/{workspace_id}/squads/{squad_id}/messages", status_code=201)
async def send_message(
    request: Request,
    response: Response,
    squad_id: str,
    body: schemas.SendMessageRequest,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("issue:write")),
):
    await _rate_limit(request, user, response, "write", WRITE_LIMIT, WRITE_WINDOW_SECONDS)
    return {
        "data": await _squad_service(request).send_message(
            actor=context.member,
            workspace_id=context.workspace.id,
            squad_id=_path_uuid(squad_id),
            body=body,
        )
    }


@router.get("/workspaces/{workspace_id}/squads/{squad_id}/activity")
async def list_activity(
    request: Request,
    squad_id: str,
    task_id: str | None = None,
    action: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace("issue:read")),
):
    sid = _path_uuid(squad_id)
    await _assert_squad_read(
        _sf(request), workspace_id=context.workspace.id, squad_id=sid, member=context.member
    )
    return await _squad_service(request).list_activity(
        workspace_id=context.workspace.id,
        squad_id=sid,
        task_id=_path_uuid(task_id) if task_id else None,
        action=action,
        limit=limit,
        cursor=cursor,
    )


__all__ = ["router"]
