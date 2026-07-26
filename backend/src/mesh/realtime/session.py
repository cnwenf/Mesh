"""WebSocket connection state machine (README §6.7 / §6.16).

Protocol (JSON frames):

Client → server:
    {"op": "auth", "token": "..."}              (MUST be the first frame)
    {"op": "subscribe", "channel": "...", "resume_from": <int|null>}
    {"op": "unsubscribe", "channel": "..."}
    {"op": "ping"}

Server → client:
    {"op": "auth_ok"}
    {"op": "subscribed", "channel": "...", "last_seq": <int>}
    {"op": "event", "channel": "...", "seq": <int>, "event": "...", "payload": {...}}
    {"op": "resync_required", "channel": "...", "watermark": <int>, "rest": "<url>"}
    {"op": "error", "code": "...", "message": "..."}
    {"op": "ping"}

Stale cursor semantics (§6.7): ``resume_from`` older than the retention window
(no stored event at/below it while the channel watermark is ahead) →
``resync_required`` with the current watermark and the reconciliation REST URL.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from sqlalchemy import func, select

from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import Authenticator, ChannelAuthorizer, Principal
from mesh.realtime.pubsub import RedisSubscriber

logger = logging.getLogger("mesh.realtime")

RECONCILIATION_PATH = "/api/v1/realtime/events"
AUTH_TIMEOUT_SECONDS = 10.0

OP_AUTH = "auth"
OP_SUBSCRIBE = "subscribe"
OP_UNSUBSCRIBE = "unsubscribe"
OP_PING = "ping"

FRAME_AUTH_OK = "auth_ok"
FRAME_SUBSCRIBED = "subscribed"
FRAME_EVENT = "event"
FRAME_RESYNC_REQUIRED = "resync_required"
FRAME_ERROR = "error"
FRAME_PING = "ping"


class WebSocketChannel(Protocol):
    """Transport abstraction so the state machine is testable without a socket."""

    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, data: dict[str, Any]) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class SubscriberFactory(Protocol):
    def __call__(self) -> RedisSubscriber: ...


@dataclass
class ConnectionState:
    """Per-connection mutable state."""

    principal: Principal | None = None
    subscriptions: set[str] = field(default_factory=set)


def resync_rest_url(channel: str, since: int) -> str:
    """The reconciliation REST URL handed to the client on ``resync_required``."""
    return f"{RECONCILIATION_PATH}?channel={quote(channel, safe='')}&since={since}"


class RealtimeSession:
    """Drives one WebSocket connection through auth → subscribe → replay/live."""

    def __init__(
        self,
        transport: WebSocketChannel,
        *,
        session_factory: Any,
        authenticator: Authenticator,
        authorizer: ChannelAuthorizer,
        subscriber_factory: SubscriberFactory,
        replay_page_size: int = 200,
        ping_interval: float = 30.0,
        auth_timeout: float = AUTH_TIMEOUT_SECONDS,
    ) -> None:
        self._transport = transport
        self._session_factory = session_factory
        self._authenticator = authenticator
        self._authorizer = authorizer
        self._subscriber_factory = subscriber_factory
        self._replay_page_size = replay_page_size
        self._ping_interval = ping_interval
        self._auth_timeout = auth_timeout
        self._state = ConnectionState()
        self._send_lock = asyncio.Lock()
        self._closed = asyncio.Event()

    async def _send(self, frame: dict[str, Any]) -> None:
        async with self._send_lock:
            await self._transport.send_json(frame)

    async def _send_error(self, code: str, message: str, *, channel: str | None = None) -> None:
        frame: dict[str, Any] = {"op": FRAME_ERROR, "code": code, "message": message}
        # Channel-scoped errors carry the channel so the client can correlate the
        # error to the subscription it answers (e.g. retry a forbidden subscribe).
        if channel is not None:
            frame["channel"] = channel
        await self._send(frame)

    async def run(self) -> None:
        """Run the connection until it closes."""
        if not await self._authenticate():
            with contextlib.suppress(Exception):
                await self._transport.close()
            return

        pump_task = asyncio.create_task(self._pump())
        heartbeat_task = asyncio.create_task(self._heartbeat())
        try:
            await self._message_loop()
        finally:
            self._closed.set()
            pump_task.cancel()
            heartbeat_task.cancel()
            for task in (pump_task, heartbeat_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _authenticate(self) -> bool:
        try:
            first = await asyncio.wait_for(
                self._transport.receive_json(), timeout=self._auth_timeout
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                await self._send_error("unauthorized", "authentication timed out")
            return False
        except Exception:
            # Client disconnected or sent an undecodable first frame.
            with contextlib.suppress(Exception):
                await self._send_error("unauthorized", "authentication failed")
            return False

        if not isinstance(first, dict) or first.get("op") != OP_AUTH:
            await self._send_error("unauthorized", "first frame must be auth")
            return False
        token = first.get("token")
        principal = await self._authenticator.authenticate(token) if isinstance(token, str) else None
        if principal is None:
            await self._send_error("unauthorized", "invalid or expired token")
            return False
        self._state.principal = principal
        await self._send({"op": FRAME_AUTH_OK})
        return True

    async def _message_loop(self) -> None:
        while not self._closed.is_set():
            try:
                frame = await self._transport.receive_json()
            except Exception:
                return  # client disconnected
            if not isinstance(frame, dict):
                await self._send_error("validation_error", "frame must be an object")
                continue
            op = frame.get("op")
            if op == OP_SUBSCRIBE:
                await self._handle_subscribe(frame)
            elif op == OP_UNSUBSCRIBE:
                channel = frame.get("channel")
                if isinstance(channel, str):
                    self._state.subscriptions.discard(channel)
            elif op == OP_PING:
                await self._send({"op": FRAME_PING})
            else:
                await self._send_error("validation_error", f"unknown op: {op!r}")

    async def _handle_subscribe(self, frame: dict[str, Any]) -> None:
        channel = frame.get("channel")
        if not isinstance(channel, str):
            await self._send_error("validation_error", "channel is required")
            return
        resume_from = frame.get("resume_from")
        # Strict type check: JSON `true`/`false` are bools, an int subclass in
        # Python — isinstance would let them through to the replay SQL, where
        # they abort the connection. Negative seq values are meaningless.
        if resume_from is not None and (type(resume_from) is not int or resume_from < 0):
            await self._send_error(
                "validation_error", "resume_from must be a non-negative integer"
            )
            return

        principal = self._state.principal
        if principal is None:
            await self._send_error("unauthorized", "not authenticated")
            return
        owner = await self._authorizer.authorize(principal, channel)
        if owner is None:
            await self._send_error("forbidden", f"not authorized for channel: {channel}", channel=channel)
            return

        # Subscribe the pump FIRST so events projected during replay are not
        # dropped (at-least-once: clients merge by channel seq — §6.7 allows
        # duplicates, never gaps).
        self._state.subscriptions.add(channel)
        await self._replay(channel, resume_from or 0, owner)

    async def _replay(self, channel: str, resume_from: int, owner_workspace) -> None:
        """Replay stored events from ``resume_from``; emit resync_required when stale.

        Pages through the whole backlog (not a single page) so a large backlog
        can never silently drop events, then confirms with ``subscribed``. The
        owning workspace's tenant GUC is set on every session so the queries work
        under the restricted (RLS-enforced) app role (M1, §6.2 rule 5).
        """
        async with self._session_factory() as session:
            await set_tenant_context(session, owner_workspace)
            watermark = await session.scalar(
                select(RealtimeChannel.last_seq).where(RealtimeChannel.channel == channel)
            )
            min_seq = await session.scalar(
                select(func.min(RealtimeEvent.seq)).where(RealtimeEvent.channel == channel)
            )

        last_seq = watermark or 0
        if resume_from > 0:
            stale = (min_seq is not None and resume_from < min_seq) or (
                min_seq is None and resume_from <= last_seq
            )
            if stale:
                self._state.subscriptions.discard(channel)
                await self._send(
                    {
                        "op": FRAME_RESYNC_REQUIRED,
                        "channel": channel,
                        "watermark": last_seq,
                        "rest": resync_rest_url(channel, resume_from),
                    }
                )
                return

        next_seq = resume_from
        while True:
            async with self._session_factory() as session:
                await set_tenant_context(session, owner_workspace)
                rows = (
                    await session.execute(
                        select(RealtimeEvent.seq, RealtimeEvent.event, RealtimeEvent.payload)
                        .where(RealtimeEvent.channel == channel, RealtimeEvent.seq >= next_seq)
                        .order_by(RealtimeEvent.seq.asc())
                        .limit(self._replay_page_size)
                    )
                ).all()
            for seq, event, payload in rows:
                await self._send(
                    {
                        "op": FRAME_EVENT,
                        "channel": channel,
                        "seq": seq,
                        "event": event,
                        "payload": payload,
                    }
                )
            if len(rows) < self._replay_page_size:
                break
            next_seq = rows[-1][0] + 1

        async with self._session_factory() as session:
            await set_tenant_context(session, owner_workspace)
            last_seq = (
                await session.scalar(
                    select(RealtimeChannel.last_seq).where(RealtimeChannel.channel == channel)
                )
                or 0
            )
        await self._send({"op": FRAME_SUBSCRIBED, "channel": channel, "last_seq": last_seq})

    async def _pump(self) -> None:
        """Forward Redis fan-out frames to subscribed channels."""
        subscriber = self._subscriber_factory()
        try:
            await subscriber.start()
            async for channel, frame in subscriber.frames():
                if self._closed.is_set():
                    return
                if channel in self._state.subscriptions:
                    await self._send(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fan-out death must not leave the client silently stuck on a live
            # connection: surface an error and close so the client reconnects
            # with resume_from and replays from realtime_events (§6.7 recovery).
            logger.exception("fan-out pump failed")
            with contextlib.suppress(Exception):
                await self._send_error(
                    "service_unavailable", "realtime fan-out failed; please reconnect"
                )
            self._closed.set()
            with contextlib.suppress(Exception):
                await self._transport.close()
        finally:
            with contextlib.suppress(Exception):
                await subscriber.close()

    async def _heartbeat(self) -> None:
        try:
            while not self._closed.is_set():
                await asyncio.sleep(self._ping_interval)
                await self._send({"op": FRAME_PING})
        except asyncio.CancelledError:
            raise
