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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy import select

from mesh.db.models.realtime import RealtimeChannel
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


@runtime_checkable
class ChannelAuthorizer(Protocol):
    """Decides whether a principal may subscribe to a channel."""

    async def authorize(self, principal: Principal, channel: str) -> bool: ...


class DefaultChannelAuthorizer:
    """Database-level channel ownership + optional per-entity resource checks."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._prefix_checkers: dict[str, PrefixChecker] = {}

    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None:
        """Register a resource-level checker for channels with ``entity:<key>``."""
        self._prefix_checkers[entity] = checker

    async def authorize(self, principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        async with self._session_factory() as session:
            owner_workspace = await session.scalar(
                select(RealtimeChannel.workspace_id).where(RealtimeChannel.channel == channel)
            )
        if owner_workspace is None or not principal.can_access_workspace(owner_workspace):
            return False
        checker = self._prefix_checkers.get(info.entity)
        if checker is None:
            return True
        return await checker(principal, channel)
