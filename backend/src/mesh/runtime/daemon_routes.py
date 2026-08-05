"""Machine API (daemon) routes — runtime.md §3.2.

Namespace ``/api/v1/daemon/`` — explicitly separate from the console API.
Bearer token = runtime token (``mesh_rt_`` prefix, hash-only storage);
workspace is derived from the stored runtime row, NEVER the request body.
Every endpoint is TLS-only (§3.5 red line) and rate-limited.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response

from mesh.db.models.runtime import Runtime
from mesh.errors import ConflictError, ForbiddenError, NotFoundError
from mesh.runtime import approvals as approvals_mod
from mesh.runtime import checkout as checkout_mod
from mesh.runtime import context_appends as context_appends_mod
from mesh.runtime import logs as logs_mod
from mesh.runtime.attempts import renew_lease, transition_attempt
from mesh.runtime.claim import claim_execution
from mesh.runtime.credentials import refetch_envelopes, revoke_execution_envelopes
from mesh.runtime.daemon_auth import require_runtime
from mesh.runtime.schemas import (
    ActivateRuntimeRequest,
    AppendLogsRequest,
    ApprovalCreateRequest,
    AttemptTransitionRequest,
    CheckoutReportRequest,
    ClaimRequest,
    HeartbeatRequest,
    RefetchCredentialsRequest,
    RenewLeaseRequest,
)

router = APIRouter(prefix="/api/v1/daemon", tags=["runtime-daemon"])

DAEMON_LIMIT = 600
DAEMON_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _rate_limit_daemon(request: Request, runtime: Runtime, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"daemon:{runtime.id}:{_client_ip(request)}",
        limit=DAEMON_LIMIT,
        window_seconds=DAEMON_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(DAEMON_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _assert_runtime_matches(path_runtime_id: str, runtime: Runtime) -> None:
    """Token may only operate its OWN runtime (§3.5): mismatch → 403."""
    try:
        path_id = uuid.UUID(path_runtime_id)
    except ValueError as exc:
        raise NotFoundError("runtime not found") from exc
    if path_id != runtime.id:
        raise ForbiddenError("token cannot operate another runtime")


def _attempt_uuid(attempt_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(attempt_id)
    except ValueError as exc:
        raise NotFoundError("attempt not found") from exc


# ---------------------------------------------------------------------------
# Activation (one-time code → long-lived token)
# ---------------------------------------------------------------------------


@router.post("/runtimes:activate")
async def activate_runtime(
    request: Request,
    response: Response,
    body: ActivateRuntimeRequest,
) -> dict:
    settings = request.app.state.settings
    from mesh.runtime.daemon_auth import assert_daemon_tls

    assert_daemon_tls(
        request,
        tls_required=settings.daemon_tls_required,
        trusted_proxies=settings.daemon_trusted_proxies,
    )
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"daemon-activate:{_client_ip(request)}", limit=30, window_seconds=300
    )
    response.headers["X-RateLimit-Limit"] = "30"
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)
    service = request.app.state.runtime_service
    data = await service.activate_runtime(
        activation_code=body.activation_code,
        metadata=body.metadata,
        protocol_version=body.protocol_version,
        provider_manifest=body.provider_manifest,
        daemon_features=body.daemon_features,
    )
    return {"data": data}


# ---------------------------------------------------------------------------
# Heartbeat + claim
# ---------------------------------------------------------------------------


@router.post("/runtimes/{runtime_id}:heartbeat")
async def heartbeat(
    request: Request,
    response: Response,
    runtime_id: str,
    body: HeartbeatRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    _assert_runtime_matches(runtime_id, runtime)
    await _rate_limit_daemon(request, runtime, response)
    service = request.app.state.runtime_service
    data = await service.heartbeat(
        runtime=runtime,
        current_load=body.current_load,
        health=body.health,
        metrics=body.metrics,
        inflight=body.inflight,
        protocol_version=body.protocol_version,
        context_progress=[entry.model_dump() for entry in body.context_progress],
        operational_state=body.operational_state,
        diagnostics=[entry.model_dump() for entry in body.diagnostics],
    )
    return {"data": data}


@router.post("/runtimes/{runtime_id}/executions:claim")
async def claim(
    request: Request,
    response: Response,
    runtime_id: str,
    body: ClaimRequest,
    runtime: Runtime = Depends(require_runtime),
):
    """Atomic claim (§2.5). 200 with the task + one-shot credential envelopes,
    or 204 (queue empty / no label-or-capability match / capacity full — in
    every 204 case current_load is unchanged, T20). Body ``diagnostics`` is
    ignored for matching (server-stored values only)."""
    _assert_runtime_matches(runtime_id, runtime)
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    result = await claim_execution(
        request.app.state.session_factory,
        runtime=runtime,
        lease_seconds=settings.runtime_lease_seconds,
        signing_secret=settings.jwt_secret,
        envelope_ttl=settings.runtime_envelope_ttl,
    )
    if result is None:
        return Response(status_code=204)
    return {"data": {"execution": result.execution, "attempt": result.attempt}}


# ---------------------------------------------------------------------------
# Attempt lifecycle (lease-fenced)
# ---------------------------------------------------------------------------


@router.patch("/attempts/{attempt_id}")
async def patch_attempt(
    request: Request,
    response: Response,
    attempt_id: str,
    body: AttemptTransitionRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    data = await transition_attempt(
        request.app.state.session_factory,
        attempt_id=_attempt_uuid(attempt_id),
        runtime=runtime,
        lease_seq=body.lease_seq,
        new_status=body.status,
        result=body.result,
        failure_reason=body.failure_reason,
        signing_secret=settings.jwt_secret,
        storage=request.app.state.storage,
    )
    return {"data": data}


@router.post("/attempts/{attempt_id}:renew-lease")
async def renew_attempt_lease(
    request: Request,
    response: Response,
    attempt_id: str,
    body: RenewLeaseRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    data = await renew_lease(
        request.app.state.session_factory,
        attempt_id=_attempt_uuid(attempt_id),
        runtime=runtime,
        lease_seq=body.lease_seq,
        lease_seconds=settings.runtime_lease_seconds,
    )
    return {"data": data}


@router.post("/attempts/{attempt_id}/logs")
async def append_logs(
    request: Request,
    response: Response,
    attempt_id: str,
    body: AppendLogsRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    data = await logs_mod.append_log_lines(
        request.app.state.session_factory,
        request.app.state.storage,
        attempt_id=_attempt_uuid(attempt_id),
        runtime=runtime,
        lease_seq=body.lease_seq,
        stream=body.stream,
        start_offset=body.start_offset,
        lines=body.lines,
        signing_secret=settings.jwt_secret,
    )
    return {"data": data}


@router.post("/attempts/{attempt_id}/checkouts")
async def report_checkout(
    request: Request,
    response: Response,
    attempt_id: str,
    body: CheckoutReportRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    data = await checkout_mod.report_checkout(
        request.app.state.session_factory,
        attempt_id=_attempt_uuid(attempt_id),
        runtime=runtime,
        lease_seq=body.lease_seq,
        status=body.status,
        repo_url=body.repo_url,
        base_ref=body.base_ref,
        commit_sha=body.commit_sha,
        local_path=body.local_path,
        diff=body.diff,
        storage=request.app.state.storage,
        signing_secret=settings.jwt_secret,
    )
    return {"data": data}


@router.post("/attempts/{attempt_id}/credentials:refetch")
async def refetch_credentials(
    request: Request,
    response: Response,
    attempt_id: str,
    body: RefetchCredentialsRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    """Lost-response recovery (§2.2): new envelopes, old ones revoked,
    per-attempt counter advances; exceeding the cap FREEZES the execution
    (all envelopes revoked, critical alert) and returns 409."""
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    session_factory = request.app.state.session_factory
    parsed = _attempt_uuid(attempt_id)

    # Fence + refetch in ONE transaction: the attempt row is locked FOR UPDATE
    # while lease_seq / status are validated and the envelopes rotate, closing
    # the TOCTOU window between check and issue (review M1).
    from mesh.db.tenant import set_tenant_context
    from mesh.runtime.attempts import _assert_lease, _load_daemon_attempt

    try:
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, runtime.workspace_id)
            attempt = await _load_daemon_attempt(
                session, attempt_id=parsed, runtime=runtime
            )
            if attempt.status not in ("claimed", "running"):
                raise ConflictError(
                    "attempt not in flight",
                    code="attempt_terminal",
                    details={"status": attempt.status},
                )
            _assert_lease(attempt, body.lease_seq)
            execution_id = attempt.execution_id
            delivered = await refetch_envelopes(
                session,
                workspace_id=runtime.workspace_id,
                attempt_id=parsed,
                signing_secret=settings.jwt_secret,
                envelope_ttl=settings.runtime_envelope_ttl,
                refetch_limit=settings.runtime_credential_refetch_limit,
            )
    except ConflictError as exc:
        if exc.code == "credential_refetch_limit":
            # Freeze: revoke everything, keep the scene, alert humans.
            async with session_factory() as session, session.begin():
                await set_tenant_context(session, runtime.workspace_id)
                await revoke_execution_envelopes(session, execution_id=execution_id)
            raise ConflictError(
                "credential refetch limit exceeded — execution frozen for review",
                code="credential_refetch_limit",
                details={"limit": settings.runtime_credential_refetch_limit},
            ) from exc
        raise
    return {
        "data": {
            "credentials": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "env": item.env,
                    "value": item.value,
                    "envelope": item.envelope,
                    "expires_at": item.expires_at.isoformat(),
                }
                for item in delivered
            ]
        }
    }


# ---------------------------------------------------------------------------
# High-risk tool approvals (README §6.10)
# ---------------------------------------------------------------------------


@router.post("/executions/{execution_id}/approvals")
async def request_approval(
    request: Request,
    response: Response,
    execution_id: str,
    body: ApprovalCreateRequest,
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    await _rate_limit_daemon(request, runtime, response)
    settings = request.app.state.settings
    try:
        execution_uuid = uuid.UUID(execution_id)
    except ValueError as exc:
        raise NotFoundError("execution not found") from exc
    data = await approvals_mod.request_tool_approval(
        request.app.state.session_factory,
        execution_id=execution_uuid,
        runtime=runtime,
        attempt_id=_attempt_uuid(body.attempt_id),
        lease_seq=body.lease_seq,
        action_summary=body.action_summary,
        resume_context=body.resume_context,
        approval_ttl=settings.runtime_approval_ttl,
    )
    return {"data": data}


# ---------------------------------------------------------------------------
# Runtime context appends (MES-82 /btw, runtime.md §3.2)
# ---------------------------------------------------------------------------


@router.get("/executions/{execution_id}/context-appends")
async def get_context_appends(
    request: Request,
    response: Response,
    execution_id: str,
    attempt_id: str,
    since_seq: int = Query(default=0, ge=0),
    runtime: Runtime = Depends(require_runtime),
) -> dict:
    """Fetch pending context appends for an in-flight execution (MES-82).

    Daemon-authenticated exactly like the approvals endpoint: ``attempt_id``
    (REQUIRED) must belong to THIS runtime AND to the path execution. Returns
    the attempt-scoped pending set — ``seq > since_seq`` with the current
    attempt's already-receipted rows filtered out (single-pointer model; old
    attempt rows were cleared on requeue). Delivery is at-least-once (runtime.md
    「运行期上下文追加」): the daemon injects at the next turn boundary and the
    downstream tolerates duplicate blocks as untrusted data (README §6.15).
    """
    await _rate_limit_daemon(request, runtime, response)
    try:
        execution_uuid = uuid.UUID(execution_id)
    except ValueError as exc:
        raise NotFoundError("execution not found") from exc
    if since_seq < 0:
        raise NotFoundError("execution not found")
    rows = await context_appends_mod.get_context_appends_for_daemon(
        request.app.state.session_factory,
        runtime=runtime,
        execution_id=execution_uuid,
        since_seq=since_seq,
        attempt_id=_attempt_uuid(attempt_id),
    )
    return {"data": rows}
