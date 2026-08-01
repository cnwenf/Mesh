"""Skill HTTP routes (skill.md §3.1 / §3.4).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Reads need workspace membership; writes need the admin-level manage role
(service layer). Import- and marketplace-class endpoints get their own
stricter rate bucket (§3.4: protect the external sources).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response

from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.db.models.user import User
from mesh.errors import NotFoundError, ValidationError
from mesh.skill.bindings import BindingService
from mesh.skill.importer import ImportService
from mesh.skill.installations import InstallationService
from mesh.skill.schemas import (
    ApproveRequest,
    BindRequest,
    CreateSkillRequest,
    CreateVersionRequest,
    ImportRequest,
    InstallRequest,
    PatchBindingRequest,
    PatchInstallationRequest,
    PatchSkillRequest,
    RollbackRequest,
)
from mesh.skill.service import SkillPatch, SkillService

router = APIRouter(prefix="/api/v1", tags=["skill"])

# auth.md §3.6 general write class.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60
# §3.4: import / marketplace pull endpoints get their own tighter bucket.
SOURCE_PULL_LIMIT = 30
SOURCE_PULL_WINDOW_SECONDS = 60

_NOT_FOUND = "skill not found"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _client_meta(request: Request) -> dict:
    return {
        "ip_address": _client_ip(request),
        "user_agent": request.headers.get("user-agent"),
    }


def _skill_service(request: Request) -> SkillService:
    return request.app.state.skill_service


def _installation_service(request: Request) -> InstallationService:
    return request.app.state.skill_installation_service


def _binding_service(request: Request) -> BindingService:
    return request.app.state.skill_binding_service


def _import_service(request: Request) -> ImportService:
    return request.app.state.skill_import_service


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"skill-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _rate_limit_source_pull(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"skill-source-pull:{user.id}:{_client_ip(request)}",
        limit=SOURCE_PULL_LIMIT,
        window_seconds=SOURCE_PULL_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(SOURCE_PULL_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _path_uuid(value: str, *, not_found: str = _NOT_FOUND) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError(not_found) from exc


def _body_uuid(value: str | None, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value or "")
    except ValueError as exc:
        raise ValidationError(f"invalid {field}", details={field: (value or "")[:64]}) from exc


# --- skills CRUD (§3.1) --------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/skills")
async def list_skills(
    request: Request,
    status: str = "all",
    source_type: str = "all",
    q: str | None = None,
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _skill_service(request)
    items, next_cursor = await service.list_skills(
        workspace_id=context.workspace.id,
        status=status,
        source_type=source_type,
        q=q,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/skills", status_code=201)
async def create_skill(
    body: CreateSkillRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _skill_service(request)
    created = await service.create_skill(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        summary=body.summary,
        slug=body.slug,
        tags=body.tags,
        icon=body.icon,
        required_capabilities=body.required_capabilities,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/skills/{skill_id}")
async def get_skill(
    request: Request,
    skill_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _skill_service(request)
    detail = await service.get_skill(
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
    )
    return {"data": detail}


@router.patch("/workspaces/{workspace_id}/skills/{skill_id}")
async def update_skill(
    body: PatchSkillRequest,
    request: Request,
    response: Response,
    skill_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    fields = body.model_fields_set
    service = _skill_service(request)
    updated = await service.update_skill(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        patch=SkillPatch(
            name=body.name if "name" in fields else None,
            summary=body.summary if "summary" in fields else None,
            tags=body.tags if "tags" in fields else None,
            icon=body.icon if "icon" in fields else None,
            status=body.status if "status" in fields else None,
            required_capabilities=(
                body.required_capabilities if "required_capabilities" in fields else None
            ),
        ),
        **_client_meta(request),
    )
    return {"data": updated}


@router.delete("/workspaces/{workspace_id}/skills/{skill_id}", status_code=204)
async def delete_skill(
    request: Request,
    response: Response,
    skill_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> None:
    await _rate_limit_write(request, user, response)
    service = _skill_service(request)
    await service.delete_skill(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        **_client_meta(request),
    )


# --- versions (§3.1) ---------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/skills/{skill_id}/versions")
async def list_versions(
    request: Request,
    skill_id: str,
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _skill_service(request)
    items, next_cursor = await service.list_versions(
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/skills/{skill_id}/versions", status_code=201)
async def create_version(
    body: CreateVersionRequest,
    request: Request,
    response: Response,
    skill_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    manifest = {
        "version": body.version,
        "instructions": body.instructions,
        "scripts": [
            {
                "path": script.path,
                "runtime": script.runtime,
                "entrypoint": script.entrypoint,
                "required_capabilities": script.required_capabilities or [],
            }
            for script in (body.scripts or [])
        ],
        "references": [
            {
                "path": reference.path,
                "media_type": reference.media_type,
                "summary": reference.summary,
            }
            for reference in (body.references or [])
        ],
        "triggers": [
            {
                "trigger_type": trigger.trigger_type,
                "pattern": trigger.pattern,
                "weight": trigger.weight,
            }
            for trigger in (body.triggers or [])
        ],
        "changelog": body.changelog,
        "io_contract": body.io_contract,
        "required_capabilities": body.required_capabilities,
    }
    script_bodies = {
        script.path: script.content.encode("utf-8") for script in (body.scripts or [])
    }
    reference_bodies = {
        reference.path: reference.content.encode("utf-8")
        for reference in (body.references or [])
    }
    service = _skill_service(request)
    created = await service.create_version(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        manifest=manifest,
        script_bodies=script_bodies,
        reference_bodies=reference_bodies,
        content_store=request.app.state.skill_content_store,
        publish=body.publish,
        **_client_meta(request),
    )
    return {"data": created}


@router.get("/workspaces/{workspace_id}/skills/{skill_id}/versions/{version_id}")
async def get_version(
    request: Request,
    skill_id: str,
    version_id: str,
    include_content: bool = False,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _skill_service(request)
    detail = await service.get_version(
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        version_id=_path_uuid(version_id, not_found="skill version not found"),
        include_content=include_content,
        content_store=request.app.state.skill_content_store,
    )
    return {"data": detail}


# --- import / approval (§3.1 / §3.5) --------------------------------------------------------


@router.post("/workspaces/{workspace_id}/skills/import", status_code=202)
async def import_skill(
    body: ImportRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_source_pull(request, user, response)
    service = _import_service(request)
    task = await service.start_import(
        actor=context.member,
        workspace_id=context.workspace.id,
        source_type=body.source_type,
        uri=body.uri,
        ref=body.ref,
        **_client_meta(request),
    )
    return {"data": task}


@router.get("/workspaces/{workspace_id}/skills/import/{task_id}")
async def get_import_task(
    request: Request,
    task_id: str,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _import_service(request)
    task = await service.get_task(
        workspace_id=context.workspace.id,
        task_id=_path_uuid(task_id, not_found="import task not found"),
    )
    return {"data": task}


@router.post("/workspaces/{workspace_id}/skills/{skill_id}/approve")
async def approve_skill(
    body: ApproveRequest,
    request: Request,
    response: Response,
    skill_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _import_service(request)
    result = await service.approve(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_path_uuid(skill_id),
        task_id=_body_uuid(body.task_id, field="task_id"),
        granted_capabilities=body.granted_capabilities,
        decision=body.decision,
        comment=body.comment,
        **_client_meta(request),
    )
    return {"data": result}


# --- marketplace (K10) -----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/marketplace/skills")
async def list_marketplace(
    request: Request,
    response: Response,
    q: str | None = None,
    limit: int = 20,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_source_pull(request, user, response)
    service = _import_service(request)
    items, next_cursor = await service.list_marketplace(
        workspace_id=context.workspace.id,
        q=q,
        limit=limit,
    )
    return {"data": items, "next_cursor": next_cursor}


# --- installations (§3.1) ---------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/skill-installations")
async def list_installations(
    request: Request,
    skill_id: str | None = None,
    scope: str = "all",
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _installation_service(request)
    items, next_cursor = await service.list_installations(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_body_uuid(skill_id, field="skill_id") if skill_id else None,
        scope=scope,
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/skill-installations", status_code=201)
async def install_skill(
    body: InstallRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _installation_service(request)
    installed = await service.install(
        actor=context.member,
        workspace_id=context.workspace.id,
        skill_id=_body_uuid(body.skill_id, field="skill_id"),
        skill_version_id=_body_uuid(body.skill_version_id, field="skill_version_id"),
        scope=body.scope,
        agent_id=(
            _body_uuid(body.agent_id, field="agent_id") if body.agent_id else None
        ),
        auto_update=body.auto_update,
        **_client_meta(request),
    )
    return {"data": installed}


@router.patch("/workspaces/{workspace_id}/skill-installations/{installation_id}")
async def update_installation(
    body: PatchInstallationRequest,
    request: Request,
    response: Response,
    installation_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    fields = body.model_fields_set
    service = _installation_service(request)
    updated = await service.update_installation(
        actor=context.member,
        workspace_id=context.workspace.id,
        installation_id=_path_uuid(
            installation_id, not_found="skill installation not found"
        ),
        skill_version_id=(
            _body_uuid(body.skill_version_id, field="skill_version_id")
            if "skill_version_id" in fields
            else None
        ),
        install_status=body.install_status if "install_status" in fields else None,
        auto_update=body.auto_update if "auto_update" in fields else None,
        **_client_meta(request),
    )
    return {"data": updated}


@router.delete(
    "/workspaces/{workspace_id}/skill-installations/{installation_id}", status_code=204
)
async def uninstall_skill(
    request: Request,
    response: Response,
    installation_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> None:
    await _rate_limit_write(request, user, response)
    service = _installation_service(request)
    await service.uninstall(
        actor=context.member,
        workspace_id=context.workspace.id,
        installation_id=_path_uuid(
            installation_id, not_found="skill installation not found"
        ),
        **_client_meta(request),
    )


@router.post("/workspaces/{workspace_id}/skill-installations/{installation_id}/rollback")
async def rollback_installation(
    body: RollbackRequest,
    request: Request,
    response: Response,
    installation_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _installation_service(request)
    rolled_back = await service.rollback(
        actor=context.member,
        workspace_id=context.workspace.id,
        installation_id=_path_uuid(
            installation_id, not_found="skill installation not found"
        ),
        target_version_id=_body_uuid(body.target_version_id, field="target_version_id"),
        reason=body.reason,
        **_client_meta(request),
    )
    return {"data": rolled_back}


# --- agent bindings (§3.1) ----------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/agents/{agent_id}/skills")
async def list_agent_skills(
    request: Request,
    agent_id: str,
    limit: int = 20,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    service = _binding_service(request)
    items, next_cursor = await service.list_agent_skills(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id, not_found="agent not found"),
        limit=limit,
        cursor=cursor,
    )
    return {"data": items, "next_cursor": next_cursor}


@router.post("/workspaces/{workspace_id}/agents/{agent_id}/skills", status_code=201)
async def bind_skill(
    body: BindRequest,
    request: Request,
    response: Response,
    agent_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _binding_service(request)
    binding = await service.bind(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id, not_found="agent not found"),
        skill_installation_id=_body_uuid(
            body.skill_installation_id, field="skill_installation_id"
        ),
        skill_version_id=(
            _body_uuid(body.skill_version_id, field="skill_version_id")
            if body.skill_version_id
            else None
        ),
        auto_trigger=body.auto_trigger,
        priority=body.priority,
        **_client_meta(request),
    )
    return {"data": binding}


@router.patch("/workspaces/{workspace_id}/agents/{agent_id}/skills/{binding_id}")
async def update_binding(
    body: PatchBindingRequest,
    request: Request,
    response: Response,
    agent_id: str,
    binding_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _binding_service(request)
    updated = await service.update_binding(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id, not_found="agent not found"),
        binding_id=_path_uuid(binding_id, not_found="skill binding not found"),
        enabled=body.enabled,
        auto_trigger=body.auto_trigger,
        priority=body.priority,
        **_client_meta(request),
    )
    return {"data": updated}


@router.delete(
    "/workspaces/{workspace_id}/agents/{agent_id}/skills/{binding_id}", status_code=204
)
async def unbind_skill(
    request: Request,
    response: Response,
    agent_id: str,
    binding_id: str,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace()),
) -> None:
    await _rate_limit_write(request, user, response)
    service = _binding_service(request)
    await service.unbind(
        actor=context.member,
        workspace_id=context.workspace.id,
        agent_id=_path_uuid(agent_id, not_found="agent not found"),
        binding_id=_path_uuid(binding_id, not_found="skill binding not found"),
        **_client_meta(request),
    )
