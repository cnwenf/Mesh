"""Auth HTTP routes (auth.md §3.1–§3.3).

Public endpoints (register/login/refresh/reset/verify) carry no Bearer; the rest
require a valid access JWT via :func:`get_current_user`. Login-class endpoints
are rate limited (Redis sliding window) and login additionally enforces the
(IP, email) lockout in the service layer. Tokens never appear in URLs (§6.16).

Web refresh contract (R4-H1): login/refresh deliver the refresh token ONLY via
the ``Set-Cookie: mesh_session`` cookie (HttpOnly/Secure/SameSite=Strict/Path=/)
— the response body never carries one and the client never self-declares its
form. CLI/device sessions refresh with ``Authorization: Bearer mesh_rft_…``
(issued once by the device token endpoint); one request accepts exactly one
transport. Refresh rotation follows the §3.8 bounded-idempotent contract: the
winner gets the new refresh (its transport's channel), a grace-window loser
gets ONLY a fresh access token.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import get_session
from mesh.auth.deps import (
    get_auth_service,
    get_current_principal,
    get_current_user,
    require_current_access,
    require_recent_auth,
)
from mesh.auth.ratelimit import RateLimiter
from mesh.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MfaConfirmRequest,
    MfaVerifyRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
    VerifyEmailRequest,
)
from mesh.auth.security import REFRESH_TOKEN_PREFIX
from mesh.auth.service import (
    AuthService,
    MfaRequiredResult,
    RefreshWinner,
    TokenResult,
    UserUpdate,
)
from mesh.config import Settings
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, UnauthorizedError, ValidationError

router = APIRouter(prefix="/api/v1", tags=["auth"])

# Web session cookie carrying the refresh token (R4-H1 / §3 refresh contract).
SESSION_COOKIE = "mesh_session"

# Rate-limit thresholds (auth.md §3.6 — login-class is the tightest).
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 60
REGISTER_LIMIT = 5
REGISTER_WINDOW_SECONDS = 60
# H2: the MFA second factor is a 6-digit TOTP, so its verify endpoint is tightly
# limited per (IP, ticket) — a leaked password cannot brute-force the code.
MFA_VERIFY_LIMIT = 5
MFA_VERIFY_WINDOW_SECONDS = 60
# MES-39: change-password verifies the old password server-side, so it carries
# the login-class (IP, email) throttle (§3.6) against online brute force.
CHANGE_PASSWORD_LIMIT = 5
CHANGE_PASSWORD_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


async def _rate_limit(
    request: Request, key: str, *, limit: int, window: int, response: Response
) -> None:
    limiter: RateLimiter = request.app.state.rate_limiter
    remaining, reset_in = await limiter.check(key, limit=limit, window_seconds=window)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    response.headers["X-RateLimit-Reset"] = str(reset_in)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _set_session_cookie(
    response: Response, refresh_token: str, settings: Settings, *, remember: bool
) -> None:
    """Deliver the Web refresh token (R4-H1): HttpOnly/Secure/SameSite=Strict."""
    ttl = settings.remember_refresh_token_ttl if remember else settings.refresh_token_ttl
    response.set_cookie(
        SESSION_COOKIE,
        refresh_token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _same_origin(request: Request) -> bool:
    """Origin/Referer same-site check for cookie-authenticated mutations.

    Defence in depth on top of ``SameSite=Strict`` (R4-H1): the request's
    Origin (or Referer) host must match one of the site hosts the request
    presents itself as (``Host`` / ``X-Forwarded-Host``, proxy-aware). Missing
    Origin AND Referer → deny.
    """
    site_hosts = {request.headers.get("host", "")}
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        site_hosts.update(part.strip() for part in forwarded.split(",") if part.strip())

    def _host(value: str) -> str:
        return urlsplit(value).netloc

    origin = request.headers.get("origin")
    if origin:
        return _host(origin) in site_hosts
    referer = request.headers.get("referer")
    if referer:
        return _host(referer) in site_hosts
    return False


def _access_payload(result: TokenResult | RefreshWinner) -> dict:
    """The access-only body — a refresh NEVER appears in a response body here."""
    return {
        "access_token": result.access_token,
        "token_type": "Bearer",
        "expires_in": result.expires_in,
    }


# --- public auth -------------------------------------------------------------


@router.post("/auth/register", status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response) -> dict:
    # §3.6: login-class endpoints are limited on the (IP, email) tuple — same
    # dimension as the login lockout, so a burst against one account/IP pair
    # cannot be spread across emails (or vice versa) to dodge the limit.
    await _rate_limit(
        request,
        f"register:{_client_ip(request)}:{body.email.lower()}",
        limit=REGISTER_LIMIT,
        window=REGISTER_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    user, _token = await service.register(
        email=body.email, password=body.password, display_name=body.display_name
    )
    return {"data": user}


@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict:
    ip = _client_ip(request)
    await _rate_limit(
        request,
        f"login:{ip}:{body.email.lower()}",
        limit=LOGIN_LIMIT,
        window=LOGIN_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    result = await service.login(
        email=body.email,
        password=body.password,
        remember=body.remember,
        ip_address=ip,
        user_agent=_user_agent(request),
    )
    if isinstance(result, MfaRequiredResult):
        return {"data": {"mfa_required": True, "mfa_ticket": result.mfa_ticket}}
    # R4-H1: refresh ONLY via HttpOnly cookie — the body has no refresh field.
    _set_session_cookie(response, result.refresh_token, _settings(request), remember=body.remember)
    return {"data": _access_payload(result)}


@router.post("/auth/mfa/verify")
async def mfa_verify(body: MfaVerifyRequest, request: Request, response: Response) -> dict:
    # H2: bound TOTP brute force — limited per (IP, ticket); the ticket is
    # short-lived and single-purpose, so this caps attempts per challenge.
    await _rate_limit(
        request,
        f"mfa-verify:{_client_ip(request)}:{body.mfa_ticket}",
        limit=MFA_VERIFY_LIMIT,
        window=MFA_VERIFY_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    result = await service.verify_mfa(
        mfa_ticket=body.mfa_ticket,
        code=body.code,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    # R4-H1: second-factor completion delivers refresh via cookie as well.
    _set_session_cookie(response, result.refresh_token, _settings(request), remember=False)
    return {"data": _access_payload(result)}


@router.post("/auth/refresh")
async def refresh(request: Request, response: Response) -> dict:
    """Renew an access token (§3.8 bounded idempotent rotation).

    Transport is determined by CREDENTIAL PRESENTATION, never by a client
    self-declaration (R4-H1): the ``mesh_session`` cookie marks a web-origin
    session (rotation delivered via ``Set-Cookie``); ``Bearer mesh_rft_…``
    marks a device-origin session (rotation delivered in the body). Exactly
    one transport per request — presenting both is an ambiguous request (400),
    presenting neither is unauthenticated (401). A cookie refresh additionally
    passes the Origin/Referer same-site check (CSRF defence in depth).
    """
    service: AuthService = get_auth_service(request)
    cookie_token = request.cookies.get(SESSION_COOKIE)
    authorization = request.headers.get("Authorization") or ""
    bearer_token: str | None = None
    if authorization.startswith("Bearer "):
        bearer_token = authorization[len("Bearer ") :].strip() or None
    if bearer_token is not None and not bearer_token.startswith(REFRESH_TOKEN_PREFIX):
        # Only session refresh tokens may hit this endpoint — an access JWT or
        # PAT here is a protocol violation.
        raise UnauthorizedError("invalid or expired token")
    if cookie_token and bearer_token:
        raise ValidationError(
            "exactly one refresh transport per request",
            code="invalid_request",
            details={"reason": "ambiguous_refresh_transport"},
        )

    if bearer_token:
        # Device/CLI transport: rotation plaintext leaves via the body.
        outcome = await service.refresh(
            presented_token=bearer_token,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
        if isinstance(outcome, RefreshWinner):
            return {
                "data": {
                    **_access_payload(outcome),
                    "refresh_token": outcome.refresh_token,
                }
            }
        return {"data": _access_payload(outcome)}  # grace: access only

    if cookie_token:
        if not _same_origin(request):
            # Missing / cross-origin Origin/Referer with a cookie credential —
            # CSRF defence (R4-H1); SameSite=Strict is the primary shield.
            raise ForbiddenError("cross-origin cookie refresh rejected", code="forbidden")
        outcome = await service.refresh(
            presented_token=cookie_token,
            ip_address=_client_ip(request),
            user_agent=_user_agent(request),
        )
        if isinstance(outcome, RefreshWinner):
            _set_session_cookie(
                response, outcome.refresh_token, _settings(request), remember=False
            )
        # Grace path: deliberately NO Set-Cookie — the winner already updated
        # the shared cookie jar; a second rotation would amplify the chain.
        return {"data": _access_payload(outcome)}

    raise UnauthorizedError("invalid or expired token")


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request, response: Response) -> dict:
    # §3.6: (IP, email) tuple, matching login/register.
    await _rate_limit(
        request,
        f"reset:{_client_ip(request)}:{body.email.lower()}",
        limit=REGISTER_LIMIT,
        window=REGISTER_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    await service.request_password_reset(email=body.email)
    # Constant response regardless of whether the account exists (anti-enumeration).
    return {"data": {"status": "ok"}}


@router.post("/auth/reset-password")
async def reset_password(body: ResetPasswordRequest, request: Request) -> dict:
    service: AuthService = get_auth_service(request)
    await service.reset_password(token=body.token, new_password=body.new_password)
    return {"data": {"status": "ok"}}


@router.post("/auth/verify-email")
async def verify_email(body: VerifyEmailRequest, request: Request) -> dict:
    service: AuthService = get_auth_service(request)
    await service.verify_email(token=body.token)
    return {"data": {"status": "ok"}}


# --- protected auth ----------------------------------------------------------


@router.get("/auth/token")
async def introspect_token(
    request: Request, principal=Depends(get_current_principal)
) -> dict:
    """Self-introspection (review H7): metadata for the CURRENT credential —
    kind/token_id/masked prefix/scopes/expiry/last-use, never a plaintext
    fragment. Powers ``mesh auth status``."""
    service: AuthService = get_auth_service(request)
    return {"data": await service.introspect_credential(principal=principal)}


@router.delete("/auth/token")
async def revoke_token_self(
    request: Request, principal=Depends(get_current_principal)
) -> dict:
    """Self-revocation (review H7): revoke the presented credential itself —
    no token id needed. PAT: immediate 401 afterwards; session: refresh dies
    at once, access with its TTL. Powers ``mesh auth logout --revoke``."""
    service: AuthService = get_auth_service(request)
    await service.revoke_credential(
        principal=principal,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    return {"data": {"status": "ok"}}


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    """Revoke the session named by the presented credential (auth.md §3.1).

    Web: the ``mesh_session`` cookie; CLI: ``Bearer mesh_rft_…``; as a last
    resort an access JWT's ``sid`` (so ``mesh auth logout`` with only the
    in-memory access still revokes its session). The cookie is cleared either
    way.
    """
    service: AuthService = get_auth_service(request)
    cookie_token = request.cookies.get(SESSION_COOKIE)
    authorization = request.headers.get("Authorization") or ""
    if cookie_token:
        await service.logout(refresh_token=cookie_token)
    elif authorization.startswith("Bearer "):
        token = authorization[len("Bearer ") :].strip()
        if token.startswith(REFRESH_TOKEN_PREFIX):
            await service.logout(refresh_token=token)
        else:
            claims = require_current_access(request)
            if claims.sid is None:
                raise UnauthorizedError("invalid or expired token")
            await service.logout(session_id=claims.sid, user_id=claims.subject)
    else:
        raise UnauthorizedError("invalid or expired token")
    _clear_session_cookie(response)
    return {"data": {"status": "ok"}}


@router.post("/auth/logout-all")
async def logout_all(request: Request, user: User = Depends(get_current_user)) -> dict:
    service: AuthService = get_auth_service(request)
    revoked = await service.logout_all(user_id=user.id)
    return {"data": {"revoked": revoked}}


@router.post("/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> dict:
    # §4.2/§5.5 (R7-M1): authenticated password change. Re-entering the old
    # password IS the step-up re-authentication itself, so no separate
    # recent-auth gate; the (IP, email) throttle (§3.6) bounds online brute
    # force of the old password by a hijacked session. The initiating session
    # is identified by the access JWT's sid — the body carries no refresh.
    await _rate_limit(
        request,
        f"change-password:{_client_ip(request)}:{user.email.lower()}",
        limit=CHANGE_PASSWORD_LIMIT,
        window=CHANGE_PASSWORD_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    claims = require_current_access(request)
    await service.change_password(
        user_id=user.id,
        old_password=body.old_password,
        new_password=body.new_password,
        current_session_id=claims.sid,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    return {"data": {"status": "ok"}}


@router.get("/me")
async def get_me(
    request: Request,
    principal=Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Current identity — works for every credential kind the unified Bearer
    gate routes (auth.md §5.2 representative-endpoint coverage): sessions and
    human PATs resolve to the user; agent credentials resolve to their roster
    identity (agents have no user row)."""
    from mesh.auth.service import user_to_dict

    if principal.kind == "agent":
        member = await session.get(Member, principal.member_id)
        if member is None:
            raise UnauthorizedError("invalid or expired token")
        return {
            "data": {
                "kind": "agent",
                "id": member.id,
                "member_type": member.member_type,
                "workspace_id": member.workspace_id,
                "role": member.role,
                "name": member.display_override,
                "scopes": sorted(principal.scopes),
            }
        }
    user = await session.scalar(select(User).where(User.id == principal.user_id))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return {"data": user_to_dict(user)}


@router.patch("/users/me")
async def update_me(
    body: UpdateUserRequest, request: Request, user: User = Depends(get_current_user)
) -> dict:
    service: AuthService = get_auth_service(request)
    patch = UserUpdate(
        display_name=body.display_name,
        avatar_url=body.avatar_url,
        timezone=body.timezone,
        settings=body.settings.model_dump(exclude_unset=True) if body.settings else None,
    )
    updated = await service.update_user(user_id=user.id, patch=patch)
    return {"data": updated}


@router.get("/sessions")
async def list_sessions(request: Request, user: User = Depends(get_current_user)) -> dict:
    service: AuthService = get_auth_service(request)
    sessions = await service.list_sessions(user_id=user.id)
    return {"data": sessions, "next_cursor": None}


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: str, request: Request, user: User = Depends(get_current_user)
) -> dict:
    import uuid

    from mesh.errors import ValidationError

    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise ValidationError("invalid session id", code="validation_error") from exc
    service: AuthService = get_auth_service(request)
    await service.revoke_session(user_id=user.id, session_id=sid)
    return {"data": {"status": "ok"}}


# --- MFA management ----------------------------------------------------------


@router.post("/auth/mfa/setup")
async def mfa_setup(request: Request, user: User = Depends(get_current_user)) -> dict:
    service: AuthService = get_auth_service(request)
    return {"data": await service.mfa_setup(user_id=user.id)}


@router.post("/auth/mfa/enable")
async def mfa_enable(
    body: MfaConfirmRequest, request: Request, user: User = Depends(require_recent_auth)
) -> dict:
    # §5.5: enabling 2FA is sensitive — requires a recent re-authentication.
    service: AuthService = get_auth_service(request)
    await service.mfa_enable(user_id=user.id, code=body.code)
    return {"data": {"mfa_enabled": True}}


@router.post("/auth/mfa/disable")
async def mfa_disable(
    body: MfaConfirmRequest, request: Request, user: User = Depends(require_recent_auth)
) -> dict:
    # §5.5: disabling 2FA is sensitive — requires a recent re-authentication.
    service: AuthService = get_auth_service(request)
    await service.mfa_disable(user_id=user.id, code=body.code)
    return {"data": {"mfa_enabled": False}}


__all__ = ["router"]
