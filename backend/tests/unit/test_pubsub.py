"""Redis fan-out (publish/subscribe) against a real Redis."""

from __future__ import annotations

import asyncio

from mesh.realtime.pubsub import RedisFanOut, RedisSubscriber, mesh_channel, redis_channel


def test_channel_mapping_roundtrip():
    assert redis_channel("issue:abc") == "mesh:rt:issue:abc"
    assert mesh_channel("mesh:rt:issue:abc") == "issue:abc"


async def test_publish_and_receive_frame(redis_client):
    subscriber = RedisSubscriber(redis_client)
    await subscriber.start()
    await asyncio.sleep(0.1)  # let the psubscribe register

    fanout = RedisFanOut(redis_client)
    frame = {"op": "event", "channel": "issue:rt1", "seq": 7, "event": "issue.updated", "payload": {}}
    await fanout.publish_frame("issue:rt1", frame)

    async def _one():
        async for channel, received in subscriber.frames():
            return channel, received

    channel, received = await asyncio.wait_for(_one(), timeout=5)
    assert channel == "issue:rt1"
    assert received == frame
    await subscriber.close()


async def test_invalid_json_frames_are_skipped(redis_client):
    subscriber = RedisSubscriber(redis_client)
    await subscriber.start()
    await asyncio.sleep(0.1)
    await redis_client.publish("mesh:rt:issue:bad", "not-json")
    await RedisFanOut(redis_client).publish_frame("issue:bad", {"op": "event", "seq": 1})

    async def _one():
        async for channel, received in subscriber.frames():
            return channel, received

    channel, received = await asyncio.wait_for(_one(), timeout=5)
    assert channel == "issue:bad"
    assert received["seq"] == 1
    await subscriber.close()
