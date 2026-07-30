"""DingTalk Stream long-connection receive channel (integrations.md §3.2, MES-87).

Mesh dials the DingTalk gateway itself (no public callback address, no
inbound port):

    stream worker (supervised asyncio task inside mesh.workers — same
    process family as the outbox relay, NOT a new compose service)
      → per active im_dingtalk integration with receive_mode='stream':
        advisory-lock single-instance mutex (pg_try_advisory_lock on
        hashtext('dingtalk_stream:'||integration_id)); integrations that
        SHARE one app_key share ONE physical connection (platform cap:
        50 connections per app — frames route by robotCode)
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
import json
import logging
import random
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesh.db.models.integration import Integration
from mesh.integrations.dingtalk import (
    GATEWAY_OPEN_PATH,
    STREAM_CARD_TOPIC,
    STREAM_MESSAGE_TOPIC,
    normalize_message_payload,
    resolve_gateway_base,
    stream_user_agent,
)
from mesh.integrations.ingest import ingest_verified_event
from mesh.outbox.service import emit_realtime
from mesh.runtime.credentials import decrypt_credential_value

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
    heartbeat = float(
        reconnect.get("heartbeat_timeout_seconds") or DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
    )
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

    return await websockets.connect(
        url, ssl=ssl_context, max_size=1024 * 1024, open_timeout=10
    )


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
            logger.error(
                "dingtalk gateway returned a non-wss endpoint — refused (anti-downgrade)"
            )
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
            return {"specVersion": "1.0", "type": "SYSTEM", "headers": {"topic": "malformed"}}
        return frame if isinstance(frame, dict) else None

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

    Runs inside mesh.workers as a supervised task. Each scan: load active
    stream-mode integrations, take the per-integration advisory lock
    (single-instance mutex), group locked integrations by app_key (one
    physical connection per app), and serve each group on a dedicated task
    with reconnect/backoff. Credential rotation / disable / deletion close
    the connection (reconciled every scan).
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
        self._group_signals: dict[str, asyncio.Event] = {}
        # (app_key, secret fingerprint) per running group — rotation detection.
        self._group_secrets: dict[str, str] = {}
        self._gateway_warned = False

    # -- public entry ------------------------------------------------------

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
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
        await self.shutdown()

    async def shutdown(self) -> None:
        for event in self._group_signals.values():
            event.set()
        for task in self._groups.values():
            task.cancel()
        self._groups.clear()
        self._group_signals.clear()
        self._group_secrets.clear()

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

        locked = await self._load_locked_integrations()
        # Group by app_key — one physical connection per app (platform cap 50).
        groups: dict[str, list[Integration]] = {}
        for integration in locked:
            app_key = str((integration.config or {}).get("app_key") or "")
            if app_key:
                groups.setdefault(app_key, []).append(integration)

        # Stop groups whose app_key vanished / secret rotated.
        for app_key in list(self._groups.keys()):
            current = groups.get(app_key)
            fingerprint = self._secrets_fingerprint(current or [])
            if not current or self._group_secrets.get(app_key) != fingerprint:
                logger.info(
                    "dingtalk stream group %s closing (disabled/deleted/rotated)",
                    app_key,
                )
                self._group_signals[app_key].set()
                self._groups.pop(app_key, None)
                self._group_signals.pop(app_key, None)
                self._group_secrets.pop(app_key, None)

        # Start groups we do not serve yet.
        for app_key, integrations in groups.items():
            if app_key in self._groups:
                continue
            signal = asyncio.Event()
            self._group_signals[app_key] = signal
            self._group_secrets[app_key] = self._secrets_fingerprint(integrations)
            self._groups[app_key] = asyncio.create_task(
                self._serve_group(app_key, integrations, gateway_base, signal),
                name=f"dingtalk-stream:{app_key}",
            )

    def _secrets_fingerprint(self, integrations: list[Integration]) -> str:
        """Rotation detection: the ciphertext refs (never the plaintext)."""
        refs = []
        for integration in sorted(integrations, key=lambda i: str(i.id)):
            config = dict(integration.config or {})
            refs.append(f"{integration.id}:{config.get('app_secret_ref') or integration.secret_ref}")
        return "|".join(refs)

    async def _load_locked_integrations(self) -> list[Integration]:
        """Active stream-mode integrations this process won the advisory
        lock for (single-instance mutex; held on a dedicated session for
        the connection's lifetime, released on close)."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Integration).where(
                        Integration.kind == "im_dingtalk",
                        Integration.status == "active",
                        Integration.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
            candidates = [
                integration
                for integration in rows
                if str((integration.config or {}).get("receive_mode") or "") == "stream"
            ]
        kept: list[Integration] = []
        for integration in candidates:
            hold = self._session_factory()
            acquired = (
                await hold.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                    {"key": f"dingtalk_stream:{integration.id}"},
                )
            ).scalar_one()
            if acquired:
                integration._lock_session = hold  # type: ignore[attr-defined]
                kept.append(integration)
            else:
                await hold.close()
        return kept

    # -- group serve loop ----------------------------------------------------

    async def _serve_group(
        self,
        app_key: str,
        integrations: list[Integration],
        gateway_base: str,
        signal: asyncio.Event,
    ) -> None:
        integration = integrations[0]  # group representative for config/secret
        app_secret = _decrypt_app_secret(integration, self._settings.jwt_secret)
        if not app_secret:
            for item in integrations:
                await self._set_stream_state(item, STATE_DOWN, backoff_seconds=0)
            logger.error(
                "dingtalk stream: undecryptable app_secret for app_key=%s — "
                "zero ingestion (credential equivalent of invalid signature)",
                app_key,
            )
            return
        base, maximum, heartbeat = _reconnect_config(integration)
        client = DingTalkStreamClient(
            app_key=app_key,
            app_secret=app_secret,
            gateway_base=gateway_base,
            ssl_context=self._ssl_context,
            http_factory=self._http_factory,
            ws_connect=self._ws_connect,
        )
        attempt = 0
        try:
            while not signal.is_set():
                await self._mark_group(integrations, STATE_RECONNECTING, attempt, base, maximum)
                try:
                    await client.open_connection()
                except StreamEndpointInsecure:
                    await self._mark_group(integrations, STATE_DOWN, attempt, base, maximum)
                    await self._interruptible_sleep(
                        compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng),
                        signal,
                    )
                    attempt += 1
                    continue
                except (StreamOpenError, httpx.HTTPError, OSError) as exc:
                    logger.error("dingtalk stream open failed (app_key=%s): %s", app_key, exc)
                    await self._mark_group(integrations, STATE_DOWN, attempt, base, maximum)
                    await self._interruptible_sleep(
                        compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng),
                        signal,
                    )
                    attempt += 1
                    continue
                await self._mark_group(integrations, STATE_CONNECTED, 0, base, maximum)
                attempt = 0
                immediate_reconnect = await self._frame_loop(
                    client, integrations, heartbeat, signal
                )
                await client.close()
                if signal.is_set():
                    break
                if not immediate_reconnect:
                    await self._interruptible_sleep(
                        compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng),
                        signal,
                    )
                    attempt += 1
                # disconnect topic ⇒ immediate reconnect (attempt stays low).
        finally:
            await client.close()
            for item in integrations:
                lock_session = getattr(item, "_lock_session", None)
                if lock_session is not None:
                    try:
                        await lock_session.execute(
                            text("SELECT pg_advisory_unlock(hashtext(:key))"),
                            {"key": f"dingtalk_stream:{item.id}"},
                        )
                    except Exception:  # noqa: BLE001 — unlock is best-effort
                        pass
                    await lock_session.close()

    async def _frame_loop(
        self,
        client: DingTalkStreamClient,
        integrations: list[Integration],
        heartbeat: float,
        signal: asyncio.Event,
    ) -> bool:
        """Consume frames until close/heartbeat-timeout/disconnect. Returns
        True when the caller should reconnect IMMEDIATELY (disconnect frame)."""
        by_robot_code = {
            str((i.config or {}).get("robot_code") or (i.config or {}).get("app_key") or ""): i
            for i in integrations
        }
        last_persist = 0.0
        while not signal.is_set():
            try:
                frame = await client.recv(timeout=heartbeat)
            except TimeoutError:
                logger.warning("dingtalk stream heartbeat timeout — reconnecting")
                return False
            except Exception:  # noqa: BLE001 — socket error ⇒ reconnect
                return False
            if frame is None:
                return False  # clean close ⇒ reconnect with backoff

            frame_type = str(frame.get("type") or "")
            headers = frame.get("headers") or {}
            topic = str(headers.get("topic") or "")

            if frame_type == "SYSTEM" and topic == "ping":
                # MUST ACK — echo the original payload data verbatim.
                ping_data = frame.get("data")
                frame_payload = frame.get("payload")
                if ping_data is None and isinstance(frame_payload, dict):
                    ping_data = frame_payload.get("data")
                await client.send_ack(frame, ping_data)
                continue
            if frame_type == "SYSTEM" and topic == "disconnect":
                logger.info("dingtalk stream: platform-requested disconnect — reconnecting")
                return True
            if frame_type == "CALLBACK" and topic == STREAM_MESSAGE_TOPIC:
                await self._ingest_message_frame(integrations, by_robot_code, frame)
                # ACK AFTER the ingest transaction commits — an un-ACKed
                # frame is redelivered; msgId dedup makes that idempotent.
                await client.send_ack(frame, "received")
                continue
            if frame_type == "CALLBACK" and topic == STREAM_CARD_TOPIC:
                # Card authorization chain — MES-89 wires the handler.
                logger.info("dingtalk stream: card callback frame received (audit only)")
                await client.send_ack(frame, "received")
                continue

            # Persist last_frame_at (throttled, NO realtime broadcast —
            # transitions broadcast; liveness refresh is a state write only).
            import time as _time

            now_epoch = _time.time()
            if now_epoch - last_persist > _FRAME_PERSIST_INTERVAL_SECONDS:
                last_persist = now_epoch
                for integration in integrations:
                    await self._set_stream_state(
                        integration, STATE_CONNECTED, backoff_seconds=0, broadcast=False
                    )
        return False

    async def _ingest_message_frame(
        self,
        integrations: list[Integration],
        by_robot_code: dict[str, Integration],
        frame: dict[str, Any],
    ) -> None:
        payload = frame.get("data")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, json.JSONDecodeError):
                payload = None
        if not isinstance(payload, dict):
            logger.warning("dingtalk stream: unparseable message frame — skipped")
            return
        robot_code = str(payload.get("robotCode") or "")
        integration = by_robot_code.get(robot_code)
        if integration is None:
            integration = integrations[0] if len(integrations) == 1 else None
        if integration is None:
            logger.warning(
                "dingtalk stream: no integration for robotCode=%s — skipped", robot_code
            )
            return
        try:
            envelope = normalize_message_payload(
                payload,
                max_chars=int(getattr(self._settings, "im_inbound_text_max_chars", 4000)),
                channel="stream",
            )
        except Exception:  # noqa: BLE001 — malformed payload: audit-log, never crash
            logger.exception("dingtalk stream: payload normalization failed")
            return
        guardrails = None
        if self._redis is not None:
            from mesh.integrations.guardrails import InboundGuardrails

            guardrails = InboundGuardrails(
                self._redis,
                per_identity_per_min=self._settings.im_inbound_per_identity_per_min,
                per_conversation_per_min=self._settings.im_inbound_per_conversation_per_min,
                max_pending_per_conversation=self._settings.im_queue_max_pending_per_conversation,
            )
        async with self._session_factory() as session, session.begin():
            await ingest_verified_event(
                session,
                integration=integration,
                envelope=envelope,
                now=self._now(),
                guardrails=guardrails,
            )

    # -- state persistence ---------------------------------------------------

    async def _interruptible_sleep(self, seconds: float, signal: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(signal.wait(), timeout=max(0.0, seconds))
        except TimeoutError:
            pass

    async def _mark_group(
        self,
        integrations: list[Integration],
        state: str,
        attempt: int,
        base: float,
        maximum: float,
    ) -> None:
        backoff = compute_backoff(attempt, base=base, maximum=maximum, rng=self._rng)
        for integration in integrations:
            await self._set_stream_state(
                integration, state, backoff_seconds=backoff if state != STATE_CONNECTED else 0
            )

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
        payload_state: dict[str, Any] = {
            "state": state,
            "last_frame_at": (integration.stream_state or {}).get("last_frame_at"),
            "last_attempt_at": now.isoformat(),
            "backoff_seconds": round(float(backoff_seconds), 2),
        }
        if state == STATE_CONNECTED:
            payload_state["last_frame_at"] = now.isoformat()
        async with self._session_factory() as session, session.begin():
            row = await session.get(Integration, integration.id)
            if row is None:
                return
            previous = dict(row.stream_state or {})
            row.stream_state = {**previous, **payload_state}
            row.updated_at = now
            if broadcast:
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
                    idempotency_key=(
                        f"integration-stream:{row.id}:{state}:{now.isoformat()}"
                    ),
                )
        # Keep the in-memory copy coherent for subsequent decisions.
        integration.stream_state = {**(integration.stream_state or {}), **payload_state}


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
