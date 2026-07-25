"""Realtime broadcast for auth revocation (auth.md §3.7/§5.6 — C4).

When a session or token is revoked, the revocation is published through the
outbox → projector → realtime gateway unique write path (README §6.6/§6.7),
NOT an in-process event bus. Affected connections learn of the revocation on
their next heartbeat (re-auth fails → reconnect rejected); because access JWTs
are short-lived, the worst-case revocation latency is the access TTL.

The event is fanned out on the workspace channels the holder belongs to
(``workspace:<id>``), which is where live clients are subscribed. The broadcast
row commits in the SAME transaction as the revocation, so it is atomic.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mesh.outbox.service import emit_realtime

SESSION_REVOKED_EVENT = "session.revoked"
_WORKSPACE_CHANNEL = "workspace:{workspace_id}"

# Active memberships in non-deleted workspaces only — revocation need not fan out
# to workspaces the user has left or that are gone. SECURITY DEFINER bypasses RLS
# (the caller has no single tenant context for a user-global revocation).
_USER_WORKSPACES_SQL = (
    "SELECT m.workspace_id FROM mesh_my_workspaces(:uid) m "
    "JOIN workspaces w ON w.id = m.workspace_id "
    "WHERE m.status = 'active' AND w.deleted_at IS NULL"
)


async def user_workspace_ids(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """The active, non-deleted workspaces a user belongs to."""
    rows = (await session.execute(text(_USER_WORKSPACES_SQL), {"uid": user_id})).scalars().all()
    return list(rows)


async def broadcast_session_revoked(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_ids: list[uuid.UUID],
) -> int:
    """Emit ``session.revoked`` on each workspace channel. Returns fan-out count."""
    for ws_id in workspace_ids:
        await emit_realtime(
            session,
            workspace_id=ws_id,
            channel=_WORKSPACE_CHANNEL.format(workspace_id=ws_id),
            event=SESSION_REVOKED_EVENT,
            data={"user_id": str(user_id)},
        )
    return len(workspace_ids)


async def broadcast_user_revocation(session: AsyncSession, *, user_id: uuid.UUID) -> int:
    """Resolve the user's workspaces and broadcast ``session.revoked`` to each."""
    workspace_ids = await user_workspace_ids(session, user_id)
    return await broadcast_session_revoked(
        session, user_id=user_id, workspace_ids=workspace_ids
    )
