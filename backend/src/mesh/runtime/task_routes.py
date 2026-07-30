"""Task principal API routes — §2.2 S-05 / auth.md §2.5.1.

Namespace ``/api/v1/task/`` — endpoints that accept ``mesh_task_`` tokens
via the ``resolve_task_principal`` dependency. These are the routes the
daemon's task broker calls on behalf of an attempt:

- Read current issue/project context
- Write result comment on the current issue
- Read execution status
- Current squad task operations (§2.2 S-05): a squad leader's orchestrator
  attempt reads the squad roster and submits its decomposition through
  ``squad.subtasks`` — the runtime acting for the leader with a SHORT-LIVED
  task token (squad.md §5.3), never a long-lived PAT.

Regular console routes reject ``mesh_task_`` tokens (auth.md §2.5.1:
only routes that explicitly declare task principal support accept them).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from mesh.db.models.runtime import AttemptTaskToken
from mesh.errors import ForbiddenError, NotFoundError, UnauthorizedError
from mesh.runtime.daemon_auth import resolve_task_principal

router = APIRouter(prefix="/api/v1/task", tags=["task-principal"])

TASK_LIMIT = 120
TASK_WINDOW_SECONDS = 60


async def _rate_limit_task(request: Request, task_token: AttemptTaskToken) -> None:
    """§2.2 S-05: token + attempt dual-dimension rate limiting."""
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else None
    await limiter.check(
        f"task:{task_token.id}:{client_ip}",
        limit=TASK_LIMIT,
        window_seconds=TASK_WINDOW_SECONDS,
    )


@router.get("/context")
async def get_task_context(
    request: Request,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read the current attempt's frozen context (§2.2 S-05).

    Returns the execution/attempt/issue identifiers and frozen scopes
    from the task token. The task sandbox uses this to know what
    resources it can access.
    """
    await _rate_limit_task(request, task_token)
    scopes = task_token.scopes or {}
    return {
        "data": {
            "attempt_id": str(task_token.attempt_id),
            "workspace_id": str(task_token.workspace_id),
            "issue_id": scopes.get("issue_id"),
            "agent_id": scopes.get("agent_id"),
            "methods": scopes.get("methods", []),
            "denied": scopes.get("denied", []),
            "expires_at": task_token.expires_at.isoformat(),
        }
    }


@router.get("/executions/{execution_id}")
async def get_task_execution(
    request: Request,
    execution_id: str,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read execution status — scoped to the token's attempt (§2.2 S-05)."""
    from sqlalchemy import select

    from mesh.db.models.runtime import TaskExecution
    from mesh.db.tenant import set_tenant_context

    await _rate_limit_task(request, task_token)
    try:
        exec_uuid = uuid.UUID(execution_id)
    except ValueError as exc:
        raise NotFoundError("execution not found") from exc

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        execution = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.id == exec_uuid,
                    TaskExecution.workspace_id == task_token.workspace_id,
                )
            )
        ).scalar_one_or_none()
    if execution is None:
        raise NotFoundError("execution not found")
    return {
        "data": {
            "id": str(execution.id),
            "status": execution.status,
            "trigger": execution.trigger,
            "issue_id": str(execution.issue_id) if execution.issue_id else None,
        }
    }


# -- current squad task operations (§2.2 S-05, squad.md §5.3) ------------------


class TaskSubtaskInput(BaseModel):
    """One decomposition entry submitted by a leader's orchestrator attempt.

    ``assignee_member_id`` names a SQUAD MEMBER (members.id); the service
    re-validates membership, depth and cycles (squad.md §3.4)."""

    title: str = Field(min_length=1)
    assignee_member_id: str | None = None
    stage: int | None = None
    depends_on: list[str] = Field(default_factory=list)


class TaskSubtasksRequest(BaseModel):
    plan_markdown: str | None = None
    # Server-side cap mirrors the daemon broker's _SUBTASKS_MAX (defense in
    # depth: the L1 security review's max_length=16 — a single LLM-driven
    # decomposition may never fan out unboundedly).
    subtasks: list[TaskSubtaskInput] = Field(min_length=1, max_length=16)


def _require_method(task_token: AttemptTaskToken, method: str) -> None:
    """Task-token scope gate — same semantics as ``validate_task_token``'s
    ``required_scope``: denied list wins, then allow-list membership."""
    scopes = task_token.scopes or {}
    methods = scopes.get("methods", [])
    denied = scopes.get("denied", [])
    if method in denied or method not in methods:
        raise UnauthorizedError("scope not permitted")


async def _load_squad_task_for_token(request: Request, task_token: AttemptTaskToken):
    """Resolve the frozen squad task this attempt belongs to.

    Correlation is the execution's frozen ``task_spec.squad_task_id``
    (squad.md §4.4 — same key the terminal observer uses). Returns
    ``(task, squad_role)``; raises 404 when the attempt's execution is not a
    squad-spawned one or the task is gone (fail-closed: a task token can
    never operate on a squad task its own execution does not belong to).
    """
    from sqlalchemy import select

    from mesh.db.models.runtime import ExecutionAttempt, TaskExecution
    from mesh.db.models.squad import SquadTask
    from mesh.db.tenant import set_tenant_context

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        attempt = (
            await session.execute(
                select(ExecutionAttempt).where(ExecutionAttempt.id == task_token.attempt_id)
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise UnauthorizedError("attempt not found")
        execution = (
            await session.execute(
                select(TaskExecution).where(
                    TaskExecution.id == attempt.execution_id,
                    TaskExecution.workspace_id == task_token.workspace_id,
                )
            )
        ).scalar_one_or_none()
        spec = execution.task_spec if execution is not None and isinstance(
            execution.task_spec, dict
        ) else None
        raw_task_id = (spec or {}).get("squad_task_id")
        squad_role = (spec or {}).get("squad_role")
        try:
            task_uuid = uuid.UUID(str(raw_task_id)) if raw_task_id else None
        except (ValueError, AttributeError, TypeError):
            task_uuid = None
        if task_uuid is None:
            raise NotFoundError("execution is not a squad task")
        task = (
            await session.execute(
                select(SquadTask).where(
                    SquadTask.id == task_uuid,
                    SquadTask.workspace_id == task_token.workspace_id,
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise NotFoundError("squad task not found")
        role = squad_role if isinstance(squad_role, str) and squad_role else "executor"
        return task, role


async def _actor_member_for_token(request: Request, task_token: AttemptTaskToken):
    """The Member row the task token acts as: the agent member that owns the
    attempt (token scopes pin ``agent_id``). squad.md §5.3 — the server
    verifies the caller really is the task's orchestrator downstream."""
    from sqlalchemy import select

    from mesh.db.models.member import Member
    from mesh.db.tenant import set_tenant_context

    scopes = task_token.scopes or {}
    raw_agent_id = scopes.get("agent_id")
    try:
        agent_uuid = uuid.UUID(str(raw_agent_id)) if raw_agent_id else None
    except (ValueError, AttributeError, TypeError):
        agent_uuid = None
    if agent_uuid is None:
        raise UnauthorizedError("task token carries no agent identity")
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        member = (
            await session.execute(
                select(Member).where(
                    Member.workspace_id == task_token.workspace_id,
                    Member.member_type == "agent",
                    Member.agent_id == agent_uuid,
                )
            )
        ).scalar_one_or_none()
    if member is None:
        raise UnauthorizedError("agent member not found")
    return member


def _require_issue_scope(task_token: AttemptTaskToken, issue_id: str) -> None:
    """Task-token resource pinning (validate_task_token semantics): these
    routes only ever serve the token's CURRENT issue — a mismatch is a
    fail-closed 401, never a 404 probe oracle."""
    scoped = (task_token.scopes or {}).get("issue_id")
    if scoped is None or scoped != issue_id:
        raise UnauthorizedError("resource scope mismatch")


@router.get("/issues/{issue_id}")
async def read_task_issue(
    request: Request,
    issue_id: str,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read the current issue (scope ``issue:read``) — the broker's
    ``issue.read`` action lands here, authenticated as the attempt's agent
    member (regular console routes reject task tokens, auth.md §2.5.1)."""
    await _rate_limit_task(request, task_token)
    _require_method(task_token, "issue:read")
    _require_issue_scope(task_token, issue_id)
    actor = await _actor_member_for_token(request, task_token)
    try:
        issue_uuid = uuid.UUID(issue_id)
    except ValueError as exc:
        raise NotFoundError("issue not found") from exc
    return {
        "data": await request.app.state.issue_service.get_issue(
            viewer=actor,
            workspace_id=task_token.workspace_id,
            issue_id=issue_uuid,
        )
    }


class TaskCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=8000)


@router.post("/issues/{issue_id}/comments", status_code=201)
async def create_task_issue_comment(
    request: Request,
    issue_id: str,
    body: TaskCommentRequest,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Comment on the current issue AS the attempt's agent member (scope
    ``issue:comment:write``). ``suppress_triggers=True``: a broker-mediated
    comment must never re-wake agents (leader↔member loop suppression,
    squad.md §5.3 / comment-inbox loop rule)."""
    await _rate_limit_task(request, task_token)
    _require_method(task_token, "issue:comment:write")
    _require_issue_scope(task_token, issue_id)
    actor = await _actor_member_for_token(request, task_token)
    try:
        issue_uuid = uuid.UUID(issue_id)
    except ValueError as exc:
        raise NotFoundError("issue not found") from exc
    return {
        "data": await request.app.state.comment_service.create_comment(
            workspace_id=task_token.workspace_id,
            issue_id=issue_uuid,
            author_member=actor,
            body_markdown=body.body,
            suppress_triggers=True,
        )
    }


class TaskStatusRequest(BaseModel):
    status: str = Field(
        pattern=r"^(todo|in_progress|in_review|done|blocked|backlog|cancelled)$"
    )


@router.patch("/issues/{issue_id}/status")
async def set_task_issue_status(
    request: Request,
    issue_id: str,
    body: TaskStatusRequest,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Set the current issue status as the attempt's agent member (scope
    ``issue:status:write``) — goes through the regular issue service so the
    write gate and (when enabled) strict-mode transition rules apply. The
    broker speaks status NAMES; this route resolves the workspace's
    issue_statuses row (the console API takes status ids)."""
    from sqlalchemy import func, select

    from mesh.db.models.issue import IssueStatus
    from mesh.db.tenant import set_tenant_context
    from mesh.issue.service import IssuePatch

    await _rate_limit_task(request, task_token)
    _require_method(task_token, "issue:status:write")
    _require_issue_scope(task_token, issue_id)
    actor = await _actor_member_for_token(request, task_token)
    try:
        issue_uuid = uuid.UUID(issue_id)
    except ValueError as exc:
        raise NotFoundError("issue not found") from exc
    # Workspace status names are display strings ("Done", "In Progress") —
    # the broker contract speaks lowercase slugs, so resolve case-insensitively.
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        status_row = (
            await session.execute(
                select(IssueStatus).where(
                    IssueStatus.workspace_id == task_token.workspace_id,
                    func.lower(func.replace(IssueStatus.name, " ", "_")) == body.status,
                )
            )
        ).scalars().first()
    if status_row is None:
        raise NotFoundError("status not found")
    return {
        "data": await request.app.state.issue_service.update_issue(
            actor=actor,
            workspace_id=task_token.workspace_id,
            issue_id=issue_uuid,
            patch=IssuePatch(status_id=status_row.id),
        )
    }


@router.get("/squad/members")
async def list_squad_members_for_task(
    request: Request,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Read the squad roster for the current squad task (scope
    ``squad:task:read``). The leader's orchestrator run uses this to choose
    subtask assignees BEFORE decomposing — member ids, roles and names only;
    no credentials, no cross-squad data."""
    from sqlalchemy import select

    from mesh.db.models.agent import Agent
    from mesh.db.models.member import Member
    from mesh.db.models.squad import SquadMember
    from mesh.db.tenant import set_tenant_context

    await _rate_limit_task(request, task_token)
    _require_method(task_token, "squad:task:read")
    task, _role = await _load_squad_task_for_token(request, task_token)

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        await set_tenant_context(session, task_token.workspace_id)
        rows = (
            await session.execute(
                select(SquadMember, Member, Agent.name)
                .join(Member, Member.id == SquadMember.member_id)
                .outerjoin(Agent, Agent.id == Member.agent_id)
                .where(
                    SquadMember.squad_id == task.squad_id,
                    SquadMember.left_at.is_(None),
                )
            )
        ).all()
    members = [
        {
            "member_id": str(sm.member_id),
            "role": sm.role,
            "member_type": member.member_type,
            "name": member.display_override or agent_name or f"member-{str(sm.member_id)[:8]}",
            "is_agent": member.member_type == "agent",
        }
        for sm, member, agent_name in rows
    ]
    return {"data": {"squad_id": str(task.squad_id), "task_id": str(task.id), "members": members}}


@router.post("/squad/subtasks", status_code=201)
async def create_squad_subtasks_for_task(
    request: Request,
    body: TaskSubtasksRequest,
    task_token: AttemptTaskToken = Depends(resolve_task_principal),
) -> dict:
    """Submit the leader's decomposition for the CURRENT squad task (scope
    ``squad:task:decompose`` — orchestrator attempts only, §2.2 S-05).

    Delegates to the SAME ``SquadTaskService.create_subtasks`` the console
    route uses, so the state machine, depth/cycle guards and dispatch all
    apply unchanged; ``_assert_can_orchestrate`` inside it is the authoritative
    check that the caller really is this task's leader (squad.md §5.3)."""
    from mesh.squad.schemas import CreateSubtasksRequest, SquadMemberInput, SubtaskInput

    await _rate_limit_task(request, task_token)
    _require_method(task_token, "squad:task:decompose")
    task, role = await _load_squad_task_for_token(request, task_token)
    if role != "orchestrator":
        raise ForbiddenError("only the orchestrator attempt may decompose")
    actor = await _actor_member_for_token(request, task_token)

    subtasks = [
        SubtaskInput(
            title=sub.title,
            assignee=(
                SquadMemberInput(member_id=sub.assignee_member_id, role="member")
                if sub.assignee_member_id
                else None
            ),
            stage=sub.stage,
            depends_on=list(sub.depends_on),
        )
        for sub in body.subtasks
    ]
    # SquadTaskService opens its own transaction (with tenant context) — the
    # console subtasks route calls it the same way.
    return {
        "data": await request.app.state.squad_task_service.create_subtasks(
            actor=actor,
            workspace_id=task_token.workspace_id,
            squad_id=task.squad_id,
            task_id=task.id,
            body=CreateSubtasksRequest(plan_markdown=body.plan_markdown, subtasks=subtasks),
        )
    }
