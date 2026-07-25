"""Owner invariant guard — a workspace must never lose its last active owner.

The roster must always contain at least one member that is both
``role='owner'`` and ``status='active'``: workspace entry is gated on
``status='active'`` (auth/rbac.py resolve_workspace_context), so a workspace
with zero active owners is unreachable and can only be repaired by direct
database intervention. This module is the single enforcement point shared by
the three paths that can strip active-owner status — role demotion
(workspace/members.py), removal and disable (member/service.py); protections
are server-enforced and never rely on UI disabling (member.md §3.3/§5.3).

TOCTOU serialization: the count is taken AFTER locking every active owner row
``FOR UPDATE`` in ascending ``id`` order. Concurrent guard calls in the same
workspace therefore queue on the first row; the loser re-reads after the
winner commits and sees the reduced count, so exactly one of two racing
"leave exactly one owner behind" operations can succeed. The shared ascending
lock order makes cross-transaction deadlocks impossible; locks release with
the enclosing transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member
from mesh.errors import ConflictError

LAST_OWNER_CODE = "last_owner"


async def ensure_not_last_active_owner(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    error_message: str,
) -> int:
    """Raise 409 ``last_owner`` if the workspace has ≤1 active owner.

    Runs inside the caller's transaction (locks held until its end).
    ``error_message`` names the rejected operation (demote / remove /
    disable). Returns the locked active-owner count (≥ 2) on success.
    """
    owner_ids = (
        await session.execute(
            select(Member.id)
            .where(
                Member.workspace_id == workspace_id,
                Member.role == "owner",
                Member.status == "active",
            )
            .order_by(Member.id.asc())
            .with_for_update()
        )
    ).scalars().all()
    if len(owner_ids) <= 1:
        raise ConflictError(error_message, code=LAST_OWNER_CODE)
    return len(owner_ids)


__all__ = ["LAST_OWNER_CODE", "ensure_not_last_active_owner"]
