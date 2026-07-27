"""Board presence — ``view.presence`` online indicator (kanban.md §3.5/§6.7).

Collaboration awareness is OPTIONAL (kanban §1.2 "协作感知(可选)"). When a member
subscribes/unsubscribes to a ``view:{id}`` channel the gateway updates a Redis
set of present subjects and broadcasts ``view.presence`` (online count + subject)
on that view's channel through the standard outbox → projector single write path
(README §6.6/§6.7), so every other open board sees the count change.

Presence is best-effort by design: any failure is swallowed so it can NEVER
break the WebSocket session it rides on.
"""

from __future__ import annotations

import contextlib
from typing import Any

from mesh.db.tenant import set_tenant_context
from mesh.outbox.service import emit_realtime

# Presence sets expire so a crashed gateway can't leak a stale "online" member.
PRESENCE_TTL_SECONDS = 3600
VIEW_CHANNEL_PREFIX = "view:"


def _presence_key(channel: str) -> str:
    return f"mesh:presence:{channel}"


async def note_presence(
    session_factory: Any,
    redis: Any,
    *,
    workspace_id: Any,
    channel: str,
    subject: str,
    joined: bool,
) -> None:
    """Update the present-subjects set for a view channel and broadcast it."""
    with contextlib.suppress(Exception):
        key = _presence_key(channel)
        if joined:
            await redis.sadd(key, subject)
        else:
            await redis.srem(key, subject)
        await redis.expire(key, PRESENCE_TTL_SECONDS)
        members = sorted(await redis.smembers(key))
        view_id = channel.partition(":")[2]
        async with session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            await emit_realtime(
                session,
                workspace_id=workspace_id,
                channel=channel,
                event="view.presence",
                data={
                    "view_id": view_id,
                    "online": len(members),
                    "subject": subject,
                    "joined": joined,
                    "members": members,
                },
            )


__all__ = ["PRESENCE_TTL_SECONDS", "VIEW_CHANNEL_PREFIX", "note_presence"]
