"""DingTalk Stream long-connection receive channel (integrations.md §3.2, MES-87).

Mesh dials the DingTalk gateway itself (no public callback address, no
inbound port). Re-review round: backoff reset on CONNECTED,
transition-only state broadcast, alive undecryptable-secret retry loop
with per-cycle config refresh (rotation self-heal).

    stream worker (supervised asyncio task inside mesh.workers — same
    process family as the outbox relay, NOT a new compose service)
      → per active im_dingtalk app_key with receive_mode='stream':
        advisory-lock single-instance mutex (pg_try_advisory_lock on
        hashtext('dingtalk_stream_app:'||app_key)); every integration that
        SHARES the app_key is acquired atomically and uses ONE physical
        connection (platform cap: 50 connections per app — frames route by
        the exact (chatbotCorpId, robotCode) pair and ambiguous/missing
        identities fail closed)
      → POST {gateway}/v1.0/gateway/connections/open
        {clientId, clientSecret(in-memory plaintext only), subscriptions
         [CALLBACK /v1.0/im/bot/messages/get, CALLBACK
         /v1.0/card/instances/callback], ua}
        → {endpoint, ticket} → WSS connect wss://<endpoint>?ticket=<ticket>
      → frame protocol (specVersion '1.0'):
        · SYSTEM/ping → MUST ACK {code:200, headers:verbatim, message:'OK',
          data:original payload.data} (no ACK → platform deems the
          connection unhealthy and drops it)
        · SYSTEM/disconnect → platform-requested teardown: close +
          IMMEDIATELY re-run connections/open
        · CALLBACK messages/get → the SAME shared ingestion core the HTTP
          adapter uses (ingest_verified_event, signature_status='valid',
          payload._mesh_channel='stream') → ACK {…data:'received'} after
          the ingest transaction commits (an un-ACKed frame is redelivered
          — msgId dedup makes redelivery idempotent)
        · CALLBACK card instances/callback → card-hook (MES-89 owns the
          authorization chain) → ACK
      → Mesh-side heartbeat probe (no frame for heartbeat_timeout ⇒
        reconnect) ALONGSIDE the platform ping — either liveness failure
        triggers a reconnect
      → exponential backoff 2→300s with ±20% jitter on reconnect

Transport hardening (hard constraints): wss:// ONLY (non-wss endpoint ⇒
refuse + alert, anti-downgrade); TLS certificate verification ALWAYS on
(never verify=False); the one-shot short-lived ``ticket`` rides the WS URL
query because the DingTalk protocol forces it there — the named exception
to README §6.16 "no tokens in URL query" (which targets Mesh's own /ws
long-lived session tokens); mitigated by wss + cert verification +
re-fetching the ticket on every reconnect.

The gateway base is a DEPLOY-TIME environment variable ONLY
(MESH_DINGTALK_GATEWAY_BASE — the test-injection door, M2): it never
enters integrations.config nor any admin API; a non-default value in a
production boot triggers a startup warning + audit entry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import ssl
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.constraints import violates as _violates_constraint
from mesh.db.models.integration import Integration
from mesh.db.models.runtime import Approval
from mesh.db.tenant import set_tenant_context
from mesh.errors import ValidationError
from mesh.integrations.dingtalk import (
    GATEWAY_OPEN_PATH,
    STREAM_CARD_TOPIC,
    STREAM_MESSAGE_TOPIC,
    normalize_message_payload,
    resolve_gateway_base,
    stream_user_agent,
)
from mesh.integrations.dingtalk_cards import (
    extract_dingtalk_action,
    handle_dingtalk_card_callback,
    parse_out_track_context,
)
from mesh.integrations.ingest import audit_payload, ingest_verified_event, store_event
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import decrypt_credential_value, redact_text

logger = logging.getLogger("mesh.integrations.dingtalk_stream")

# §3.2 reconnect schedule (config.stream_reconnect overrides per integration).
DEFAULT_BACKOFF_BASE_SECONDS = 2.0
DEFAULT_BACKOFF_MAX_SECONDS = 300.0
DEFAULT_BACKOFF_JITTER = 0.2
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 90.0

# stream_state literals (§2.2 / §3.9 stream-status).
STATE_CONNECTED = "connected"
STATE_RECONNECTING = "reconnecting"
STATE_DOWN = "down"

STREAM_CHANNEL_SUBJECT = "stream_channel"

# last_frame_at persistence throttle (in-memory between; DB writes bounded).
_FRAME_PERSIST_INTERVAL_SECONDS = 30.0

CONNECTIONS_OPEN_TIMEOUT_SECONDS = 10.0


class StreamOpenError(Exception):
    """connections/open failed (HTTP-level; auth failures carry status)."""

    def __init__(self, status: int, reason: str = "") -> None:
        super().__init__(reason or f"connections/open failed ({status})")
        self.status = status


class StreamEndpointInsecure(Exception):
    """The gateway returned a non-wss endpoint — refused (anti-downgrade)."""


class StreamFrameMalformed(Exception):
    """A wire frame could not be decoded into a JSON object."""


def compute_backoff(
    attempt: int,
    *,
    base: float = DEFAULT_BACKOFF_BASE_SECONDS,
    maximum: float = DEFAULT_BACKOFF_MAX_SECONDS,
    jitter: float = DEFAULT_BACKOFF_JITTER,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff 2→300s with ±20% jitter (§3.2)."""
    raw = min(base * (2 ** max(0, attempt)), maximum)
    rand = rng or random.Random()
    return raw * (1.0 + rand.uniform(-jitter, jitter))


def build_ack(frame: dict[str, Any], data: Any) -> dict[str, Any]:
    """The frame ACK: code 200 + the ORIGINAL headers verbatim + data.

    SYSTEM ping echoes the original payload data (official-SDK-isomorphic
    KeepAlive); message callbacks answer 'received'.
    """
    return {
        "code": 200,
        "headers": dict(frame.get("headers") or {}),
        "message": "OK",
        "data": data,
    }


def _reconnect_config(integration: Integration) -> tuple[float, float, float]:
    config = dict(integration.config or {})
    reconnect = config.get("stream_reconnect") or {}
    if not isinstance(reconnect, dict):
        reconnect = {}
    base = float(reconnect.get("base_seconds") or DEFAULT_BACKOFF_BASE_SECONDS)
    maximum = float(reconnect.get("max_seconds") or DEFAULT_BACKOFF_MAX_SECONDS)
    heartbeat = float(reconnect.get("heartbeat_timeout_seconds") or DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)
    return base, maximum, heartbeat


def _decrypt_app_secret(integration: Integration, signing_secret: str) -> str | None:
    config = dict(integration.config or {})
    cipher = config.get("app_secret_ref") or integration.secret_ref
    if not cipher:
        return None
    try:
        return decrypt_credential_value(str(cipher), signing_secret)
    except Exception:  # noqa: BLE001 — undecryptable secret: cannot connect
        return None


async def _default_ws_connect(url: str, *, ssl_context: ssl.SSLContext):
    """Production WS connector: the real ``websockets`` library with FORCED
    certificate verification (``ssl_context`` — never verify=False)."""
    import websockets

    return await websockets.connect(url, ssl=ssl_context, max_size=1024 * 1024, open_timeout=10)


class DingTalkStreamClient:
    """One physical gateway connection (may serve several integrations
    sharing one app_key)."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        gateway_base: str,
        ua: str | None = None,
        ssl_context: ssl.SSLContext | None = None,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
        ws_connect: Callable[..., Any] | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._gateway_base = gateway_base.rstrip("/")
        self._ua = ua or stream_user_agent()
        self._ssl_context = ssl_context or ssl.create_default_context()
        self._http_factory = http_factory
        self._ws_connect = ws_connect or _default_ws_connect
        self._ws: Any | None = None

    async def open_connection(self) -> None:
        """connections/open → WSS connect. Raises StreamOpenError on
        platform rejection (bad credentials ⇒ the connection never opens —
        the channel-level signature equivalent of 'signature invalid') and
        StreamEndpointInsecure on a non-wss endpoint."""
        if self._http_factory is not None:
            http = self._http_factory()
            owns_http = True
        else:
            http = httpx.AsyncClient(timeout=CONNECTIONS_OPEN_TIMEOUT_SECONDS)
            owns_http = True
        try:
            resp = await http.post(
                f"{self._gateway_base}{GATEWAY_OPEN_PATH}",
                json={
                    "clientId": self._app_key,
                    "clientSecret": self._app_secret,
                    "subscriptions": [
                        {"type": "CALLBACK", "topic": STREAM_MESSAGE_TOPIC},
                        {"type": "CALLBACK", "topic": STREAM_CARD_TOPIC},
                    ],
                    "ua": self._ua,
                },
            )
        finally:
            if owns_http:
                await http.aclose()
        if resp.status_code != 200:
            # §6.16: NEVER log the request body (clientSecret). method/url/status only.
            logger.error(
                "dingtalk connections/open failed: method=POST url=%s status=%s",
                f"{self._gateway_base}{GATEWAY_OPEN_PATH}",
                resp.status_code,
            )
            raise StreamOpenError(resp.status_code)
        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            raise StreamOpenError(resp.status_code, "non-JSON connections/open body") from None
        endpoint = str(body.get("endpoint") or "")
        ticket = str(body.get("ticket") or "")
        if not endpoint or not ticket:
            raise StreamOpenError(resp.status_code, "connections/open missing endpoint/ticket")
        if not endpoint.startswith("wss://"):
            # Anti-downgrade: a non-wss endpoint is refused + alerted.
            logger.error("dingtalk gateway returned a non-wss endpoint — refused (anti-downgrade)")
            raise StreamEndpointInsecure(endpoint)
        url = f"{endpoint}?ticket={quote(ticket, safe='')}"
        self._ws = await self._ws_connect(url, ssl_context=self._ssl_context)

    async def recv(self, *, timeout: float) -> dict[str, Any] | None:
        """One frame (parsed JSON) or None when the socket closed."""
        if self._ws is None:
            return None
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            frame = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            raise StreamFrameMalformed("malformed stream frame: invalid JSON") from None
        if not isinstance(frame, dict):
            raise StreamFrameMalformed("malformed stream frame: expected JSON object")
        return frame

    async def send_ack(self, frame: dict[str, Any], data: Any) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(build_ack(frame, data), ensure_ascii=False))

    async def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 — closing is best-effort
                pass


# ---------------------------------------------------------------------------
# The supervised worker
# ---------------------------------------------------------------------------


class StreamManager:
    """Reconciles DingTalk Stream connections for the whole deployment.

    Runs inside mesh.workers as a supervised task. Each scan groups active
    stream-mode integrations by app_key, takes one advisory lock for the whole
    group (single-instance, all-or-nothing ownership), and serves it on a
    dedicated task with reconnect/backoff. Credential rotation / disable /
    deletion close the connection (reconciled every scan).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings,
        *,
        redis=None,
        ssl_context: ssl.SSLContext | None = None,
        http_factory: Callable[[], httpx.AsyncClient] | None = None,
        ws_connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], Any] | None = None,
        rng: random.Random | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._redis = redis
        self._ssl_context = ssl_context
        self._http_factory = http_factory
        self._ws_connect = ws_connect
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self._now = now_fn or (lambda: datetime.now(UTC))
        self._groups: dict[str, asyncio.Task] = {}
        # Lock-close fallbacks scheduled by a task's done callback. A group
        # cancelled before its coroutine runs never enters ``_serve_group``'s
        # finally block, so these tasks are tracked and drained synchronously
        # by reconciliation/shutdown rather than being fire-and-forget.
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._group_signals: dict[str, asyncio.Event] = {}
        # app_key → durable connection fingerprint.  Credential rotation and
        # an explicit reconnect request both change this value, so the lock
        # owning worker closes and rebuilds the physical connection even when
        # the API request was served by another process.
        self._group_fingerprints: dict[str, str] = {}
        # Integration ids currently served by THIS manager — the serving
        # group holds their advisory locks on dedicated sessions; the scan
        # must not try to re-acquire them (it would fail and then close the
        # very group it serves — connection flapping).
        self._served_ids: set[uuid.UUID] = set()
        self._group_integrations: dict[str, list[Integration]] = {}
        self._gateway_warned = False
        self._ownership_conflicts_warned: set[str] = set()
        # Per-app failure diagnostics (R2: failures never disappear). Counts
        # and throttles are group-local so one noisy app cannot suppress a
        # second app's first durable marker. Throttled tails are flushed when
        # the frame loop exits.
        self._frame_error_counts: dict[str, int] = {}
        self._last_frame_error_persist: dict[str, float] = {}
        self._pending_frame_errors: dict[str, tuple[int, str, datetime]] = {}
        # §6.16 defense-in-depth: decrypted app_secret plaintexts are
        # registered here and scrubbed from every error log line this
        # manager emits (the wire-level guarantee is "never log the body";
        # this is the second wall).
        self._redact_values: list[str] = []

    # -- public entry ------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        try:
            while stop is None or not stop.is_set():
                try:
                    await self.scan_once()
                except Exception:  # noqa: BLE001 — the supervisor keeps us alive
                    logger.exception("dingtalk stream scan failed")
                interval = float(getattr(self._settings, "dingtalk_stream_scan_interval", 5.0))
                if stop is not None:
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=interval)
                    except TimeoutError:
                        pass
                else:
                    await self._sleep(interval)
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        tasks = list(self._groups.values())
        for event in self._group_signals.values():
            event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._drain_cleanup_tasks()
        self._groups.clear()
        self._group_signals.clear()
        self._group_fingerprints.clear()
        self._group_integrations.clear()
        self._served_ids.clear()

    # -- scan + reconciliation ----------------------------------------------

    async def scan_once(self) -> None:
        gateway_base, is_non_default = resolve_gateway_base(
            getattr(self._settings, "dingtalk_gateway_base", None)
        )
        if is_non_default and not self._gateway_warned:
            # M2: production boot against a non-official gateway ⇒ warn + audit.
            self._gateway_warned = True
            logger.error(
                "AUDIT: MESH_DINGTALK_GATEWAY_BASE is non-default (%s) — Stream "
                "credentials travel to an untrusted gateway; this must only "
                "happen in test environments",
                gateway_base,
            )

        # Two views: ALL active stream integrations (reconciliation truth —
        # includes the ones this manager already serves) and the NEWLY
        # LOCKED subset (candidates for a new group; served integrations are
        # excluded — their advisory locks are held by the serving group's
        # dedicated session, and re-acquiring would fail).
        all_active, locked = await self._load_locked_integrations()

        def _by_app(rows: list[Integration]) -> dict[str, list[Integration]]:
            grouped: dict[str, list[Integration]] = {}
            for integration in rows:
                app_key = str((integration.config or {}).get("app_key") or "")
                if app_key:
                    grouped.setdefault(app_key, []).append(integration)
            return grouped

        active_groups = _by_app(all_active)
        new_groups = _by_app(locked)
        # A lock returned by _load_locked_integrations is provisional until a
        # supervised group task is registered. Any cancellation/exception in
        # between must release it synchronously.
        untransferred_groups = dict(new_groups)

        try:
            # Stop groups whose app_key vanished / secret or routing identity rotated.
            retiring_tasks: list[asyncio.Future] = []
            for app_key in list(self._groups.keys()):
                current = active_groups.get(app_key)
                fingerprint = self._connection_fingerprint(current or [])
                if not current or self._group_fingerprints.get(app_key) != fingerprint:
                    logger.info(
                        "dingtalk stream group %s closing (disabled/deleted/rotated)",
                        app_key,
                    )
                    self._group_signals[app_key].set()
                    task = self._groups.pop(app_key, None)
                    if task is not None and not task.done():
                        # A signal alone cannot interrupt an in-flight websocket
                        # recv until its heartbeat timeout. Cancellation reaches
                        # the group's finally block immediately, closes the
                        # physical socket, and releases its advisory lock.
                        task.cancel()
                    if task is not None:
                        retiring_tasks.append(task)
                    self._group_signals.pop(app_key, None)
                    self._group_fingerprints.pop(app_key, None)
                    self._served_ids.difference_update(i.id for i in (current or []))

            # Cancellation is part of reconciliation, not detached cleanup.
            if retiring_tasks:
                await asyncio.gather(*retiring_tasks, return_exceptions=True)
                await self._drain_cleanup_tasks()

            # Start groups we do not serve yet (from the newly locked subset).
            for app_key, integrations in new_groups.items():
                if app_key in self._groups:
                    continue
                signal = asyncio.Event()
                self._group_signals[app_key] = signal
                self._group_fingerprints[app_key] = self._connection_fingerprint(integrations)
                self._served_ids.update(i.id for i in integrations)
                self._group_integrations[app_key] = integrations
                task = asyncio.create_task(
                    self._serve_group(app_key, integrations, gateway_base, signal),
                    name=f"dingtalk-stream:{app_key}",
                )
                # H1 crash recovery: whatever ends the group task (clean close,
                # cancel, or an escaping exception), reap ALL bookkeeping + the
                # advisory-lock sessions so the next scan re-locks and rebuilds.
                task.add_done_callback(
                    lambda exited, key=app_key, owned=integrations: self._on_group_exit(key, exited, owned)
                )
                self._groups[app_key] = task
                untransferred_groups.pop(app_key, None)
        finally:
            await self._release_group_locks(untransferred_groups)

    def _connection_fingerprint(self, integrations: list[Integration]) -> str:
        """Return the durable inputs that require a physical reconnect.

        The fingerprint deliberately contains ciphertext references (never
        plaintext credentials), the immutable per-socket message routing
        identity, and the opaque reconnect request id written by the management
        API. Because every worker reads these values from PostgreSQL during
        reconciliation, credential, routing, and explicit reconnect changes
        work across processes.
        """
        refs = []
        for integration in sorted(integrations, key=lambda i: str(i.id)):
            config = dict(integration.config or {})
            reconnect_request_id = (integration.stream_state or {}).get("reconnect_request_id")
            refs.append(
                json.dumps(
                    [
                        str(integration.id),
                        str(config.get("app_secret_ref") or integration.secret_ref or ""),
                        str(config.get("corp_id") or ""),
                        str(config.get("robot_code") or config.get("app_key") or ""),
                        list(_reconnect_config(integration)),
                        str(reconnect_request_id or ""),
                    ],
                    separators=(",", ":"),
                )
            )
        return "|".join(refs)

    def _on_group_exit(
        self,
        app_key: str,
        task: asyncio.Future,
        integrations: list[Integration],
    ) -> None:
        """Reap a finished group task: clear bookkeeping and close the
        advisory-lock sessions (releasing the locks) so the next scan
        re-acquires and rebuilds the group — crash-safe lifecycle (H1).

        A done callback may run after reconciliation has installed a
        replacement for the same app key.  Registry cleanup is therefore
        identity-guarded: a stale task can clean only its own lock objects,
        never the replacement's task, signals, fingerprint, or served ids.
        """
        current = self._groups.get(app_key)
        replacement_active = current is not None and current is not task
        if current is task:
            self._groups.pop(app_key, None)
            self._group_signals.pop(app_key, None)
            self._group_fingerprints.pop(app_key, None)
        if self._group_integrations.get(app_key) is integrations:
            self._group_integrations.pop(app_key, None)
        if not replacement_active:
            owned_elsewhere = {item.id for rows in self._group_integrations.values() for item in rows}
            self._served_ids.difference_update(
                item.id for item in integrations if item.id not in owned_elsewhere
            )
        for integration in integrations:
            hold = getattr(integration, "_lock_session", None)
            if hold is None:
                continue
            integration._lock_session = None  # type: ignore[attr-defined]
            try:
                cleanup = asyncio.get_running_loop().create_task(self._release_lock_session(app_key, hold))
            except RuntimeError:
                pass  # loop gone (process shutdown) — nothing to release
            else:
                self._cleanup_tasks.add(cleanup)
                cleanup.add_done_callback(self._cleanup_tasks.discard)

    async def _drain_cleanup_tasks(self) -> None:
        """Wait for every fallback lock close scheduled by group callbacks."""
        # Let done callbacks of just-gathered group tasks enqueue their cleanup
        # before taking the first snapshot.
        await asyncio.sleep(0)
        while self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
            await asyncio.sleep(0)

    async def _release_group_locks(self, groups: dict[str, list[Integration]]) -> None:
        """Release provisional app-key locks not owned by group tasks."""
        for app_key, integrations in groups.items():
            holder = next(
                (item for item in integrations if getattr(item, "_lock_session", None) is not None),
                None,
            )
            if holder is None:
                continue
            lock_session = holder._lock_session  # type: ignore[attr-defined]
            holder._lock_session = None  # type: ignore[attr-defined]
            await self._release_lock_session(app_key, lock_session)

    @staticmethod
    async def _release_lock_session(app_key: str, lock_session) -> None:
        """Explicitly unlock before returning a pooled DB session.

        PostgreSQL session-level advisory locks survive transaction rollback;
        merely closing an AsyncSession can return the same physical connection
        to SQLAlchemy's pool with the lock still held.
        """
        try:
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": f"dingtalk_stream_app:{app_key}"},
            )
        except Exception:  # noqa: BLE001 — close still releases a real connection
            logger.exception("dingtalk stream: advisory unlock failed during cleanup")
        try:
            await lock_session.close()
        except Exception:  # noqa: BLE001 — cleanup is best-effort on broken connections
            logger.exception("dingtalk stream: lock session close failed during cleanup")

    async def _load_locked_integrations(self) -> tuple[list[Integration], list[Integration]]:
        """Returns ``(all_active_stream, newly_locked)``.

        ``all_active_stream``: every active stream-mode integration (the
        reconciliation truth — includes integrations this manager already
        serves). ``newly_locked``: complete app-key groups for which this
        process just won the advisory lock (single-instance mutex; one
        dedicated session per app key for the connection's lifetime).
        Group-level ownership is all-or-nothing, so two workers cannot split
        integrations sharing an app key and open duplicate physical sockets.
        Already-served groups are never re-locked here.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Integration).where(
                            Integration.kind == "im_dingtalk",
                            Integration.status == "active",
                            Integration.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            active = [
                integration
                for integration in rows
                if str((integration.config or {}).get("receive_mode") or "") == "stream"
            ]
            owner_by_app: dict[str, uuid.UUID] = {}
            for app_key in {str((integration.config or {}).get("app_key") or "") for integration in active}:
                if app_key == "":
                    continue
                owner = await session.scalar(
                    text("SELECT mesh_dingtalk_app_owner_workspace(:app_key)"),
                    {"app_key": app_key},
                )
                if owner is not None:
                    owner_by_app[app_key] = uuid.UUID(str(owner))
        filtered_active: list[Integration] = []
        conflicted_apps: set[str] = set()
        for integration in active:
            app_key = str((integration.config or {}).get("app_key") or "")
            owner_workspace = owner_by_app.get(app_key)
            if owner_workspace is not None and integration.workspace_id != owner_workspace:
                conflicted_apps.add(app_key)
                continue
            filtered_active.append(integration)
        for app_key in sorted(conflicted_apps - self._ownership_conflicts_warned):
            logger.error(
                "AUDIT: ignored a foreign-workspace DingTalk integration for owned app_key=%s",
                app_key,
            )
        self._ownership_conflicts_warned.intersection_update(conflicted_apps)
        self._ownership_conflicts_warned.update(conflicted_apps)
        active = filtered_active
        groups: dict[str, list[Integration]] = {}
        for integration in active:
            app_key = str((integration.config or {}).get("app_key") or "")
            if app_key:
                groups.setdefault(app_key, []).append(integration)

        kept: list[Integration] = []
        acquired_groups: dict[str, list[Integration]] = {}
        try:
            for app_key, integrations in sorted(groups.items()):
                integrations.sort(key=lambda item: str(item.id))
                if any(integration.id in self._served_ids for integration in integrations):
                    continue
                hold = self._session_factory()
                try:
                    acquired = (
                        await hold.execute(
                            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                            {"key": f"dingtalk_stream_app:{app_key}"},
                        )
                    ).scalar_one()
                except BaseException:
                    # The query may have reached PostgreSQL before client-side
                    # cancellation/result handling failed. Explicit unlock is
                    # safe whether acquisition succeeded or not; close alone
                    # can return a still-locked physical connection to pool.
                    await self._release_lock_session(app_key, hold)
                    raise
                if acquired:
                    # The first row owns the group's single lock session. The
                    # entire list travels together into _serve_group.
                    integrations[0]._lock_session = hold  # type: ignore[attr-defined]
                    acquired_groups[app_key] = integrations
                    kept.extend(integrations)
                else:
                    await hold.close()
        except BaseException:
            await self._release_group_locks(acquired_groups)
            raise
        return active, kept

    # -- group serve loop ----------------------------------------------------

    async def _refresh_configs(self, integrations: list[Integration]) -> None:
        """Re-read config/secret_ref from the DB each cycle so a credential
        ROTATION becomes visible to the running group without a full rescan
        (§3.2: 凭据轮换 → 断连并以新密文重连) — the decrypted-secret check
        and the client-rebuild check below both key off the refreshed row."""
        async with self._session_factory() as session:
            for integration in integrations:
                row = await session.get(Integration, integration.id)
                if row is None:
                    continue  # deleted — the scan reconciles the group away
                integration.config = dict(row.config or {})
                integration.secret_ref = row.secret_ref

    async def _serve_group(
        self,
        app_key: str,
        integrations: list[Integration],
        gateway_base: str,
        signal: asyncio.Event,
    ) -> None:
        integration = integrations[0]  # group representative for config/secret
        base, maximum, heartbeat = _reconnect_config(integration)
        client: DingTalkStreamClient | None = None
        attempt = 0
        secret_was_bad = False
        try:
            while not signal.is_set():
                await self._refresh_configs(integrations)
                # The reconnect policy is app/socket scoped. Admission keeps
                # sibling values identical; recompute after every refresh so
                # a hot update is effective in this cycle too.
                base, maximum, heartbeat = _reconnect_config(integrations[0])
                route_identities = [
                    (
                        str((item.config or {}).get("corp_id") or ""),
                        str(
                            (item.config or {}).get("robot_code") or (item.config or {}).get("app_key") or ""
                        ),
                    )
                    for item in integrations
                ]
                decrypted_secrets = [
                    _decrypt_app_secret(item, self._settings.jwt_secret) for item in integrations
                ]
                reconnect_policies = [_reconnect_config(item) for item in integrations]
                group_configuration_valid = (
                    all(corp_id and robot_code for corp_id, robot_code in route_identities)
                    and len(set(route_identities)) == len(route_identities)
                    and all(secret is not None for secret in decrypted_secrets)
                    and len(set(decrypted_secrets)) == 1
                    and len(set(reconnect_policies)) == 1
                )
                # M3: undecryptable app_secret (rotated ciphertext, revoked
                # key) — the credential equivalent of "signature invalid".
                # The group STAYS ALIVE: DOWN + capped backoff, re-trying
                # decryption each cycle so a rotation to a valid ciphertext
                # reconnects without a scan round-trip; the DOWN broadcast
                # fires ONCE (transition-only) — no outbox/realtime flood.
                app_secret = decrypted_secrets[0] if group_configuration_valid else None
                if app_secret is None:
                    if not secret_was_bad:
                        message, _hits = redact_text(
                            "dingtalk stream: invalid shared-app credential, routing, "
                            "or reconnect "
                            f"configuration for app_key={app_key} — down + backoff "
                            "(zero ingestion until fixed)",
                            self._redact_values,
                        )
                        logger.error(message)
                        secret_was_bad = True
                    delay = compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng)
                    await self._mark_group(integrations, STATE_DOWN, backoff_seconds=delay)
                    await self._interruptible_sleep(delay, signal)
                    attempt += 1
                    continue
                if secret_was_bad:
                    logger.info(
                        "dingtalk stream: shared app configuration for app_key=%s "
                        "is valid again — reconnecting",
                        app_key,
                    )
                    secret_was_bad = False
                    attempt = 0
                if app_secret not in self._redact_values:
                    self._redact_values.append(app_secret)  # §6.16 scrub registry
                # Credential rotation ⇒ rebuild the client with the new
                # plaintext (the old connection is dropped, §3.2).
                if client is None or client._app_secret != app_secret:
                    if client is not None:
                        await client.close()
                    client = DingTalkStreamClient(
                        app_key=app_key,
                        app_secret=app_secret,
                        gateway_base=gateway_base,
                        ssl_context=self._ssl_context,
                        http_factory=self._http_factory,
                        ws_connect=self._ws_connect,
                    )
                try:
                    connected = await self._serve_once(
                        client, integrations, attempt, base, maximum, heartbeat, signal
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — never let one bad cycle kill the group
                    message, _hits = redact_text(
                        "dingtalk stream: serve cycle crashed — backoff + retry",
                        self._redact_values,
                    )
                    logger.exception(message)
                    delay = compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng)
                    await self._mark_group(integrations, STATE_DOWN, backoff_seconds=delay)
                    await self._interruptible_sleep(delay, signal)
                    attempt += 1
                    continue
                if signal.is_set():
                    break
                if connected:
                    # M2: the cycle reached CONNECTED — it is NOT a
                    # consecutive open failure; reset the backoff counter so
                    # the next drop reconnects at ~base instead of the
                    # historical maximum.
                    attempt = 0
                else:
                    attempt += 1
        finally:
            if client is not None:
                await client.close()
            lock_holder = next(
                (item for item in integrations if getattr(item, "_lock_session", None) is not None),
                None,
            )
            if lock_holder is not None:
                lock_session = lock_holder._lock_session  # type: ignore[attr-defined]
                await self._release_lock_session(app_key, lock_session)
                lock_holder._lock_session = None  # type: ignore[attr-defined]

    async def _serve_once(
        self,
        client: DingTalkStreamClient,
        integrations: list[Integration],
        attempt: int,
        base: float,
        maximum: float,
        heartbeat: float,
        signal: asyncio.Event,
    ) -> bool:
        """One connect → frame-loop cycle. Returns True when the cycle
        REACHED CONNECTED (caller resets the backoff counter — M2); False
        when the open itself failed (consecutive-failure backoff grows).
        Exceptions escape to the crash-safe supervisor in _serve_group."""
        await self._mark_group(integrations, STATE_RECONNECTING, backoff_seconds=0)
        try:
            await client.open_connection()
        except StreamEndpointInsecure:
            delay = compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng)
            await self._mark_group(integrations, STATE_DOWN, backoff_seconds=delay)
            await self._interruptible_sleep(delay, signal)
            return False
        except (StreamOpenError, httpx.HTTPError, OSError) as exc:
            message, _hits = redact_text(
                f"dingtalk stream open failed (app_key={integrations[0].config.get('app_key')}): {exc}",
                self._redact_values,
            )
            logger.error(message)
            delay = compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng)
            await self._mark_group(integrations, STATE_DOWN, backoff_seconds=delay)
            await self._interruptible_sleep(delay, signal)
            return False
        # Once open_connection succeeds, this cycle owns a physical socket.
        # Close it on every exit path — including a CONNECTED state/outbox
        # write failure before the frame loop starts — so a retry can never
        # overwrite client._ws and orphan a second live consumer.
        try:
            await self._mark_group(integrations, STATE_CONNECTED, backoff_seconds=0)
            immediate_reconnect = await self._frame_loop(client, integrations, heartbeat, signal)
        finally:
            await client.close()
        if not immediate_reconnect and not signal.is_set():
            # Connection dropped (close/heartbeat timeout) — back off before
            # the next cycle (the counter itself is reset by the caller
            # because this cycle did connect — M2).
            # This cycle connected, so the drop starts at the first rung.
            delay = compute_backoff(0, base=base, maximum=maximum, rng=self._rng)
            await self._mark_group(integrations, STATE_RECONNECTING, backoff_seconds=delay)
            await self._interruptible_sleep(delay, signal)
        return True  # reached CONNECTED (disconnect topic ⇒ immediate redo)

    async def _frame_loop(
        self,
        client: DingTalkStreamClient,
        integrations: list[Integration],
        heartbeat: float,
        signal: asyncio.Event,
    ) -> bool:
        try:
            return await self._consume_frames(client, integrations, heartbeat, signal)
        finally:
            # Persist the latest throttled count/reason even when the socket
            # closes before another 30-second persistence window begins.
            await self._flush_frame_errors(integrations)

    async def _consume_frames(
        self,
        client: DingTalkStreamClient,
        integrations: list[Integration],
        heartbeat: float,
        signal: asyncio.Event,
    ) -> bool:
        """Consume frames until close/heartbeat-timeout/disconnect. Returns
        True when the caller should reconnect IMMEDIATELY (disconnect frame)."""
        by_message_identity: dict[tuple[str, str], list[Integration]] = {}
        for integration in integrations:
            config = integration.config or {}
            identity = (
                str(config.get("corp_id") or ""),
                str(config.get("robot_code") or config.get("app_key") or ""),
            )
            by_message_identity.setdefault(identity, []).append(integration)
        last_persist = 0.0
        while not signal.is_set():
            try:
                frame = await client.recv(timeout=heartbeat)
            except TimeoutError:
                logger.warning("dingtalk stream heartbeat timeout — reconnecting")
                return False
            except StreamFrameMalformed as exc:
                # No callback identity exists at this layer, so the frame
                # cannot enter an integration ledger or be ACKed safely.
                # Persist at app-group level before reconnecting; redelivery
                # remains the platform's recovery path.
                await self._record_frame_error(integrations, exc, force_persist=True)
                return False
            except Exception:  # noqa: BLE001 — socket error ⇒ reconnect
                return False
            if frame is None:
                return False  # clean close ⇒ reconnect with backoff

            frame_type = str(frame.get("type") or "")
            headers = frame.get("headers") or {}
            topic = str(headers.get("topic") or "")

            if frame_type == "SYSTEM" and topic == "disconnect":
                logger.info("dingtalk stream: platform-requested disconnect — reconnecting")
                return True

            # Per-frame isolation (H1): one bad frame (transient DB error,
            # malformed payload, socket drop mid-ACK) must not escape and
            # kill the group task — log and keep consuming. An un-ACKed
            # frame is redelivered by the platform; msgId dedup makes that
            # idempotent (§3.2).
            try:
                if frame_type == "SYSTEM" and topic == "ping":
                    # MUST ACK — echo the original payload data verbatim.
                    ping_data = frame.get("data")
                    frame_payload = frame.get("payload")
                    if ping_data is None and isinstance(frame_payload, dict):
                        ping_data = frame_payload.get("data")
                    await client.send_ack(frame, ping_data)
                elif frame_type == "CALLBACK" and topic == STREAM_MESSAGE_TOPIC:
                    safe_to_ack = await self._ingest_message_frame(integrations, by_message_identity, frame)
                    # ACK only after either the ingest/rejected-ledger
                    # transaction commits or an unattributable frame error is
                    # durably recorded for the whole app group. If diagnostic
                    # persistence fails, leave the frame un-ACKed so platform
                    # redelivery gives us another chance to retain it.
                    if safe_to_ack:
                        await client.send_ack(frame, "received")
                elif frame_type == "CALLBACK" and topic == STREAM_CARD_TOPIC:
                    lifecycle = await self._handle_card_frame(integrations, frame)
                    # ACK only after the callback transaction (including a
                    # denial audit) commits. The lifecycle body is the card
                    # writeback; exceptions/non-lifecycle errors stay
                    # un-ACKed for platform redelivery.
                    await client.send_ack(frame, lifecycle)
                elif frame_type == "CALLBACK":
                    raise ValueError(f"unsupported callback topic: {topic or '<missing>'}")
            except Exception as exc:  # noqa: BLE001 — isolate per-frame failures
                message, _hits = redact_text(
                    "dingtalk stream: frame handling failed — frame skipped "
                    "(redelivery is idempotent via msgId)",
                    self._redact_values,
                )
                logger.exception(message)
                # Failures never disappear (R2): an exact in-memory count +
                # a THROTTLED diagnostic marker persisted into stream_state
                # — the §3.9 diagnostic truth source (the stream-status
                # endpoint serves the whitelisted state fields; the error
                # markers ride the same JSONB, readable from the truth
                # source row). The frame stays un-ACKed, so the platform
                # redelivers and msgId dedup keeps it idempotent; this
                # marker is the observability half of that contract.
                await self._record_frame_error(integrations, exc)

            # Persist last_frame_at (throttled, NO realtime broadcast —
            # transitions broadcast; liveness refresh is a state write
            # only). Runs on EVERY successfully received frame (M4).
            now_epoch = time.time()
            if now_epoch - last_persist > _FRAME_PERSIST_INTERVAL_SECONDS:
                last_persist = now_epoch
                for integration in integrations:
                    try:
                        await self._set_stream_state(
                            integration, STATE_CONNECTED, backoff_seconds=0, broadcast=False
                        )
                    except Exception:  # noqa: BLE001 — diagnostic persistence is best-effort
                        pass
        return False

    async def _handle_card_frame(
        self, integrations: list[Integration], frame: dict[str, Any]
    ) -> dict[str, Any]:
        """Authorize and execute a card click on a shared app socket.

        ``outTrackId`` anchors the approval/workspace while ``corpId``
        anchors the external tenant. A shared app may have multiple routes
        inside its owning workspace; any exact-corp sibling is equivalent
        for the existing authorization chain, but a missing/mismatched
        identity fails closed.
        """
        payload = frame.get("data")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError("unparseable card callback payload") from exc
        if not isinstance(payload, dict):
            raise ValueError("unparseable card callback payload")

        track_context = parse_out_track_context(str(payload.get("outTrackId") or ""))
        approval_id = track_context[0] if track_context is not None else None
        source_integration_id = track_context[1] if track_context is not None else None
        action = extract_dingtalk_action(payload)
        if approval_id is None or action is None or action[0] != approval_id:
            raise ValueError("card callback approval identity mismatch")
        corp_id = str(payload.get("corpId") or "")
        candidates = [
            integration
            for integration in integrations
            if corp_id and str((integration.config or {}).get("corp_id") or "") == corp_id
        ]
        if source_integration_id is not None:
            candidates = [item for item in candidates if item.id == source_integration_id]
        candidate_workspaces = {item.workspace_id for item in candidates}
        if len(candidates) != 1 or len(candidate_workspaces) != 1:
            raise ValueError("card callback tenant route missing or ambiguous")

        workspace_id = next(iter(candidate_workspaces))
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id)
            integration = await session.scalar(
                select(Integration).where(Integration.id == candidates[0].id).with_for_update()
            )
            if (
                integration is None
                or integration.deleted_at is not None
                or str((integration.config or {}).get("receive_mode") or "") != "stream"
                or str((integration.config or {}).get("corp_id") or "") != corp_id
            ):
                raise ValueError("card callback source integration is no longer a Stream route")
            approval = await session.get(Approval, approval_id)
            if approval is None or approval.workspace_id != workspace_id:
                raise ValueError("card callback approval is outside the app owner workspace")
            status, lifecycle = await handle_dingtalk_card_callback(
                session,
                self._session_factory,
                integration=integration,
                payload=payload,
                now=self._now(),
                app_base_url=str(getattr(self._settings, "app_base_url", "") or ""),
            )
        # Leave the transaction before raising so disabled/misconfigured
        # denial audits are durable even though the frame intentionally stays
        # un-ACKed for platform redelivery.
        if status not in (200, 403):
            raise RuntimeError(f"card callback failed with status {status}")
        return lifecycle

    @staticmethod
    def _frame_error_group_key(integrations: list[Integration]) -> str:
        app_keys = {str((integration.config or {}).get("app_key") or "") for integration in integrations}
        if len(app_keys) == 1 and "" not in app_keys:
            return next(iter(app_keys))
        return "ids:" + ",".join(sorted(str(item.id) for item in integrations))

    async def _record_frame_error(
        self,
        integrations: list[Integration],
        exc: Exception,
        *,
        force_persist: bool = False,
    ) -> bool:
        """Exact per-app count + throttled stream_state marker for a
        per-frame ingest/handling failure (R2 — failures never disappear).

        Best-effort by contract: a diagnostic write must never mask the
        isolation it reports. Throttled to at most one persistence per
        ``_FRAME_PERSIST_INTERVAL_SECONDS`` even under a continuous error
        cycle (the counter stays exact; the latest throttled tail is flushed
        when the frame loop exits). The marker fields (``frame_error_count`` /
        ``last_frame_error_at`` / ``last_frame_error``) ride the
        stream_state JSONB — the §3.9 diagnostic truth source that
        ``GET .../stream-status`` reads (the endpoint serves the
        whitelisted state view; the markers stay queryable on the truth
        source row itself). The error text is redacted through the same
        secret blacklist as every other log line this manager writes."""
        group_key = self._frame_error_group_key(integrations)
        if group_key not in self._frame_error_counts:
            self._frame_error_counts[group_key] = await self._frame_error_baseline(integrations)
        count = self._frame_error_counts.get(group_key, 0) + 1
        self._frame_error_counts[group_key] = count
        now_epoch = time.time()
        reason, _hits = redact_text(f"{type(exc).__name__}: {exc}"[:200], self._redact_values)
        now = self._now()
        self._pending_frame_errors[group_key] = (count, reason, now)
        last_persist = self._last_frame_error_persist.get(group_key, 0.0)
        if not force_persist and now_epoch - last_persist < _FRAME_PERSIST_INTERVAL_SECONDS:
            return False
        self._last_frame_error_persist[group_key] = now_epoch
        persisted = await self._persist_frame_error(integrations, count, reason, now)
        if persisted:
            self._pending_frame_errors.pop(group_key, None)
        return persisted

    async def _frame_error_baseline(self, integrations: list[Integration]) -> int:
        """Load the durable high-water mark after a restart/owner handoff."""
        highest = 0
        try:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(Integration.stream_state).where(
                                Integration.id.in_([item.id for item in integrations])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            for state in rows:
                try:
                    highest = max(highest, int((state or {}).get("frame_error_count") or 0))
                except (TypeError, ValueError):
                    continue
        except Exception:  # noqa: BLE001 — the best-effort writer below still runs
            return 0
        return highest

    async def _flush_frame_errors(self, integrations: list[Integration]) -> None:
        group_key = self._frame_error_group_key(integrations)
        pending = self._pending_frame_errors.get(group_key)
        if pending is None:
            return
        count, reason, occurred_at = pending
        if await self._persist_frame_error(integrations, count, reason, occurred_at):
            self._pending_frame_errors.pop(group_key, None)

    async def _persist_frame_error(
        self,
        integrations: list[Integration],
        count: int,
        reason: str,
        occurred_at: datetime,
    ) -> bool:
        complete = True
        for integration in integrations:
            try:
                async with self._session_factory() as session, session.begin():
                    row = await session.scalar(
                        select(Integration).where(Integration.id == integration.id).with_for_update()
                    )
                    if row is None:
                        complete = False
                        continue
                    persisted_state = {
                        **(row.stream_state or {}),
                        "frame_error_count": max(
                            count,
                            int((row.stream_state or {}).get("frame_error_count") or 0),
                        ),
                        "last_frame_error_at": occurred_at.isoformat(),
                        "last_frame_error": reason,
                    }
                    row.stream_state = persisted_state
                    row.updated_at = self._now()
                integration.stream_state = persisted_state
            except Exception:  # noqa: BLE001 — diagnostic persistence is best-effort
                complete = False
        return complete

    async def _ingest_message_frame(
        self,
        integrations: list[Integration],
        by_message_identity: dict[tuple[str, str], list[Integration]],
        frame: dict[str, Any],
    ) -> bool:
        payload = frame.get("data")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, json.JSONDecodeError):
                payload = None
        if not isinstance(payload, dict):
            logger.warning("dingtalk stream: unparseable message frame — skipped")
            return await self._record_frame_error(
                integrations,
                ValueError("unparseable message frame"),
                force_persist=True,
            )
        identity = (
            str(payload.get("chatbotCorpId") or ""),
            str(payload.get("robotCode") or ""),
        )
        candidates = by_message_identity.get(identity, [])
        if len(candidates) != 1:
            logger.warning("dingtalk stream: message routing identity missing or ambiguous — skipped")
            return await self._record_frame_error(
                integrations,
                ValueError("message routing identity missing or ambiguous"),
                force_persist=True,
            )
        integration = candidates[0]
        try:
            envelope = normalize_message_payload(
                payload,
                max_chars=int(getattr(self._settings, "im_inbound_text_max_chars", 4000)),
                channel="stream",
            )
        except ValidationError:
            logger.warning(
                "dingtalk stream: payload normalization failed (integration=%s) — rejected audit",
                integration.id,
            )
            await self._audit_malformed_message(integration, payload)
            return True
        # Same shared core as the HTTP channel — the two receive modes
        # differ ONLY in the auth adapter in front (§2.10:651-664). The
        # §2.10 guards (fail-closed) and the §3.8 ack window read from the
        # same Settings the HTTP routes use — no drift between channels.
        route_missing = False
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, integration.workspace_id)
            current = await session.scalar(
                select(Integration).where(Integration.id == integration.id).with_for_update()
            )
            if current is None:
                route_missing = True
            else:
                current_config = current.config or {}
                current_identity = (
                    str(current_config.get("corp_id") or ""),
                    str(current_config.get("robot_code") or current_config.get("app_key") or ""),
                )
                if (
                    current.deleted_at is not None
                    or str(current_config.get("receive_mode") or "") != "stream"
                    or current_identity != identity
                ):
                    await self._audit_inactive_stream_route(
                        session,
                        integration=current,
                        envelope=envelope,
                        reason=(
                            "integration_deleted"
                            if current.deleted_at is not None
                            else "receive_mode_changed"
                            if str(current_config.get("receive_mode") or "") != "stream"
                            else "route_identity_changed"
                        ),
                    )
                    return True
                # The row lock linearizes this frame against disable/delete/
                # mode updates. ingest_verified_event sees the authoritative
                # status and records disabled traffic as rejected.
                await ingest_verified_event(
                    session,
                    integration=current,
                    envelope=envelope,
                    now=self._now(),
                    redis=self._redis,
                    settings=self._settings,
                )
        if route_missing:
            return await self._record_frame_error(
                integrations,
                ValueError("message source integration no longer exists"),
                force_persist=True,
            )
        return True

    async def _audit_inactive_stream_route(
        self,
        session: AsyncSession,
        *,
        integration: Integration,
        envelope: Any,
        reason: str,
    ) -> None:
        """Persist an authenticated frame rejected by current route truth."""
        canonical = json.dumps(
            envelope.raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        forensic = audit_payload(
            {**envelope.raw_payload, "_mesh_channel": "stream"},
            "rejected",
        )
        forensic = {
            **forensic,
            "_mesh_channel": "stream",
            "_mesh_reject_reason": reason,
        }
        try:
            async with session.begin_nested():
                await store_event(
                    session,
                    workspace_id=integration.workspace_id,
                    integration_id=integration.id,
                    external_event_id="rejected:" + hashlib.sha256(canonical).hexdigest(),
                    event_type=envelope.event_type,
                    payload=forensic,
                    signature_status="valid",
                    process_status="rejected",
                    now=self._now(),
                )
        except IntegrityError as exc:
            if not _violates_constraint(exc, "uq_integration_event_dedup"):
                raise

    async def _audit_malformed_message(self, integration: Integration, payload: dict[str, Any]) -> None:
        """Persist an authenticated, exactly-routed but malformed Stream
        message in the same rejected namespace as the HTTP adapter.

        Unknown visibility is deliberate: normalization could not establish a
        trustworthy conversation/binding. Repeated delivery of the same body
        deduplicates, after which it is safe to ACK again.
        """
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        forensic = audit_payload({**payload, "_mesh_channel": "stream"}, "rejected")
        forensic = {
            **forensic,
            "_mesh_channel": "stream",
            "_mesh_reject_reason": "malformed_payload",
        }
        async with self._session_factory() as session, session.begin():
            await set_tenant_context(session, integration.workspace_id)
            try:
                async with session.begin_nested():
                    await store_event(
                        session,
                        workspace_id=integration.workspace_id,
                        integration_id=integration.id,
                        external_event_id=("rejected:" + hashlib.sha256(canonical).hexdigest()),
                        event_type="im.bot.message",
                        payload=forensic,
                        signature_status="valid",
                        process_status="rejected",
                        now=self._now(),
                    )
            except IntegrityError as exc:
                if not _violates_constraint(exc, "uq_integration_event_dedup"):
                    raise

    # -- state persistence ---------------------------------------------------

    async def _interruptible_sleep(self, seconds: float, signal: asyncio.Event) -> None:
        """Sleep ``seconds`` (through the INJECTED sleeper — real
        asyncio.sleep in production, instant in tests) but wake IMMEDIATELY
        when the stop signal fires."""
        if signal.is_set():
            return
        stop_waiter = asyncio.ensure_future(signal.wait())
        sleeper = asyncio.ensure_future(self._sleep(max(0.0, seconds)))
        try:
            await asyncio.wait({stop_waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (stop_waiter, sleeper):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stop_waiter, sleeper, return_exceptions=True)

    async def _mark_group(
        self,
        integrations: list[Integration],
        state: str,
        *,
        backoff_seconds: float,
    ) -> None:
        for integration in integrations:
            await self._set_stream_state(integration, state, backoff_seconds=backoff_seconds)

    async def _set_stream_state(
        self,
        integration: Integration,
        state: str,
        *,
        backoff_seconds: float,
        broadcast: bool = True,
    ) -> None:
        """Persist stream_state + (on transitions) broadcast
        integration.updated(subject='stream_channel') via the outbox."""
        now = self._now()
        persisted_state: dict[str, Any] | None = None
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(Integration).where(Integration.id == integration.id).with_for_update()
            )
            if row is None:
                return
            previous = dict(row.stream_state or {})
            payload_state: dict[str, Any] = {
                "state": state,
                "last_frame_at": previous.get("last_frame_at"),
                "last_attempt_at": now.isoformat(),
                "backoff_seconds": round(float(backoff_seconds), 2),
            }
            if state == STATE_CONNECTED:
                payload_state["last_frame_at"] = now.isoformat()
            persisted_state = {**previous, **payload_state}
            row.stream_state = persisted_state
            row.updated_at = now
            # M3: broadcast ONLY on state TRANSITIONS — a sustained
            # reconnecting/down must not flood the outbox/realtime path
            # (the idempotency key carries now.isoformat() precisely so
            # repeats cannot dedup; the transition rule is the real gate).
            if broadcast and previous.get("state") != state:
                await emit_realtime(
                    session,
                    workspace_id=row.workspace_id,
                    channel=f"workspace:{row.workspace_id}:integrations",
                    event="integration.updated",
                    data={
                        "integration_id": str(row.id),
                        "kind": row.kind,
                        "status": row.status,
                        "subject": STREAM_CHANNEL_SUBJECT,
                        "stream_state": state,
                    },
                    idempotency_key=(f"integration-stream:{row.id}:{state}:{now.isoformat()}"),
                )
        # Keep the in-memory copy coherent for subsequent decisions.
        integration.stream_state = persisted_state


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_BACKOFF_JITTER",
    "DEFAULT_BACKOFF_MAX_SECONDS",
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "DingTalkStreamClient",
    "STATE_CONNECTED",
    "STATE_DOWN",
    "STATE_RECONNECTING",
    "STREAM_CHANNEL_SUBJECT",
    "StreamEndpointInsecure",
    "StreamManager",
    "StreamOpenError",
    "build_ack",
    "compute_backoff",
]
