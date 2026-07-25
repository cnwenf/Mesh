"""API-token + audit HTTP routes (auth.md §3.2 / §3.3).

Middleware chain per README §6.14: Bearer → membership → RBAC → rate limit.
Token management is gated by ``token:manage`` (owner/admin/member — a member
manages only their OWN tokens, enforced in the service); creating a token for
another holder, and reading the audit log, require admin-or-owner.

The ``whoami`` endpoint authenticates a PAT/agent credential itself (Bearer
``mesh_pat_…`` / ``mesh_agt_…``) so CLI/runtime callers can verify a credential
and so the PAT path is exercised end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from mesh.api.deps import extract_bearer_token, get_session
from mesh.api.pagination import paginate
from mesh.auth.deps import get_current_user
from mesh.auth.rbac import WorkspaceContext, require_workspace
from mesh.auth.token_schemas import CreateTokenRequest
from mesh.auth.tokens import AGENT_TOKEN_PREFIX, PAT_TOKEN_PREFIX, ResolvedToken, TokenService
from mesh.db.models.audit import AuditLog
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, NotFoundError, UnauthorizedError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["token"])

# auth.md §3.6 — general API write class: 120 req/min per token/user.
WRITE_LIMIT = 120
WRITE_WINDOW_SECONDS = 60

_TOKEN_PREFIXES = (PAT_TOKEN_PREFIX, AGENT_TOKEN_PREFIX)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _client_meta(request: Request) -> dict:
    return {"ip_address": _client_ip(request), "user_agent": request.headers.get("user-agent")}


def _token_service(request: Request) -> TokenService:
    return request.app.state.token_service


async def _rate_limit_write(request: Request, user: User, response: Response) -> None:
    limiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(
        f"token-write:{user.id}:{_client_ip(request)}",
        limit=WRITE_LIMIT,
        window_seconds=WRITE_WINDOW_SECONDS,
    )
    response.headers["X-RateLimit-Limit"] = str(WRITE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _require_admin(context: WorkspaceContext) -> None:
    """admin-or-owner gate for cross-holder token creation and audit reads."""
    if context.member.role not in ("admin", "owner"):
        raise ForbiddenError("admin role required")


def _path_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NotFoundError("token not found") from exc


def _parse_timestamp(value: str, *, field: str) -> datetime:
    """Parse an RFC3339 timestamp (auth.md §5.3 audit time-range filter)."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(
            f"invalid {field}", code="validation_error", details={field: value[:64]}
        ) from exc
    # Normalise naive inputs to UTC so comparisons against TIMESTAMPTZ are sound.
    if parsed.tzinfo is None:

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


# --- PAT / agent credential management (§3.2) --------------------------------


@router.post("/workspaces/{workspace_id}/api-tokens", status_code=201)
async def create_token(
    body: CreateTokenRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("token:manage")),
) -> dict:
    await _rate_limit_write(request, user, response)
    owner_member_id: uuid.UUID | None = None
    if body.owner_member_id is not None:
        try:
            owner_member_id = uuid.UUID(body.owner_member_id)
        except ValueError as exc:
            raise ValidationError(
                "invalid owner_member_id", details={"owner_member_id": body.owner_member_id[:64]}
            ) from exc
        # Creating a token for another holder (e.g. an agent) needs admin+.
        if owner_member_id != context.member.id:
            _require_admin(context)
    service = _token_service(request)
    data = await service.create_token(
        actor=context.member,
        workspace_id=context.workspace.id,
        name=body.name,
        scopes=body.scopes,
        role_override=body.role_override,
        expires_at=body.expires_at,
        owner_member_id=owner_member_id,
        **_client_meta(request),
    )
    return {"data": data}


@router.get("/workspaces/{workspace_id}/api-tokens")
async def list_tokens(
    request: Request,
    context: WorkspaceContext = Depends(require_workspace("token:manage")),
) -> dict:
    service = _token_service(request)
    items = await service.list_tokens(actor=context.member, workspace_id=context.workspace.id)
    return {"data": items, "next_cursor": None}


@router.delete("/workspaces/{workspace_id}/api-tokens/{token_id}")
async def revoke_token(
    token_id: str,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    context: WorkspaceContext = Depends(require_workspace("token:manage")),
) -> dict:
    await _rate_limit_write(request, user, response)
    service = _token_service(request)
    await service.revoke_token(
        actor=context.member,
        workspace_id=context.workspace.id,
        token_id=_path_uuid(token_id),
        **_client_meta(request),
    )
    return {"data": {"status": "ok"}}


# --- PAT-authenticated principal (CLI/runtime credential check) --------------


async def get_request_pat(request: Request) -> ResolvedToken:
    """Resolve a Bearer PAT/agent token to its effective principal (401 if bad)."""
    token = extract_bearer_token(request.headers.get("Authorization"))
    if not token.startswith(_TOKEN_PREFIXES):
        raise UnauthorizedError("invalid or expired token")
    service: TokenService = request.app.state.token_service
    resolved = await service.resolve_pat(token=token, ip_address=_client_ip(request))
    if resolved is None:
        raise UnauthorizedError("invalid or expired token")
    return resolved


@router.get("/api-tokens/whoami")
async def token_whoami(pat: ResolvedToken = Depends(get_request_pat)) -> dict:
    return {
        "data": {
            "token_id": pat.id,
            "workspace_id": pat.workspace_id,
            "owner_member_id": pat.owner_member_id,
            "member_type": pat.member_type,
            "role": pat.role,
            "scopes": sorted(pat.scopes),
            "name": pat.name,
        }
    }


# --- audit log query (§3.3, admin+) ------------------------------------------


@router.get("/workspaces/{workspace_id}/audit-logs")
async def list_audit_logs(
    request: Request,
    action: str | None = None,
    actor_member_id: str | None = None,
    before: str | None = None,
    after: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    context: WorkspaceContext = Depends(require_workspace()),
    session=Depends(get_session),
) -> dict:
    _require_admin(context)
    stmt = select(AuditLog).where(AuditLog.workspace_id == context.workspace.id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if actor_member_id is not None:
        try:
            actor_uuid = uuid.UUID(actor_member_id)
        except ValueError as exc:
            raise ValidationError(
                "invalid actor_member_id", details={"actor_member_id": actor_member_id[:64]}
            ) from exc
        stmt = stmt.where(AuditLog.actor_member_id == actor_uuid)
    # §5.3 time-range filter (consumed by the §4.4 audit page): created_at in
    # (after, before). Half-open bounds keep cursor pagination consistent.
    if before is not None:
        stmt = stmt.where(AuditLog.created_at < _parse_timestamp(before, field="before"))
    if after is not None:
        stmt = stmt.where(AuditLog.created_at > _parse_timestamp(after, field="after"))
    page = await paginate(
        session,
        stmt,
        sort_column=AuditLog.created_at,
        id_column=AuditLog.id,
        sort_value_of=lambda row: row.created_at,
        id_of=lambda row: row.id,
        cursor=cursor,
        limit=limit,
        descending=True,
    )
    items = [
        {
            "id": row.id,
            "actor_member_id": row.actor_member_id,
            "actor_kind": row.actor_kind,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "ip_address": str(row.ip_address) if row.ip_address is not None else None,
            "metadata": row.metadata_,
            "created_at": row.created_at,
        }
        for row in page.items
    ]
    return {"data": items, "next_cursor": page.next_cursor}
