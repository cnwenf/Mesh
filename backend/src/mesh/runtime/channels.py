"""Per-channel resource authorization for ``execution:{id}[:logs]`` channels.

Every subscription re-runs resource-level authorization (README §6.7):
execution channels are subscribable only by members of the execution's own
workspace. Registered on BOTH the API and the realtime gateway factories so
the independently-deployed processes cannot drift (CWE-862), mirroring
agent/channels.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.db.models.runtime import TaskExecution
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel

_LOGS_SUFFIX = ":logs"


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_execution_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    authorizer.register_prefix_checker("execution", make_execution_channel_checker(session_factory))


def make_execution_channel_checker(session_factory) -> PrefixChecker:
    """``execution:{id}`` and ``execution:{id}:logs`` → workspace membership."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        key = info.key
        if key.endswith(_LOGS_SUFFIX):
            key = key[: -len(_LOGS_SUFFIX)]
        try:
            execution_id = uuid.UUID(key)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                found = await session.scalar(
                    select(TaskExecution.id).where(
                        TaskExecution.id == execution_id,
                        TaskExecution.workspace_id == workspace_id,
                    )
                )
                if found is not None:
                    return True
        return False

    return check


__all__ = ["make_execution_channel_checker", "register_execution_checkers"]
