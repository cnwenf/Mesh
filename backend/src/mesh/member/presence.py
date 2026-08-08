"""Member online presence — gateway-driven online set (member.md §3.1/§3.5).

While a member has at least one live realtime connection subscribed to a
channel of the workspace, the gateway counts that member **online** for the
workspace. The source of truth is a Redis hash (member id → live connection
count) with a TTL so a crashed gateway can never leak a stale "online" member.

Transitions broadcast ``member.presence`` (``{"member_id", "presence"}``) on
the workspace channel through the standard outbox → projector single write
path (README §6.6/§6.7); the REST snapshot powers initial page paint.

Presence is best-effort by design: any failure is swallowed so it can NEVER
break the WebSocket session it rides on — mirroring ``view.presence``
(kanban §3.5).
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime
from mesh.workspace.service import WORKSPACE_CHANNEL

# Online entries expire so a crashed gateway can't leak a stale "online".
MEMBER_PRESENCE_TTL_SECONDS = 3600

PRESENCE_ONLINE = "online"
PRESENCE_OFFLINE = "offline"


def _presence_key(workspace_id: uuid.UUID) -> str:
    return f"mesh:presence:members:{workspace_id}"


def _members_channel(workspace_id: uuid.UUID) -> str:
    return WORKSPACE_CHANNEL.format(workspace_id=workspace_id)


async def member_presence_snapshot(
    redis: Any, *, workspace_id: uuid.UUID
) -> list[str]:
    """Sorted online member ids for the workspace.

    Best-effort: on any Redis failure the workspace simply reads as having no
    online members — presence must never break the roster page.
    """
    with contextlib.suppress(Exception):
        fields = await redis.hkeys(_presence_key(workspace_id))
        return sorted(fields)
    return []


async def note_member_presence(
    session_factory: Any,
    redis: Any,
    *,
    workspace_id: uuid.UUID,
    member_id: uuid.UUID,
    joined: bool,
) -> None:
    """Adjust the connection count and broadcast on online/offline transitions.

    One member can hold several connections (tabs/devices); only the 0→1 and
    1→0 edges change presence, so extra subscribes/unsubscribes are quiet.
    """
    with contextlib.suppress(Exception):
        key = _presence_key(workspace_id)
        field = str(member_id)
        if joined:
            count = await redis.hincrby(key, field, 1)
            await redis.expire(key, MEMBER_PRESENCE_TTL_SECONDS)
            if count != 1:
                return  # already online — no transition
            presence = PRESENCE_ONLINE
        else:
            count = await redis.hincrby(key, field, -1)
            if count > 0:
                await redis.expire(key, MEMBER_PRESENCE_TTL_SECONDS)
                return  # other connections still open — no transition
            await redis.hdel(key, field)
            presence = PRESENCE_OFFLINE
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=_members_channel(workspace_id),
                event="member.presence",
                data={"member_id": field, "presence": presence},
            )


__all__ = [
    "MEMBER_PRESENCE_TTL_SECONDS",
    "PRESENCE_OFFLINE",
    "PRESENCE_ONLINE",
    "member_presence_snapshot",
    "note_member_presence",
]
