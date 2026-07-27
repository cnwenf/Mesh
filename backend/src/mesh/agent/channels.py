"""Per-channel resource authorization for ``agent:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7): an
``agent:{id}[:presence]`` channel requires workspace membership PLUS agent
visibility — ``private`` agents (agent.md §3.5) are subscribable only by
their owner and workspace admins. Registered on BOTH the API and the
realtime gateway factories so the independently-deployed processes cannot
drift (CWE-862), mirroring project/channels.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.auth.rbac import role_satisfies
from mesh.db.models.agent import Agent
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel

# ``agent:{id}:presence`` (agent.md §3.6) — the presence suffix is stripped
# before resolving the agent row.
_PRESENCE_SUFFIX = ":presence"


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_agent_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    """Register the ``agent`` entity checker everywhere at once."""
    authorizer.register_prefix_checker("agent", make_agent_channel_checker(session_factory))


def make_agent_channel_checker(session_factory) -> PrefixChecker:
    """Build the ``agent`` entity checker bound to a session factory."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        key = info.key
        if key.endswith(_PRESENCE_SUFFIX):
            key = key[: -len(_PRESENCE_SUFFIX)]
        try:
            agent_id = uuid.UUID(key)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                agent = await session.scalar(
                    select(Agent).where(
                        Agent.id == agent_id,
                        Agent.workspace_id == workspace_id,
                    )
                )
                if agent is None:
                    continue
                if agent.deleted_at is not None:
                    return False
                if agent.visibility == "workspace":
                    return True
                return await _private_agent_allowed(
                    session, principal=principal, agent=agent, workspace_id=workspace_id
                )
        return False

    return check


async def _private_agent_allowed(
    session, *, principal: Principal, agent: Agent, workspace_id: uuid.UUID
) -> bool:
    try:
        user_id = uuid.UUID(principal.subject)
    except ValueError:
        # Development principal: full workspace access by definition.
        return True
    member = await session.scalar(
        select(Member).where(
            Member.workspace_id == workspace_id,
            Member.user_id == user_id,
            Member.status == "active",
        )
    )
    if member is None:
        return False
    # §3.5: private agents — owner and workspace admins only.
    return agent.owner_user_id == user_id or role_satisfies(member.role, "agent:manage")


__all__ = ["make_agent_channel_checker", "register_agent_checkers"]
