"""Redis pub/sub fan-out (README §6.7).

Redis is fan-out ONLY — never the source of truth. Missed messages are covered
by replaying ``realtime_events`` from the database.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

PUBSUB_PREFIX = "mesh:rt:"


def redis_channel(channel: str) -> str:
    """Map a Mesh channel name to its Redis pub/sub channel."""
    return f"{PUBSUB_PREFIX}{channel}"


def mesh_channel(redis_key: str) -> str:
    """Inverse of :func:`redis_channel`."""
    return redis_key.removeprefix(PUBSUB_PREFIX)


class RedisFanOut:
    """Publishes projected frames to Redis after the DB transaction commits."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client

    async def publish_frame(self, channel: str, frame: dict) -> None:
        await self._redis.publish(redis_channel(channel), json.dumps(frame))

    async def publish_frames(self, frames: list[tuple[str, dict]]) -> None:
        for channel, frame in frames:
            await self.publish_frame(channel, frame)


class RedisSubscriber:
    """Consumes fan-out frames for every ``mesh:rt:*`` channel."""

    def __init__(self, redis_client: Any) -> None:
        self._pubsub = redis_client.pubsub()

    async def start(self) -> None:
        await self._pubsub.psubscribe(f"{PUBSUB_PREFIX}*")

    async def frames(self) -> AsyncIterator[tuple[str, dict]]:
        """Yield (mesh_channel, frame) tuples as they arrive."""
        async for message in self._pubsub.listen():
            if message.get("type") != "pmessage":
                continue
            channel = mesh_channel(message["channel"])
            try:
                frame = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            yield channel, frame

    async def close(self) -> None:
        await self._pubsub.punsubscribe()
        await self._pubsub.aclose()
