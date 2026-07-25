"""FastAPI dependencies.

Middleware chain per README §6.14: parse Bearer token → workspace membership →
RBAC → rate limit. The skeleton wires the first link (token → principal) via a
pluggable authenticator; membership/RBAC/rate-limit hooks are module concerns
that plug into ``current_principal`` consumers as they land.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.config import Settings
from mesh.errors import UnauthorizedError
from mesh.realtime.auth import Authenticator, Principal

BEARER_PREFIX = "Bearer "


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_authenticator(request: Request) -> Authenticator:
    return request.app.state.authenticator


def extract_bearer_token(authorization: str | None) -> str:
    """Extract the Bearer token from an Authorization header value."""
    if not authorization or not authorization.startswith(BEARER_PREFIX):
        raise UnauthorizedError("missing bearer token")
    token = authorization[len(BEARER_PREFIX) :].strip()
    if not token:
        raise UnauthorizedError("empty bearer token")
    return token


async def current_principal(request: Request) -> Principal:
    """Resolve the requesting principal from the Authorization header."""
    authenticator: Authenticator = request.app.state.authenticator
    token = extract_bearer_token(request.headers.get("Authorization"))
    principal = await authenticator.authenticate(token)
    if principal is None:
        raise UnauthorizedError("invalid or expired token")
    return principal
