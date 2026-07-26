"""Realtime authentication and per-channel authorization (README §6.7 / §6.16).

Tokens never travel in the URL (§6.16): the WebSocket protocol authenticates
via the first frame after connect; REST reconciliation uses the Bearer header.

Every channel subscription re-runs resource-level authorization. The mandatory
floor is database-level channel ownership (``realtime_channels.workspace_id``);
modules register additional per-entity checkers (project visibility, issue
visibility, ...) via :meth:`DefaultChannelAuthorizer.register_prefix_checker`.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy import select, text

from mesh.db.models.member import Member
from mesh.db.models.realtime import RealtimeChannel
from mesh.db.models.user import User
from mesh.db.tenant import set_tenant_context
from mesh.errors import UnauthorizedError
from mesh.realtime.channels import parse_channel

DEV_TOKEN_PREFIX = "mesh-dev:"


@dataclass(frozen=True)
class Principal:
    """An authenticated caller and the workspaces it may access."""

    subject: str
    workspace_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    def can_access_workspace(self, workspace_id: uuid.UUID) -> bool:
        return workspace_id in self.workspace_ids


@runtime_checkable
class Authenticator(Protocol):
    """Token → principal. Returns None when the token is not valid."""

    async def authenticate(self, token: str) -> Principal | None: ...


class DevTokenAuthenticator:
    """Development-only authenticator (``auth_mode=dev``).

    Token format: ``mesh-dev:<workspace-uuid>`` — grants a dev principal access
    to exactly that workspace. Replaced by the auth module's real
    authenticator; never active when ``auth_mode=production``.
    """

    async def authenticate(self, token: str) -> Principal | None:
        if not token.startswith(DEV_TOKEN_PREFIX):
            return None
        raw_workspace = token[len(DEV_TOKEN_PREFIX) :].strip()
        try:
            workspace_id = uuid.UUID(raw_workspace)
        except ValueError:
            return None
        return Principal(subject="dev-user", workspace_ids=frozenset({workspace_id}))


class NullAuthenticator:
    """Production placeholder until the auth module lands: rejects everything."""

    async def authenticate(self, token: str) -> Principal | None:  # noqa: ARG002
        return None


# Resource-level checker for one channel entity prefix (e.g. "issue", "project").
PrefixChecker = Callable[[Principal, str], Awaitable[bool]]

# Channels whose key is itself a workspace id (``workspace:{ws}[:…]``). Their
# privacy boundary is workspace membership alone, so authorization parses the
# workspace straight from the key and needs no resource checker or channel row.
WORKSPACE_SCOPED_ENTITY = "workspace"
MEMBER_INBOX_ENTITY = "member"
MEMBER_INBOX_SUFFIX = ":inbox"

# Resource-scoped entities: their privacy boundary is finer than workspace
# membership (e.g. a *private* project inside a workspace), so they MUST have a
# registered checker. If such an entity is subscribed without a checker we deny
# fail-closed (CWE-862) — a missing registration must never silently disclose a
# private resource. Entities NOT listed here (e.g. not-yet-implemented modules)
# keep the workspace-membership floor until they opt into resource-level checks
# by adding themselves here AND registering a checker.
RESOURCE_SCOPED_ENTITIES: frozenset[str] = frozenset({"project", "agent"})


@runtime_checkable
class ChannelAuthorizer(Protocol):
    """Decides whether a principal may subscribe to a channel.

    Returns the owning workspace id when authorized (callers use it to scope
    tenant-bound queries), or ``None`` when the subscription is denied.
    """

    async def authorize(self, principal: Principal, channel: str) -> uuid.UUID | None: ...


class DefaultChannelAuthorizer:
    """Database-level channel ownership + optional per-entity resource checks."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._prefix_checkers: dict[str, PrefixChecker] = {}

    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None:
        """Register a resource-level checker for channels with ``entity:<key>``."""
        self._prefix_checkers[entity] = checker

    async def authorize(self, principal: Principal, channel: str) -> uuid.UUID | None:
        """Return the owning workspace when the principal may subscribe, else None.

        Authorization is layered so a missing projector row can never lock a
        legitimate owner out, and an unregistered resource entity can never leak
        (CWE-862 fail-closed):

        * **Workspace-scoped channels** (``workspace:{ws}[:…]``): the workspace is
          parsed straight from the channel key and checked against the principal's
          memberships — independent of any ``realtime_channels`` row, so the very
          first subscribe (before the projector has materialised the row) succeeds
          for a member instead of racing to ``forbidden``.
        * **Resource-scoped channels** (``project:{id}``, …): the workspace is
          resolved from the persisted channel row (the row carries the authoritative
          ``workspace_id``), then a registered *resource checker* re-verifies
          resource-level visibility (e.g. private-project membership) on top of the
          workspace floor.
        * **Fail-closed**: a resource entity that has *no* registered checker is
          denied. Without a checker we cannot enforce resource-level privacy, so we
          refuse rather than fall back to the workspace floor — a missing
          registration must never silently disclose a private resource.
        """
        info = parse_channel(channel)
        if info is None:
            return None

        if info.entity == WORKSPACE_SCOPED_ENTITY:
            # The key may carry sub-paths (``workspace:{ws}:projects``); the
            # workspace id is always the leading colon-delimited segment.
            try:
                owner = uuid.UUID(info.key.split(":", 1)[0])
            except ValueError:
                return None
            if owner not in principal.workspace_ids:
                return None
            return owner

        if info.entity == MEMBER_INBOX_ENTITY and info.key.endswith(MEMBER_INBOX_SUFFIX):
            # member:{member_id}:inbox — ownership is resolved from the
            # ROSTER, not from realtime_channels: an inbox that has never
            # received a notification has no channel row yet, and the owner
            # must still be able to subscribe to receive the FIRST one live
            # (comment-inbox.md §3.6 / I9 realtime badge).
            owner = await self._member_inbox_workspace(principal, info.key)
            if owner is None:
                return None
            checker = self._prefix_checkers.get(info.entity)
            if checker is not None and not await checker(principal, channel):
                return None
            return owner

        owner = await self._owning_workspace(principal, channel)
        if owner is None:
            return None
        checker = self._prefix_checkers.get(info.entity)
        if checker is None:
            # Fail-closed for declared resource entities lacking a checker
            # (CWE-862); unknown entities keep the workspace-membership floor.
            if info.entity in RESOURCE_SCOPED_ENTITIES:
                return None
            return owner
        if not await checker(principal, channel):
            return None
        return owner

    async def _member_inbox_workspace(
        self, principal: Principal, key: str
    ) -> uuid.UUID | None:
        """Resolve the owning workspace of a ``member:{id}:inbox`` channel.

        The member row must exist (active) in one of the principal's
        workspaces AND belong to the principal's user (dev principals —
        non-UUID subjects — are workspace-scoped by definition, matching the
        dev convention elsewhere in this module).
        """
        member_raw = key[: -len(MEMBER_INBOX_SUFFIX)]
        try:
            member_id = uuid.UUID(member_raw)
        except ValueError:
            return None
        try:
            user_id = uuid.UUID(principal.subject)
        except ValueError:
            user_id = None
        for workspace_id in sorted(principal.workspace_ids):
            async with self._session_factory() as session:
                await set_tenant_context(session, workspace_id)
                member = await session.scalar(
                    select(Member).where(
                        Member.id == member_id,
                        Member.workspace_id == workspace_id,
                        Member.status == "active",
                    )
                )
            if member is None:
                continue
            if user_id is not None and member.user_id != user_id:
                return None  # someone else's inbox — never leak existence
            return workspace_id
        return None

    async def _owning_workspace(self, principal: Principal, channel: str) -> uuid.UUID | None:
        for workspace_id in sorted(principal.workspace_ids):
            async with self._session_factory() as session:
                await set_tenant_context(session, workspace_id)
                owner = await session.scalar(
                    select(RealtimeChannel.workspace_id).where(
                        RealtimeChannel.channel == channel,
                        RealtimeChannel.workspace_id == workspace_id,
                    )
                )
            if owner is not None:
                return owner
        return None


class JwtPrincipalAuthenticator:
    """Session access JWT → principal (auth.md §3.1 credentials, README §6.16).

    Verifies the access JWT (the expected algorithm is fixed; the token header
    is never trusted), then resolves the user's ``active`` roster entries to
    ``workspace_ids`` via the ``mesh_my_workspaces`` SECURITY DEFINER function
    (the caller's workspaces are not known up front, so no tenant GUC can be
    set first — RLS stays fail-closed everywhere else, workspace.md §5.1).
    Invalid / expired / foreign-signed tokens return ``None`` so a chained
    authenticator can try its remaining candidates.
    """

    def __init__(self, session_factory, *, jwt_secret: str, jwt_algorithm: str) -> None:
        self._session_factory = session_factory
        self._jwt_secret = jwt_secret
        self._jwt_algorithm = jwt_algorithm

    async def authenticate(self, token: str) -> Principal | None:
        # Deferred import: mesh.auth.__init__ pulls auth.deps → api.deps →
        # realtime.auth (this module), so a top-level import would cycle.
        from mesh.auth.jwt import decode_access_token

        try:
            claims = decode_access_token(
                token, secret=self._jwt_secret, algorithm=self._jwt_algorithm
            )
        except UnauthorizedError:
            return None
        async with self._session_factory() as session:
            user = await session.scalar(select(User).where(User.id == claims.subject))
            if user is None or user.status != "active":
                return None
            rows = (
                await session.execute(
                    text("SELECT workspace_id FROM mesh_my_workspaces(:uid) WHERE status = 'active'"),
                    {"uid": claims.subject},
                )
            ).all()
        return Principal(
            subject=str(claims.subject),
            workspace_ids=frozenset(row.workspace_id for row in rows),
        )


class ChainedAuthenticator:
    """Try authenticators in order; the first principal wins (dev: JWT + dev token)."""

    def __init__(self, authenticators: Sequence[Authenticator]) -> None:
        self._authenticators = tuple(authenticators)

    async def authenticate(self, token: str) -> Principal | None:
        for authenticator in self._authenticators:
            principal = await authenticator.authenticate(token)
            if principal is not None:
                return principal
        return None


def build_authenticator(
    *,
    auth_mode: str,
    jwt_secret: str,
    jwt_algorithm: str,
    session_factory,
) -> Authenticator:
    """Build the token → principal authenticator for an app (README §6.16).

    ``production``: session access JWTs only. ``dev``: JWTs plus the
    loopback-only ``mesh-dev:<workspace-id>`` token, so both real sessions
    and scripted fixtures authenticate against the local stack.
    """
    jwt_authenticator = JwtPrincipalAuthenticator(
        session_factory, jwt_secret=jwt_secret, jwt_algorithm=jwt_algorithm
    )
    if auth_mode == "dev":
        return ChainedAuthenticator((jwt_authenticator, DevTokenAuthenticator()))
    return jwt_authenticator
