"""Per-channel resource authorization for ``data_job:{id}`` channels.

Every subscription re-runs resource-level authorization (README §6.7 /
import-export.md §3.11): a data-job channel is subscribable only by the
job's requester or a workspace admin/owner. Registered on BOTH the API
and the realtime gateway factories so the independently-deployed
processes cannot drift (CWE-862), mirroring runtime/channels.py.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select

from mesh.db.models.data_job import DataJob
from mesh.db.models.member import Member
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import PrefixChecker, Principal
from mesh.realtime.channels import parse_channel

_MANAGER_ROLES = frozenset({"admin", "owner"})


class _CheckerRegistrar(Protocol):
    def register_prefix_checker(self, entity: str, checker: PrefixChecker) -> None: ...


def register_data_job_checkers(authorizer: _CheckerRegistrar, session_factory) -> None:
    authorizer.register_prefix_checker("data_job", make_data_job_channel_checker(session_factory))


def make_data_job_channel_checker(session_factory) -> PrefixChecker:
    """``data_job:{id}`` → requested_by member or workspace admin/owner."""

    async def check(principal: Principal, channel: str) -> bool:
        info = parse_channel(channel)
        if info is None:
            return False
        try:
            job_id = uuid.UUID(info.key)
        except ValueError:
            return False
        try:
            subject_id = uuid.UUID(principal.subject)
        except ValueError:
            return False
        for workspace_id in sorted(principal.workspace_ids):
            async with session_factory() as session:
                await set_tenant_context(session, workspace_id)
                job_requested_by = await session.scalar(
                    select(DataJob.requested_by).where(
                        DataJob.id == job_id,
                        DataJob.workspace_id == workspace_id,
                    )
                )
                if job_requested_by is None:
                    continue
                member = await session.scalar(
                    select(Member).where(
                        Member.workspace_id == workspace_id,
                        Member.user_id == subject_id,
                        Member.status == "active",
                    )
                )
                if member is None:
                    continue
                if member.id == job_requested_by or member.role in _MANAGER_ROLES:
                    return True
        return False

    return check


__all__ = ["make_data_job_channel_checker", "register_data_job_checkers"]
