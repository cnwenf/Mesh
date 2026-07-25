"""FastAPI dependencies for the auth module.

``get_current_user`` is the protected-route gate: it parses the Bearer access
JWT (fixed ``alg`` from config — the token header is never trusted), then loads
the corresponding ``users`` row. Workspace-scoped authorization (RBAC over
``members.role``) layers on top of this in the member/workspace modules; the
auth core only needs the global login identity.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import extract_bearer_token, get_session
from mesh.auth import jwt as jwt_mod
from mesh.config import Settings
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, UnauthorizedError


def get_auth_service(request: Request):
    """The shared :class:`AuthService` built at app startup."""
    return request.app.state.auth_service


async def get_current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Resolve the authenticated user from a Bearer access JWT."""
    settings: Settings = request.app.state.settings
    token = extract_bearer_token(request.headers.get("Authorization"))
    claims = jwt_mod.decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    user = await session.scalar(select(User).where(User.id == claims.subject))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return user


async def require_recent_auth(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Step-up gate for sensitive operations (auth.md §5.5).

    Requires the bearer to have performed a primary authentication (password /
    TOTP) within ``reauth_window`` — recorded in the access token's ``auth_time``
    and forwarded across silent refreshes. A stale authentication (e.g. a stolen
    session being reused long after login) yields ``403 reauth_required`` so the
    user must re-enter credentials before changing MFA / OAuth bindings / PATs.
    """
    settings: Settings = request.app.state.settings
    token = extract_bearer_token(request.headers.get("Authorization"))
    claims = jwt_mod.decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    if datetime.now(UTC) - claims.authenticated_at > settings.reauth_window:
        raise ForbiddenError(
            "recent re-authentication required", code="reauth_required"
        )
    user = await session.scalar(select(User).where(User.id == claims.subject))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return user
