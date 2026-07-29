"""Auth HTTP routes (auth.md §3.1–§3.3).

Public endpoints (register/login/refresh/reset/verify) carry no Bearer; the rest
require a valid access JWT via :func:`get_current_user`. Login-class endpoints
are rate limited (Redis sliding window) and login additionally enforces the
(IP, email) lockout in the service layer. Tokens never appear in URLs (§6.16).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from mesh.auth.deps import get_auth_service, get_current_user, require_recent_auth
from mesh.auth.ratelimit import RateLimiter
from mesh.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MfaConfirmRequest,
    MfaVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
    VerifyEmailRequest,
)
from mesh.auth.service import AuthService, MfaRequiredResult, TokenResult, UserUpdate
from mesh.config import SESSION_COOKIE_NAME
from mesh.db.models.user import User

router = APIRouter(prefix="/api/v1", tags=["auth"])

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


def _tokens_payload(result: TokenResult) -> dict:
    return {
        "access_token": result.access_token,
        "token_type": "Bearer",
        "expires_in": result.expires_in,
        "refresh_token": result.refresh_token,
    }


def _cookie_secure(settings) -> bool:
    return (
        settings.cookie_secure
        if settings.cookie_secure is not None
        else settings.auth_mode != "dev"
    )


def _set_session_cookie(
    response: Response, settings, refresh_token: str, *, remember: bool = False
) -> None:
    """Issue the HttpOnly mesh_session cookie (auth.md §5.5 / theme.md §2.3 ①).

    Additive parallel channel to the in-body refresh token: the entry middleware
    reads it server-side to resolve the first-frame theme. HttpOnly + Secure +
    SameSite=Strict; max_age tracks the refresh TTL (remember extends it).
    """
    ttl = (
        settings.remember_refresh_token_ttl if remember else settings.refresh_token_ttl
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        value=refresh_token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=_cookie_secure(settings),
        samesite="strict",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


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
    # §4.1 auto-login: issue the session cookie so the post-register first
    # navigation already carries the theme negotiation context.
    if _token is not None:
        _set_session_cookie(response, request.app.state.settings, _token)
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
    _set_session_cookie(
        response, request.app.state.settings, result.refresh_token, remember=body.remember
    )
    return {"data": _tokens_payload(result)}


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
    _set_session_cookie(response, request.app.state.settings, result.refresh_token)
    return {"data": _tokens_payload(result)}


@router.post("/auth/refresh")
async def refresh(body: RefreshRequest, request: Request, response: Response) -> dict:
    service: AuthService = get_auth_service(request)
    result = await service.refresh(
        refresh_token=body.refresh_token,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    # Rotation issues a fresh refresh → refresh the cookie (same max_age).
    _set_session_cookie(response, request.app.state.settings, result.refresh_token)
    return {"data": _tokens_payload(result)}


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


@router.post("/auth/logout")
async def logout(
    body: LogoutRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> dict:
    service: AuthService = get_auth_service(request)
    await service.logout(user_id=user.id, refresh_token=body.refresh_token)
    _clear_session_cookie(response)
    return {"data": {"status": "ok"}}


@router.post("/auth/logout-all")
async def logout_all(
    request: Request, response: Response, user: User = Depends(get_current_user)
) -> dict:
    service: AuthService = get_auth_service(request)
    revoked = await service.logout_all(user_id=user.id)
    _clear_session_cookie(response)
    return {"data": {"revoked": revoked}}


@router.post("/auth/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
) -> dict:
    # §4.2/§5.5: authenticated password change. Re-entering the old password IS
    # the sensitive-operation step-up re-authentication ("近期重新输入密码"), so
    # no separate recent-auth gate; the (IP, email) throttle (§3.6) bounds
    # online brute force of the old password by a hijacked session.
    await _rate_limit(
        request,
        f"change-password:{_client_ip(request)}:{user.email.lower()}",
        limit=CHANGE_PASSWORD_LIMIT,
        window=CHANGE_PASSWORD_WINDOW_SECONDS,
        response=response,
    )
    service: AuthService = get_auth_service(request)
    await service.change_password(
        user_id=user.id,
        old_password=body.old_password,
        new_password=body.new_password,
        current_refresh_token=body.refresh_token,
        ip_address=_client_ip(request),
        user_agent=_user_agent(request),
    )
    return {"data": {"status": "ok"}}


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)) -> dict:
    from mesh.auth.service import user_to_dict

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
