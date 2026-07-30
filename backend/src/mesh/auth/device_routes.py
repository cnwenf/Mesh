"""Device-code authorization routes (auth.md §3.1.1, cli.md §3.2).

The service contract (table / state machine / brute-force protection) is
authoritative in auth.md §2.4.2; this layer adds the §3.6 rate-limit classes:

* ``device/code``  — 10/min per source IP (login-class baseline);
* ``device/token`` — DUAL dimension: 30/min per IP AND ≤ 1/interval per
  device_code — violations answer ``429 slow_down`` (Retry-After; the CLI adds
  +5s) and count against the per-code ceiling that voids the grant (>5);
* confirmation page (``GET /auth/device`` / approve / deny) — 10/min per
  logged-in user + IP (the user_code hit check is an online probe oracle).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import get_current_principal, require_current_access
from mesh.auth.device_codes import DeviceCodeService
from mesh.auth.ratelimit import RateLimiter
from mesh.auth.schemas import (
    DEVICE_GRANT_TYPE,
    DeviceApproveRequest,
    DeviceCodeRequest,
    DeviceDenyRequest,
    DeviceTokenRequest,
)
from mesh.errors import ForbiddenError, RateLimitedError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["device-auth"])

# §3.6 rate-limit classes.
CODE_LIMIT = 10
CODE_WINDOW_SECONDS = 60
POLL_IP_LIMIT = 30
POLL_IP_WINDOW_SECONDS = 60
CONFIRM_LIMIT = 10
CONFIRM_WINDOW_SECONDS = 60
# CLI adds this to its interval on slow_down (cli.md §3.2 / auth.md §3.5).
SLOW_DOWN_BACKOFF_SECONDS = 5


def _device_service(request: Request) -> DeviceCodeService:
    return request.app.state.device_code_service


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _require_web_session_claims(request: Request, session: AsyncSession):
    """approve/deny are WEB-session-only (auth.md §1.1 credential matrix).

    A presented PAT/agent token has no interactive session → ``403
    reauth_required`` with ``reason=interactive_session_required`` (never a
    bare 401 — the credential itself is VALID, it is merely the wrong KIND
    for a human approval act). A session JWT without a locatable ``sid`` gets
    the same gate as the other step-up paths (session_not_locatable).
    """
    principal = await get_current_principal(request, session)
    if principal.kind != "session":
        raise ForbiddenError(
            "recent re-authentication required",
            code="reauth_required",
            details={"reason": "interactive_session_required"},
        )
    claims = require_current_access(request)
    if claims.sid is None or claims.subject is None:
        raise ForbiddenError(
            "recent re-authentication required",
            code="reauth_required",
            details={"reason": "session_not_locatable"},
        )
    return claims


async def _rate_limit(
    request: Request, key: str, *, limit: int, window: int, response: Response
) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(key, limit=limit, window_seconds=window)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


@router.post("/auth/device/code")
async def device_code(body: DeviceCodeRequest, request: Request, response: Response) -> dict:
    """Issue a pending device grant (public; IP-throttled, §3.6)."""
    await _rate_limit(
        request,
        f"device-code:{_client_ip(request)}",
        limit=CODE_LIMIT,
        window=CODE_WINDOW_SECONDS,
        response=response,
    )
    scopes = body.scope.split() if body.scope else []
    issued = await _device_service(request).create_code(
        client_id=body.client_id, scopes=scopes, ip_address=_client_ip(request)
    )
    issued.pop("_id", None)  # internal correlation — never leaves the API
    return {"data": issued}


@router.post("/auth/device/token")
async def device_token(body: DeviceTokenRequest, request: Request, response: Response) -> dict:
    """Poll for the grant outcome (public; dual-dimension throttled, §3.6)."""
    if body.grant_type != DEVICE_GRANT_TYPE:
        raise ValidationError(
            "unsupported grant_type",
            code="invalid_request",
            details={"expected": DEVICE_GRANT_TYPE},
        )
    ip = _client_ip(request)
    # Dimension 1: source IP global.
    await _rate_limit(
        request,
        f"device-poll-ip:{ip}",
        limit=POLL_IP_LIMIT,
        window=POLL_IP_WINDOW_SECONDS,
        response=response,
    )
    service = _device_service(request)
    interval = service._settings.device_poll_interval
    # Dimension 2: per device_code ≤ 1/interval — the fast-poll shield. A hit
    # counts against the per-code ceiling that voids the grant past >5 (§5.5).
    limiter: RateLimiter = request.app.state.rate_limiter
    try:
        await limiter.check(
            f"device-poll-code:{body.device_code[:128]}",
            limit=1,
            window_seconds=interval,
        )
    except RateLimitedError:
        await service.register_poll_violation(device_code=body.device_code)
        raise RateLimitedError(
            "polling faster than the granted interval",
            code="slow_down",
            retry_after=interval + SLOW_DOWN_BACKOFF_SECONDS,
        ) from None

    grant = await service.exchange(
        device_code=body.device_code,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    return {
        "data": {
            "access_token": grant.tokens.access_token,
            "refresh_token": grant.tokens.refresh_token,
            "token_type": "Bearer",
            "expires_in": grant.tokens.expires_in,
            "scope": " ".join(grant.scopes),
            "workspace": {"id": str(grant.workspace_id), "slug": grant.workspace_slug},
        }
    }


@router.get("/auth/device")
async def device_confirm_data(
    request: Request,
    response: Response,
    user_code: str,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Confirmation-page data for the logged-in approver (web login state)."""
    from sqlalchemy import select

    from mesh.db.models.user import User
    from mesh.errors import UnauthorizedError

    if principal.kind != "session" or principal.user_id is None:
        raise UnauthorizedError("invalid or expired token")
    await _rate_limit(
        request,
        f"device-confirm:{principal.user_id}:{_client_ip(request)}",
        limit=CONFIRM_LIMIT,
        window=CONFIRM_WINDOW_SECONDS,
        response=response,
    )
    user = await session.scalar(select(User).where(User.id == principal.user_id))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    data = await _device_service(request).confirm_data(user_code=user_code, approver=user)
    return {"data": data}


@router.post("/auth/device/approve")
async def device_approve(
    body: DeviceApproveRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Approve the TYPED user_code, bound to the chosen workspace.

    Requires a web session access JWT (Bearer) — the approver's identity and
    session come from the token's ``sub``/``sid``, never from the body.
    """
    claims = await _require_web_session_claims(request, session)
    await _rate_limit(
        request,
        f"device-confirm:{claims.subject}:{_client_ip(request)}",
        limit=CONFIRM_LIMIT,
        window=CONFIRM_WINDOW_SECONDS,
        response=response,
    )
    result = await _device_service(request).approve(
        user_code=body.user_code,
        workspace_id=body.workspace_id,
        approver_user_id=claims.subject,
        approver_sid=claims.sid,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"data": result}


@router.post("/auth/device/deny")
async def device_deny(
    body: DeviceDenyRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Deny the TYPED user_code (web session Bearer; idempotent on terminals)."""
    claims = await _require_web_session_claims(request, session)
    await _rate_limit(
        request,
        f"device-confirm:{claims.subject}:{_client_ip(request)}",
        limit=CONFIRM_LIMIT,
        window=CONFIRM_WINDOW_SECONDS,
        response=response,
    )
    result = await _device_service(request).deny(
        user_code=body.user_code,
        denier_user_id=claims.subject,
        denier_sid=claims.sid,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"data": result}
