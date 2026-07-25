"""WebSocket endpoint wiring for the realtime gateway."""

from __future__ import annotations

from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from mesh.realtime.pubsub import RedisSubscriber
from mesh.realtime.session import RealtimeSession


class FastApiWebSocketChannel:
    """Adapts a Starlette WebSocket to the transport protocol."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def receive_json(self) -> dict[str, Any]:
        return await self._ws.receive_json()

    async def send_json(self, data: dict[str, Any]) -> None:
        await self._ws.send_json(data)

    async def close(self) -> None:
        if self._ws.client_state == WebSocketState.CONNECTED:
            await self._ws.close()


async def realtime_ws_endpoint(websocket: WebSocket) -> None:
    """The ``/ws`` endpoint: one :class:`RealtimeSession` per connection."""
    state = websocket.app.state
    await websocket.accept()
    session = RealtimeSession(
        FastApiWebSocketChannel(websocket),
        session_factory=state.session_factory,
        authenticator=state.authenticator,
        authorizer=state.authorizer,
        subscriber_factory=lambda: RedisSubscriber(state.redis),
        replay_page_size=state.settings.realtime_replay_page_size,
        ping_interval=state.settings.ws_ping_interval,
    )
    await session.run()
