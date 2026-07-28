"""Data-jobs API routes (import-export.md §3.1).

Workspace-less paths resolve the tenant through the SECURITY DEFINER
lookup (migration 0021) and run the membership gate; human JWTs and
agent API tokens share the endpoints (attachment-auth pattern). Create
endpoints are rate-limited (§3.0) and support ``Idempotency-Key``
(README §6.14).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session, get_session_factory
from mesh.attachment.auth import Caller, authenticate, gate_workspace
from mesh.data_jobs.schemas import CreateExportJobRequest, CreateImportJobRequest
from mesh.data_jobs.service import DataJobService
from mesh.db.models.member import Member
from mesh.errors import NotFoundError

router = APIRouter(prefix="/api/v1", tags=["data-jobs"])

CREATE_LIMIT = 30  # settings-tuned via data_job_create_limit in the limiter call
RATE_WINDOW_SECONDS = 60

_JOB_NOT_FOUND = "data job not found"
_WORKSPACE_NOT_FOUND = "workspace not found"


def _service(request: Request) -> DataJobService:
    return request.app.state.data_job_service


def _path_uuid(raw: str, *, message: str = _JOB_NOT_FOUND) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise NotFoundError(message) from exc


def _caller_identity(caller: Caller) -> str:
    if caller.token is not None:
        return f"token:{caller.token.id}"
    assert caller.user is not None
    return f"user:{caller.user.id}"


async def _rate_limit_create(request: Request, caller: Caller, response: Response) -> None:
    settings = request.app.state.settings
    limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    remaining, reset_in = await limiter.check(
        f"data-job-create:{_caller_identity(caller)}:{client_ip}",
        limit=settings.data_job_create_limit,
        window_seconds=settings.data_job_create_window_seconds,
    )
    response.headers["X-RateLimit-Limit"] = str(settings.data_job_create_limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


async def _resolve_job_context(
    request: Request, session: AsyncSession, job_id: uuid.UUID
) -> tuple[Member, uuid.UUID]:
    """Workspace-less path: tenant via SECURITY DEFINER → membership gate."""
    workspace_id = await _service(request).resolve_job_workspace(job_id)
    if workspace_id is None:
        raise NotFoundError(_JOB_NOT_FOUND)
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, workspace_id)
    return member, workspace_id


# ----------------------------------------------------------------------
# create (§3.2 / §3.5)
# ----------------------------------------------------------------------


@router.post("/data-jobs/import", status_code=201)
async def create_import_job(
    body: CreateImportJobRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_create(request, await authenticate(request, get_session_factory(request)), response)
    workspace_id = _path_uuid(body.workspace_id, message=_WORKSPACE_NOT_FOUND)
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, workspace_id)
    created = await _service(request).create_import_job(
        workspace_id=workspace_id,
        member=member,
        body=body,
        idempotency_key=idempotency_key,
    )
    return {"data": created}


@router.post("/data-jobs/export", status_code=201)
async def create_export_job(
    body: CreateExportJobRequest,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _rate_limit_create(request, await authenticate(request, get_session_factory(request)), response)
    workspace_id = _path_uuid(body.workspace_id, message=_WORKSPACE_NOT_FOUND)
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, workspace_id)
    created = await _service(request).create_export_job(
        workspace_id=workspace_id,
        member=member,
        body=body,
        idempotency_key=idempotency_key,
    )
    return {"data": created}


# ----------------------------------------------------------------------
# list (§3.6) — declared before the {job_id} routes (match order)
# ----------------------------------------------------------------------


@router.get("/data-jobs")
async def list_jobs(
    request: Request,
    workspace_id: str = Query(...),
    kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    requested_by: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed_workspace = _path_uuid(workspace_id, message=_WORKSPACE_NOT_FOUND)
    caller = await authenticate(request, get_session_factory(request))
    member = await gate_workspace(session, caller, parsed_workspace)
    requested_by_id = _path_uuid(requested_by, message="member not found") if requested_by else None
    return await _service(request).list_jobs(
        workspace_id=parsed_workspace,
        member=member,
        kind=kind,
        status=status,
        requested_by=requested_by_id,
        cursor=cursor,
        limit=limit,
    )


# ----------------------------------------------------------------------
# two-phase import actions (§3.3 / §3.4)
# ----------------------------------------------------------------------


@router.post("/data-jobs/import/{job_id}/validate")
async def validate_import_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(job_id)
    member, workspace_id = await _resolve_job_context(request, session, parsed)
    result = await _service(request).validate_import(workspace_id=workspace_id, member=member, job_id=parsed)
    return {"data": result}


@router.post("/data-jobs/import/{job_id}/run", status_code=202)
async def run_import_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(job_id)
    member, workspace_id = await _resolve_job_context(request, session, parsed)
    result = await _service(request).run_import(workspace_id=workspace_id, member=member, job_id=parsed)
    return {"data": result}


# ----------------------------------------------------------------------
# get / download (§3.6)
# ----------------------------------------------------------------------


@router.get("/data-jobs/{job_id}")
async def get_job(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(job_id)
    member, workspace_id = await _resolve_job_context(request, session, parsed)
    result = await _service(request).get_job(workspace_id=workspace_id, member=member, job_id=parsed)
    return {"data": result}


@router.get("/data-jobs/{job_id}/download")
async def download_job_product(
    job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    parsed = _path_uuid(job_id)
    member, workspace_id = await _resolve_job_context(request, session, parsed)
    return await _service(request).download_job(
        workspace_id=workspace_id,
        member=member,
        job_id=parsed,
        ip_address=request.client.host if request.client is not None else None,
        user_agent=request.headers.get("user-agent"),
    )
