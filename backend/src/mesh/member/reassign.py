"""Issue reassignment hook (member.md §3.2 / M11 资产再分配).

Removing or transferring a member reassigns their unfinished issues to another
member. Issue ownership lives in the issue.md increment, which does not exist
yet — so this module defines the seam the issue module plugs into:

- :class:`IssueReassigner` is the protocol the member module calls inside its
  own transaction (it receives the live session so the reassignment commits
  atomically with the membership change and writes ``issue.updated`` events).
- :class:`NullReassigner` is the default until the issue module lands: it
  validates nothing itself (the service validates the target member) and
  reports ``0`` reassigned issues. ``GET /members/{id}`` likewise reports
  ``counts.open_issues_assigned = 0`` until then.

The issue.md increment replaces ``NullReassigner`` with a real implementation
(one that runs ``UPDATE issues SET assignee_id = :to WHERE assignee_id = :from
AND status = ANY(:statuses)`` + per-issue audit + ``issue.updated``); nothing
else in the member module changes.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class IssueReassigner(Protocol):
    """Reassign unfinished issues from one member to another in-transaction."""

    async def reassign(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        from_member_id: uuid.UUID,
        to_member_id: uuid.UUID,
        statuses: list[str],
    ) -> int:
        """Move open issues; return the number reassigned."""
        ...

    async def open_issues_assigned(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
    ) -> int:
        """Count unfinished issues currently assigned to a member."""
        ...


class NullReassigner:
    """No-op reassigner used until the issue.md increment provides a real one."""

    async def reassign(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        from_member_id: uuid.UUID,
        to_member_id: uuid.UUID,
        statuses: list[str],
    ) -> int:
        return 0

    async def open_issues_assigned(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        member_id: uuid.UUID,
    ) -> int:
        return 0


# member.md §3.2 default open-issue statuses for bulk reassignment.
DEFAULT_REASSIGN_STATUSES: tuple[str, ...] = ("todo", "in_progress", "in_review")
