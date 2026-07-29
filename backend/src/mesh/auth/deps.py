"""FastAPI dependencies for the auth module.

``get_current_principal`` is the unified Bearer gate (auth.md §2.5.1 prefix
registry, review H7): one dependency routes EVERY regular ``/api/v1`` request
by credential prefix — session access JWT (stateless verify, fixed ``alg`` —
the token header is never trusted), ``mesh_pat_`` / ``mesh_agt_`` (hashed
lookup via :class:`TokenService`, type-semantics enforced), while
``mesh_rt_`` / ``mesh_rft_`` are rejected on regular routes (machine tokens
belong to ``/api/v1/daemon/*``; refresh tokens to ``/auth/refresh`` only).
``get_current_user`` adapts a principal to the ``users`` row for routes that
need the global login identity; workspace-scoped authorization (RBAC over
``members.role`` ∩ token scopes) layers on top in the rbac module.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.api.deps import extract_bearer_token, get_session
from mesh.auth import jwt as jwt_mod
from mesh.config import Settings
from mesh.db.models.member import Member
from mesh.db.models.user import Session, User
from mesh.errors import ForbiddenError, UnauthorizedError


def get_auth_service(request: Request):
    """The shared :class:`AuthService` built at app startup."""
    return request.app.state.auth_service


def require_current_access(request: Request) -> jwt_mod.AccessToken:
    """Decode the request's Bearer access JWT into its validated claims.

    For routes that need claim-level detail (``sid`` for session lifecycle
    operations — auth.md §1.1 registry) beyond the ``User`` row that
    :func:`get_current_user` provides. Verification is identical: fixed
    algorithm, ``exp`` enforced, ``typ=access`` required.
    """
    settings: Settings = request.app.state.settings
    token = extract_bearer_token(request.headers.get("Authorization"))
    return jwt_mod.decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """A Bearer credential resolved to its acting identity (auth.md §2.5.1).

    ``kind`` is ``session`` (access JWT), ``pat`` (human personal token) or
    ``agent`` (agent runtime credential). Effective permissions are ALWAYS
    ``scopes ∩ holder role`` — for sessions the access JWT carries the fixed
    scope claim (empty ⇒ role-based, web sessions), for PAT/agent tokens the
    token service already intersected scopes with the current role.
    """

    kind: str  # "session" | "pat" | "agent"
    member_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None  # PAT: owning ws; session: device binding
    session_id: uuid.UUID | None = None
    subject: uuid.UUID | None = None  # JWT sub (sessions)
    token_id: uuid.UUID | None = None  # api_tokens.id (PAT/agent)
    scopes: frozenset[str] = field(default_factory=frozenset)


async def get_current_principal(
    request: Request, session: AsyncSession = Depends(get_session)
) -> AuthenticatedPrincipal:
    """Route the Bearer credential by prefix (auth.md §2.5.1 registry)."""
    from mesh.auth.security import REFRESH_TOKEN_PREFIX
    from mesh.auth.tokens import AGENT_TOKEN_PREFIX, PAT_TOKEN_PREFIX
    from mesh.db.models.api_token import RUNTIME_TOKEN_PREFIX

    settings: Settings = request.app.state.settings
    token = extract_bearer_token(request.headers.get("Authorization"))

    # Machine + refresh tokens have no business on regular routes: mesh_rt_
    # belongs to /api/v1/daemon/* (runtime.md §3.5), mesh_rft_ ONLY to
    # POST /auth/refresh. Their appearance here is rejected outright.
    if token.startswith(RUNTIME_TOKEN_PREFIX) or token.startswith(REFRESH_TOKEN_PREFIX):
        raise UnauthorizedError(
            "invalid or expired token", details={"reason": "credential_misrouted"}
        )

    if token.startswith(PAT_TOKEN_PREFIX) or token.startswith(AGENT_TOKEN_PREFIX):
        token_service = request.app.state.token_service
        resolved = await token_service.resolve_pat(
            token=token,
            ip_address=request.client.host if request.client else None,
        )
        if resolved is None:
            raise UnauthorizedError("invalid or expired token")
        # Type semantics (§2.5.1 R2-H2): the prefix must match the holder's
        # member type — a mesh_pat_ resolving to an agent row (or vice versa)
        # is a forged/mis-issued credential.
        expected = "human" if token.startswith(PAT_TOKEN_PREFIX) else "agent"
        if resolved.member_type != expected:
            raise UnauthorizedError(
                "invalid or expired token", details={"reason": "credential_type_mismatch"}
            )
        member = await session.scalar(
            select(Member).where(
                Member.workspace_id == resolved.workspace_id,
                Member.id == resolved.owner_member_id,
            )
        )
        return AuthenticatedPrincipal(
            kind="pat" if expected == "human" else "agent",
            member_id=resolved.owner_member_id,
            user_id=member.user_id if member is not None else None,
            workspace_id=resolved.workspace_id,
            token_id=resolved.id,
            scopes=resolved.scopes,
        )

    # Everything else must be a session access JWT.
    claims = jwt_mod.decode_access_token(
        token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return AuthenticatedPrincipal(
        kind="session",
        subject=claims.subject,
        user_id=claims.subject,
        session_id=claims.sid,
        workspace_id=claims.workspace_id,
        scopes=claims.scopes,
    )


async def get_current_user(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the authenticated USER behind the credential.

    Session principals resolve by ``sub``; human PAT principals by the
    holder's ``members.user_id``. Agent credentials have no user identity —
    user-level routes reject them (agent-facing endpoints consume the
    principal directly).
    """
    if principal.user_id is None:
        raise UnauthorizedError(
            "this credential has no user identity",
            details={"reason": "agent_credential"},
        )
    user = await session.scalar(select(User).where(User.id == principal.user_id))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return user


async def _recent_auth(
    request: Request, session: AsyncSession, *, allowed_types: tuple[str, ...]
) -> User:
    """Session-location-invariant step-up gate (auth.md §1.1 / §5.5, R6/R7).

    Sensitive operations ALWAYS read the sessions row by the access JWT's
    ``sid`` (never the claim alone) — a revoked session inside the access TTL
    window cannot pass, and the freshness verdict comes from the authoritative
    ``sessions.authenticated_at``: NULL (no recent primary auth) or stale ⇒
    ``403 reauth_required``. PAT/agent tokens have no interactive session and
    are rejected with ``reason=interactive_session_required`` — the CLI
    recovery path is Web reauth + re-approving a device login (cli.md §4.3).
    """
    settings: Settings = request.app.state.settings
    principal = await get_current_principal(request, session)
    if principal.kind != "session":
        raise ForbiddenError(
            "recent re-authentication required",
            code="reauth_required",
            details={"reason": "interactive_session_required"},
        )
    if principal.session_id is None:
        raise ForbiddenError(
            "recent re-authentication required",
            code="reauth_required",
            details={"reason": "session_not_locatable"},
        )
    now = datetime.now(UTC)
    row = await session.scalar(
        select(Session).where(
            Session.id == principal.session_id,
            Session.user_id == principal.subject,
            Session.type.in_(allowed_types),
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
    )
    if row is None:
        # Invariant: 0 rows ⇒ 401, never distinguishing why (anti-enumeration).
        raise UnauthorizedError("invalid or expired token")
    if (
        row.authenticated_at is None
        or now - row.authenticated_at > settings.reauth_window
    ):
        raise ForbiddenError("recent re-authentication required", code="reauth_required")
    user = await session.scalar(select(User).where(User.id == row.user_id))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return user


async def require_recent_auth(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Step-up gate for PAT create/revoke + agent credential issuance —
    ``web`` OR ``cli`` sessions (auth.md §1.1 credential matrix: a freshly
    approved device login inherits the approver's authentication moment)."""
    return await _recent_auth(request, session, allowed_types=("web", "cli"))


async def require_recent_auth_web_only(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Step-up gate for 2FA management / OAuth link+unlink — WEB sessions
    only (auth.md §1.1 credential matrix)."""
    return await _recent_auth(request, session, allowed_types=("web",))


async def require_web_session_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User:
    """Web-session-only identity gate (no freshness window) — for routes the
    credential matrix reserves to interactive browser sessions (e.g.
    change-password, whose own old-password check IS the re-auth, R7-M1)."""
    principal = await get_current_principal(request, session)
    if principal.kind != "session":
        raise ForbiddenError(
            "interactive browser session required",
            code="forbidden",
            details={"reason": "web_session_required"},
        )
    if principal.session_id is None:
        raise UnauthorizedError("invalid or expired token")
    now = datetime.now(UTC)
    row = await session.scalar(
        select(Session).where(
            Session.id == principal.session_id,
            Session.user_id == principal.subject,
            Session.type == "web",
            Session.revoked_at.is_(None),
            Session.expires_at > now,
        )
    )
    if row is None:
        raise UnauthorizedError("invalid or expired token")
    user = await session.scalar(select(User).where(User.id == row.user_id))
    if user is None or user.status != "active":
        raise UnauthorizedError("invalid or expired token")
    return user
