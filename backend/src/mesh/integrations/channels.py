"""Per-channel resource authorization for ``integration:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7):
integration channels are subscribable only by members of the integration's
own workspace. Registered on BOTH the API and realtime gateway factories
so the independently-deployed processes cannot drift (mirrors
autopilot/channels.py). ``workspace:{ws}:integrations`` is covered by the
shared workspace-membership checker.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.db.models.integration import Integration
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_integration_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    authorizer.register_prefix_checker(
        "integration", make_integration_channel_checker(session_factory)
    )


def make_integration_channel_checker(session_factory) -> PrefixChecker:
    """``integration:{id}`` → workspace membership of that integration."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        try:
            integration_id = uuid.UUID(info.key)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                found = await session.scalar(
                    select(Integration.id).where(
                        Integration.id == integration_id,
                        Integration.workspace_id == workspace_id,
                    )
                )
                if found is not None:
                    return True
        return False

    return check


__all__ = ["make_integration_channel_checker", "register_integration_checkers"]
