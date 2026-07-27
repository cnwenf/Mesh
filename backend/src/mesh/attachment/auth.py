"""Principal resolution for attachment routes (attachment.md §3 / §5.3).

Humans authenticate with session JWTs; agent runtimes use API tokens over the
SAME endpoints (§5.3 core difference) — so attachment routes accept either
bearer credential and de-polymorphise both into a ``members`` roster row
(README §6.1: no discriminator columns, humans and agents share the roster).

Workspace gating reuses ``resolve_workspace_context`` for JWT callers; token
callers are pinned to the token's own workspace and re-validated against the
holder's CURRENT role (mirrors auth/tokens.py behavior).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.api.deps import extract_bearer_token
from mesh.auth import jwt as jwt_mod
from mesh.auth.rbac import role_satisfies
from mesh.auth.tokens import ResolvedToken, TokenService
from mesh.db.models.member import Member
from mesh.db.models.user import User
from mesh.errors import ForbiddenError, NotFoundError, UnauthorizedError

_WORKSPACE_NOT_FOUND = "workspace not found"


@dataclass(frozen=True)
class Caller:
    """An authenticated attachment API caller (human JWT or agent/human PAT)."""

    user: User | None
    token: ResolvedToken | None

    @property
    def is_token(self) -> bool:
        return self.token is not None


async def authenticate(
    request: Request, session_factory: async_sessionmaker[AsyncSession]
) -> Caller:
    """Parse the Bearer credential: JWT session first, then API token."""
    token = extract_bearer_token(request.headers.get("Authorization"))
    settings = request.app.state.settings

    # 1) Session JWT (human browser/CLI).
    try:
        claims = jwt_mod.decode_access_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except Exception:  # noqa: BLE001 — fall through to PAT before failing
        claims = None
    if claims is not None:
        async with session_factory() as session:
            user = await session.scalar(select(User).where(User.id == claims.subject))
        if user is not None:
            return Caller(user=user, token=None)

    # 2) API token (agent runtime or human PAT, §5.3).
    token_service = TokenService(session_factory)
    client_ip = request.client.host if request.client is not None else None
    resolved = await token_service.resolve_pat(token=token, ip_address=client_ip)
    if resolved is not None:
        return Caller(user=None, token=resolved)

    raise UnauthorizedError("invalid or missing credentials")


async def gate_workspace(
    session: AsyncSession,
    caller: Caller,
    workspace_id: uuid.UUID,
    *,
    permission: str | None = None,
) -> Member:
    """Resolve the caller's roster entry for ``workspace_id`` (membership + RBAC).

    Every invisible case collapses onto one 404 (unknown / foreign / deleted
    workspace are indistinguishable, §5.3).
    """
    if caller.token is not None:
        resolved = caller.token
        if resolved.workspace_id != workspace_id:
            # A token scoped to another workspace learns nothing (no leak).
            raise NotFoundError(_WORKSPACE_NOT_FOUND)
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == workspace_id,
                Member.id == resolved.owner_member_id,
                Member.status == "active",
            )
        )
        if member is None:
            raise NotFoundError(_WORKSPACE_NOT_FOUND)
        if permission is not None and not role_satisfies(resolved.role, permission):
            raise ForbiddenError("insufficient role for this action")
        return member

    from mesh.auth.rbac import resolve_workspace_context

    assert caller.user is not None  # authenticate() guarantees one or the other
    context = await resolve_workspace_context(
        session, user=caller.user, workspace_id=workspace_id, permission=permission
    )
    return context.member
