"""``members.search_name`` projection sync (search-command-palette.md §2.2).

Every write path that can change a member's display name resyncs the
projection through ONE function — ``public.mesh_resync_search_name`` — so
index expressions, query expressions, backfill and reconcile can never
drift apart (the normalization algorithm lives exclusively in
``public.mesh_search_norm``).

The SQL function is SECURITY DEFINER (owner role): a ``users`` rename
touches that user's member rows in EVERY workspace, which the tenant GUC /
RLS on the ``mesh_app`` connection would otherwise hide — the same pattern
as the ``mesh_<entity>_workspace_id`` resolvers (README §6.2 rule 5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_RESYNC = text("SELECT public.mesh_resync_search_name(:kind, :ident)")


async def sync_member_search_name(session: AsyncSession, member_id: uuid.UUID) -> int:
    """Resync one member row; returns the number of rows changed (0 or 1)."""
    return (await session.execute(_RESYNC, {"kind": "member", "ident": member_id})).scalar_one()


async def recompute_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Resync ALL member rows of a human identity (every workspace).

    Called when ``users.display_name`` / ``users.email`` change.
    """
    return (await session.execute(_RESYNC, {"kind": "user", "ident": user_id})).scalar_one()


async def recompute_for_agent(session: AsyncSession, agent_id: uuid.UUID) -> int:
    """Resync ALL member rows of an agent identity. Called on agent rename."""
    return (await session.execute(_RESYNC, {"kind": "agent", "ident": agent_id})).scalar_one()


async def reconcile_search_names(session: AsyncSession) -> int:
    """Full comparison + repair; returns the number of rows fixed.

    The daily reconcile task (§2.2 周期对账): drift becomes observable via
    the returned count instead of silently accumulating.
    """
    return (
        await session.execute(text("SELECT public.mesh_resync_search_name('all')"))
    ).scalar_one()
