"""Owner invariant guard — a workspace must never lose its last active owner.

The roster must always contain at least one member that is both
``role='owner'`` and ``status='active'``: workspace entry is gated on the
member row's ``status='active'`` (auth/rbac.py resolve_workspace_context) and
on the user being active (auth/deps.py), so a workspace with zero active
owners is unreachable and can only be repaired by direct database
intervention. (The users-level gate is not enforced here — no write path
changes ``users.status`` today; an account-deactivation feature must extend
this invariant.) This module is the single enforcement point shared by the
three paths that can strip active-owner status — role demotion
(workspace/members.py), removal and disable (member/service.py); protections
are server-enforced and never rely on UI disabling (member.md §3.3/§5.3).

TOCTOU serialization: callers must decide whether an operation reduces the
active-owner count ONLY from post-lock state. ``lock_active_owner_set`` locks
the target row plus every active owner row in ONE ``SELECT ... FOR UPDATE``
statement ordered by ascending ``id``, and refreshes any session-cached
Member entity with the locked values. Two properties follow:

1. Gate-skip safety. A reducer whose pre-lock read saw the target as a plain
   member cannot race a concurrent promotion: the sweep locks the target row,
   so it blocks until the in-flight promotion commits, then re-reads the
   target as an owner (EvalPlanQual + populate_existing) and the guard fires.
2. Deadlock freedom. Every locking participant acquires its member-row locks
   in a single ascending-id sweep (LockRows runs above Sort), so two
   concurrent operations queue on the lowest shared row instead of forming a
   cycle. The loser re-reads the winner's committed reduction and is
   rejected; locks release with the enclosing transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.db.models.member import Member

LAST_OWNER_CODE = "last_owner"


async def lock_active_owner_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    target_id: uuid.UUID,
) -> tuple[int, Member | None]:
    """Lock the target row + all active owners; return post-lock truth.

    Runs inside the caller's transaction (locks held until its end). The
    ``populate_existing`` option refreshes a Member already in the identity
    map (e.g. from an earlier unlocked read) so callers decide on the locked
    state, not a stale snapshot.

    Returns ``(active_owner_count, target)`` where ``target`` is the locked
    member row, or ``None`` when ``target_id`` is not a member of this
    workspace. The count includes the target when it is itself an active
    owner.
    """
    rows = (
        await session.execute(
            select(Member)
            .where(
                Member.workspace_id == workspace_id,
                or_(
                    and_(Member.role == "owner", Member.status == "active"),
                    Member.id == target_id,
                ),
            )
            .order_by(Member.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    target = None
    active_owners = 0
    for row in rows:
        if row.id == target_id:
            target = row
        if row.role == "owner" and row.status == "active":
            active_owners += 1
    return active_owners, target


__all__ = ["LAST_OWNER_CODE", "lock_active_owner_set"]
