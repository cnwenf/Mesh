"""OAuth HTTP routes (auth.md §3.1, §4.2/§4.5).

``start`` is a 302 to the provider authorization endpoint (state + PKCE); the
provider redirects back to ``callback`` which logs in (auto-registering and
binding on first login, A5) or binds to the calling account (A6). ``identities``
lists bindings; ``DELETE /{provider}`` unbinds (keeping ≥1 login method).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from mesh.auth.deps import get_current_user, require_recent_auth
from mesh.auth.oauth import OAuthService
from mesh.auth.routes import _set_session_cookie, _settings
from mesh.db.models.user import User
from mesh.errors import ValidationError

router = APIRouter(prefix="/api/v1/auth/oauth", tags=["oauth"])


def _oauth_service(request: Request) -> OAuthService:
    return request.app.state.oauth_service


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_redirect_uri(redirect_uri: str | None) -> str:
    if not redirect_uri:
        raise ValidationError(
            "redirect_uri is required", code="validation_error",
            details={"field": "redirect_uri"},
        )
    return redirect_uri


@router.get("/{provider}/start")
async def oauth_start(
    provider: str,
    request: Request,
    redirect_uri: str | None = None,
) -> RedirectResponse:
    """Public login start: 302 to the provider authorization endpoint."""
    service = _oauth_service(request)
    uri = _require_redirect_uri(redirect_uri)
    result = await service.start(provider_name=provider, redirect_uri=uri, mode="login")
    return RedirectResponse(result["authorization_url"], status_code=302)


@router.get("/{provider}/bind")
async def oauth_bind_start(
    provider: str,
    request: Request,
    redirect_uri: str | None = None,
    user: User = Depends(require_recent_auth),  # §5.5 step-up: binding OAuth is sensitive
) -> RedirectResponse:
    """Authenticated bind start: 302 to the provider, returning to bind."""
    service = _oauth_service(request)
    uri = _require_redirect_uri(redirect_uri)
    result = await service.start(
        provider_name=provider, redirect_uri=uri, mode="bind", user_id=user.id
    )
    return RedirectResponse(result["authorization_url"], status_code=302)


@router.get("/{provider}/callback")
@router.post("/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    response: Response,
    code: str | None = None,
    state: str | None = None,
) -> dict:
    """Complete the flow: exchange code, login-or-bind, return tokens/status."""
    if not code or not state:
        raise ValidationError(
            "code and state are required",
            code="validation_error",
            details={"code": bool(code), "state": bool(state)},
        )
    service = _oauth_service(request)
    data = await service.callback(
        provider_name=provider,
        code=code,
        state=state,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    # R4-H1: a login-mode callback delivers the refresh via HttpOnly cookie —
    # never in the JSON body (bind-mode responses carry no refresh).
    refresh_token = data.pop("refresh_token", None)
    if refresh_token is not None:
        _set_session_cookie(response, refresh_token, _settings(request), remember=False)
    return {"data": data}


@router.get("/identities")
async def list_identities(
    request: Request, user: User = Depends(get_current_user)
) -> dict:
    service = _oauth_service(request)
    items = await service.list_identities(user_id=user.id)
    return {"data": items, "next_cursor": None}


@router.delete("/{provider}")
async def unbind_identity(
    provider: str, request: Request, user: User = Depends(require_recent_auth)  # §5.5 step-up
) -> dict:
    service = _oauth_service(request)
    await service.unbind_identity(user_id=user.id, provider_name=provider)
    return {"data": {"status": "ok"}}
