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
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from sqlalchemy import func, select

from mesh.db.models.realtime import RealtimeChannel, RealtimeEvent
from mesh.db.tenant import set_tenant_context
from mesh.realtime.auth import Authenticator, ChannelAuthorizer, Principal
from mesh.realtime.channels import parse_channel
from mesh.realtime.pubsub import RedisSubscriber

logger = logging.getLogger("mesh.realtime")

RECONCILIATION_PATH = "/api/v1/realtime/events"
# Unauthenticated connections are closed after this window: short enough that
# silent sockets cannot occupy gateway resources for long (M4 DoS hardening),
# long enough for a real client to send the first-frame auth (§6.16).
AUTH_TIMEOUT_SECONDS = 5.0

# Per-connection DoS limits (M4): subscriptions are capped so one connection
# cannot fan out an unbounded channel set, and inbound frames are limited per
# rolling second so a flooding client is dropped instead of saturating the
# gateway. The frame-size ceiling is enforced at the transport layer
# (uvicorn ``--ws-max-size``, docker-compose).
DEFAULT_MAX_SUBSCRIPTIONS = 256
DEFAULT_MAX_FRAMES_PER_SECOND = 30
RATE_LIMIT_WINDOW_SECONDS = 1.0

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
        max_subscriptions: int = DEFAULT_MAX_SUBSCRIPTIONS,
        max_frames_per_second: int = DEFAULT_MAX_FRAMES_PER_SECOND,
        clock: Callable[[], float] | None = None,
        redis: Any = None,
    ) -> None:
        self._transport = transport
        self._session_factory = session_factory
        self._authenticator = authenticator
        self._authorizer = authorizer
        self._subscriber_factory = subscriber_factory
        self._replay_page_size = replay_page_size
        self._ping_interval = ping_interval
        self._auth_timeout = auth_timeout
        self._max_subscriptions = max_subscriptions
        self._max_frames_per_second = max_frames_per_second
        self._clock = clock or time.monotonic
        self._redis = redis
        self._frame_times: deque[float] = deque()
        self._state = ConnectionState()
        self._send_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        # view:{id} channels this connection is present on → owner workspace
        # (kanban §3.5 view.presence); cleared on unsubscribe/disconnect.
        self._presence_channels: dict[str, Any] = {}

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

    async def _note_view_presence(self, channel: str, owner: Any, *, joined: bool) -> None:
        """Broadcast view.presence for view:{id} channels (kanban §3.5).

        Best-effort and isolated: presence must never break the session. The
        ``redis`` dependency is optional so sessions constructed without it
        (e.g. older tests) simply skip presence.
        """
        if self._redis is None:
            return
        from mesh.views.presence import VIEW_CHANNEL_PREFIX, note_presence

        if not channel.startswith(VIEW_CHANNEL_PREFIX):
            return
        principal = self._state.principal
        subject = principal.subject if principal is not None else "anonymous"
        if joined:
            self._presence_channels[channel] = owner
            effective_owner = owner
        else:
            # Leaving: use the owner recorded at join time (the caller may not
            # have it handy on disconnect cleanup).
            effective_owner = self._presence_channels.pop(channel, None)
        if effective_owner is None:
            return
        await note_presence(
            self._session_factory,
            self._redis,
            workspace_id=effective_owner,
            channel=channel,
            subject=subject,
            joined=joined,
        )

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
            # Announce departure from any view channels still present on.
            for channel in list(self._presence_channels):
                with contextlib.suppress(Exception):
                    await self._note_view_presence(channel, None, joined=False)
            pump_task.cancel()
            heartbeat_task.cancel()
            for task in (pump_task, heartbeat_task):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def _authenticate(self) -> bool:
        try:
            first = await asyncio.wait_for(self._transport.receive_json(), timeout=self._auth_timeout)
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

    def _inbound_rate_limited(self) -> bool:
        """True when the client exceeded the per-second inbound frame budget.

        Rolling window: timestamps older than ``RATE_LIMIT_WINDOW_SECONDS`` are
        evicted, then the frame count within the window is compared against the
        limit. A sustained flood trips it immediately; a burst followed by
        silence recovers once the window clears (M4 DoS hardening).
        """
        now = self._clock()
        times = self._frame_times
        times.append(now)
        while times and times[0] <= now - RATE_LIMIT_WINDOW_SECONDS:
            times.popleft()
        return len(times) > self._max_frames_per_second

    async def _message_loop(self) -> None:
        while not self._closed.is_set():
            try:
                frame = await self._transport.receive_json()
            except Exception:
                return  # client disconnected
            if self._inbound_rate_limited():
                # A flooding client is dropped, not serviced: answer once,
                # close, and let a well-behaved client reconnect (§6.7
                # resume_from recovery).
                self._closed.set()
                with contextlib.suppress(Exception):
                    await self._send_error("rate_limited", "frame rate limit exceeded")
                with contextlib.suppress(Exception):
                    await self._transport.close()
                return
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
                    await self._note_view_presence(channel, None, joined=False)
            elif op == OP_PING:
                await self._send({"op": FRAME_PING})
            else:
                # Never echo the raw op content back (M4): attacker-controlled
                # bytes would be amplified into the error frame.
                await self._send_error("validation_error", "unknown op")

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
            await self._send_error("validation_error", "resume_from must be a non-negative integer")
            return

        # Per-connection subscription cap (M4): re-subscribing an already
        # active channel stays allowed (idempotent replay), new channels past
        # the cap are refused without closing the connection.
        if (
            channel not in self._state.subscriptions
            and len(self._state.subscriptions) >= self._max_subscriptions
        ):
            await self._send_error(
                "too_many_subscriptions",
                f"subscription limit reached ({self._max_subscriptions})",
                channel=channel,
            )
            return

        principal = self._state.principal
        if principal is None:
            await self._send_error("unauthorized", "not authenticated")
            return
        owner = await self._authorizer.authorize(principal, channel)
        if owner is None:
            # The structured ``channel`` field carries correlation; the message
            # itself is fixed so no client-controlled text is echoed (M4).
            await self._send_error("forbidden", "not authorized for channel", channel=channel)
            return

        # Subscribe the pump FIRST so events projected during replay are not
        # dropped (at-least-once: clients merge by channel seq — §6.7 allows
        # duplicates, never gaps).
        self._state.subscriptions.add(channel)
        await self._replay(channel, resume_from or 0, owner)
        # Replay may drop the subscription (stale → resync_required); only
        # announce presence when the channel is still subscribed.
        if channel in self._state.subscriptions:
            await self._note_view_presence(channel, owner, joined=True)

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
            if not await self._resource_subscription_still_authorized(channel):
                return
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
                # A role/grant can be revoked while this page is being sent.
                # Reauthorize at the actual row-delivery boundary so a large
                # replay page cannot leak its remaining project events.
                if not await self._resource_subscription_still_authorized(channel):
                    return
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

    async def _resource_subscription_still_authorized(self, channel: str) -> bool:
        """Re-check mutable project access before replay/live delivery.

        Subscription-time authorization is insufficient for project privacy:
        visibility, membership, and guest grants can change while a WebSocket
        remains connected. Project channels therefore fail closed at the
        delivery boundary and are removed as soon as their checker denies the
        current principal.
        """
        info = parse_channel(channel)
        # This delivery-time check was introduced for private-project event
        # privacy. Applying a full DB authorization pass to every agent,
        # execution, chat, and data-job frame would create an O(frame×viewer)
        # query regression across unrelated realtime products.
        if info is None or info.entity != "project":
            return True
        principal = self._state.principal
        if principal is not None and await self._authorizer.authorize(principal, channel) is not None:
            return True
        self._state.subscriptions.discard(channel)
        await self._note_view_presence(channel, None, joined=False)
        await self._send_error(
            "forbidden",
            "authorization changed; subscription removed",
            channel=channel,
        )
        return False

    async def _pump(self) -> None:
        """Forward Redis fan-out frames to subscribed channels."""
        subscriber = self._subscriber_factory()
        try:
            await subscriber.start()
            async for channel, frame in subscriber.frames():
                if self._closed.is_set():
                    return
                if channel in self._state.subscriptions:
                    if await self._resource_subscription_still_authorized(channel):
                        await self._send(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Fan-out death must not leave the client silently stuck on a live
            # connection: surface an error and close so the client reconnects
            # with resume_from and replays from realtime_events (§6.7 recovery).
            logger.exception("fan-out pump failed")
            with contextlib.suppress(Exception):
                await self._send_error("service_unavailable", "realtime fan-out failed; please reconnect")
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
